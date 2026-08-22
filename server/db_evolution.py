"""db_evolution.py — SQLite 数据层 · 经验日志 / 基因库 / L1 索引

四层进化架构的数据持久层（胶囊+审计事件拆到 db_evolution_audit.py）：
  Layer 1  agent_learnings   — 经验日志（self-improving-agent 的 LEARNINGS.md）
  Layer 3  agent_genes       — 基因库（EvoMap/evolver 的 Gene）
  L1索引   insight_index      — 存 domain_context（<=20 行极简索引）

连接统一走 db.py 的 get_conn（惰性导入避免循环依赖）。
"""
import json
from datetime import datetime, timezone

# 向后兼容：re-export 胶囊+事件+L1索引+序号+建表（调用方仍可用 dbe.save_capsule 等）
from db_evolution_audit import (  # noqa: F401
    save_capsule, get_recent_capsules, get_capsule,
    log_event, get_events,
    get_insight_index, set_insight_index, _seq,
    init_evolution_tables,
)

__all__ = [
    # agent_learnings
    "record_learning", "get_learnings", "get_pending_learnings",
    "promote_learning", "resolve_learning",
    # agent_genes
    "save_gene", "get_gene", "get_active_genes", "get_all_genes",
    "update_gene_stats", "set_gene_status",
    # capsules + events (re-exported)
    "save_capsule", "get_recent_capsules", "get_capsule",
    "log_event", "get_events",
    # L1 insight_index + 建表（re-exported from db_evolution_audit）
    "get_insight_index", "set_insight_index", "init_evolution_tables",
]


def _conn():
    from db import get_conn
    return get_conn()


# ---------------- agent_learnings（经验日志 Layer 1） ----------------

def record_learning(domain, trigger_type, pattern_key=None, source="system",
                    details="", metadata=None):
    """记录一条经验日志。pattern_key 命中已有条目则 recurrence_count+1（去重）。
    返回 learning_id。"""
    now = datetime.now(timezone.utc).isoformat()
    meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
    with _conn() as conn:
        if pattern_key:
            row = conn.execute(
                "SELECT id, recurrence_count, metadata_json FROM agent_learnings "
                "WHERE domain=? AND pattern_key=? AND status='open' "
                "ORDER BY id DESC LIMIT 1",
                (domain, pattern_key)).fetchone()
            if row:
                merged_meta = None
                if meta_json or row["metadata_json"]:
                    old = {}
                    try:
                        old = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
                    except (json.JSONDecodeError, TypeError):
                        pass
                    new = metadata or {}
                    old.update(new)
                    merged_meta = json.dumps(old, ensure_ascii=False)
                conn.execute(
                    "UPDATE agent_learnings SET recurrence_count=?, last_seen=?, "
                    "details=?, metadata_json=COALESCE(?, metadata_json) WHERE id=?",
                    (row["recurrence_count"] + 1, now, details or "",
                     merged_meta, row["id"]))
                return row["id"]
        lid = f"LRN-{datetime.now().strftime('%Y%m%d')}-{_seq(conn)}"
        conn.execute(
            "INSERT INTO agent_learnings(id, logged, domain, trigger_type, "
            "pattern_key, recurrence_count, status, source, details, "
            "first_seen, last_seen, metadata_json) "
            "VALUES(?,?,?,?,?,1,'open',?,?,?,?,?)",
            (lid, now, domain, trigger_type, pattern_key, source,
             details, now, now, meta_json))
        return lid


def get_learnings(domain, status=None, limit=100):
    """查询经验日志；可按 domain/status 过滤"""
    sql = "SELECT * FROM agent_learnings"
    where, params = [], []
    if domain:
        where.append("domain=?")
        params.append(domain)
    if status:
        where.append("status=?")
        params.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_learning_dict(r) for r in rows]


