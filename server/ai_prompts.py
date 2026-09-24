"""ai_prompts.py — 提示词片段与 AI 生成器（从 ai.py 外移，架构自检 L1）

职责：方言提示片段 + 熟客提醒 / 经营洞察 / 熟客画像 / 报税建议 四个生成任务。

依赖方向：本模块**不在顶层 import ai**；通过 `_chat/_json/_available` 中转函数在函数内
延迟导入 `ai` 的对应能力，避免与 ai.py 形成顶层导入环。
"""
from __future__ import annotations

import json
import logging

from config import load_settings

log = logging.getLogger("ai_prompts")


def _chat(*args, **kwargs):
    from ai import chat          # 延迟导入：ai 顶层会 re-export 本模块，避免成环
    return chat(*args, **kwargs)


def _json(text):
    from ai import _extract_json
    return _extract_json(text)


def _available():
    from ai import ai_available
    return ai_available()


def _language_hint() -> str:
    """口语/方言偏好提示：非普通话时提醒模型按语义理解，别纠结字面。

    设置页可选「普通话/粤语/四川话/英语…」；中文方言用词不标准，
    不认识时容易漏金额或判错方向，这里显式提示模型。
    """
    try:
        lang = (load_settings().get("language") or "").strip()
    except Exception:  # noqa: BLE001
        lang = ""
    if lang and lang not in ("普通话", "zh", "中文", "zh-CN", "zh_CN"):
        return (f"注意：店主可能用「{lang}」表达（口语/方言），请按语义理解，"
                "不要因为用词不标准而漏掉金额或算错收支方向。\n")
    return ""




def generate_reminders(customer_brief: str) -> list:
    """根据熟客画像生成今天该做的事（问候、留货、追单）"""
    if not _available():
        return []
    prompt = (
        "你是街边小店店主的记忆外挂，帮他记住那些'不值钱但暖心'的细节。\n"
        "下面是一份熟客档案（名字、常点、最近记忆点）。请输出今天适合店主做的事：\n"
        "1) 每个人最多1条；2) 口语化，像随口提醒一样；3) 只挑最有价值的2-3条，不要凑数。\n"
        "只输出JSON数组，如 [{\"customer\":\"王阿姨\",\"content\":\"上次她说孙子考了一百分，今天可以问问\"}]。\n"
        f"熟客档案：{customer_brief}\n"
    )
    try:
        return _json(_chat([{"role": "user", "content": prompt}], temperature=0.6,
                                  domain="熟客提醒"))
    except Exception:
        return []


# ---------------- 4. 月度经营洞察 ----------------
def generate_insights(monthly_data: dict, prev_context: str = "",
                      business_days: int | None = None) -> str:
    """基于月度收支汇总生成经营洞察（环比、异常品类、可执行建议）。

    business_days：本月的**实际营业天数**。必须传，否则模型会默认按 30 天
    折算日均，与单店模型的「实际日销」口径对不上 —— 例如某月只营业 16 天、
    收入 21120 元，按 30 天算日均 704 元，按营业天数算是 1320 元，
    两个说法出现在同一场演示里会自相矛盾。
    """
    if not _available():
        # 降级：模板化数据分析
        income = monthly_data.get("income", 0)
        expense = monthly_data.get("expense", 0)
        net = income - expense
        cats = monthly_data.get("categories", [])
        top_expense = max((c for c in cats if c.get("trans_type") == "expense"),
                          key=lambda c: c.get("total", 0), default=None) if cats else None
        lines = [f"本月收入 {income:.0f} 元，支出 {expense:.0f} 元，净{'收入' if net >= 0 else '支出'} {abs(net):.0f} 元。"]
        if business_days:
            lines.append(f"按 {business_days} 天营业计，日均进账 {income / business_days:.0f} 元。")
        if top_expense:
            lines.append(f"支出最高的是{top_expense.get('friendly', top_expense.get('category', ''))}，{top_expense.get('total', 0):.0f} 元。")
        if net < 0:
            lines.append("这个月入不敷出，得想办法开源节流。")
        elif net > 0 and income > 0:
            lines.append("这个月有结余，可以考虑攒着备货或改善设备。")
        lines.append("(提示：在设置页填入 API Key 后可获得更深入的 AI 分析)")
        return " ".join(lines)
    prompt = (
        "你是一家街边小店的AI掌柜，负责帮老板看懂每月经营数据，用大白话给建议。\n"
        f"本月收支数据：{json.dumps(monthly_data, ensure_ascii=False, default=str)}\n"
        + (f"本月实际营业天数：{business_days} 天\n" if business_days else "")
        + (f"上次分析参考：{prev_context}\n" if prev_context else "")
        + "输出不超过3条：① 本月赚亏 ② 最该盯的支出 ③ 一个明天可做的动作。\n"
        "口语化，不写专业术语和空话，动作要带具体金额或品类。\n"
        "重要：算日均营业额、日均开销时，**用上面给的实际营业天数去除**，"
        "不要默认按 30 天折算 —— 老板会拿这个数字跟别的页面对照，算错就穿帮了。\n"
        "另外：**不要推算「日保本线」**。日保本线由「单店模型」页按标准 30 天/月计算，"
        "你这边只有半个多月的数据，两边算法不同会给出不同数字。"
        "你只说月保本流水（月固定成本 ÷ 毛利率）即可。"
    )
    # 数据汇总已由本地完成；默认关闭思考，避免月度洞察长时间等待。
    return _chat([{"role": "user", "content": prompt}], temperature=0.5,
                max_tokens=180, domain="经营洞察").strip()


