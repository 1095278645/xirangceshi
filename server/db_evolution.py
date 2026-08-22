"""db_evolution.py — SQLite 数据层 · 自适应进化（经验日志 / 基因 / 胶囊 / 审计事件）

四层进化架构的数据持久层，对标三项目源码：
  Layer 1  agent_learnings   — 经验日志（self-improving-agent 的 LEARNINGS.md）
  Layer 3  agent_genes       — 基因库（EvoMap/evolver 的 Gene）
          agent_capsules     — 胶囊库（EvoMap/evolver 的 Capsule）
          agent_events       — 审计日志（EvoMap/evolver 的 Event，append-only + SHA-256）

连接统一走 db.py 的 get_conn（惰性导入避免循环依赖），调用方式与 db_arch 等一致。
"""
import json
import hashlib
from datetime import datetime, timezone

__all__ = [
    # agent_learnings
    "record_learning", "get_learnings", "get_pending_learnings",
    "promote_learning", "resolve_learning",
    # agent_genes
    "save_gene", "get_gene", "get_active_genes", "get_all_genes",
    "update_gene_stats", "set_gene_status",
    # agent_capsules
    "save_capsule", "get_recent_capsules", "get_capsule",
    # agent_events
    "log_event", "get_events",
    # L1 insight_index（存 domain_context）
    "get_insight_index", "set_insight_index",
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
        # 去重：同 domain + pattern_key 的 open 条目复现 +1
        if pattern_key:
            row = conn.execute(
                "SELECT id, recurrence_count, metadata_json FROM agent_learnings "
                "WHERE domain=? AND pattern_key=? AND status='open' "
                "ORDER BY id DESC LIMIT 1",
                (domain, pattern_key)).fetchone()
            if row:
                # 合并 metadata：新 metadata 覆盖旧的同名键
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
        # 新条目
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


# ---------------- agent_capsules（胶囊库 Layer 3） ----------------

def save_capsule(capsule_id, gene_id, domain, task_context=None, content="",
                 user_adopted=False, user_edited=False, edit_diff=None,
                 confidence=None):
    """创建一条胶囊记录"""
    tc_json = json.dumps(task_context, ensure_ascii=False) if task_context else None
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO agent_capsules(capsule_id, gene_id, domain, task_context, "
            "content, user_adopted, user_edited, edit_diff, confidence, timestamp) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (capsule_id, gene_id, domain, tc_json, content,
             1 if user_adopted else 0, 1 if user_edited else 0, edit_diff,
             confidence, now))
    return {"capsule_id": capsule_id, "gene_id": gene_id}


def get_recent_capsules(domain, limit=10):
    """获取某域最近的胶囊记录"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_capsules WHERE domain=? "
            "ORDER BY timestamp DESC LIMIT ?",
            (domain, limit)).fetchall()
    return [_capsule_dict(r) for r in rows]


def get_capsule(capsule_id):
    """读取单个胶囊"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM agent_capsules WHERE capsule_id=?", (capsule_id,)).fetchone()
    return _capsule_dict(row) if row else None


def _capsule_dict(r):
    d = dict(r)
    d["user_adopted"] = bool(d.get("user_adopted"))
    d["user_edited"] = bool(d.get("user_edited"))
    try:
        d["task_context"] = json.loads(d.get("task_context") or "{}")
    except (json.JSONDecodeError, TypeError):
        d["task_context"] = {}
    return d


# ---------------- agent_events（审计日志，append-only） ----------------

def log_event(event_type, gene_id=None, capsule_id=None, domain=None,
              details="", content=None):
    """记录一条审计事件（append-only，SHA-256 内容寻址）"""
    eid = f"evt-{_seq_event()}"
    now = datetime.now(timezone.utc).isoformat()
    # SHA-256 内容寻址
    hash_src = f"{event_type}|{gene_id or ''}|{capsule_id or ''}|{domain or ''}|{content or details or ''}"
    content_hash = "sha256:" + hashlib.sha256(hash_src.encode()).hexdigest()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO agent_events(event_id, event_type, gene_id, capsule_id, "
            "domain, details, timestamp, content_hash) VALUES(?,?,?,?,?,?,?,?)",
            (eid, event_type, gene_id, capsule_id, domain, details, now, content_hash))
    return {"event_id": eid, "content_hash": content_hash}


def get_events(domain=None, event_type=None, limit=50):
    """查询审计事件"""
    sql = "SELECT * FROM agent_events"
    where, params = [], []
    if domain:
        where.append("domain=?")
        params.append(domain)
    if event_type:
        where.append("event_type=?")
        params.append(event_type)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ---------------- L1 insight_index（存 domain_context，<=20 行） ----------------

def get_insight_index(domain):
    """读取某域的 L1 极简索引；无则返回空列表"""
    from db_arch import get_domain_context
    item = get_domain_context(domain, "insight_index")
    if not item or not item.get("value"):
        return []
    val = item["value"]
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except json.JSONDecodeError:
            val = val.strip().split("\n") if val.strip() else []
    return val if isinstance(val, list) else []


def set_insight_index(domain, lines):
    """写入 L1 极简索引（硬约束 <=20 行）"""
    from db_arch import set_domain_context
    # 硬约束：超过 20 行淘汰最久未命中的（简单实现：保留前 20 行）
    if len(lines) > 20:
        lines = lines[:20]
    set_domain_context(domain, "insight_index", lines)
    return {"domain": domain, "lines": len(lines)}


# ---------------- 内部辅助 ----------------

_SEQ_CACHE = {"date": "", "seq": 0, "event_seq": 0}


def _seq(conn):
    """生成当日序号（LRN-YYYYMMDD-XXX）"""
    today = datetime.now().strftime("%Y%m%d")
    if _SEQ_CACHE["date"] != today:
        _SEQ_CACHE["date"] = today
        _SEQ_CACHE["seq"] = 0
    _SEQ_CACHE["seq"] += 1
    return f"{_SEQ_CACHE['seq']:03d}"


def _seq_event():
    """生成事件序号"""
    now = datetime.now().strftime("%Y%m%d%H%M%S")
    _SEQ_CACHE["event_seq"] += 1
    return f"{now}{_SEQ_CACHE['event_seq']:04d}"