def get_pending_learnings(domain):
    """获取某域 status=open 的待回顾条目（任务前注入提示词用）"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_learnings WHERE domain=? AND status='open' "
            "ORDER BY recurrence_count DESC, last_seen DESC",
            (domain,)).fetchall()
    return [_learning_dict(r) for r in rows]


def promote_learning(lid):
    """标记条目为已晋升（status=promoted）"""
    with _conn() as conn:
        conn.execute("UPDATE agent_learnings SET status='promoted' WHERE id=?", (lid,))
    return {"id": lid, "status": "promoted"}


def resolve_learning(lid):
    """标记条目为已解决（status=resolved）"""
    with _conn() as conn:
        conn.execute("UPDATE agent_learnings SET status='resolved' WHERE id=?", (lid,))
    return {"id": lid, "status": "resolved"}


def _learning_dict(r):
    d = dict(r)
    if d.get("metadata_json"):
        try:
            d["metadata"] = json.loads(d["metadata_json"])
        except (json.JSONDecodeError, TypeError):
            d["metadata"] = d["metadata_json"]
    else:
        d["metadata"] = None
    d.pop("metadata_json", None)
    return d


# ---------------- agent_genes（基因库 Layer 3） ----------------

def save_gene(gene_id, domain, trigger_signals, system_prompt_addon="",
              strategy_steps=None, confidence=0.5, success_count=0,
              failure_count=0, consecutive_inert=0, status="active",
              category="innovate", is_distilled=0):
    """插入或更新一个基因。trigger_signals 为 list，strategy_steps 为 list。"""
    # 边界护栏：confidence 限制在 [0, 1]
    confidence = max(0.0, min(1.0, confidence))
    ts_json = json.dumps(trigger_signals, ensure_ascii=False) if trigger_signals else "[]"
    ss_json = json.dumps(strategy_steps, ensure_ascii=False) if strategy_steps else None
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        existing = conn.execute(
            "SELECT gene_id FROM agent_genes WHERE gene_id=?", (gene_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE agent_genes SET domain=?, trigger_signals=?, system_prompt_addon=?, "
                "strategy_steps=?, confidence=?, success_count=?, failure_count=?, "
                "consecutive_inert=?, status=?, category=?, is_distilled=?, last_used=? "
                "WHERE gene_id=?",
                (domain, ts_json, system_prompt_addon, ss_json, confidence,
                 success_count, failure_count, consecutive_inert, status,
                 category, is_distilled, now, gene_id))
        else:
            conn.execute(
                "INSERT INTO agent_genes(gene_id, domain, trigger_signals, "
                "system_prompt_addon, strategy_steps, confidence, success_count, "
                "failure_count, consecutive_inert, status, category, created, last_used, "
                "is_distilled) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (gene_id, domain, ts_json, system_prompt_addon, ss_json, confidence,
                 success_count, failure_count, consecutive_inert, status, category,
                 now, now, is_distilled))
    return {"gene_id": gene_id, "status": status}


def get_gene(gene_id):
    """读取单个基因"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM agent_genes WHERE gene_id=?", (gene_id,)).fetchone()
    return _gene_dict(row) if row else None


def get_active_genes(domain):
    """获取某域 status=active 的基因"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_genes WHERE domain=? AND status='active' "
            "ORDER BY confidence DESC",
            (domain,)).fetchall()
    return [_gene_dict(r) for r in rows]


def get_all_genes(domain):
    """获取某域全部基因（含 suppressed）"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_genes WHERE domain=? ORDER BY confidence DESC",
            (domain,)).fetchall()
    return [_gene_dict(r) for r in rows]


def update_gene_stats(gene_id, success=False, failure=False, inert=False):
    """更新基因统计：成功/失败计数，连续无效次数"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT success_count, failure_count, consecutive_inert FROM agent_genes "
            "WHERE gene_id=?", (gene_id,)).fetchone()
        if not row:
            return None
        sc = row["success_count"] + (1 if success else 0)
        fc = row["failure_count"] + (1 if failure else 0)
        ci = 0 if success else row["consecutive_inert"] + (1 if inert else 0)
        total = sc + fc
        confidence = (sc + 1) / (total + 2) if total > 0 else 0.5
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE agent_genes SET success_count=?, failure_count=?, "
            "consecutive_inert=?, confidence=?, last_used=? WHERE gene_id=?",
            (sc, fc, ci, confidence, now, gene_id))
    return {"gene_id": gene_id, "success_count": sc, "failure_count": fc,
            "consecutive_inert": ci, "confidence": confidence}


def set_gene_status(gene_id, status):
    """设置基因状态（active/suppressed）"""
    with _conn() as conn:
        conn.execute("UPDATE agent_genes SET status=? WHERE gene_id=?", (status, gene_id))
    return {"gene_id": gene_id, "status": status}


def _gene_dict(r):
    if not r:
        return None
    d = dict(r)
    try:
        d["trigger_signals"] = json.loads(d.get("trigger_signals") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["trigger_signals"] = []
    try:
        d["strategy_steps"] = json.loads(d.get("strategy_steps") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["strategy_steps"] = []
    return d


# L1 insight_index / _seq 已拆到 db_evolution_audit.py（见顶部 re-export）
