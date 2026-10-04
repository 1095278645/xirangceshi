"""db_relations.py — SQLite 数据层 · 跨域关系索引（knowledge_edges）

参考图「看得懂」：把平铺在各业务域表格里的实体（熟客/商品/供应商/发票/流水/赊账）显式
连成**带证据的边**，支持增量合并（只为变化付代价）与可回溯回收（软删在前）。

- 本模块是 knowledge_edges **唯一**允许出现 SQL 的地方（shop_relations 只做编排）。
- 边身份 = `edge_key`（五元组 sha256，由 shop_relations.edge_key 生成，幂等靠它）；`edge_id`
  是该行主键，两者不要混用。关系只软删（active=0 + valid_to），物理清理走 `prune_edges`。
- 建表 DDL/索引与 db_knowledge_schema.init_knowledge_tables 逐字一致，谁先跑都行，因此本层
  测试可以独立跑，不依赖建表队友的进度。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import uuid4

import config

log = logging.getLogger("db_relations")

__all__ = ["init_relations_tables", "upsert_edge", "upsert_edges", "mark_edges_inactive",
           "list_edges", "get_edge", "get_edges_by_keys", "active_edge_keys", "neighbors",
           "find_paths", "relation_stats", "graph_health", "prune_edges"]

LIST_EDGES_MAX_LIMIT = 500      # 契约冻结：list_edges 的硬上限
EDGE_KEY_HARD_CAP = 20000       # 一次取 key / 明细的硬上限（演示库 3 万+ 笔，只取窗口）
_KEY_BATCH = 400                # IN (...) 分片，避免超过 SQLite 变量数上限
_EDGE_COLUMNS = ("subject_type", "subject_ref", "subject_label", "relation",
                 "object_type", "object_ref", "object_label", "evidence", "confidence")

# 与 db_knowledge_schema.init_knowledge_tables 逐字一致（含 4 个索引名）
_RELATIONS_DDL = """
CREATE TABLE IF NOT EXISTS knowledge_edges (
    edge_id TEXT PRIMARY KEY, edge_key TEXT NOT NULL,
    subject_type TEXT NOT NULL DEFAULT '', subject_ref TEXT NOT NULL DEFAULT '', subject_label TEXT DEFAULT '',
    relation TEXT NOT NULL,
    object_type TEXT NOT NULL DEFAULT '', object_ref TEXT NOT NULL DEFAULT '', object_label TEXT DEFAULT '',
    evidence TEXT DEFAULT '', confidence REAL DEFAULT 0.5, active INTEGER DEFAULT 1,
    first_seen TEXT DEFAULT '', last_seen TEXT DEFAULT '', valid_from TEXT DEFAULT '', valid_to TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_knowledge_edges_key      ON knowledge_edges(edge_key);
CREATE INDEX IF NOT EXISTS idx_knowledge_edges_subject  ON knowledge_edges(subject_type, subject_ref, active);
CREATE INDEX IF NOT EXISTS idx_knowledge_edges_object   ON knowledge_edges(object_type, object_ref, active);
CREATE INDEX IF NOT EXISTS idx_knowledge_edges_relation ON knowledge_edges(relation, active);
"""


def init_relations_tables(conn) -> None:
    """幂等建 knowledge_edges 表 + 4 个索引（与 db_knowledge_schema 侧逐字一致）。
    意义：关系层测试可**独立**跑，不等建表队友合入；两边都跑只有 IF NOT EXISTS，无害。
    """
    conn.executescript(_RELATIONS_DDL)


def _conn():
    from db import get_conn  # 惰性导入：与 db.py 聚合层解耦，避免循环导入
    return get_conn()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _int_in(value, default, lo, hi) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = default
    return max(lo, min(v, hi))


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["active"] = bool(d.get("active"))
    d["confidence"] = _to_float(d.get("confidence"))
    return d


def _normalize(edge: dict) -> dict:
    data = {k: ("" if edge.get(k) is None else edge.get(k)) for k in _EDGE_COLUMNS}
    for k in ("subject_type", "subject_ref", "relation", "object_type", "object_ref"):
        data[k] = str(data[k])
    data["confidence"] = _to_float(edge.get("confidence"), 0.5)
    return data


def _content_same(row, data: dict) -> bool:
    """证据/标签/端点是否逐字未变（confidence 用容差比，避免浮点噪声触发假更新）。"""
    for k in _EDGE_COLUMNS:
        if k == "confidence":
            if abs(_to_float(row[k]) - _to_float(data[k])) > 1e-9:
                return False
        elif str(row[k] or "") != str(data[k] or ""):
            return False
    return True


def _update_content(conn, edge_id, data, now, clear_valid_to=False) -> None:
    sets = ",".join(f"{k}=?" for k in _EDGE_COLUMNS)
    params = [data[k] for k in _EDGE_COLUMNS] + [now]
    sql = f"UPDATE knowledge_edges SET {sets}, active=1, last_seen=?"
    if clear_valid_to:
        sql += ", valid_to=''"
    conn.execute(sql + " WHERE edge_id=?", params + [edge_id])

def _upsert(conn, edge: dict) -> dict:
    """upsert 单条（调用方负责连接与事务）。"""
    from shop_relations import edge_key as _edge_key  # 同一套 key 算法，保证幂等一致
    data = _normalize(edge)
    key = edge.get("edge_key") or _edge_key(
        data["subject_type"], data["subject_ref"], data["relation"],
        data["object_type"], data["object_ref"])
    now = _now()
    row = conn.execute(
        "SELECT * FROM knowledge_edges WHERE edge_key=? ORDER BY rowid DESC LIMIT 1",
        (key,)).fetchone()
    if row is None:
        edge_id = str(uuid4())
        conn.execute(
            "INSERT INTO knowledge_edges(edge_id, edge_key, subject_type, subject_ref, "
            "subject_label, relation, object_type, object_ref, object_label, evidence, "
            "confidence, active, first_seen, last_seen, valid_from, valid_to) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,'')",
            (edge_id, key, data["subject_type"], data["subject_ref"], data["subject_label"],
             data["relation"], data["object_type"], data["object_ref"], data["object_label"],
             data["evidence"], data["confidence"], now, now, now))
        return {"edge_id": edge_id, "edge_key": key, "action": "created"}
    edge_id = row["edge_id"]
    if not row["active"]:
        # 曾被回收（软删）后又出现：复活。first_seen / valid_from 保留首次时间，这样
        # "这条关系最早什么时候被看见"仍可回溯；valid_to 清空表示它现在又是有效的。
        _update_content(conn, edge_id, data, now, clear_valid_to=True)
        return {"edge_id": edge_id, "edge_key": key, "action": "revived"}
    if _content_same(row, data):
        conn.execute("UPDATE knowledge_edges SET last_seen=? WHERE edge_id=?", (now, edge_id))
        return {"edge_id": edge_id, "edge_key": key, "action": "unchanged"}
    _update_content(conn, edge_id, data, now)
    return {"edge_id": edge_id, "edge_key": key, "action": "updated"}


def upsert_edge(edge: dict) -> dict:
    """按 `edge_key` 幂等写一条边；返回 `{"edge_id", "edge_key", "action"}`。

    action：created 新边（first_seen=last_seen=valid_from=now）／unchanged 内容逐字未变且
    active=1（只刷新 last_seen，增量合并的省钱路径）／updated 内容变化（保留首次时间）／
    revived 原本 active=0，重新激活并清空 valid_to。`edge_key` 缺省时按抽取层同一算法补算。
    """
    with _conn() as conn:
        return _upsert(conn, edge)


def upsert_edges(edges: list[dict]) -> list[dict]:
    """批量版 upsert：一个连接内按序处理（一次 merge 可能上千条边，避免 N 次开关连接）。"""
    with _conn() as conn:
        return [_upsert(conn, e) for e in edges or []]


def mark_edges_inactive(edge_keys: list[str]) -> int:
    """软删：把给定 key 的 active 置 0、valid_to=now；返回**本次真正回收**的行数。

    关系不能物理删 —— 掌柜事后要能回溯"这条关系什么时候有过、什么时候没了"，物理清理
    统一交给 prune_edges（保留期走 config）。已是 0 的行不重复计入。
    """
    keys = [str(k) for k in (edge_keys or []) if k]
    if not keys:
        return 0
    now, total = _now(), 0
    with _conn() as conn:
        for i in range(0, len(keys), _KEY_BATCH):
            batch = keys[i:i + _KEY_BATCH]
            cur = conn.execute(
                "UPDATE knowledge_edges SET active=0, valid_to=? WHERE active=1 "
                f"AND edge_key IN ({','.join('?' * len(batch))})", [now, *batch])
            total += cur.rowcount or 0
    return total


def list_edges(subject_type=None, subject_ref=None, relation=None,
               active=None, limit=100) -> list[dict]:
    """按端点/关系/状态过滤查询边；`active=None` 不过滤状态。返回 dict，`active` 为 bool。
    按 last_seen DESC 排序；limit 硬上限 500（契约冻结）。
    """
    limit = _int_in(limit, 100, 1, LIST_EDGES_MAX_LIMIT)
    where, params = [], []
    for col, val in (("subject_type", subject_type), ("subject_ref", subject_ref),
                     ("relation", relation)):
        if val is not None and val != "":
            where.append(f"{col}=?")
            params.append(val)
    if active is not None:
        where.append("active=?")
        params.append(1 if active else 0)
    sql = "SELECT * FROM knowledge_edges"
    if where:
        sql += " WHERE " + " AND ".join(where)
    with _conn() as conn:
        rows = conn.execute(sql + " ORDER BY last_seen DESC, rowid DESC LIMIT ?",
                            [*params, limit]).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_edge(edge_id) -> dict | None:
    """按 edge_id 取单条边；找不到返回 None。"""
    with _conn() as conn:
        row = conn.execute("SELECT * FROM knowledge_edges WHERE edge_id=?",
                           (str(edge_id),)).fetchone()
    return _row_to_dict(row) if row else None


def get_edges_by_keys(edge_keys: list[str]) -> list[dict]:
    """按 edge_key 批量取明细（供 merge 记录"这次回收了哪几条"、关系地图聚合节点）。"""
    keys = [str(k) for k in (edge_keys or []) if k]
    out = []
    with _conn() as conn:
        for i in range(0, len(keys), _KEY_BATCH):
            batch = keys[i:i + _KEY_BATCH]
            rows = conn.execute(
                "SELECT * FROM knowledge_edges WHERE edge_key IN "
                f"({','.join('?' * len(batch))}) ORDER BY last_seen DESC, rowid DESC",
                batch).fetchall()
            out.extend(_row_to_dict(r) for r in rows)
    return out


def active_edge_keys(limit: int = EDGE_KEY_HARD_CAP) -> list[str]:
    """全部生效边的 edge_key（增量合并据此算出"这次没抽到、需要回收"的差集）。"""
    limit = _int_in(limit, EDGE_KEY_HARD_CAP, 1, EDGE_KEY_HARD_CAP)
    with _conn() as conn:
        rows = conn.execute("SELECT edge_key FROM knowledge_edges WHERE active=1 "
                            "ORDER BY last_seen DESC LIMIT ?", (limit,)).fetchall()
    return [r["edge_key"] for r in rows]


def relation_stats() -> dict:
    """关系索引统计：总量/生效量/按关系/按类型对/时间跨度。

    `by_relation`、`by_type_pair` 只统计 **active=1**（现在的图长什么样），`edges_total`
    含已回收的边（历史上见过多少）。
    """
    with _conn() as conn:
        q = conn.execute
        total = q("SELECT COUNT(*) AS c FROM knowledge_edges").fetchone()["c"]
        active = q("SELECT COUNT(*) AS c FROM knowledge_edges WHERE active=1").fetchone()["c"]
        by_relation = {r["relation"]: r["c"] for r in q(
            "SELECT relation, COUNT(*) AS c FROM knowledge_edges WHERE active=1 "
            "GROUP BY relation ORDER BY c DESC, relation")}
        by_type_pair = {f"{r['subject_type']}→{r['object_type']}": r["c"] for r in q(
            "SELECT subject_type, object_type, COUNT(*) AS c FROM knowledge_edges "
            "WHERE active=1 GROUP BY subject_type, object_type ORDER BY c DESC, 1, 2")}
        first = q("SELECT MIN(first_seen) AS m FROM knowledge_edges "
                  "WHERE first_seen!=''").fetchone()["m"]
        last = q("SELECT MAX(last_seen) AS m FROM knowledge_edges "
                 "WHERE last_seen!=''").fetchone()["m"]
    return {"edges_total": int(total or 0), "edges_active": int(active or 0),
            "by_relation": by_relation, "by_type_pair": by_type_pair,
            "first_seen_min": first, "last_seen_max": last}


def graph_health() -> dict:
    """关系索引质量体检（"关系可核验"的落点）。

    orphan_edges 端点引用为空的边（应为 0，出现即抽取层写坏了）；dangling_objects
    object_type 不在 ENTITY_TYPES 内或 object_ref 为空；duplicate_edge_keys 同一 edge_key
    出现多行的 key 数（破坏幂等，应为 0）；stale_edges active=1 但 last_seen 早于
    config.KNOWLEDGE_EDGE_STALE_DAYS 天（含 last_seen 为空：没被"最近证实"过就算陈旧）。
    """
    from shop_relations import ENTITY_TYPES   # 真源在关系层，函数内导入避免循环依赖
    stale_days = int(getattr(config, "KNOWLEDGE_EDGE_STALE_DAYS", 30) or 30)
    cutoff = (datetime.now() - timedelta(days=stale_days)).strftime("%Y-%m-%d %H:%M:%S")
    types = tuple(ENTITY_TYPES)
    with _conn() as conn:
        q = conn.execute
        orphan = q("SELECT COUNT(*) AS c FROM knowledge_edges WHERE subject_ref='' "
                   "OR object_ref=''").fetchone()["c"]
        dangling = q("SELECT COUNT(*) AS c FROM knowledge_edges WHERE object_ref='' "
                     f"OR object_type NOT IN ({','.join('?' * len(types))})", types).fetchone()["c"]
        dup = q("SELECT COUNT(*) AS c FROM (SELECT edge_key FROM knowledge_edges "
                "GROUP BY edge_key HAVING COUNT(*)>1)").fetchone()["c"]
        stale = q("SELECT COUNT(*) AS c FROM knowledge_edges WHERE active=1 "
                  "AND (last_seen='' OR last_seen < ?)", (cutoff,)).fetchone()["c"]
    counts = {"orphan_edges": int(orphan or 0), "dangling_objects": int(dangling or 0),
              "duplicate_edge_keys": int(dup or 0), "stale_edges": int(stale or 0)}
    return {**counts, "ok": all(v == 0 for v in counts.values())}


def prune_edges(days: int | None = None) -> int:
    """物理删除"已回收且过了保留期"的边，返回删除行数。

    保留期取 `config.KNOWLEDGE_EDGE_KEEP_DAYS`（不写魔法数）；valid_to 为空的已回收行无法
    证明"什么时候失效的"，一律不删（宁可留着，也不要删掉无从回溯的关系）。
    """
    keep = int(days) if days is not None else int(
        getattr(config, "KNOWLEDGE_EDGE_KEEP_DAYS", 90) or 90)
    cutoff = (datetime.now() - timedelta(days=max(keep, 0))).strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as conn:
        cur = conn.execute("DELETE FROM knowledge_edges WHERE active=0 "
                           "AND valid_to!='' AND valid_to < ?", (cutoff,))
        return cur.rowcount or 0


# 图上走路的两个原语搬到 db_relations_graph.py（本文件已到 400 行硬限），
# 这里 re-export 保持 `db_relations.neighbors / find_paths` 调用方式不变。
from db_relations_graph import neighbors, find_paths  # noqa: E402,F401
