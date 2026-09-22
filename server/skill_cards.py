"""skill_cards.py — 技能卡片引擎：统一注册、条件评估、三层输出

## 定位

这是"掌柜技能架构"的薄层实现：不改变 team_domains 的编排骨架，
只把散落在提示词里的**关注点**显式建模为"技能卡片"（Skill as Card）。

一张卡片包含：
  - id            内部唯一标识
  - name          用户看到的名字（必须是大白话）
  - trigger       触发条件表达式（简单 Python 表达式，上下文变量见 CONTEXT_VARS）
  - severity      展示优先级：high > medium > low
  - summary       一句话结论（第一层）
  - detail        为什么在意 + 怎么做（第二层）
  - evidence      支撑事实的键值对（第三层）

三层深度（渐进式披露）：
  - 第一层：summary   —— 90% 的用户只看这一句 + 一个行动
  - 第二层：detail    —— 想深入了解的人点开看"为什么 + 怎么做"
  - 第三层：evidence  —— 会计/银行/技术评审看的完整数据
"""

from __future__ import annotations

from typing import Any

from plain_language import polish

__all__ = ["SKILL_CARDS", "build_context", "evaluate_triggers",
           "build_layered_review", "top_skills"]


# ---------------- 上下文变量 ----------------
# build_context() 返回的 key → 技能 trigger 表达式里可用的变量名。
# 保持简单：全是标量（int/float/str/bool），不嵌套 dict，避免表达式写起来像编程。

CONTEXT_VARS = [
    "today_income", "today_expense", "today_balance",
    "month_income", "month_expense", "month_balance",
    "customer_count", "stale_customer_days",
    "low_stock_count", "expiring_count",
    "invoice_rate", "month_purchase",
    "cash_runway_months", "breakeven_daily",
]


# ---------------- 从现有 DB 构建上下文 ----------------

def build_context() -> dict[str, Any]:
    """从现有数据层构建技能卡片上下文（纯本地查询，不调 AI）。

    任何一段查询失败都返回该维度的安全默认值，不影响其余维度。
    这样技能卡片引擎在数据不完整时依然可用（触发条件拿到 0/空值）。
    """
    from datetime import date as _date

    ctx: dict[str, Any] = {k: 0 for k in CONTEXT_VARS}
    ctx["breakeven_daily"] = 0.0

    try:
        from db import today_summary, monthly_summary
        t = today_summary()
        m = monthly_summary()
        ctx.update({
            "today_income": float(t.get("income") or 0),
            "today_expense": float(t.get("expense") or 0),
            "today_balance": float(t.get("balance") or 0),
            "month_income": float(m.get("income") or 0),
            "month_expense": float(m.get("expense") or 0),
            "month_balance": float(m.get("balance") or 0),
        })
        for cat in m.get("categories") or []:
            if cat.get("category") == "进货":
                ctx["month_purchase"] = float(cat.get("total") or 0)
                break
    except Exception:
        pass

    try:
        from db import list_customers
        rows = list_customers()
        ctx["customer_count"] = len(rows or [])
        today = _date.today()
        oldest_days = 0
        for c in rows or []:
            last = (c.get("last_visit") or "").strip()
            if last:
                try:
                    from datetime import datetime as _dt
                    last_date = _dt.strptime(last[:10], "%Y-%m-%d").date()
                    days_since = (today - last_date).days
                    if days_since > 0:
                        oldest_days = max(oldest_days, min(days_since, 999))
                except Exception:
                    continue
        ctx["stale_customer_days"] = oldest_days
    except Exception:
        pass

    try:
        from db import stock_summary
        s = stock_summary()
        ctx["low_stock_count"] = len(s.get("low_stock") or [])
        ctx["expiring_count"] = len(s.get("expiring") or [])
    except Exception:
        pass

    try:
        from db import invoice_summary
        inv = invoice_summary(_date.today().strftime("%Y-%m"))
        by = {x.get("kind"): x for x in (inv.get("by_kind") or [])}
        invoice_in_total = float((by.get("in") or {}).get("total") or 0)
        if ctx["month_purchase"] > 0:
            ctx["invoice_rate"] = invoice_in_total / ctx["month_purchase"]
        else:
            ctx["month_purchase"] = invoice_in_total
            ctx["invoice_rate"] = 1.0 if invoice_in_total > 0 else 0.0
    except Exception:
        ctx["month_purchase"] = 0.0
        ctx["invoice_rate"] = 1.0

    try:
        from db import list_store_profiles
        profiles = list_store_profiles()
        if profiles:
            p = profiles[0]
            ctx["cash_runway_months"] = round(float(p.get("cash_on_hand") or 0) / max(1.0, float(ctx.get("month_expense") or 1)), 1)
    except Exception:
        pass

    return ctx


