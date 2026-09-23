"""记账 / 流水 / 凭证 / 交易更正 / 月度汇总（查账）"""
import logging

from fastapi import APIRouter, HTTPException, Query

import ai
import db
import notifications
import tax as taxcalc
from categories import is_known_category, normalize_category
from schemas import (OrderIn, RefundIn, TransactionEditIn, VoidIn)

log = logging.getLogger("orders")
router = APIRouter(prefix="/api", tags=["orders"])

@router.post("/orders")
def create_order(data: OrderIn):
    """一句话记账：AI解析 + 熟客归档 + 借贷凭证。

    **金额没听懂时不落库**：旧实现会插一行 amount=0 的流水并弹一句
    "金额没听清，只记了流水"，结果流水里留下一条既不进合计、又不会消失的
    幽灵记录 —— 店主当天发现不了，月底对不上账也查不出来源。
    现在改为返回 amount_missing + draft（草稿字段），由界面追问"这笔多少钱"，
    用户补上金额后再记一次。熟客仍然照常归档（这一步没有副作用，且补记时要用）。

    **一句话多笔**：店主常说"今天收入1250，支出320"这种一句两笔。以前要么把
    两笔加成一个数、要么只记第一笔（实测 8 条多笔用例 0 通过）。现在解析层会
    给出 transactions 列表，这里**逐笔记账**（各自生成凭证），并返回 recorded_list。
    """
    # 补记路径：界面把 AI 已解析的字段（item/category）连同金额一起带回来时，
    # 直接采信这些字段，**不再调一次 AI** —— 既省一次调用，也保证补记的科目与
    # 熟客跟第一次解析完全一致（重新解析有漂移的风险）。
    explicit = bool((data.item or "").strip() and data.amount is not None
                    and float(data.amount or 0) > 0)
    if explicit:
        parsed = {
            "customer": data.customer,
            "item": data.item.strip(),
            "amount": data.amount,
            "note": data.note,
            "tags": "",
            "category": data.category or "主营业务收入",
            "trans_type": data.trans_type or "income",
        }
    else:
        parsed = ai.parse_transaction(data.text)

    # 入口防呆：方向、分类或多笔笔数低把握时，不急着落库。
    # 金额缺失已有追问机制；这里处理的是更隐蔽的“听懂了但可能听错了”。
    if not explicit and parsed.get("needs_check"):
        return {
            "order_id": None,
            "parsed": parsed,
            "customer_id": None,
            "customer_new": False,
            "amount_missing": parsed.get("amount") is None,
            "needs_check": True,
            "check_question": parsed.get("question") or "这笔账我先核对一下再记",
            "draft": {
                "text": data.text,
                "item": parsed.get("item", "") or data.text,
                "customer": data.customer or parsed.get("customer", ""),
                "amount": parsed.get("amount"),
                "trans_type": parsed.get("trans_type", "income"),
                "category": normalize_category(parsed.get("category", "")),
                "note": parsed.get("note", ""),
            },
            "voucher": None,
            "summary": db.today_summary(),
        }

    # 一句话多笔：逐笔记账（见函数 docstring）
    subs = parsed.get("transactions") or []
    if len(subs) >= 2:
        return _record_multi(parsed, subs, data.text)

    customer = data.customer or parsed.get("customer", "")
    cid = None
    is_new = False
    if customer:
        # set_favorite=False：这里传的是"本笔买了什么"，不是"常点什么"。
        # 若让它覆盖，每记一笔账熟客的常点就变一次（实测会把
        # "肉包,豆浆" 冲成 "两个肉包一杯豆浆"）。新客户仍会用它做首次学习。
        cid, is_new = db.find_or_create_customer(
            customer, tags=parsed.get("tags", ""), favorite=parsed.get("item", ""),
            set_favorite=False)
    amount = data.amount if data.amount is not None else parsed.get("amount")
    trans_type = parsed.get("trans_type", "income")

    # 分类归一：模型可能返回近义词（实测返回过"工资"）。旧实现直接判
    # is_known_category → False → 静默兜底到办公费，账本品类与凭证科目对不上。
    # 现在先归一，归一时记日志；仍认不出来才走关键词兜底。
    raw_category = parsed.get("category", "") or ""
    category = normalize_category(raw_category)
    if category and raw_category.strip() != category:
        log.info("分类已归一：%r → %r", raw_category, category)
    if not category:
        if raw_category:
            log.warning("分类无法归一（模型返回 %r），改用关键词兜底", raw_category)
        category, trans_type = db.detect_category(data.text)

    # 金额缺失（或显式给 0）：不落库，返回草稿让界面追问
    if amount is None or float(amount or 0) <= 0:
        log.info("金额缺失，未落库（draft）：text=%r customer=%s", data.text, customer or "-")
        return {
            "order_id": None,
            "parsed": parsed,
            "customer_id": cid,
            "customer_new": cid and is_new,
            "amount_missing": True,
            "draft": {
                "text": data.text,
                "item": parsed.get("item", "") or data.text,
                "customer": customer,
                "trans_type": trans_type,
                "category": category,
                "note": parsed.get("note", ""),
            },
            "voucher": None,
            "friendly_category": db.FRIENDLY_NAMES.get(category, category),
            "summary": db.today_summary(),
            "safety_warning": "这笔账还缺金额，没有记进账本 —— 补上金额我再记一次",
        }

    tid, voucher = db.add_transaction(
        cid, parsed.get("item", "") or data.text, amount,
        trans_type=trans_type, category=category,
        counterparty=customer, note=parsed.get("note", ""))
    safety_warning = taxcalc.detect_boundary(data.text) or taxcalc.check_amount_guard(amount)
    log.info("order created tid=%s type=%s category=%s amount=%s customer=%s",
             tid, trans_type, category, amount, customer or "-")
    result = {
        "order_id": tid, "parsed": parsed,
        "customer_id": cid, "customer_new": cid and is_new,
        "amount_missing": False, "voucher": voucher,
        # 把"这笔到底记成了什么"回给前端：金额/方向/分类/熟客四项都摆出来，
        # 界面上可当场核对与就地更正（AI 理解错了才有机会被发现）
        "recorded": {
            "transaction_id": tid,
            "amount": amount,
            "trans_type": trans_type,
            "category": category,
            "friendly_category": db.FRIENDLY_NAMES.get(category, category),
            "item": parsed.get("item", "") or data.text,
            "customer": customer,
            "category_normalized": bool(raw_category and raw_category.strip() != category),
            "raw_category": raw_category,
            "evidence": _evidence(amount, category, raw_category, customer, trans_type,
                                  parsed, data.text),
        },
        "friendly_category": db.FRIENDLY_NAMES.get(category, category),
        "summary": db.today_summary(), "safety_warning": safety_warning,
    }
    # 开放 API：记账成功后异步通知 webhook 订阅者（不阻塞响应）
    notifications.dispatch_event_async(
        "order_created", f"记账成功：{parsed.get('item', '') or data.text}",
        f"{'收入' if trans_type == 'income' else '支出'} {amount} 元，分类「{category}」",
        business_key=f"txn-{tid}")
    return result


