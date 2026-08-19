"""db_arch.py — SQLite 数据层 · 架构落地（领域上下文 / 任务队列 / 单店档案）

来自《一人公司设计哲学对标报告》技术架构部分：
1. 领域上下文 domain_context：每个业务域（账本/客户/文案/税务/单店）独立的
   经营记忆，可被心跳与各路由按需读写 —— 对标 TinyAGI 的 workspace 隔离。
2. 任务队列 job_tasks：pending → processing → done/dead，带重试与死信上限，
   供心跳、日报等异步任务复用 —— 对标 TinyAGI 的 SQLite 队列。
3. 单店档案 store_profiles：把「单店经营引擎」的输入参数存档，随时可重跑
   calc_store_model，让方法论沉淀为可复用资产。

连接统一走 db.py 的 get_conn（惰性导入避免循环依赖），调用方式与 db_customers 等一致。
"""
import json
from datetime import datetime

__all__ = [
    # 领域上下文
    "get_domain_context", "set_domain_context", "list_domain_context",
    # 任务队列
    "enqueue_job", "claim_next_job", "mark_job_done", "mark_job_failed",
    "list_jobs", "requeue_job",
    # 单店档案
    "save_store_profile", "load_store_profile", "list_store_profiles",
    "delete_store_profile",
]

# 任务队列默认死信上限（重试达到该次数仍失败则进入 dead）
DEFAULT_MAX_RETRIES = 5
# 任务状态
JOB_PENDING, JOB_RUNNING, JOB_DONE, JOB_DEAD = "pending", "running", "done", "dead"


def _conn():
    from db import get_conn  # 惰性导入：db.py 聚合层加载完成后才执行
    return get_conn()


# ---------------- 领域上下文（每个业务域独立的经营记忆） ----------------
def set_domain_context(domain, key, value):
    """写入某业务域的上下文；同 (domain,key) 覆盖更新，value 可为任意 JSON 序列化对象"""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    with _conn() as conn:
        conn.execute(
            "INSERT INTO domain_context(domain, key, value, updated_at) "
            "VALUES(?,?,?,datetime('now','localtime')) "
            "ON CONFLICT(domain,key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at",
            (domain, key, value))
    return {"domain": domain, "key": key, "value": value}


def get_domain_context(domain, key):
    """读取某业务域的单条上下文；找不到返回 None"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT value, updated_at FROM domain_context WHERE domain=? AND key=?",
            (domain, key)).fetchone()
    if not row:
        return None
    value = row["value"]
    try:
        value = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        pass
    return {"domain": domain, "key": key, "value": value,
            "updated_at": row["updated_at"]}


def list_domain_context(domain=None):
    """列出某业务域（或不限）的全部上下文，返回 [{domain,key,value,updated_at}]"""
    with _conn() as conn:
        if domain:
            rows = conn.execute(
                "SELECT domain,key,value,updated_at FROM domain_context "
                "WHERE domain=? ORDER BY updated_at DESC", (domain,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT domain,key,value,updated_at FROM domain_context "
                "ORDER BY domain, updated_at DESC").fetchall()
    out = []
    for r in rows:
        value = r["value"]
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            pass
        out.append({"domain": r["domain"], "key": r["key"], "value": value,
                    "updated_at": r["updated_at"]})
    return out


# ---------------- 任务队列（心跳/日报推送复用） ----------------
def enqueue_job(task_type, payload=None, max_retries=DEFAULT_MAX_RETRIES):
    """入队一个待执行任务；payload 为任意 JSON 可序列化对象，返回 job_id"""
    if payload is not None and not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False)
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO job_tasks(task_type, payload, status, max_retries) "
            "VALUES(?,?,?,?)",
            (task_type, payload or "", JOB_PENDING, max_retries))
        return cur.lastrowid


def claim_next_job():
    """领取一条 pending 任务并置为 running；无任务返回 None。
    简化版（单进程）：直接把 pending 置 running 返回，避免复杂租约。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM job_tasks WHERE status=? ORDER BY id LIMIT 1",
            (JOB_PENDING,)).fetchone()
        if not row:
            return None
        conn.execute("UPDATE job_tasks SET status=? WHERE id=?",
                     (JOB_RUNNING, row["id"]))
    job = dict(row)
    job["status"] = JOB_RUNNING  # 已置 processing，返回最新状态
    try:
        job["payload"] = json.loads(job["payload"]) if job["payload"] else None
    except (json.JSONDecodeError, TypeError):
        pass
    return job


