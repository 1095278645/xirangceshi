"""AI 能力层：记账解析、文案生成、熟客提醒 —— 支持多种 OpenAI 兼容大模型

多 agent 团队编排已抽到 team_domains.py；文本解析辅助拆到 ai_parsing.py。
为兼容旧入口，底部从 team_domains 再导出 generate_copy / generate_store_diagnosis。
"""
import json
import logging
import re
import time

import config
from config import load_settings
from categories import detect_category
from ai_parsing import extract_amount as _extract_amount, extract_customer as _extract_customer  # noqa: F401

log = logging.getLogger("ai")

# 递增重试的 max_tokens 上限。思考型模型（deepseek-flash / v4-pro）的推理开销
# 随提示词长度与任务复杂度增长：实测短提示 600~1700 tokens，多问句任务
# （如报税建议要答三个问题）推理可达 5000+ tokens。上限留足余量。
_CHAT_TOKEN_CAP = 16000

# 起始预算下限：思考型模型即使最短提示也要 600+ tokens 推理，低于此量级
# 几乎必然返回空，直接从这个值起步可省掉无谓的重试往返。
# 非思考模型（deepseek-chat）不会因为预算变大而多输出，只是允许更长回答。
_CHAT_TOKEN_MIN = 2000

# 默认推理强度。deepseek-flash 的思考模式默认 effort=high，但本项目多数调用
# 是信息抽取/短文案这类不需要深度推理的任务。实测同一提示词的差异：
#   思考 low  : 2.5s，推理 513 字，输出 255 tokens
#   非思考     : 0.5s，推理   0 字，输出  26 tokens   ← 快 5 倍、便宜 10 倍，质量相当
# 因此默认关闭思考；需要判断/分析的任务在调用点显式指定 effort。
_DEFAULT_REASONING_EFFORT = "off"

# effort="off" 映射为非思考模式（thinking.type=disabled）。
# 另一层原因：思考模式下 temperature 被服务端忽略（官方文档明确说明），
# 而多 agent 编排依赖 temperature 区分员工角色（创意文案师 0.9 要发散、
# 合规审核 0.2 要稳定），这些任务必须走非思考模式才能保住角色差异化。
_THINKING_OFF = "off"

# 支持思考模式开关的模型（精确匹配模型名，小写）。
# 官方文档 MODELS 页：deepseek-flash → DeepSeek-V4.1-Flash、deepseek-v4-pro → DeepSeek-V4-Pro；
# 旧名 deepseek-v4-flash 仍被接受但已下线，请求由 V4.1-Flash 承接。
# 不在此列表（如 deepseek-chat，或任何第三方模型）不发送思考相关参数。
_THINKING_MODELS = frozenset({
    "deepseek-flash", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp",
    "deepseek-v4-pro",
    # 企业网关（OpenAI 兼容）上的 V4.1-Flash 实际模型名带版本号，
    # 同一模型的另一写法；不登记会被当成非思考模型，默认思考模式下的
    # 推理开销会拖慢演示并可能吃光预算。
    "deepseek-v4.1-flash",
})

# 官方允许的推理强度档位（非法值会被服务端拒绝，这里先收敛）
_REASONING_EFFORTS = frozenset({"low", "high", "max"})

def ai_available():
    """是否已配置 API Key（每次实时读取，设置页保存后立即生效）"""
    return bool(load_settings()["api_key"])


def get_client():
    """懒加载 openai：未安装或未配置时抛清晰错误，不影响其它功能启动"""
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("未安装 openai 依赖：pip install openai")
    s = load_settings()
    return OpenAI(api_key=s["api_key"], base_url=s["base_url"])


def _prompt_chars(messages) -> int:
    total = 0
    for m in messages or []:
        try:
            total += len(str(m.get("content") or ""))
        except AttributeError:
            continue
    return total


