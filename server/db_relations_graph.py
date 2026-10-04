"""db_relations_graph.py — 关系图的检索原语（从 db_relations 外移，架构自检 L1）

为什么单独一个文件：`db_relations.py` 已达 400 行硬上限（`scripts/arch_check.py` LINE_FAIL）。
这里只做"搬家"——把**图上走路**的两个函数（`neighbors` 广度邻域、`find_paths` 最短路）
及其私有辅助移出来，逻辑一字未改；`db_relations.neighbors / find_paths` 仍原样可用
（末尾 re-export），调用方与既有测试无需改动。

依赖方向：本模块**不 import db_relations**；连接与整数钳制走"原语下沉"——
自己拿 `db.get_conn`，自己的 `_int_in`。这样没有环，也不会与写边的数据层互相牵制。
"""
from __future__ import annotations

import db

__all__ = ["neighbors", "find_paths"]

_LIST_EDGES_MAX_LIMIT = 500      # 与 db_relations.LIST_EDGES_MAX_LIMIT 保持一致


def _conn():
    from db import get_conn
    return get_conn()


def _int_in(value, default, lo, hi) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _walk(conn, etype, eref):
    """某实体（两个方向）的全部 active 邻接边。

    产出 `(other_type, other_ref, other_label, relation, direction)`：
    direction="out" 表示当前实体是 subject（顺着关系的正向走），
    "in" 表示当前实体是 object（从关系的反向走）。
    方向必须带出去 —— 否则"追线索"会给出店里并不存在的方向（独立复核指出）。
    """
    rows = conn.execute(
        "SELECT * FROM knowledge_edges WHERE active=1 AND "
        "((subject_type=? AND subject_ref=?) OR (object_type=? AND object_ref=?)) "
        "ORDER BY last_seen DESC, rowid DESC",
        (etype, eref, etype, eref)).fetchall()
    for row in rows:
        if row["subject_type"] == etype and row["subject_ref"] == eref:
            yield (row["object_type"], row["object_ref"], row["object_label"],
                   row["relation"], "out")
        else:
            yield (row["subject_type"], row["subject_ref"], row["subject_label"],
                   row["relation"], "in")


def _label_of(conn, etype, eref, fallback="") -> str:
    """从一个端点的历史标签里取名字（节点没有独立档案表，标签就存在边上）。"""
    for col in ("subject", "object"):
        row = conn.execute(
            f"SELECT {col}_label AS l FROM knowledge_edges WHERE {col}_type=? AND {col}_ref=? "
            f"AND {col}_label!='' ORDER BY rowid DESC LIMIT 1", (etype, eref)).fetchone()
        if row and row["l"]:
            return row["l"]
    return fallback


def neighbors(entity_type: str, entity_ref: str, depth: int = 1, limit: int = 50) -> list[dict]:
    """BFS 邻域：只走 active=1 的边，返回 `{type, ref, label, depth, via_relation, from_ref,
    direction}`。起点 depth=0（via_relation=""/from_ref=""）；同 (type, ref) 只出现一次；
    空 ref 的脏边不生成节点（脏数据不该在图上生造实体）。
    """
    depth = _int_in(depth, 0, 0, 5)
    limit = _int_in(limit, 50, 1, _LIST_EDGES_MAX_LIMIT)
    etype, eref = str(entity_type or ""), str(entity_ref or "")
    out: list[dict] = []
    with _conn() as conn:
        out.append({"type": etype, "ref": eref, "label": _label_of(conn, etype, eref, eref),
                    "depth": 0, "via_relation": "", "from_ref": ""})
        visited, frontier = {(etype, eref)}, [(etype, eref, 0)]
        while frontier and len(out) < limit:
            nxt = []
            for t, r, d in frontier:
                if d >= depth:
                    continue
                for ot, oref, olabel, rel, direction in _walk(conn, t, r):
                    oref = str(oref or "")
                    if not oref or (ot, oref) in visited:
                        continue
                    visited.add((ot, oref))
                    out.append({"type": ot, "ref": oref, "label": olabel or oref,
                                "depth": d + 1, "via_relation": rel, "from_ref": r,
                                "direction": direction})
                    nxt.append((ot, oref, d + 1))
                    if len(out) >= limit:
                        break
                if len(out) >= limit:
                    break
            frontier = nxt
    return out


def find_paths(src_type, src_ref, dst_type, dst_ref, max_depth=3) -> list[list[dict]]:
    """前 3 条**最短**路径；每条是边序列 `[{type, ref, label, relation, direction}]`。
    元素里的 type/ref/label 是该跳的**终点**、relation 是走到它用的边、direction 说明这一跳
    是顺着关系还是从反向走（否则会给店主一个店里不存在的方向）。
    防环：同一路径内 (type, ref) 不重复；同一**节点序列**只保留最先（最短）那条，
    否则 3 条名额会被同一条路线的不同走法占满（独立复核指出）。起点即终点不算线索，返回 []。
    """
    src = (str(src_type or ""), str(src_ref or ""))
    dst = (str(dst_type or ""), str(dst_ref or ""))
    if not src[1] or not dst[1] or src == dst:
        return []
    max_depth = _int_in(max_depth, 3, 1, 5)
    paths: list[list[dict]] = []
    seen_seq: set[tuple] = set()
    with _conn() as conn:
        frontier = [(src, [], {src})]
        for _ in range(max_depth):
            nxt = []
            for (cur, steps, seen) in frontier:
                for ot, oref, olabel, rel, direction in _walk(conn, cur[0], cur[1]):
                    oref = str(oref or "")
                    node = (ot, oref)
                    if not oref or node in seen:   # 防环
                        continue
                    hop = {"type": ot, "ref": oref, "label": olabel or oref,
                           "relation": rel, "direction": direction}
                    new_steps = steps + [hop]
                    if node == dst:
                        seq = tuple((s["type"], s["ref"]) for s in new_steps)
                        if seq in seen_seq:
                            continue           # 同一条节点路线不重复出
                        seen_seq.add(seq)
                        paths.append(new_steps)
                        if len(paths) >= 3:
                            return paths
                    else:
                        nxt.append((node, new_steps, seen | {node}))
            frontier = nxt
            if not frontier:
                break
    return paths