def mark_job_done(job_id, result=None):
    """任务成功：置 done 并记录结果（可选）"""
    if result is not None and not isinstance(result, str):
        result = json.dumps(result, ensure_ascii=False)
    with _conn() as conn:
        conn.execute(
            "UPDATE job_tasks SET status=?, result=?, error='' WHERE id=?",
            (JOB_DONE, result or "", job_id))


def mark_job_failed(job_id, error=""):
    """任务失败：未达死信上限则重试（retries+1 回 pending），否则进 dead"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT retries, max_retries FROM job_tasks WHERE id=?",
            (job_id,)).fetchone()
        if not row:
            return None
        retries = row["retries"] + 1
        if retries >= (row["max_retries"] or DEFAULT_MAX_RETRIES):
            conn.execute(
                "UPDATE job_tasks SET status=?, retries=?, error=? WHERE id=?",
                (JOB_DEAD, retries, (error or "")[:500], job_id))
            return "dead"
        conn.execute(
            "UPDATE job_tasks SET status=?, retries=?, error=? WHERE id=?",
            (JOB_PENDING, retries, (error or "")[:500], job_id))
        return "requeued"


def requeue_job(job_id):
    """手动把某条任务（done/dead）重新入队，retries 清零重新排队"""
    with _conn() as conn:
        conn.execute("UPDATE job_tasks SET status=?, retries=0, error='' WHERE id=?",
                     (JOB_PENDING, job_id))


def list_jobs(task_type=None, status=None, limit=50):
    """查询任务列表；可按类型/状态过滤，按 id 倒序取最近 limit 条"""
    sql = "SELECT * FROM job_tasks"
    where, params = [], []
    if task_type:
        where.append("task_type=?")
        params.append(task_type)
    if status:
        where.append("status=?")
        params.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload"]) if d["payload"] else None
        except (json.JSONDecodeError, TypeError):
            pass
        out.append(d)
    return out


# ---------------- 单店档案（引擎输入沉淀为可复用资产） ----------------
_PROFILE_FIELDS = (
    "biz_type", "gross_margin", "rent", "salary", "utilities",
    "total_investment", "cash_on_hand", "traffic", "competitor",
)


def save_store_profile(name, profile_id=None, **inputs):
    """保存/更新一个门店档案；返回档案 id。
    inputs 兼容 calc_store_model 的入参名，null 字段以 None 存储。"""
    data = {k: inputs.get(k) for k in _PROFILE_FIELDS}
    with _conn() as conn:
        if profile_id:
            conn.execute(
                "UPDATE store_profiles SET name=?, biz_type=?, gross_margin=?, "
                "rent=?, salary=?, utilities=?, total_investment=?, "
                "cash_on_hand=?, traffic=?, competitor=?, updated_at=datetime('now','localtime') "
                "WHERE id=?",
                (name, data["biz_type"], data["gross_margin"], data["rent"],
                 data["salary"], data["utilities"], data["total_investment"],
                 data["cash_on_hand"], data["traffic"], data["competitor"],
                 profile_id))
            return profile_id
        cur = conn.execute(
            "INSERT INTO store_profiles(name, biz_type, gross_margin, rent, salary, "
            "utilities, total_investment, cash_on_hand, traffic, competitor) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (name, data["biz_type"], data["gross_margin"], data["rent"],
             data["salary"], data["utilities"], data["total_investment"],
             data["cash_on_hand"], data["traffic"], data["competitor"]))
        return cur.lastrowid


def load_store_profile(profile_id):
    """读取一个店档案，返回可直喂 calc_store_model 的 dict（含 id/name）"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM store_profiles WHERE id=?", (profile_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["gross_margin"] = d["gross_margin"] if d["gross_margin"] is not None else None
    return d


def list_store_profiles():
    """列出全部店档案（含最近更新时间），按 id 倒序"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, name, biz_type, gross_margin, rent, salary, utilities, "
            "total_investment, cash_on_hand, traffic, competitor, updated_at "
            "FROM store_profiles ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def delete_store_profile(profile_id):
    """删除店档案，返回是否删除成功"""
    with _conn() as conn:
        cur = conn.execute("DELETE FROM store_profiles WHERE id=?", (profile_id,))
        return cur.rowcount > 0