def _enforce_prompt_budget(messages, domain: str = ""):
    """L11 速度硬约束：提示词进模型前先做预算检查。

    - 超过 AI_PROMPT_WARN_CHARS：告警（提示应先摘要）；
    - 超过 AI_PROMPT_MAX_CHARS：截断（**保留 system 全文**，其余按顺序保留、截尾）。

    为什么要统一在这里做：项目里散落着各处的 `[:N]` 自觉截断，容易随迭代退化；
    这里是一道"兜底闸门"，保证任何脚本原始大输出都不会未经处理直接喂给模型。
    """
    total = _prompt_chars(messages)
    if total <= config.AI_PROMPT_WARN_CHARS:
        return messages
    log.warning("提示词偏长：%d 字符（告警阈值 %d，domain=%s）——建议先摘要再喂模型",
                total, config.AI_PROMPT_WARN_CHARS, domain or "-")
    if total <= config.AI_PROMPT_MAX_CHARS:
        return messages

    sys_msgs = [m for m in messages if str(m.get("role") or "") == "system"]
    others = [m for m in messages if str(m.get("role") or "") != "system"]
    remain = max(0, config.AI_PROMPT_MAX_CHARS
                 - sum(len(str(m.get("content") or "")) for m in sys_msgs))
    out = list(sys_msgs)
    for m in others:
        content = str(m.get("content") or "")
        if len(content) <= remain:
            out.append(m)
            remain -= len(content)
        else:
            out.append({**m, "content": content[:remain]})
            remain = 0
    log.warning("提示词超上限已截断：%d → ≤%d 字符（domain=%s）",
                total, config.AI_PROMPT_MAX_CHARS, domain or "-")
    return out


def chat(messages, temperature=0.7, max_tokens=1024, reasoning_effort=None, domain=""):
    """调用模型，返回正文文本。

    关于思考型模型（deepseek-flash / deepseek-v4-pro）：
      - 服务端默认开启思考模式且 effort=high，推理过程会先占用大量输出预算；
        而各业务点的 max_tokens（260~500）是按非思考模型定的，
        预算被推理吃光后正文返回空串 —— 表现为"AI 功能没反应"。
      - 思考模式下 temperature 被服务端忽略（官方文档明确说明：不报错但也不生效），
        而多 agent 编排依赖 temperature 区分角色。
    因此：
      1. 默认**关闭思考**（_DEFAULT_REASONING_EFFORT = "off"）：
         本项目多数调用是信息抽取/短文案，实测同一提示词下
         思考 low 用 2.5s/255 tokens，非思考只要 0.5s/26 tokens，质量相当；
         关闭后 temperature 也恢复生效，多 agent 角色差异化得以保留。
         确需深度推理的任务（经营洞察 low）在调用点显式指定。
      2. 正文为空时递增预算重试，直到 _CHAT_TOKEN_CAP。
      3. 仍为空则抛清晰异常，由调用方走兜底，而不是把空串一路传下去。
    """
    messages = _enforce_prompt_budget(messages, domain=domain)   # L11：提示词预算护栏
    model = load_settings()["model"]
    client = get_client()
    budget = max(int(max_tokens or 0), _CHAT_TOKEN_MIN)
    extra = _thinking_params(model, reasoning_effort)
    last_reasoning = 0
    t0 = time.perf_counter()
    last_usage = None
    while True:
        try:
            resp = client.chat.completions.create(
                model=model, messages=messages, temperature=temperature,
                max_tokens=budget, **extra
            )
        except Exception as e:  # noqa: BLE001
            # 失败也要留痕：否则看板里只有成功调用，成功率永远是 100%
            _record_metric(model, domain, t0, None, ok=False, error=str(e))
            raise
        msg = resp.choices[0].message
        last_usage = getattr(resp, "usage", None)
        text = msg.content
        if text and text.strip():
            _record_metric(model, domain, t0, last_usage, ok=True,
                           reasoning_chars=len(getattr(msg, "reasoning_content", "") or ""))
            return text
        # 记录推理消耗，便于排查是「预算被推理吃光」还是「模型真没输出」
        rc = getattr(msg, "reasoning_content", None)
        last_reasoning = len(rc or "")
        if budget >= _CHAT_TOKEN_CAP:
            break
        budget = min(budget * 2, _CHAT_TOKEN_CAP)
    _record_metric(model, domain, t0, last_usage, ok=False,
                   error=f"空正文（推理约 {last_reasoning} 字符）")
    raise RuntimeError(
        f"模型返回空内容（{model}）：已把 max_tokens 提升到 {budget} 仍无正文，"
        f"推理过程约占 {last_reasoning} 字符；请检查模型名是否正确"
    )


def _record_metric(model, domain, t0, usage, ok, error="", reasoning_chars=0):
    """把一次调用的耗时/用量落库（旁路，任何异常都不得影响主流程）。"""
    try:
        from db_metrics import record_ai_call
        record_ai_call(model, int((time.perf_counter() - t0) * 1000), usage,
                       domain=domain, ok=ok, error=error,
                       reasoning_chars=reasoning_chars)
    except Exception:  # noqa: BLE001
        pass


