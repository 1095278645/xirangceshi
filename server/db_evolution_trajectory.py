"""db_evolution_trajectory.py — 对话轨迹数据层（借鉴 SkillClaw 的 Client Capture）

记录 _run_team 每次执行的无损对话轨迹：输入、各员工/评审产出、掌柜裁决与最终结果。
轨迹用于事后复盘与技能进化素材，与胶囊（结果级）互补 —— 胶囊存结论，轨迹存过程。

表 agent_trajectories（append-only + SHA-256 内容寻址）：
  traj_id      TEXT PK   — TRJ-YYYYMMDD-XXX
  domain       TEXT      域
  task         TEXT      原始任务
  gene_id      TEXT      命中的基因（可为空）
  mode         TEXT      collaborative / competitive
  inputs_json  TEXT      员工 system+user 输入（完整上下文）
  turns_json   TEXT      各员工/评审产出
  verdict      TEXT      掌柜裁决语
  final        TEXT      最终输出
  adopted_json TEXT      被采纳的内容
  timestamp    TEXT
  content_hash TEXT      SHA-256 内容寻址
"""
import hashlib
import json
from datetime import datetime, timezone

__all__ = ["save_trajectory", "get_recent_trajectories", "get_trajectory",
           "init_trajectory_tables", "_traj_seq"]


def _conn():
    from db import get_conn
    return get_conn()


# 序号缓存
_TRAJ_SEQ = {"date": "", "seq": 0}


def _traj_seq():
    """生成当日轨迹序号（TRJ-YYYYMMDD-XXX）"""
    today = datetime.now().strftime("%Y%m%d")
    if _TRAJ_SEQ["date"] != today:
        _TRAJ_SEQ["date"] = today
        _TRAJ_SEQ["seq"] = 0
    _TRAJ_SEQ["seq"] += 1
    return f"TRJ-{today}-{_TRAJ_SEQ['seq']:03d}"


def save_trajectory(domain, task, system_inputs=None, turns=None,
                    mode="competitive", gene_id=None, verdict="", final="",
                    adopted=None):
    """写入一条完整对话轨迹。system_inputs/turns/adopted 为 dict/list，自动 JSON 序列化。

    返回 {"traj_id": ..., "content_hash": ...}。
    """
    traj_id = _traj_seq()
    inputs_json = json.dumps(system_inputs, ensure_ascii=False) if system_inputs else "{}"
    turns_json = json.dumps(turns, ensure_ascii=False) if turns else "[]"
    adopted_json = json.dumps(adopted, ensure_ascii=False) if adopted else "[]"
    now = datetime.now(timezone.utc).isoformat()

    hash_src = f"{domain}|{task}|{inputs_json}|{turns_json}|{final}"
    content_hash = "sha256:" + hashlib.sha256(hash_src.encode()).hexdigest()

    with _conn() as conn:
        conn.execute(
            "INSERT INTO agent_trajectories(traj_id, domain, task, gene_id, mode, "
            "system_inputs, turns, verdict, final, adopted_json, timestamp, content_hash) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (traj_id, domain, task, gene_id, mode, inputs_json, turns_json,
             verdict, final, adopted_json, now, content_hash))
    return {"traj_id": traj_id, "content_hash": content_hash}


def get_recent_trajectories(domain, limit=10):
    """获取某域最近的轨迹（按时间倒序）"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_trajectories WHERE domain=? "
            "ORDER BY timestamp DESC LIMIT ?", (domain, limit)).fetchall()
    return [_traj_dict(r) for r in rows]


def get_trajectory(traj_id):
    """读取单条轨迹"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM agent_trajectories WHERE traj_id=?", (traj_id,)).fetchone()
    return _traj_dict(row) if row else None


def _traj_dict(r):
    d = dict(r)
    for key in ("system_inputs", "turns", "adopted_json"):
        try:
            d[key] = json.loads(d.get(key) or ("{}" if key == "system_inputs" else "[]"))
        except (json.JSONDecodeError, TypeError):
            pass
    return d


def init_trajectory_tables(conn):
    """创建对话轨迹表（幂等）"""
    conn.execute("""
    CREATE TABLE IF NOT EXISTS agent_trajectories (
        traj_id       TEXT PRIMARY KEY,
        domain        TEXT NOT NULL,
        task          TEXT NOT NULL,
        gene_id       TEXT,
        mode          TEXT,
        system_inputs TEXT,
        turns         TEXT,
        verdict       TEXT,
        final         TEXT,
        adopted_json  TEXT,
        timestamp     TEXT NOT NULL,
        content_hash  TEXT
    )""")