def _evidence(amount, category, raw_category, customer, trans_type, parsed, text) -> dict:
    """结论可解释：这笔账"凭什么这么记"的依据（供界面展开核对）。"""
    return {
        "text": text,
        "amount_source": "金额来自解析结果" if amount is not None else "未取到金额",
        "category_source": (
            "模型返回并已归一" if raw_category and raw_category.strip() != category
            else ("模型返回" if raw_category else "关键词兜底"),
        ),
        "category_raw": raw_category or "",
        "direction": "收入" if trans_type == "income" else "支出",
        "customer_matched": bool(customer),
        "item": parsed.get("item", "") or text,
        "voucher_rule": "复式记账：收入→借库存现金/贷主营业务收入；支出→借相关费用/贷库存现金",
    }


def _record_multi(parsed: dict, subs: list[dict], text: str) -> dict:
    """一句话里有多笔：逐笔记账，各自生成凭证。

    取舍说明：
      - 每笔的熟客各自 find_or_create（"刘姐9块，赵姐15块"是两个人）；
      - 分类逐笔归一 + 关键词兜底（不能用整句去兜底，否则两笔会串科目）；
      - **只要有一笔没听出金额就整句都不落库**，把缺的那几笔列出来让店主补。
        理由是"记一半"最坑：店主以为记完了，其实少一笔，月底对不上账。
        返回 missing 列表，界面一次问全。
      - 顶层 recorded 取第一笔，保证老前端不改也能显示一条；
        新前端读 recorded_list 展示全部（每条都能就地更正）。
    """
    missing = [s for s in subs if not s.get("amount")]
    if missing:
        log.info("多笔中有 %s 笔缺金额，未落库：text=%r", len(missing), text)
        return {
            "order_id": None,
            "parsed": parsed,
            "customer_id": None,
            "customer_new": False,
            "amount_missing": True,
            "multi": True,
            "missing": [
                {"item": s.get("item", "") or text,
                 "customer": s.get("customer", ""),
                 "trans_type": s.get("trans_type", "income"),
                 "category": s.get("category", "")}
                for s in missing],
            "subs": subs,
            "draft": {
                "text": text,
                "item": (missing[0].get("item") or text),
                "customer": missing[0].get("customer", ""),
                "trans_type": missing[0].get("trans_type", "income"),
                "category": missing[0].get("category", ""),
                "note": "",
            },
            "voucher": None,
            "friendly_category": db.FRIENDLY_NAMES.get(
                missing[0].get("category", ""), missing[0].get("category", "")),
            "summary": db.today_summary(),
            "safety_warning": f"这句话里听出 {len(subs)} 笔，其中 "
                              f"{len(missing)} 笔没说金额 —— 都没记账，补上金额我再记",
        }

    recorded_list = []
    for sub in subs:
        cust = (sub.get("customer") or "").strip()
        cid = None
        if cust:
            cid, _is_new = db.find_or_create_customer(
                cust, tags="", favorite=sub.get("item", ""), set_favorite=False)
        raw_cat = (sub.get("category") or "").strip()
        category = normalize_category(raw_cat)
        ttype = sub.get("trans_type") or "income"
        if not category:
            # 用这一笔自己的文本兜底，不要用整句（整句会把两笔的科目混起来）
            category, ttype2 = db.detect_category(
                f"{sub.get('item', '')} {raw_cat}".strip() or text)
            if ttype not in ("income", "expense"):
                ttype = ttype2
        tid, voucher = db.add_transaction(
            cid, sub.get("item", "") or text, sub["amount"],
            trans_type=ttype, category=category,
            counterparty=cust, note=sub.get("note", ""))
        recorded_list.append({
            "transaction_id": tid,
            "amount": sub["amount"],
            "trans_type": ttype,
            "category": category,
            "friendly_category": db.FRIENDLY_NAMES.get(category, category),
            "item": sub.get("item", "") or text,
            "customer": cust,
            "voucher_no": (voucher or {}).get("voucher_no") if voucher else None,
            "category_normalized": bool(raw_cat and raw_cat != category),
            "raw_category": raw_cat,
        })

    log.info("order created（多笔 %s 条）：text=%r", len(recorded_list), text)
    first = recorded_list[0]
    return {
        "order_id": first["transaction_id"],
        "parsed": parsed,
        "customer_id": None,
        "customer_new": False,
        "amount_missing": False,
        "voucher": None,
        "multi": True,
        "recorded": first,               # 向后兼容：老前端至少显示第一笔
        "recorded_list": recorded_list,  # 新前端：逐条展示与更正
        "friendly_category": first["friendly_category"],
        "summary": db.today_summary(),
        "safety_warning": f"这句话里听出 {len(recorded_list)} 笔，都记上了，请核一下",
    }


