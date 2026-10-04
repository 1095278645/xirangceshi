"""shop_relations.py — 跨域关系层：实体关系抽取 / 增量合并 / 图谱检索

参考图「看得懂 · GraphRAG / LightRAG」：不新增原始数据，只把已经存在的单据（流水 / 商品
档案 / 发票 / 赊账 / 记忆点）显式连成**带证据的边**，让掌柜能回答"这条结论怎么串起来的"。
三条硬约束：① 证据全部来自原始表，**一次 AI 都不调用**；② 增量合并（LightRAG 思想）：重算
抽取 → 只 upsert 变化的边 → 回收这次没抽到的边；③ 护栏：抽取为空一律不清空索引，回收必须
`full=True` 且 `confirm=True`（见 `merge_relations`）。SQL 全在 `db_relations`，本层只做编排。
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime

import config
import db
import db_relations

log = logging.getLogger("shop_relations")

__all__ = ["ENTITY_TYPES", "RELATION_TYPES", "RELATION_ZH", "edge_key", "last_extract_stats",
           "extract_edges", "merge_relations", "entity_landscape", "knowledge_chain",
           "relations_changed_since", "relations_summary"]

# ---------------- 冻结契约：实体类型 / 关系类型 / 中文展示 ----------------
ENTITY_TYPES = ("customer", "product", "supplier", "invoice", "transaction", "debt", "unknown")
RELATION_TYPES = ("purchased", "sold_in", "supplied_by", "provides", "invoice_party",
                  "owed_to", "owes_me", "mentions", "linked_transaction")
RELATION_ZH = {"purchased": "买过", "sold_in": "卖出在", "supplied_by": "由供货",
               "provides": "供应", "invoice_party": "开票对象", "owed_to": "我欠他",
               "owes_me": "他欠我", "mentions": "提及", "linked_transaction": "关联流水"}

# ---------------- 规模上限（scan 阈值走 config，不写魔法数） ----------------
TRANSACTION_SCAN_HARD_CAP = 5000   # 一次抽取最多扫多少笔流水（config 之上的硬上限）
MONTHS_LOOKBACK = 12               # 往前回看几个月找流水
EDGE_LOOKUP_CAP = 20000            # 一次最多取多少条生效边的 key/明细
MAX_CHANGED_EDGES = 20             # 返回里"变了什么"的条数上限
LANDSCAPE_EDGE_LIMIT = 50          # 关系地图最多展示多少条边
NEIGHBOR_LIMIT = 200               # 追线索时的邻域上限
MAX_CHAIN_DEPTH = 3
MAX_PATHS = 10
CHECKPOINT_DOMAIN = "ledger"       # 复用既有 domain_context，不新增表
CHECKPOINT_KEY = "relation_checkpoint"

_LAST_SCAN: dict = {}   # 最近一次 extract_edges 的扫描规模（供 merge 如实说明"扫了多少"）


def last_extract_stats() -> dict:
    """最近一次抽取的扫描统计（transactions_scanned / scan_limit / truncated 等）。"""
    return dict(_LAST_SCAN)


def edge_key(subject_type, subject_ref, relation, object_type, object_ref) -> str:
    """边的确定性身份：sha256(五元组按 \\n 连接)[:32]，前缀 "sha256:"。
    幂等全靠它 —— 同一条关系无论抽多少次都是同一个 key，`upsert_edge` 据此决定 INSERT 还是
    只刷新 last_seen。
    """
    raw = "\n".join(str(x if x is not None else "")
                    for x in (subject_type, subject_ref, relation, object_type, object_ref))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _key_of(edge: dict) -> str:
    return edge.get("edge_key") or edge_key(
        edge.get("subject_type"), edge.get("subject_ref"), edge.get("relation"),
        edge.get("object_type"), edge.get("object_ref"))


def _norm(v) -> str:
    return str(v if v is not None else "").strip()


def _money(v) -> str:
    try:
        return f"{float(v or 0):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _edge(st, sref, slabel, rel, ot, oref, olabel, evidence, confidence) -> dict:
    return {"subject_type": st, "subject_ref": sref, "subject_label": slabel,
            "relation": rel, "object_type": ot, "object_ref": oref,
            "object_label": olabel, "evidence": evidence, "confidence": confidence}


def _match_product(text: str, names: list[str]) -> str:
    """商品名精确子串匹配，命中最长的一个（避免"豆浆"被"浆"抢先命中）。
    names 由调用方按长度倒序传入，第一个命中即最长命中。
    """
    for name in names:
        if name and name in text:
            return name
    return ""


def _safe(fn, default, what):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001  某一域读不到不该拖垮整次抽取
        log.warning("关系抽取：读取%s失败（%s），该域本次跳过", what, e)
        return default


def _scan_transactions() -> tuple[list[dict], dict]:
    """按"最近 N 笔"抽样流水：从最近有账的月份往前取，总量受 config 与硬上限约束。
    数据层 `list_transactions(year=None, month=None, limit=100)` 是**按月**的（不传即当月），
    没有"全局最近 N 笔"的签名；所以先 `list_active_periods` 定位月份，再逐月倒序取数直到
    预算用完（32000+ 笔的演示库不能全量重算）。
    """
    months = list((_safe(lambda: db.list_active_periods(limit=MONTHS_LOOKBACK), {}, "流水月份") or {}).get("months") or [])
    budget = min(max(int(getattr(config, "KNOWLEDGE_EDGE_SCAN_LIMIT", 3000) or 3000), 1),
                 TRANSACTION_SCAN_HARD_CAP)
    txns: list[dict] = []
    for period in months:
        if len(txns) >= budget:
            break
        try:
            y, m = int(str(period)[:4]), int(str(period)[5:7])
            txns.extend(db.list_transactions(year=y, month=m, limit=budget - len(txns)))
        except Exception as e:  # noqa: BLE001
            log.warning("关系抽取：读取 %s 流水失败（%s）", period, e)
    return txns, {"transactions_scanned": len(txns), "scan_limit": budget,
                  "months_scanned": len(months), "truncated": len(txns) >= budget}


def extract_edges() -> list[dict]:
    """从现有数据层抽取跨域关系边（**全部证据来自原始表，不调用 AI**）。

    返回 `[{subject_type, subject_ref, subject_label, relation, object_type, object_ref,
    object_label, evidence, confidence}]`；`edge_key` 由 `db_relations.upsert_edge` 按同一
    五元组补算（幂等靠它）。同 key 的重复事实先按 confidence 取强者去重，扫描规模见
    `last_extract_stats()`。口径：商品/供应商没有独立 id 表，**ref 用规范化名称**（交易 item
    只有文本，统一用名字才能把 sold_in 与 supplied_by 串成同一个节点）；熟客 ref 用 id，
    发票/赊账按姓名匹配到熟客时也映射成同一个 id 节点，图才连得起来；匹配不到档案的对方
    （发票/赊账 party）用 object_type="unknown" 但保留原文标签 ——「缺失也是结论」；记忆点走
    `db.recent_memories(per_customer=3)`。
    """
    customers = _safe(db.list_customers, [], "熟客") or []
    products = _safe(db.list_products, [], "商品") or []
    invoices = _safe(db.list_invoices, [], "发票") or []
    debts = _safe(db.list_debts, [], "赊账") or []
    memories = _safe(lambda: db.recent_memories(per_customer=3), {}, "记忆点") or {}
    txns, scan = _scan_transactions()

    cust_by_id = {c.get("id"): c for c in customers}
    cust_by_name: dict[str, dict] = {}
    for c in customers:
        cust_by_name.setdefault(_norm(c.get("name")), c)
    cust_by_name.pop("", None)
    prod_names = sorted({_norm(p.get("name")) for p in products if _norm(p.get("name"))}, key=len, reverse=True)
    supplier_names = {_norm(p.get("supplier")) for p in products if _norm(p.get("supplier"))}
    edges: list[dict] = []

    # a) 熟客 -买过-> 流水；b) 商品 -卖出在-> 流水
    for t in txns:
        tid, item = t.get("id"), _norm(t.get("item"))
        ev = (f"交易#{tid} {_norm(t.get('created_at'))[:10]} {_money(t.get('amount'))} 元 {item}")
        cid, cust = t.get("customer_id"), cust_by_id.get(t.get("customer_id"))
        if cid and cust:
            edges.append(_edge("customer", str(cid), _norm(cust.get("name")) or f"熟客#{cid}",
                               "purchased", "transaction", str(tid), f"交易#{tid}", ev, 0.9))
        hit = _match_product(item, prod_names)
        if hit:
            edges.append(_edge("product", hit, hit, "sold_in", "transaction", str(tid),
                               f"交易#{tid}", ev + f"（命中商品：{hit}）", 0.7))

    # c/d) 供应商 -供应-> 商品 与 商品 -由供货-> 供应商（同一事实的正反两条边，检索方向不同）
    for p in products:
        pname, sup = _norm(p.get("name")), _norm(p.get("supplier"))
        if not pname or not sup:
            continue
        ev = f"商品#{p.get('id')} {pname} 档案供应商：{sup}"
        edges.append(_edge("supplier", sup, sup, "provides", "product", pname, pname, ev, 0.8))
        edges.append(_edge("product", pname, pname, "supplied_by", "supplier", sup, sup, ev, 0.8))

    # e) 发票 -开票对象-> 供应商(kind=in) / 熟客(kind=out) / unknown（找不到档案，保留原文）
    known = set(cust_by_name) | set(prod_names) | supplier_names
    for inv in invoices:
        party, kind = _norm(inv.get("party")), _norm(inv.get("kind"))
        if not party:
            continue
        iid = inv.get("id")
        ev = (f"发票#{iid} {'进项' if kind == 'in' else '销项'} "
              f"{_norm(inv.get('invoice_no')) or '无票号'} {_money(inv.get('amount'))} 元 对方：{party}")
        if party not in known:
            edges.append(_edge("invoice", str(iid), f"发票#{iid}", "invoice_party",
                               "unknown", party, party, ev + "（未匹配到熟客/商品档案）", 0.5))
        elif kind == "in":
            edges.append(_edge("invoice", str(iid), f"发票#{iid}", "invoice_party",
                               "supplier", party, party, ev, 0.8))
        else:
            cust = cust_by_name.get(party)
            ref, label = (str(cust.get("id")), _norm(cust.get("name"))) if cust else (party, party)
            edges.append(_edge("invoice", str(iid), f"发票#{iid}", "invoice_party",
                               "customer", ref, label, ev, 0.8))

    # f) 赊账 -我欠他-> 熟客(kind=payable) / -他欠我-> 熟客(kind=receivable)
    for d in debts:
        party, kind = _norm(d.get("party")), _norm(d.get("kind"))
        if not party:
            continue
        did = d.get("id")
        rel = "owes_me" if kind == "receivable" else "owed_to"
        ev = (f"赊账#{did} {'应收（他欠我）' if kind == 'receivable' else '应付（我欠他）'} "
              f"{party} 余额 {_money(d.get('balance'))} 元")
        cust = cust_by_name.get(party)
        if cust:
            edges.append(_edge("debt", str(did), f"赊账#{did}", rel, "customer",
                               str(cust.get("id")), _norm(cust.get("name")), ev, 0.8))
        else:
            edges.append(_edge("debt", str(did), f"赊账#{did}", rel, "unknown", party, party,
                               ev + "（未匹配到熟客档案）", 0.6))
    # g) 流水 -关联流水-> 流水：退货冲销（parent_id）
    for t in txns:
        tid, pid = t.get("id"), t.get("parent_id")
        if pid and str(pid) != str(tid):
            edges.append(_edge("transaction", str(tid), f"交易#{tid}", "linked_transaction",
                               "transaction", str(pid), f"交易#{pid}",
                               f"交易#{tid} 是 交易#{pid} 的退货冲销（原单 #{pid}）", 0.9))
    groups: dict[str, list[dict]] = {}
    for t in txns:
        wt = _norm(t.get("wx_trade_id"))
        if wt:
            groups.setdefault(wt, []).append(t)
    for wt, rows in groups.items():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda x: x.get("id") or 0)
        head = rows[0]
        for other in rows[1:]:
            hid, oid = head.get("id"), other.get("id")
            edges.append(_edge("transaction", str(hid), f"交易#{hid}", "linked_transaction",
                               "transaction", str(oid), f"交易#{oid}",
                               f"微信单号 {wt} 同时关联 交易#{hid} 与 交易#{oid}", 0.85))

    # h) 熟客 -提及-> 商品：记忆点文本里出现商品名
    for cid, contents in (memories or {}).items():
        label = _norm((cust_by_id.get(cid) or {}).get("name")) or f"熟客#{cid}"
        for content in (contents or []):
            text = _norm(content)
            if (hit := _match_product(text, prod_names)):
                edges.append(_edge("customer", str(cid), label, "mentions", "product",
                                   hit, hit, f"记忆点：{text[:40]}（命中商品：{hit}）", 0.6))

    uniq: dict[str, dict] = {}
    for e in edges:
        k = _key_of(e)
        if k not in uniq or e["confidence"] > uniq[k]["confidence"]:
            uniq[k] = e
    result = list(uniq.values())
    _LAST_SCAN.update(scan | {"raw_edges": len(edges), "edges_extracted": len(result)})
    log.info("关系抽取：扫 %s 笔流水，得 %s 条边（去重前 %s）",
             scan["transactions_scanned"], len(result), len(edges))
    return result


def _changed_item(e: dict) -> dict:
    return {"subject_label": _norm(e.get("subject_label")) or _norm(e.get("subject_ref")),
            "relation": _norm(e.get("relation")), "evidence": _norm(e.get("evidence")),
            "object_label": _norm(e.get("object_label")) or _norm(e.get("object_ref"))}
def merge_relations(full: bool = False, confirm: bool = False) -> dict:
    """增量合并（LightRAG 思想）：重算抽取 → upsert → 回收这次没抽到的 active 边。

    返回 `{"scanned","created","updated","unchanged","revived","deactivated","active",
    "changed_edges","deactivated_edges","scan"}`；护栏触发时带 `"skipped"`
    （`no_edges_extracted` / `confirm_required`）。"在场集合" = 最近
    config.KNOWLEDGE_EDGE_SCAN_LIMIT 笔流水抽出的边；不在场但原本 active 的边按
    "这次没抽到"回收（软删，可回溯）。回收是**破坏性动作**，按铁律2 两道闸：
      ① 抽取为空时**无论 full 与否一律 skipped**（数据抖动不能清空索引）；
      ② 只有 `full=True` **且** `confirm=True` 才真的执行回收。
    """
    edges = extract_edges()
    scan = dict(_LAST_SCAN)
    if not edges:
        # 护栏①：抽取为空时一律不动。边是可回溯资产，误清一次要等原始单据重新被扫到才恢复。
        skipped = {"scanned": scan.get("transactions_scanned", 0), "created": 0, "updated": 0,
                   "unchanged": 0, "revived": 0, "deactivated": 0, "changed_edges": [],
                   "deactivated_edges": [], "scan": scan, "skipped": "no_edges_extracted"}
        return skipped | {"active": db_relations.relation_stats()["edges_active"]}
    allow_recycle = bool(full and confirm)          # 护栏②：回收需 full+confirm（铁律2）
    new_keys, by_key = {_key_of(e) for e in edges}, {_key_of(e): e for e in edges}
    existing = set(db_relations.active_edge_keys(limit=EDGE_LOOKUP_CAP))
    outcomes = db_relations.upsert_edges(edges)
    counts = {"created": 0, "updated": 0, "unchanged": 0, "revived": 0}
    changed: list[dict] = []
    for o in outcomes:
        counts[o["action"]] = counts.get(o["action"], 0) + 1
        if o["action"] != "unchanged" and len(changed) < MAX_CHANGED_EDGES:
            changed.append(_changed_item(by_key.get(o["edge_key"], {})))
    to_recycle = sorted(existing - new_keys)        # 该回收的边（先算出来，再决定收不收）
    details = db_relations.get_edges_by_keys(to_recycle) if allow_recycle else []
    deactivated = db_relations.mark_edges_inactive(to_recycle) if (allow_recycle and to_recycle) else 0
    log.info("关系增量合并：+%s ~%s =%s 复活%s 回收%s", counts["created"], counts["updated"],
             counts["unchanged"], counts["revived"], deactivated)
    out = {**counts, "deactivated": deactivated,
           "active": db_relations.relation_stats()["edges_active"],
           "changed_edges": changed,
           "deactivated_edges": [_changed_item(d) for d in details[:MAX_CHANGED_EDGES]],
           "scanned": scan.get("transactions_scanned", 0), "scan": scan}
    if not allow_recycle and to_recycle:
        # 有该回收的边但没确认 → 明确告知"这次没收"，而不是静默按全量跑
        out["skipped"], out["pending_recycle"] = "confirm_required", len(to_recycle)
    return out
def entity_landscape(keyword: str = "", limit: int = 30) -> dict:
    """给前端/掌柜看的"这张店的关系地图"。

    返回 `{"nodes": {entity_type: [{ref, label, edges}]}, "edges": [≤50 条 {subject_label,
    relation_zh, object_label, evidence}], "checkpoint": domain_context 里的水位, "note"}`；
    keyword 非空时按 label 子串过滤；节点按关联边数倒序，每组取 limit 个。
    """
    kw = _norm(keyword)
    edges = db_relations.get_edges_by_keys(db_relations.active_edge_keys(limit=EDGE_LOOKUP_CAP))
    buckets: dict[str, dict[str, dict]] = {}
    for e in edges:
        for side in ("subject", "object"):
            etype, ref = _norm(e.get(f"{side}_type")), _norm(e.get(f"{side}_ref"))
            label = _norm(e.get(f"{side}_label")) or ref
            if not ref or (kw and kw not in label):
                continue
            buckets.setdefault(etype, {}).setdefault(
                ref, {"ref": ref, "label": label, "edges": 0})["edges"] += 1
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        limit = 30
    nodes = {t: sorted(b.values(), key=lambda n: (-n["edges"], n["label"]))[:limit]
             for t, b in buckets.items()}
    shown = []
    for e in edges:
        sl = _norm(e.get("subject_label")) or _norm(e.get("subject_ref"))
        ol = _norm(e.get("object_label")) or _norm(e.get("object_ref"))
        if kw and kw not in sl and kw not in ol:
            continue
        rel = _norm(e.get("relation"))
        shown.append({"subject_label": sl, "relation_zh": RELATION_ZH.get(rel, rel),
                      "evidence": _norm(e.get("evidence")), "object_label": ol})
        if len(shown) >= LANDSCAPE_EDGE_LIMIT:
            break
    ctx = db.get_domain_context(CHECKPOINT_DOMAIN, CHECKPOINT_KEY)
    return {"nodes": nodes, "edges": shown, "checkpoint": (ctx or {}).get("value"),
            "note": f"关系全部来自原始单据，可逐条回查（本次读取 {len(edges)} 条生效边；"
                    f"节点按关联边数倒序，每组最多 {limit} 个）。"}


def knowledge_chain(entity_type: str, entity_ref: str, depth: int = 2) -> dict:
    """追一条线索：`{"start", "nodes", "paths"}`。

    nodes 是 BFS 邻域（db_relations.neighbors），paths 是到邻域内最多 MAX_PATHS 个可达节点的
    最短路径（db_relations.find_paths）—— 用来回答"这条结论是怎么串起来的"。
    """
    try:
        depth = max(1, min(int(depth or 1), MAX_CHAIN_DEPTH))
    except (TypeError, ValueError):
        depth = 2
    ref = _norm(entity_ref)
    nodes = db_relations.neighbors(entity_type, ref, depth=depth, limit=NEIGHBOR_LIMIT)
    paths: list[list[dict]] = []
    for node in nodes[1:]:
        if len(paths) >= MAX_PATHS:
            break
        found = db_relations.find_paths(entity_type, ref, node["type"], node["ref"], depth)
        if found:
            paths.append(found[0])
    start = nodes[0] if nodes else {"type": _norm(entity_type), "ref": ref, "label": ref,
                                    "depth": 0, "via_relation": "", "from_ref": ""}
    return {"start": start, "nodes": nodes, "paths": paths}


def relations_changed_since(checkpoint_iso: str) -> dict:
    """增量检查点：跑一次增量合并，并把水位落进 domain_context（复用既有表）。

    返回 `{"changed", "since", "new_edges", "deactivated_edges", "checkpoint"}`；传入的
    checkpoint 为空或本地从未落过水位时视为"全量已变"（changed=True）。这里**始终走增量
    合并**（护栏生效），不会因为"首次调用"就强制清空索引。
    """
    prev = db.get_domain_context(CHECKPOINT_DOMAIN, CHECKPOINT_KEY)
    summary = merge_relations()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {"checkpoint": now, "scanned": summary.get("scanned", 0),
               "created": summary.get("created", 0), "updated": summary.get("updated", 0),
               "revived": summary.get("revived", 0), "deactivated": summary.get("deactivated", 0),
               "active": summary.get("active", 0),
               "changed_edges": summary.get("changed_edges", [])[:MAX_CHANGED_EDGES]}
    db.set_domain_context(CHECKPOINT_DOMAIN, CHECKPOINT_KEY, payload)
    moved = sum(int(summary.get(k, 0) or 0)
                for k in ("created", "updated", "revived", "deactivated"))
    changed = bool(not checkpoint_iso or prev is None or moved > 0)
    return {"changed": changed, "since": _norm(checkpoint_iso),
            "new_edges": summary.get("changed_edges", []),
            "deactivated_edges": summary.get("deactivated_edges", []),
            "checkpoint": now}


def relations_summary() -> dict:
    """汇总：索引统计 + 质量体检 + 水位 + 关系类型清单（一键回答"关系层现在什么状态"）。"""
    ctx = db.get_domain_context(CHECKPOINT_DOMAIN, CHECKPOINT_KEY)
    return {"stats": db_relations.relation_stats(), "health": db_relations.graph_health(),
            "checkpoint": (ctx or {}).get("value"), "relation_types": list(RELATION_TYPES),
            "note": "关系全部来自原始单据，可回查"}