# ---------------- 5. 客户画像 ----------------
def generate_customer_insight(customer: dict, transactions: list) -> str:
    """分析熟客交易历史，生成画像和个性化维系建议"""
    if not _available():
        # 降级：规则标签
        txs = transactions or []
        count = len(txs)
        total = sum(t.get("amount", 0) or 0 for t in txs)
        avg = total / count if count else 0
        tags = []
        if count >= 10:
            tags.append("常客")
        elif count >= 3:
            tags.append("回头客")
        if avg >= 50:
            tags.append("高消费")
        mems = customer.get("memories", [])
        lines = [f"{customer.get('name', '顾客')}：{count} 笔交易，累计 {total:.0f} 元，均价 {avg:.0f} 元。"]
        if tags:
            lines.append(f"标签：{'、'.join(tags)}。")
        if mems:
            lines.append(f"记忆点：{'；'.join(m.get('content', '') for m in mems[:3])}")
        lines.append("(提示：在设置页填入 API Key 后可获得个性化 AI 维系建议)")
        return " ".join(lines)
    prompt = (
        "你是街边小店的熟客记忆外挂，帮店主更懂他的老主顾。\n"
        f"熟客信息：{json.dumps(customer, ensure_ascii=False, default=str)}\n"
        f"近期交易：{json.dumps(transactions[:20], ensure_ascii=False, default=str)}\n"
        "请用大白话输出：① 消费偏好（爱买什么、多久来一次）② 性格猜测（大方/节俭/健谈）"
        "③ 一条个性化的维系建议（具体到这周该做什么，比如\"上次她说孙子考了一百分，这周见面可以问一句\"，"
        "记住她的细节，不要泛泛\"多问候\"）。直接输出正文，不要列表格式。"
    )
    return _chat([{"role": "user", "content": prompt}], temperature=0.6, max_tokens=400,
                domain="熟客画像").strip()


# ---------------- 6. 报税建议 ----------------
def generate_tax_advice(quarterly_revenue: float, vat_result: dict, prev_advice: str = "") -> str:
    """基于季度收入和增值税计算结果生成报税建议"""
    if not _available():
        # 降级：规则判断
        exempted = vat_result.get("exempt", False)
        vat_due = vat_result.get("vat", 0)
        lines = []
        if exempted:
            lines.append(f"季度销售额 {quarterly_revenue:.0f} 元，≤30万符合小规模免征，本季度增值税 0 元。")
        else:
            lines.append(f"季度销售额 {quarterly_revenue:.0f} 元，应缴增值税 {vat_due:.0f} 元。")
        lines.append("记得按时申报，季度结束后次月15号前完成。")
        lines.append("(提示：在设置页填入 API Key 后可获得个性化 AI 报税建议)")
        return " ".join(lines)
    prompt = (
        "你是小店的税务顾问，帮老板用大白话搞懂报税。\n"
        f"本季度销售额：{quarterly_revenue:.0f} 元\n"
        f"增值税计算结果：{json.dumps(vat_result, ensure_ascii=False)}\n"
        + (f"上次建议参考：{prev_advice}\n" if prev_advice else "")
        + "请输出：① 本季度要交多少税 ② 有没有节税空间 ③ 下个季度该注意什么。\n"
        "口语化，不要用税法术语。直接输出正文。"
    )
    # 默认走轻量推理：税务计算已由本地规则完成，AI 只负责解释和提醒。
    return _chat([{"role": "user", "content": prompt}], temperature=0.3,
                max_tokens=300, reasoning_effort="low", domain="报税建议").strip()
