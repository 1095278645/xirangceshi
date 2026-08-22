"""db_evolution_audit.py — 胶囊库 + 审计事件 + L1索引 + 序列号 + 建表

从 db_evolution.py 和 db.py 拆出，避免单文件 >250 行。
agent_capsules — 评估胶囊（EvoMap/evolver 的 Capsule）
agent_events   — 审计日志（append-only + SHA-256 内容寻址）
insight_index  — L1 极简索引（存 domain_context，<=20 行）
_seq           — 当日序号生成（LRN-YYYYMMDD-XXX）
_init_evolution_tables — 进化层建表（从 db.py init_db 拆出）
"""
import json
import hashlib
from datetime import datetime, timezone

__all__ = [
    "save_capsule", "get_recent_capsules", "get_capsule",
    "log_event", "get_events",
    "get_insight_index", "set_insight_index",
    "_seq",
    "init_evolution_tables",
]


def _conn():
    from db import get_conn
    return get_conn()


# ---------------- agent_capsules（胶囊库） ----------------

def save_capsule(capsule_id, gene_id, domain, task_context=None, content=None,
                 user_adopted=False, user_edited=False, edit_diff=None,
                 confidence=None, failure_reason=None):
    """创建一条胶囊记录

    failure_reason（借鉴 SkillClaw 三类问题区分）：未采纳时的失败归因，
    可选值：gene_deficiency / agent_runtime / env_instability / None。
    仅 gene_deficiency 触发技能蒸馏，避免无效进化。
    """
    tc_json = json.dumps(task_context, ensure_ascii=False) if task_context else None
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO agent_capsules(capsule_id, gene_id, domain, task_context, "
            "content, user_adopted, user_edited, edit_diff, confidence, timestamp, "
            "failure_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (capsule_id, gene_id, domain, tc_json, content,
             1 if user_adopted else 0, 1 if user_edited else 0, edit_diff,
             confidence, now, failure_reason))
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

_EVENT_SEQ = {"seq": 0}


def _seq_event():
    """生成事件序号"""
    now = datetime.now().strftime("%Y%m%d%H%M%S")
    _EVENT_SEQ["seq"] += 1
    return f"{now}{_EVENT_SEQ['seq']:04d}"


def log_event(event_type, gene_id=None, capsule_id=None, domain=None,
              details="", content=None):
    """记录一条审计事件（append-only，SHA-256 内容寻址）"""
    eid = f"evt-{_seq_event()}"
    now = datetime.now(timezone.utc).isoformat()
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
    if len(lines) > 20:
        lines = lines[:20]
    set_domain_context(domain, "insight_index", lines)
    return {"domain": domain, "lines": len(lines)}


# ---------------- 内部辅助：序号生成 ----------------

_SEQ_CACHE = {"date": "", "seq": 0}


def _seq(conn):
    """生成当日序号（LRN-YYYYMMDD-XXX）"""
    today = datetime.now().strftime("%Y%m%d")
    if _SEQ_CACHE["date"] != today:
        _SEQ_CACHE["date"] = today
        _SEQ_CACHE["seq"] = 0
    _SEQ_CACHE["seq"] += 1
    return f"{_SEQ_CACHE['seq']:03d}"


# ---------------- 进化层建表（从 db.py init_db 拆出） ----------------

def init_evolution_tables(conn):
    """创建自适应进化层 4 张表（幂等）：agent_learnings / agent_genes / agent_capsules / agent_events"""
    conn.execute("""
    CREATE TABLE IF NOT EXISTS agent_learnings (
        id                TEXT PRIMARY KEY,
        logged            TEXT NOT NULL,
        domain            TEXT NOT NULL,
        trigger_type      TEXT NOT NULL,
        pattern_key       TEXT,
        recurrence_count  INTEGER DEFAULT 1,
        status            TEXT DEFAULT 'open',
        source            TEXT NOT NULL,
        details           TEXT,
        first_seen        TEXT,
        last_seen         TEXT,
        metadata_json     TEXT
    )""")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS agent_genes (
        gene_id           TEXT PRIMARY KEY,
        domain            TEXT NOT NULL,
        trigger_signals   TEXT NOT NULL,
        system_prompt_addon TEXT,
        strategy_steps    TEXT,
        confidence        REAL DEFAULT 0.5,
        success_count     INTEGER DEFAULT 0,
        failure_count     INTEGER DEFAULT 0,
        consecutive_inert  INTEGER DEFAULT 0,
        status            TEXT DEFAULT 'active',
        category          TEXT DEFAULT 'innovate',
        created           TEXT NOT NULL,
        last_used         TEXT,
        is_distilled      INTEGER DEFAULT 0
    )""")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS agent_capsules (
        capsule_id        TEXT PRIMARY KEY,
        gene_id           TEXT NOT NULL,
        domain            TEXT NOT NULL,
        task_context      TEXT,
        content           TEXT,
        user_adopted      INTEGER DEFAULT 0,
        user_edited       INTEGER DEFAULT 0,
        edit_diff         TEXT,
        confidence        REAL,
        timestamp         TEXT NOT NULL,
        failure_reason    TEXT,
        FOREIGN KEY (gene_id) REFERENCES agent_genes(gene_id)
    )""")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS agent_events (
        event_id           TEXT PRIMARY KEY,
        event_type         TEXT NOT NULL,
        gene_id            TEXT,
        capsule_id         TEXT,
        domain             TEXT,
        details            TEXT,
        timestamp          TEXT NOT NULL,
        content_hash       TEXT
    )""")

    # 兼容旧库：agent_capsules 迁移增加 failure_reason 列（幂等）
    try:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(agent_capsules)")]
        if "failure_reason" not in cols:
            conn.execute("ALTER TABLE agent_capsules ADD COLUMN failure_reason TEXT")
    except Exception:  # noqa: BLE001 —— 表尚未创建或读取失败时静默跳过
        pass