@router.get("/orders/today")
def orders_today():
    return db.today_summary()
@router.get("/orders/monthly")
def orders_monthly(year: int | None = None, month: int | None = None):
    return db.monthly_summary(year, month)

@router.get("/vouchers")
def vouchers(limit: int = 50):
    return db.list_vouchers(limit)

@router.get("/transactions")
def transactions(year: int | None = None, month: int | None = None, limit: int = 100):
    return db.list_transactions(year, month, limit)


# ---------------- 交易更正（编辑 / 作废 / 退货冲销） ----------------
# 财务数据必须可更正且留痕：AI 会分类错误、店主会录错金额、顾客会退货。
# 这三个接口原先完全缺失 —— 一旦入账就永远改不了。

@router.get("/transactions/{tid}")
def transaction_detail(tid: int):
    """单笔交易详情（含更正历史），供前端点击某笔查看/更正。"""
    txn = db.get_transaction(tid)
    if not txn:
        raise HTTPException(404, "交易不存在")
    return {"transaction": txn, "audits": db.list_transaction_audits(tid)}


@router.get("/orders/{tid}/explain")
def order_explain(tid: int):
    """结论可解释：这笔账凭什么这么记（含复式分录，供店主当场核对）。"""
    txn = db.get_transaction(tid)
    if not txn:
        raise HTTPException(404, "交易不存在")
    with db.get_conn() as conn:
        v = conn.execute(
            "SELECT * FROM vouchers WHERE transaction_id=? ORDER BY id DESC LIMIT 1",
            (tid,)).fetchone()
        entries = []
        if v:
            entries = [dict(r) for r in conn.execute(
                "SELECT account_code, account_name, direction, amount "
                "FROM voucher_entries WHERE voucher_id=? ORDER BY id, direction",
                (v["id"],)).fetchall()]
    direction = "收入" if txn.get("trans_type") == "income" else "支出"
    legs = "、".join(
        f"{'借' if e['direction'] == 'debit' else '贷'} {e['account_name']} {e['amount']:.2f}"
        for e in entries)
    who = txn.get("counterparty") or "顾客"
    sentence = (f"「{who} · {txn.get('item') or '—'}」记为{direction} "
                f"{float(txn.get('amount') or 0):.2f} 元，科目「{txn.get('category') or '—'}」"
                + (f"；分录：{legs}。" if legs else "。（该笔暂无凭证分录）"))
    return {
        "transaction": txn,
        "voucher": dict(v) if v else None,
        "entries": entries,
        "explain": sentence,
        "basis": {
            "rule": "复式记账：每笔必有借有贷，金额相等",
            "category": txn.get("category") or "",
            "source_text": txn.get("note") or txn.get("item") or "",
            "direction": direction,
        },
    }


@router.post("/transactions/{tid}")
def transaction_edit(tid: int, data: TransactionEditIn):
    """更正一笔交易（旧凭证作废并生成新凭证，全程留痕）。"""
    fields = {k: v for k, v in data.model_dump().items()
              if k != "reason" and v is not None}
    if not fields:
        raise HTTPException(400, "没有要修改的内容")
    try:
        return db.edit_transaction(tid, reason=data.reason, **fields)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/transactions/{tid}/void")
def transaction_void(tid: int, data: VoidIn | None = None):
    """作废一笔交易（冲销凭证；记录保留可查，不计入合计）。"""
    reason = (data.reason if data else "") or ""
    try:
        return db.void_transaction(tid, reason=reason)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/transactions/{tid}/refund")
def transaction_refund(tid: int, data: RefundIn | None = None):
    """退货冲销：生成金额为负的关联记录，与原记录相加抵消。"""
    amount = data.amount if data else None
    reason = (data.reason if data else "") or ""
    try:
        return db.refund_transaction(tid, amount=amount, reason=reason)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/audits")
def audits(limit: int = Query(default=100, ge=1, le=500)):
    """最近的更正历史（谁在什么时候改了什么）。"""
    return {"audits": db.list_transaction_audits(limit=limit)}