# ---------------- 技能卡片注册表 ----------------
# trigger 是一个 Python 表达式，变量来自 build_context()。
# 安全性：只允许白名单变量和比较运算，不暴露任意代码执行。

SKILL_CARDS: list[dict[str, Any]] = [
    {
        "id": "detect_missing_invoices",
        "name": "抓漏票",
        "trigger": "month_purchase > 5000 and invoice_rate < 0.5",
        "severity": "high",
        "summary": "本月进货 {month_purchase} 元，票据齐了吗？",
        "detail": "进货花的钱没有对应发票，月底算税时抵不了，可能多交几百块。"
                  "建议明天找供应商把票要回来。",
        "evidence_keys": ["month_purchase", "invoice_rate"],
    },
    {
        "id": "detect_customer_churn",
        "name": "揪沉睡熟客",
        "trigger": "stale_customer_days >= 14",
        "severity": "medium",
        "summary": "最久没来的熟客已经 {stale_customer_days} 天没来了，要不要打个电话？",
        "detail": "老客人超过两周没来，可能去了隔壁或者忘了你。找个由头打个电话"
                  "（'新到的咸菜给你留一袋'），比直接催债好开口。",
        "evidence_keys": ["stale_customer_days", "customer_count"],
    },
    {
        "id": "detect_cash_shortfall",
        "name": "盯现金",
        "trigger": "cash_runway_months < 2",
        "severity": "high",
        "summary": "手里的钱还能撑 {cash_runway_months} 个月，先别大量进货。",
        "detail": "现金不够撑 3 个月就有风险。先看哪些开支能缓一缓，"
                  "别把货囤太多——东西卖不出去，钱就卡住了。",
        "evidence_keys": ["cash_runway_months", "month_expense"],
    },
    {
        "id": "detect_low_stock",
        "name": "盯缺货",
        "trigger": "low_stock_count > 0",
        "severity": "medium",
        "summary": "有 {low_stock_count} 种货快用完了，记得补。",
        "detail": "货快用完会影响明天开门。今天顺手跟供应商说一声，"
                  "别等到明天早上发现没了才急。",
        "evidence_keys": ["low_stock_count"],
    },
    {
        "id": "detect_expiring",
        "name": "盯临期",
        "trigger": "expiring_count > 0",
        "severity": "high",
        "summary": "有 {expiring_count} 种货快过期了，赶紧想办法用掉。",
        "detail": "快过期的货直接扔就是白丢钱。可以做'今日特价'、送熟客小样，"
                  "或者跟供应商商量换货——先动手，别等过期。",
        "evidence_keys": ["expiring_count"],
    },
    {
        "id": "detect_month_loss",
        "name": "盯月亏",
        "trigger": "month_balance < 0",
        "severity": "high",
        "summary": "这个月到现在亏了 {month_balance} 元，得看看花哪儿了。",
        "detail": "亏钱不可怕，不知道为什么亏才可怕。先看支出里最大的是哪三类，"
                  "再决定是砍开支还是提价。",
        "evidence_keys": ["month_income", "month_expense", "month_balance"],
    },
    {
        "id": "detect_today_profit",
        "name": "今日安心",
        "trigger": "today_balance > 0",
        "severity": "low",
        "summary": "今天赚了 {today_balance} 元，不错，照常做。",
        "detail": "今天收的比花的多。不用额外动作，保持节奏。",
        "evidence_keys": ["today_income", "today_expense", "today_balance"],
    },
]

