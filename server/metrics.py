"""metrics.py — AI 成本与性能看板（汇总引擎）

把 db_metrics 落库的原始调用记录，算成评审/商业化能直接引用的数字：

  - 调用量/成功率、token 用量
  - 延迟 P50 / P95 / 最大值（体验指标）
  - **估算成本**（按模型单价 × 用量）与**每单成本**（成本 ÷ 窗口内记账笔数）

设计取舍：
  - 单价表是**示例值**，随模型调价会变；接口会一并返回所用单价并注明"估算"，
    不把估算包装成精确账单。
  - 未知模型用 DEFAULT_PRICE，并在结果里标 `unknown_priced_models`，避免"看起来精确"。
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("metrics")

__all__ = ["PRICES_YUAN_PER_1M", "DEFAULT_PRICE", "price_for", "summarize"]

# 单价：人民币元 / 每百万 tokens。**示例价**，可在 config.local.json 里用
# "ai_prices": {"模型名": {"in": .., "out": ..}} 覆盖。
PRICES_YUAN_PER_1M = {
    "deepseek-chat":     {"in": 2.0,  "out": 8.0},
    "deepseek-flash":    {"in": 1.0,  "out": 4.0},
    "deepseek-v4.1-flash": {"in": 1.0, "out": 4.0},
    "deepseek-reasoner": {"in": 4.0,  "out": 16.0},
    "gpt-4o-mini":       {"in": 1.1,  "out": 4.4},
    "qwen-turbo":        {"in": 0.3,  "out": 0.6},
}
DEFAULT_PRICE = {"in": 2.0, "out": 8.0}


def _load_price_overrides() -> dict:
    """config.local.json 可选覆盖：{"ai_prices": {...}}"""
    try:
        from config import _LOCAL_CONFIG  # noqa: PLC0415
        if _LOCAL_CONFIG.exists():
            with open(_LOCAL_CONFIG, encoding="utf-8") as f:
                cfg = json.load(f)
            ov = cfg.get("ai_prices") or {}
            if isinstance(ov, dict):
                return {str(k).lower(): v for k, v in ov.items()}
    except Exception:  # noqa: BLE001
        pass
    return {}


def price_for(model: str) -> tuple[dict, bool]:
    """返回 (单价, 是否为已知模型)。支持前缀匹配（如 deepseek-v4.1-flash-xxxx）。"""
    m = (model or "").strip().lower()
    overrides = _load_price_overrides()
    table = {**PRICES_YUAN_PER_1M, **overrides}
    for name, p in table.items():
        if m == name or m.startswith(name):
            return {"in": float(p.get("in", 0) or 0),
                    "out": float(p.get("out", 0) or 0)}, True
    return dict(DEFAULT_PRICE), False


def _percentile(values: list[int], pct: float) -> int:
    """线性插值分位数（values 需已排序）。"""
    if not values:
        return 0
    if len(values) == 1:
        return int(values[0])
    k = (len(values) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    frac = k - lo
    return int(round(values[lo] + (values[hi] - values[lo]) * frac))


def _orders_in_window(days: int) -> int:
    try:
        from db import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM transactions "
                "WHERE created_at >= datetime('now','localtime', ?)",
                (f"-{days} days",)).fetchone()
        return int(row["c"] or 0)
    except Exception:  # noqa: BLE001
        return 0


def summarize(days: int = 7) -> dict:
    from db_metrics import metrics_window_stats  # 延迟导入，避免环
    rows = metrics_window_stats(days)
    days = max(1, int(days))

    total = len(rows)
    ok = sum(1 for r in rows if int(r.get("ok") or 0) == 1)
    failed = total - ok
    lat = sorted(int(r.get("latency_ms") or 0) for r in rows)
    prompt = sum(int(r.get("prompt_tokens") or 0) for r in rows)
    completion = sum(int(r.get("completion_tokens") or 0) for r in rows)

    cost = 0.0
    unknown: set[str] = set()
    by_model: dict[str, dict] = {}
    by_domain: dict[str, dict] = {}
    for r in rows:
        model = r.get("model") or "(未知)"
        price, known = price_for(model)
        if not known and r.get("total_tokens"):
            unknown.add(model)
        c = (int(r.get("prompt_tokens") or 0) / 1e6 * price["in"]
             + int(r.get("completion_tokens") or 0) / 1e6 * price["out"])
        cost += c
        for bucket, key in ((by_model, model), (by_domain, r.get("domain") or "(未标注)")):
            b = bucket.setdefault(key, {"calls": 0, "tokens": 0, "cost_yuan": 0.0,
                                        "latency_ms": 0})
            b["calls"] += 1
            b["tokens"] += int(r.get("total_tokens") or 0)
            b["cost_yuan"] += c
            b["latency_ms"] += int(r.get("latency_ms") or 0)

    def _finalize(bucket: dict) -> list[dict]:
        out = []
        for k, v in bucket.items():
            avg = int(round(v["latency_ms"] / v["calls"])) if v["calls"] else 0
            out.append({"name": k, "calls": v["calls"], "tokens": v["tokens"],
                        "cost_yuan": round(v["cost_yuan"], 4), "avg_latency_ms": avg})
        return sorted(out, key=lambda x: x["cost_yuan"], reverse=True)

    orders = _orders_in_window(days)
    return {
        "window_days": days,
        "calls": total,
        "ok": ok,
        "failed": failed,
        "success_rate": round(ok / total, 4) if total else None,
        "tokens": {"prompt": prompt, "completion": completion, "total": prompt + completion},
        "latency_ms": {
            "avg": int(round(sum(lat) / len(lat))) if lat else 0,
            "p50": _percentile(lat, 0.50),
            "p95": _percentile(lat, 0.95),
            "max": lat[-1] if lat else 0,
        },
        "cost_est_yuan": round(cost, 4),
        "orders_in_window": orders,
        "per_order_cost_est_yuan": round(cost / orders, 6) if orders else None,
        "by_model": _finalize(by_model),
        "by_domain": _finalize(by_domain),
        "prices_yuan_per_1m": PRICES_YUAN_PER_1M,
        "unknown_priced_models": sorted(unknown),
        "note": "成本为按 token 用量 × 示例单价的估算值，仅供量级参考；单价可在 config.local.json 的 ai_prices 覆盖。",
    }