def _thinking_params(model: str, effort: str | None) -> dict:
    """为思考型模型构造请求参数；其它模型返回空 dict。

    必须按模型精确判断：本项目支持自定义 base_url，把 DeepSeek 专有的
    thinking / reasoning_effort 参数发给 OpenAI 等其它服务可能导致 400。
    未列出的模型一律当作"不支持思考模式"，宁可不优化也不能发错参数。

    effort 取值：off（默认，思考模式关闭）/ low / high / max。
    """
    if (model or "").strip().lower() not in _THINKING_MODELS:
        return {}
    eff = (effort or _DEFAULT_REASONING_EFFORT).strip().lower()
    if eff == _THINKING_OFF:
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    if eff not in _REASONING_EFFORTS:
        eff = _DEFAULT_REASONING_EFFORT
    return {
        "reasoning_effort": eff,
        "extra_body": {"thinking": {"type": "enabled"}},
    }


def _extract_json(text):
    """从模型输出中提取 JSON（兼容 ```json 包裹）"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start:end + 1])
        raise ValueError(f"无法解析模型输出: {text[:200]}")


# ---------------- 1. 文字/文本记账解析 ----------------


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


def parse_transaction(text: str) -> dict:
    """把一句大白话转成结构化记账：'王阿姨买了两个肉包和一杯豆浆，6块' / '今天进货花了两百块'"""
    if not ai_available():
        # 无 API Key 时的兜底：朴素提取
        amount = _extract_amount(text)
        category, trans_type = detect_category(text)
        return {
            "customer": _extract_customer(text), "item": text, "amount": amount, "note": "",
            "tags": "", "category": category, "trans_type": trans_type, "fallback": True,
        }
    prompt = (
        "把店主的话解析成记账JSON，只输出JSON。字段：customer、item、amount、"
        "trans_type(income/expense)、category、note、tags、confidence、ambiguity、transactions。\n"
        "category只能选：主营业务收入/其他收入/进货/办公费/业务招待费/快递物流费/"
        "租赁及物业费/差旅费/车辆使用费/广告宣传费/软件服务费/培训费/职工薪酬。\n"
        "视角：说话者是店主。顾客吃了/买了/带走=income；店主买了/进了/交了/花了=expense；"
        "拿不准优先income并降低confidence。\n"
        "多笔互不相干收支必须逐笔放入transactions，不得合并金额或漏记；单笔则transactions为[]。\n"
        + _language_hint()
        + f"店主说：{text}"
    )
    try:
        out = _extract_json(chat([{"role": "user", "content": prompt}], temperature=0.1,
                                 domain="记账解析"))
        subs = _normalize_sub_transactions(out.get("transactions"))
        result = {
            "customer": out.get("customer", ""),
            "item": out.get("item", ""),
            "amount": out.get("amount"),
            "note": out.get("note", ""),
            "tags": ",".join(out.get("tags", [])),
            "category": out.get("category", ""),
            "trans_type": out.get("trans_type", "income"),
            "confidence": _parse_confidence(out.get("confidence")),
            "ambiguity": str(out.get("ambiguity") or "").strip(),
        }
        if subs:
            # 多笔时，顶层字段对齐第一笔，老调用方（只读 amount/customer）仍能用
            first = subs[0]
            result["transactions"] = subs
            result["amount"] = first["amount"]
            result["trans_type"] = first["trans_type"]
            result["category"] = first["category"] or result["category"]
            result["item"] = first["item"] or result["item"]
            result["customer"] = first["customer"] or result["customer"]
        return _mark_parse_uncertainty(result, text)
    except Exception:
        category, trans_type = detect_category(text)
        return {"customer": "", "item": text, "amount": None, "note": "",
                "tags": "", "category": category, "trans_type": trans_type,
                "confidence": 0.0, "ambiguity": "AI 解析失败，请核对后再记",
                "needs_check": True, "question": "AI 解析失败，请核对后再记",
                "fallback": True}


def _parse_confidence(value) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.75
    return round(min(1.0, max(0.0, confidence)), 2)


def _mark_parse_uncertainty(result: dict, text: str) -> dict:
    confidence = float(result.get("confidence", 0.75))
    ambiguity = str(result.get("ambiguity") or "").strip()
    subs = result.get("transactions") or []

    if not str(result.get("item") or "").strip():
        result["item"] = text
        confidence = min(confidence, 0.6)
    if result.get("amount") is None and not subs:
        confidence = 0.0
        ambiguity = ambiguity or "没听清金额"
    if subs and any(sub.get("amount") is None for sub in subs):
        confidence = 0.0
        ambiguity = ambiguity or "有一笔没听清金额"
    if str(result.get("ambiguity") or "").strip():
        confidence = min(confidence, 0.69)

    result["confidence"] = round(confidence, 2)
    result["ambiguity"] = ambiguity
    result["needs_check"] = confidence < 0.7 or bool(ambiguity)
    if result["needs_check"]:
        result.setdefault("question", result.get("ambiguity") or "这笔账要再核对一下")
    else:
        result.pop("question", None)
    return result


def _normalize_sub_transactions(raw) -> list[dict]:
    """把模型给的 transactions 洗干净。

    两条规矩：
      1. **金额无法解析的笔要保留**（amount 置 None），不要丢掉 ——
         丢掉等于"店主说了两笔、系统只记一笔"且不吭声。保留它会让上层走
         "缺金额追问"，店主当场补上。
      2. 只有一笔时不算多笔，交回单笔路径（避免前端多套一层列表，也让
         "子笔没金额"能落到单笔的追问流程上）。
    """
    if not isinstance(raw, list):
        return []
    out = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        raw_amt = it.get("amount")
        if raw_amt is None or (isinstance(raw_amt, str) and not raw_amt.strip()):
            amt = None                      # 真没提金额
        else:
            try:
                amt = float(raw_amt)
            except (TypeError, ValueError):
                amt = None                  # 提了但解析不出来 → 也当"没听清"
        if amt is not None and amt <= 0:
            amt = None
        ttype = it.get("trans_type")
        if ttype not in ("income", "expense"):
            ttype = "income"
        cat = (it.get("category") or "").strip()
        try:
            from categories import normalize_category
            cat = normalize_category(cat) or cat
        except Exception:  # noqa: BLE001
            pass
        out.append({
            "customer": (it.get("customer") or "").strip(),
            "item": (it.get("item") or "").strip(),
            "amount": amt,
            "trans_type": ttype,
            "category": cat,
            "note": (it.get("note") or "").strip(),
        })
    return out if len(out) >= 2 else []


# ---------------- 3. 熟客提醒生成 ----------------
def generate_reminders(customer_brief: str) -> list:
    """根据熟客画像生成今天该做的事（问候、留货、追单）"""
    if not ai_available():
        return []
    prompt = (
        "你是街边小店店主的记忆外挂，帮他记住那些'不值钱但暖心'的细节。\n"
        "下面是一份熟客档案（名字、常点、最近记忆点）。请输出今天适合店主做的事：\n"
        "1) 每个人最多1条；2) 口语化，像随口提醒一样；3) 只挑最有价值的2-3条，不要凑数。\n"
        "只输出JSON数组，如 [{\"customer\":\"王阿姨\",\"content\":\"上次她说孙子考了一百分，今天可以问问\"}]。\n"
        f"熟客档案：{customer_brief}\n"
    )
    try:
        return _extract_json(chat([{"role": "user", "content": prompt}], temperature=0.6,
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
    if not ai_available():
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
    return chat([{"role": "user", "content": prompt}], temperature=0.5,
                max_tokens=180, domain="经营洞察").strip()


# ---------------- 5. 客户画像 ----------------
def generate_customer_insight(customer: dict, transactions: list) -> str:
    """分析熟客交易历史，生成画像和个性化维系建议"""
    if not ai_available():
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
    return chat([{"role": "user", "content": prompt}], temperature=0.6, max_tokens=400,
                domain="熟客画像").strip()


# ---------------- 6. 报税建议 ----------------
def generate_tax_advice(quarterly_revenue: float, vat_result: dict, prev_advice: str = "") -> str:
    """基于季度收入和增值税计算结果生成报税建议"""
    if not ai_available():
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
    return chat([{"role": "user", "content": prompt}], temperature=0.3,
                max_tokens=300, reasoning_effort="low", domain="报税建议").strip()


# 多 agent 团队编排（朋友圈文案 / 单店诊断 / 掌柜复盘）在 team_domains.py。
# 这里**不再用顶层 import 做再导出** —— 那会和 team_domains 形成循环：
#   team_domains →（导入）ai →（末尾导入）team_domains（尚未初始化完）→ ImportError
# 实测：先 `import ai` 侥幸能用（ai 先注册进 sys.modules 了），
# 先 `import team_domains` 则直接崩。靠导入顺序活着太脆，改用 PEP 562 的
# 模块级 __getattr__ 惰性转发：`ai.generate_copy` 这类老入口照旧可用
# （mock.patch("ai.generate_copy") 也仍然有效），但导入期不再互相牵扯。
_TEAM_EXPORTS = ("generate_copy", "generate_store_diagnosis", "generate_daily_review")


def __getattr__(name):
    if name in _TEAM_EXPORTS:
        import team_domains
        return getattr(team_domains, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals()) + list(_TEAM_EXPORTS))