# 按严重度排序权重
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


# ---------------- 安全校验 ----------------



def _safe_eval(trigger: str, context: dict[str, Any]) -> bool:
    """安全评估触发条件：AST 白名单，禁止任意代码执行。"""
    if not trigger:
        return False

    import ast

    try:
        tree = ast.parse(trigger, mode="eval")
    except SyntaxError:
        return False

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            values = [visit(value) for value in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not visit(node.operand)
        if isinstance(node, ast.Compare):
            left = visit(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                right = visit(comparator)
                if isinstance(op, ast.Eq):
                    matched = left == right
                elif isinstance(op, ast.NotEq):
                    matched = left != right
                elif isinstance(op, ast.Lt):
                    matched = left < right
                elif isinstance(op, ast.LtE):
                    matched = left <= right
                elif isinstance(op, ast.Gt):
                    matched = left > right
                elif isinstance(op, ast.GtE):
                    matched = left >= right
                else:
                    raise ValueError("unsupported operator")
                if not matched:
                    return False
                left = right
            return True
        if isinstance(node, ast.Name):
            if node.id not in CONTEXT_VARS:
                raise ValueError("unknown variable")
            return context.get(node.id, 0)
        if isinstance(node, ast.Constant) and isinstance(node.value, (bool, int, float)):
            return node.value
        raise ValueError("unsupported expression")

    try:
        return bool(visit(tree))
    except Exception:
        return False


# ---------------- 触发评估 ----------------

def evaluate_triggers(context: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    """遍历所有技能卡片，返回触发命中的卡片（按严重度排序，最多 limit 张）。

    返回的每张卡片包含 summary/detail/evidence，可直接给前端渲染。
    """
    hit = []
    for card in SKILL_CARDS:
        if not _safe_eval(card["trigger"], context):
            continue
        rendered = card.copy()
        try:
            rendered["summary"] = card["summary"].format(**context)
            rendered["detail"] = card["detail"].format(**context)
        except (KeyError, IndexError, ValueError):
            pass
        rendered["evidence"] = {
            k: context.get(k) for k in card.get("evidence_keys", []) if k in context
        }
        hit.append(rendered)

    hit.sort(key=lambda c: _SEVERITY_ORDER.get(c.get("severity", "low"), 99))
    return hit[:max(0, limit)]


def top_skills(context: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    """evaluate_triggers 的别名，语义更贴近'掌柜今天盯什么'。"""
    return evaluate_triggers(context, limit)


# ---------------- 三层复盘输出 ----------------

def build_layered_review(context: dict[str, Any], full_review: str = "") -> dict[str, Any]:
    """把完整复盘文本 + 技能卡片 → 三层结构输出。

    返回：
      {
        "layer1_summary": str,     # 一句话（首页默认显示）
        "layer2_detail": str,      # 展开看：为什么 + 怎么做
        "layer3_analysis": {...},  # 再展开：完整上下文 + 技能证据
        "skills": [...],           # 触发的技能卡片
      }
    """
    skills = evaluate_triggers(context, limit=3)

    if skills:
        s = skills[0]
        layer1 = s.get("summary", "")
        layer2_parts = [s.get("detail", "")]
        for extra in skills[1:2]:
            layer2_parts.append(extra.get("summary", ""))
        layer2 = " ".join(x for x in layer2_parts if x)
    else:
        layer1 = "今天账上没别的异常，照常做。"
        layer2 = full_review or "没有触发任何技能卡片。"

    return {
        "layer1_summary": polish(layer1),
        "layer2_detail": polish(layer2),
        "layer3_analysis": {
            "context": context,
            "skills": [
                {k: s.get(k) for k in ("id", "name", "severity", "summary", "detail", "evidence")}
                for s in skills
            ],
            "full_review": full_review,
        },
        "skills": skills,
    }
