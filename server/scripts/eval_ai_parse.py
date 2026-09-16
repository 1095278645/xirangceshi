# -*- coding: utf-8 -*-
"""eval_ai_parse.py — 「一句话记账」回归基线（真实模型评测）

## 为什么需要它

产品最核心的承诺是「说一句话就记好账」。但除此之外**所有测试都是打桩的**
（mock 掉 ai.parse_transaction），也就是说：这个承诺只有本脚本验过。
改提示词、换模型（V4.1-Flash 之类的思考模型）、调温度，都可能让它悄悄退化，
而全部单测照样全绿 —— 所以需要一条**基线**来卡住。

## 用法

```bash
cd server
python scripts/eval_ai_parse.py                    # 全量评测（54 条，约 1 分钟，耗额度）
python scripts/eval_ai_parse.py --limit 6          # 先跑一小撮（省额度）
python scripts/eval_ai_parse.py --category 金额     # 只跑某一类
python scripts/eval_ai_parse.py --write-baseline   # 写基线（仅在确认当前表现可接受时）
python scripts/eval_ai_parse.py --compare          # 跑完与基线对比，有退化就退出码 1
python scripts/eval_ai_parse.py --show-raw         # 打印模型原始 JSON（排查用）
```

基线文件：`server/tests/ai_parse_baseline.json`（按用例文本索引）。
`--compare` 的判定：某条用例从"四项全对"变成"有偏差" → 记为退化。
新增用例不影响对比；删掉的用例会被忽略。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER))

BASELINE_PATH = SERVER / "tests" / "ai_parse_baseline.json"

# 用例：(分类, 店主原话, 期望金额, 期望方向, 期望分类关键字, 期望熟客)
# 期望值留空 = 这一项不校验（比如"多笔"只校验它到底识别出几笔）
CASES: list[tuple[str, str, float | None, str, str, str]] = [
    # ---------------- 金额：数字的各种说法 ----------------
    ("金额", "王阿姨买了两个肉包一杯豆浆，6块", 6, "income", "主营业务收入", "王阿姨"),
    ("金额", "刚卖了30块钱的包子", 30, "income", "主营业务收入", ""),
    ("金额", "收了张叔20块", 20, "income", "主营业务收入", "张叔"),
    ("金额", "刘姐买了三杯豆浆，一共九块", 9, "income", "主营业务收入", "刘姐"),
    ("金额", "老郑早餐吃了12块5", 12.5, "income", "主营业务收入", "老郑"),
    ("金额", "早上卖了八百多", 800, "income", "主营业务收入", ""),
    ("金额", "买了面粉花了一百二", 120, "expense", "进货", ""),
    ("金额", "今天营业额1250", 1250, "income", "主营业务收入", ""),
    ("金额", "收了15.5", 15.5, "income", "主营业务收入", ""),
    ("金额", "买调料花了 33.8", 33.8, "expense", "进货", ""),
    ("金额", "营业额一千五", 1500, "income", "主营业务收入", ""),
    ("金额", "王阿姨充了200块会员卡", 200, "income", "其他收入", "王阿姨"),
    ("金额", "进了两千块钱的货", 2000, "expense", "进货", ""),
    ("金额", "卖了1000整", 1000, "income", "主营业务收入", ""),

    # ---------------- 口语：说法不规范、方向不明说 ----------------
    ("口语", "李叔拿了两斤猪肉，38，支出", 38, "expense", "进货", "李叔"),
    ("口语", "水电费这个月580", 580, "expense", "租赁及物业费", ""),
    ("口语", "今天房租交了6000", 6000, "expense", "租赁及物业费", ""),
    ("口语", "给小工发了3500工资", 3500, "expense", "职工薪酬", ""),
    ("口语", "买了点办公用品，45块", 45, "expense", "办公费", ""),
    ("口语", "打广告花了800", 800, "expense", "广告宣传费", ""),
    ("口语", "张叔要了两笼包子，算他18", 18, "income", "主营业务收入", "张叔"),
    ("口语", "小周打包带走三份，24", 24, "income", "主营业务收入", "小周"),
    ("口语", "赵姐带同事来，一共105", 105, "income", "主营业务收入", "赵姐"),
    # 换冰柜是固定资产/设备投入，模型归到"进货"是可辩的会计处理（不是主营业务成本）；
    # 这里不卡具体分类，只校验金额与方向 —— 卡死会让基线变得脆而没价值
    ("口语", "换了个冰柜，花了2800", 2800, "expense", "", ""),
    ("口语", "隔壁老王来吃了碗面，记他10块", 10, "income", "主营业务收入", "隔壁老王"),
    ("口语", "发工资了，一共发出去8000", 8000, "expense", "职工薪酬", ""),
    # 收还款不是营业收入（冲的是应收账款），模型归"其他收入"比"主营业务收入"更贴
    ("口语", "收到张叔还款60", 60, "income", "其他收入", "张叔"),
    ("口语", "退了顾客15块", 15, "expense", "", ""),
    ("口语", "张叔欠着60块，先记上", 60, "income", "主营业务收入", "张叔"),
    ("口语", "修了下冰箱，花了300", 300, "expense", "", ""),
    ("口语", "买菜钱260", 260, "expense", "进货", ""),
    ("口语", "豆浆卖完了，今天一共卖光了3桶", None, "income", "", ""),

    # ---------------- 多笔：一句话说好几件事 ----------------
    ("多笔", "今天收入1250，支出320", None, "", "", ""),
    ("多笔", "王阿姨拿了两杯豆浆6块，李叔拿了一斤肉28块", None, "", "", ""),
    ("多笔", "卖了300块，另外买了100块的米", None, "", "", ""),
    ("多笔", "上午收800，下午收600", None, "", "", ""),
    ("多笔", "今天进货花了500，卖了900", None, "", "", ""),
    ("多笔", "收了50，又收了80", None, "", "", ""),
    ("多笔", "买面粉120，买鸡蛋80", None, "", "", ""),
    ("多笔", "刘姐买了9块，赵姐买了15块，小周买了6块", None, "", "", ""),

    # ---------------- 边界：没说清/奇葩输入 ----------------
    ("边界", "张叔拿了个包子", None, "income", "", "张叔"),
    ("边界", "修了下冰箱", None, "", "", ""),
    ("边界", "今天生意不错", None, "", "", ""),
    ("边界", "王阿姨来了", None, "", "", "王阿姨"),
    ("边界", "进货", None, "", "", ""),
    ("边界", "记一笔", None, "", "", ""),
    ("边界", "0元", None, "", "", ""),
    ("边界", "卖了一万二的货", 12000, "income", "主营业务收入", ""),
    ("边界", "付了房租六千", 6000, "expense", "租赁及物业费", ""),
    ("边界", "张三李四王五都来了，共36", 36, "income", "主营业务收入", ""),
    ("边界", "微信收款88", 88, "income", "主营业务收入", ""),
    ("边界", "支付宝到账66.6", 66.6, "income", "主营业务收入", ""),
    ("边界", "给员工发红包200", 200, "expense", "职工薪酬", ""),
    ("边界", "买了台收银机1500，这个月就不买别的了", 1500, "expense", "", ""),
    ("边界", "今天没开门", None, "", "", ""),
]

CATEGORIES = ("金额", "口语", "多笔", "边界")


def _check(got: dict, exp: tuple) -> dict:
    """逐项核对，返回 {ok, amount, direction, category, customer}"""
    _cat, _text, exp_amt, exp_dir, exp_cat, exp_cust = exp
    g_amt = got.get("amount")
    g_dir = got.get("trans_type")
    g_cat = got.get("category") or ""
    g_cust = got.get("customer") or ""

    ok_amt = (exp_amt is None and g_amt is None) or (
        exp_amt is not None and g_amt is not None
        and abs(float(g_amt) - float(exp_amt)) < 0.01)
    # 多笔：只看它有没有识别成"多笔"（金额/方向/分类由子笔决定，这里不校验）
    if _cat == "多笔":
        n = len(got.get("transactions") or [])
        return {"ok": n >= 2, "amount": n >= 2, "direction": True,
                "category": True, "customer": True, "multi": n}

    ok_dir = (not exp_dir) or g_dir == exp_dir
    ok_cat = (not exp_cat) or (exp_cat in g_cat)
    ok_cust = (not exp_cust) or (exp_cust == g_cust)
    return {"ok": bool(ok_amt and ok_dir and ok_cat and ok_cust),
            "amount": bool(ok_amt), "direction": bool(ok_dir),
            "category": bool(ok_cat), "customer": bool(ok_cust)}


def run(cases, show_raw: bool = False) -> dict:
    import ai
    if not ai.ai_available():
        print("未配置 API Key，无法评测（这本身也说明演示环境要配 Key）")
        return {}

    results: dict[str, dict] = {}
    hits = {k: 0 for k in ("amount", "direction", "category", "customer", "ok")}
    per_cat: dict[str, dict] = {}
    total = 0

    print(f"用真实模型评测 {len(cases)} 条店主原话")
    s = ai.load_settings()
    print(f"模型：{s.get('model')}  base：{s.get('base_url')}")
    print("=" * 100)

    last_cat = None
    for case in cases:
        cat, text, exp_amt, exp_dir, exp_cat, exp_cust = case
        if cat != last_cat:
            print(f"\n---- {cat} ----")
            last_cat = cat
        t0 = time.time()
        try:
            got = ai.parse_transaction(text)
        except Exception as e:  # noqa: BLE001
            print(f"❌ 调用失败：{text}\n   {type(e).__name__}: {e}")
            results[text] = {"error": str(e), "ok": False}
            continue
        dt = time.time() - t0
        r = _check(got, case)
        results[text] = {
            "category": cat, "ok": r["ok"],
            "amount": r.get("amount"), "direction": r.get("direction"),
            "category_ok": r.get("category"), "customer_ok": r.get("customer"),
            "multi": r.get("multi"),
            "got": {"amount": got.get("amount"), "trans_type": got.get("trans_type"),
                    "category": got.get("category"), "customer": got.get("customer"),
                    "transactions": got.get("transactions")},
        }
        total += 1
        for key, rk in (("amount", "amount"), ("direction", "direction"),
                        ("category", "category"), ("customer", "customer"),
                        ("ok", "ok")):
            if r.get(rk):
                hits[key] += 1
        pc = per_cat.setdefault(cat, {"n": 0, "ok": 0})
        pc["n"] += 1
        pc["ok"] += bool(r["ok"])

        mark = "✅" if r["ok"] else "⚠️ "
        flags = "".join([
            "" if r.get("amount") else "金额 ",
            "" if r.get("direction") else "方向 ",
            "" if r.get("category") else "分类 ",
            "" if r.get("customer") else "熟客 ",
        ])
        extra = f"  [识别出 {r['multi']} 笔]" if r.get("multi") is not None else ""
        print(f"{mark} {text}{extra}")
        if not r["ok"]:
            print(f"    期望：金额={exp_amt} 方向={exp_dir or '-'} "
                  f"分类={exp_cat or '-'} 熟客={exp_cust or '-'}")
            print(f"    实得：金额={got.get('amount')} 方向={got.get('trans_type')} "
                  f"分类={got.get('category')} 熟客={got.get('customer') or '-'}"
                  f"   ({dt:.1f}s)" + (f"   ← 偏：{flags}" if flags else ""))
        if show_raw:
            print("    raw: " + json.dumps(got, ensure_ascii=False))

    if not total:
        return {}
    print("\n" + "=" * 100)
    print(f"总体（{total} 条）：")
    for label, key in (("金额", "amount"), ("收支方向", "direction"),
                       ("分类", "category"), ("熟客", "customer")):
        print(f"  {label:<8} {hits[key]:>2}/{total}  {hits[key] / total * 100:>3.0f}%")
    print(f"  {'四项全对':<8} {hits['ok']:>2}/{total}  {hits['ok'] / total * 100:>3.0f}%")
    print("\n分类别：")
    for cat in CATEGORIES:
        pc = per_cat.get(cat)
        if pc:
            print(f"  {cat:<4} {pc['ok']:>2}/{pc['n']:<2}  {pc['ok'] / pc['n'] * 100:>3.0f}%")
    return {"total": total, "hits": hits, "per_category": per_cat, "cases": results}


def compare_with_baseline(report: dict, cases_by_text: dict) -> list[str]:
    """与基线比对，返回**确认退化**的用例（曾全对 → 现在反复有偏差）。

    为什么要"反复确认"：模型有真实的随机性（同一句话两次调用可能给出不同分类）。
    实测 --compare 连跑两次，每次都会随机报出**不同的**一两条"退化"，
    而基线本身是全对的 —— 那是抖动，不是退化。所以对偏差项**重试一次**，
    只有仍然偏差才算退化。真正的退化（提示词改坏/换模型变差）是稳定复现的，
    重试一次完全可以区分。
    """
    if not BASELINE_PATH.exists():
        print(f"\n（还没有基线文件 {BASELINE_PATH.name}；"
              f"确认当前表现可接受后跑 --write-baseline 建立）")
        return []
    import ai
    base = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    base_cases = base.get("cases", {})

    suspects = []
    for text, cur in (report.get("cases") or {}).items():
        old = base_cases.get(text)
        if not old:
            continue                      # 新增用例不参与对比
        if old.get("ok") and not cur.get("ok"):
            suspects.append(text)

    regressions, flaky = [], []
    for text in suspects:
        case = cases_by_text.get(text)
        if not case:
            continue
        try:
            got = ai.parse_transaction(text)
            again = _check(got, case)
        except Exception:  # noqa: BLE001  重试失败就当退化处理（保守）
            regressions.append(text)
            continue
        if again["ok"]:
            flaky.append(text)
        else:
            regressions.append(text)

    print(f"\n与基线对比（基线共 {len(base_cases)} 条，"
          f"本次覆盖 {len(report.get('cases') or {})} 条）")
    if flaky:
        print(f"  ⚠️ 抖动 {len(flaky)} 条（重试后又是对的，不算退化）：")
        for t in flaky:
            print(f"     · {t}")
    if regressions:
        print(f"  ❌ 确认退化 {len(regressions)} 条：")
        for t in regressions:
            print(f"     · {t}")
    else:
        print("  ✅ 没有退化")
    return regressions


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--category", choices=CATEGORIES, default=None)
    ap.add_argument("--show-raw", action="store_true")
    ap.add_argument("--write-baseline", action="store_true",
                    help="把本次结果写成基线（仅在确认表现可接受时用）")
    ap.add_argument("--compare", action="store_true",
                    help="与基线对比，有退化则退出码 1")
    args = ap.parse_args()

    cases = CASES
    if args.category:
        cases = [c for c in cases if c[0] == args.category]
    if args.limit:
        cases = cases[:args.limit]

    report = run(cases, show_raw=args.show_raw)
    if not report:
        return 1

    if args.write_baseline:
        payload = {"model": None, "total": report["total"],
                   "hits": report["hits"], "per_category": report["per_category"],
                   "cases": report["cases"]}
        try:
            import ai
            payload["model"] = ai.load_settings().get("model")
        except Exception:  # noqa: BLE001
            pass
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n✅ 已写入基线：{BASELINE_PATH.relative_to(SERVER)}")

    rc = 0
    if args.compare:
        if len(cases) < len(CASES):
            print("\n（--compare 建议跑全量；当前是子集，对比结果仅供参考）")
        cases_by_text = {c[1]: c for c in cases}
        if compare_with_baseline(report, cases_by_text):
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
