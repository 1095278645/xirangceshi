"""eval_report.py — 「一句话记账」评测报告：分维度汇总 + 基线对比 + Markdown 看板

把 eval_ai_parse.py 的评测结果整理成**可归档、可展示、可进 CI** 的报告：
  - 分维度准确率（金额 / 收支方向 / 分类 / 熟客 / 四项全对），总体与分类别
  - 失败用例清单（含期望 vs 实得），一眼看出退化点
  - Markdown 看板（可直接贴进周报/评审材料）
  - 纯函数，**不联网**，可被单元测试直接覆盖

之所以独立成模块：评测脚本要真的调模型（耗额度、慢），而"报告怎么算"是纯逻辑，
抽出来就能离线测试，避免只有跑真模型才能发现报告算错。
"""
from __future__ import annotations

DIMENSIONS = [("金额", "amount"), ("收支方向", "direction"),
              ("分类", "category"), ("熟客", "customer"), ("四项全对", "ok")]


def dimension_summary(hits: dict, total: int, per_category: dict | None) -> dict:
    """分维度准确率汇总（hits 键见 DIMENSIONS 的第二项）。"""
    total = int(total or 0)

    def pct(n: int) -> float:
        return round(n / total * 100, 1) if total else 0.0

    overall = {
        label: {"hit": int(hits.get(key, 0) or 0), "pct": pct(int(hits.get(key, 0) or 0))}
        for label, key in DIMENSIONS
    }
    per_cat = {}
    for cat, v in (per_category or {}).items():
        n = int(v.get("n", 0) or 0)
        ok = int(v.get("ok", 0) or 0)
        per_cat[cat] = {"n": n, "ok": ok, "pct": round(ok / n * 100, 1) if n else 0.0}
    return {"total": total, "overall": overall, "per_category": per_cat}


def regressions(base_cases: dict, cur_cases: dict) -> list[str]:
    """纯对比：基线曾"四项全对"、本次却"有偏差"的用例文本列表。"""
    out = []
    for text, cur in (cur_cases or {}).items():
        old = (base_cases or {}).get(text)
        if old and old.get("ok") and not cur.get("ok"):
            out.append(text)
    return out


def to_markdown(meta: dict, summary: dict, failures: list[dict] | None = None) -> str:
    """生成 Markdown 报告（可直接贴进材料/周报）。"""
    meta = meta or {}
    total = summary.get("total", 0)
    lines = [
        "# 「一句话记账」评测报告",
        "",
        f"- 模型：`{meta.get('model') or '未知'}`",
        f"- 用例数：**{total}**",
        f"- 生成时间：{meta.get('at') or '（未记录）'}",
        "",
        "## 分维度准确率",
        "",
        "| 维度 | 命中 | 准确率 |",
        "|---|---:|---:|",
    ]
    for label, _ in DIMENSIONS:
        d = summary["overall"].get(label, {"hit": 0, "pct": 0.0})
        lines.append(f"| {label} | {d['hit']}/{total} | {d['pct']}% |")

    lines += ["", "## 分类别（四项全对）", "", "| 分类 | 命中 | 准确率 |", "|---|---:|---:|"]
    for cat, d in summary.get("per_category", {}).items():
        lines.append(f"| {cat} | {d['ok']}/{d['n']} | {d['pct']}% |")

    failures = failures or []
    lines += ["", f"## 失败用例（{len(failures)}）", ""]
    if not failures:
        lines.append("无 —— 全部通过 🎉")
    else:
        lines += ["| 用例 | 期望 | 实得 |", "|---|---|---|"]
        for f in failures:
            exp = f.get("expected") or {}
            got = f.get("got") or {}
            lines.append(
                f"| {f.get('text', '')} | 金额={exp.get('amount')} 方向={exp.get('direction')} "
                f"分类={exp.get('category')} 熟客={exp.get('customer')} | "
                f"金额={got.get('amount')} 方向={got.get('trans_type')} "
                f"分类={got.get('category')} 熟客={got.get('customer')} |")

    lines += ["", "---", "",
              "> 本报告由 `scripts/eval_ai_parse.py --report <前缀>` 生成；",
              "> 分维度口径见 `server/eval_report.py`。"]
    return "\n".join(lines) + "\n"
