"""db_metrics.py — AI 调用指标落库（成本与性能看板的底座）

## 为什么需要

项目大量调用大模型，但此前**没有任何用量/耗时记录**：说不出"一次记账花多少钱"、
"P95 延迟多少""哪个业务域最费钱"。这些恰是评审与商业化最关心的数字。
本模块只做一件事：把每次 `ai.chat` 的模型、耗时、token 用量、成败落库。

## 设计

- 表 `ai_metrics`：一行 = 一次模型调用。
- **绝不影响主流程**：插入失败只记日志，不影响 AI 调用本身。
- **测试不污染演示库**：测试里若仍指向默认生产库路径，直接跳过写入
  （很多用例会把 db.DB_PATH 指到临时库，那种情况下正常记录，便于断言）。
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("db_metrics")

__all__ = ["init_ai_metrics_tables", "record_ai_call", "list_ai_calls",
           "metrics_window_stats", "clear_ai_metrics"]


def init_ai_metrics_tables(conn) -> None:
    """建表（幂等）。由 db.init_db() 调用。"""
    conn.execute("""
    CREATE TABLE IF NOT EXISTS ai_metrics (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        at                TEXT DEFAULT (datetime('now','localtime')),
        model             TEXT DEFAULT '',
        domain            TEXT DEFAULT '',
        latency_ms        INTEGER DEFAULT 0,
        prompt_tokens     INTEGER DEFAULT 0,
        completion_tokens INTEGER DEFAULT 0,
        total_tokens      INTEGER DEFAULT 0,
        reasoning_chars   INTEGER DEFAULT 0,
        ok                INTEGER DEFAULT 1,
        error             TEXT DEFAULT ''
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_metrics_at ON ai_metrics(at);")


def _should_skip() -> bool:
    """测试里保护真实演示库：指向默认生产库路径时跳过写入。"""
    if "PYTEST_CURRENT_TEST" not in os.environ:
        return False
    try:
        from config import DB_PATH as default_path
        from db import current_db_path
        return str(current_db_path()) == str(default_path)
    except Exception:  # noqa: BLE001
        return False


def record_ai_call(model: str, latency_ms: int, usage=None, domain: str = "",
                   ok: bool = True, error: str = "", reasoning_chars: int = 0) -> None:
    """记录一次 AI 调用。usage 为 OpenAI 风格的 usage 对象（可缺失）。

    任何异常都被吞掉：指标是"旁路"，绝不能因为它让记账失败。
    """
    if _should_skip():
        return
    prompt = completion = total = 0
    if usage is not None:
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        total = int(getattr(usage, "total_tokens", 0) or 0) or (prompt + completion)
    try:
        from db import get_conn
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO ai_metrics(model, domain, latency_ms, prompt_tokens, "
                "completion_tokens, total_tokens, reasoning_chars, ok, error) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (model or "", domain or "", int(latency_ms or 0), prompt, completion,
                 total, int(reasoning_chars or 0), 1 if ok else 0, (error or "")[:300]))
    except Exception as e:  # noqa: BLE001
        log.warning("记录 AI 指标失败（已忽略）：%s", e)


def list_ai_calls(limit: int = 50) -> list[dict]:
    from db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM ai_metrics ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def metrics_window_stats(days: int = 7) -> dict:
    """取窗口内的聚合明细（供 metrics.py 计算分位数与成本）。"""
    days = max(1, int(days))
    from db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT model, domain, latency_ms, prompt_tokens, completion_tokens, "
            "total_tokens, ok FROM ai_metrics "
            "WHERE at >= datetime('now','localtime', ?)",
            (f"-{days} days",)).fetchall()
    return [dict(r) for r in rows]


def clear_ai_metrics() -> int:
    """清空指标（仅供测试/演示重置）。"""
    from db import get_conn
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM ai_metrics")
        return cur.rowcount
