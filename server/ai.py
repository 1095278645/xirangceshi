"""AI 能力层：记账解析、文案生成、熟客提醒 —— 支持多种 OpenAI 兼容大模型"""
import json
import re

from config import load_settings
from categories import detect_category


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


def chat(messages, temperature=0.7, max_tokens=1024):
    resp = get_client().chat.completions.create(
        model=load_settings()["model"], messages=messages, temperature=temperature, max_tokens=max_tokens
    )
    return resp.choices[0].message.content


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


# ---------------- 1. 语音/文本记账解析 ----------------
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def _cn_to_int(s: str) -> int:
    """中文数字转整数：一百二十→120，三千五→3500，十块→10，五百零三→503"""
    total = section = num = 0
    last_unit = 0
    for ch in s:
        if ch in _CN_DIGITS:
            num = _CN_DIGITS[ch]
            if ch == "零":
                last_unit = 0
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if unit == _CN_UNITS["万"]:   # 万位进位（中文数字进制规则，语义化而非字面量）
                section = (section + num) * unit
                total += section
                section, num = 0, 0
            else:
                section += (num or 1) * unit
                num = 0
            last_unit = unit
        else:
            break
    if num and last_unit:
        # 口语省略：三百五=350，五千五=5500，末尾数字按 单位/10 计
        return total + section + num * last_unit // 10
    return total + section + num


def _extract_amount(text: str):
    """从大白话里提取金额：优先'X块/X元'，其次'一共/花了/付了+X'，再支持中文数字（一百二、三千五）"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:块|元|块钱)", text)
    if m:
        return float(m.group(1))
    _CN_CHARS = "零一二两三四五六七八九十百千万"
    m = re.search(rf"[{_CN_CHARS}]+\s*(?:块|元|块钱)", text)
    if m:
        return float(_cn_to_int(re.search(rf"[{_CN_CHARS}]+", m.group(0)).group(0)))
    m = re.search(rf"(?:一共|总共|共|花了|花掉|付了|付掉|收了|收进|到账|赚了)\s*(\d+(?:\.\d+)?|[零一二两三四五六七八九十百千万]+)", text)
    if m:
        g = m.group(1)
        return float(g) if g.replace(".", "", 1).isdigit() else float(_cn_to_int(g))
    return None


def _extract_customer(text: str) -> str:
    """从大白话里提取顾客称呼（兜底用）：王阿姨、李师傅、张大爷、陈哥……
    只匹配'姓/名+称呼'结构，避免把'排骨''豆浆'等商品名误当顾客。
    """
    m = re.search(
        r"([\u4e00-\u9fa5]{1,3}?"
        r"(?:老板娘|师傅|阿姨|大爷|大妈|奶奶|叔叔|大姐|小妹|老板|经理|老师|老李|老王|老张|老陈|老赵|老刘|小张|小李|小王|"
        r"哥|姐|叔|婶|伯|娘|爷|奶))",
        text,
    )
    return m.group(1) if m else ""


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
        "你是一家街边小店的AI掌柜兼代账会计，负责把店主随口说的记账话翻译成结构化数据，并按小企业会计准则分类。\n"
        "规则：只输出JSON，不要多余文字。字段：\n"
        "customer(顾客称呼,没有则空串)、item(买的东西/事由)、amount(金额,数字,没提到则null)、\n"
        "trans_type(\"income\"收入或\"expense\"支出，判断这笔钱是收进还是花出)、\n"
        "category(分类，从下面选一个最贴切的：主营业务收入/其他收入/进货/办公费/业务招待费/快递物流费/"
        "租赁及物业费/差旅费/车辆使用费/广告宣传费/软件服务费/培训费/工资)、\n"
        "note(补充说明)、tags(适合给客户打的标签数组，没有则空数组)。\n"
        f"店主说：{text}"
    )
    try:
        out = _extract_json(chat([{"role": "user", "content": prompt}], temperature=0.1))
        return {
            "customer": out.get("customer", ""),
            "item": out.get("item", ""),
            "amount": out.get("amount"),
            "note": out.get("note", ""),
            "tags": ",".join(out.get("tags", [])),
            "category": out.get("category", ""),
            "trans_type": out.get("trans_type", "income"),
        }
    except Exception:
        category, trans_type = detect_category(text)
        return {"customer": "", "item": text, "amount": None, "note": "",
                "tags": "", "category": category, "trans_type": trans_type}


# ---------------- 2. 朋友圈文案生成 ----------------
def generate_copy(shop_name: str, scene: str, extra: str, customer_name: str = "", context: str = "") -> str:
    """生成有烟火气、口语化的朋友圈文案（可带经营上下文）"""
    if not ai_available():
        ctx_part = f"（{context}）" if context else ""
        return (f"【{shop_name}】{extra}{ctx_part}\n—— 今日份营业，欢迎光临！"
                "(提示：在设置页填入 API Key 后即可生成真实文案)")
    prompt = (
        f"你是{shop_name}的老板，文化不高但特别真诚，说话带点本地烟火气，偶尔自嘲和幽默。\n"
        "请写一条不超过80字的朋友圈文案，不要用'亲''家人们''爆款''限时抢购'这类网红词，"
        "要像真人老板随手发的。"
        f"场景：{scene}\n补充信息：{extra}\n"
        + (f"经营上下文（参考但不照抄）：{context}\n" if context else "")
        + (f"今天还惦记着老主顾：{customer_name}，可以自然带一句（可选，不硬凑）。" if customer_name else "")
        + "\n直接输出文案正文，不要任何前缀。"
    )
    return chat([{"role": "user", "content": prompt}], temperature=0.9, max_tokens=300).strip()


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
        return _extract_json(chat([{"role": "user", "content": prompt}], temperature=0.6))
    except Exception:
        return []


# ---------------- 4. 月度经营洞察 ----------------
def generate_insights(monthly_data: dict, prev_context: str = "") -> str:
    """基于月度收支汇总生成经营洞察（环比、异常品类、可执行建议）"""
    if not ai_available():
        # 降级：模板化数据分析
        income = monthly_data.get("income", 0)
        expense = monthly_data.get("expense", 0)
        net = income - expense
        cats = monthly_data.get("categories", [])
        top_expense = max((c for c in cats if c.get("trans_type") == "expense"),
                          key=lambda c: c.get("total", 0), default=None) if cats else None
        lines = [f"本月收入 {income:.0f} 元，支出 {expense:.0f} 元，净{'收入' if net >= 0 else '支出'} {abs(net):.0f} 元。"]
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
        + (f"上次分析参考：{prev_context}\n" if prev_context else "")
        + "请输出3-5条经营洞察：① 环比变化趋势 ② 异常品类 ③ 可执行建议。\n"
        "口语化，不要用专业术语，像掌柜跟老板聊天一样。直接输出正文。"
    )
    return chat([{"role": "user", "content": prompt}], temperature=0.5, max_tokens=500).strip()


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
        "③ 一条个性化的维系建议（具体到这周该做什么）。直接输出正文，不要列表格式。"
    )
    return chat([{"role": "user", "content": prompt}], temperature=0.6, max_tokens=400).strip()


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
    return chat([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=400).strip()


# ---------------- 7. 单店经营诊断 ----------------
def generate_store_diagnosis(model_result: dict, prev_diagnosis: str = "") -> str:
    """基于单店模型计算结果生成个性化经营诊断和行动计划"""
    if not ai_available():
        # 降级：基于评分的固定话术
        overall = model_result.get("overall", {})
        score = overall.get("score", 0)
        verdict = overall.get("level", "")
        model = model_result.get("model", {})
        breakeven = model.get("break_even_day", 0)
        target = model.get("target_day", 0)
        lines = [f"综合评分 {score} 分，判定：{verdict}。"]
        if score < 30:
            lines.append(f"保本日销 {breakeven:.0f} 元，现在离保本线还差得远，先想办法把日均流水拉上来。")
            lines.append("建议：① 砍掉不必要开支 ② 做一个月整改窗口 ③ 差太远就果断止损。")
        elif score < 60:
            lines.append(f"保本日销 {breakeven:.0f} 元，目标 {target:.0f} 元，现在在保本线附近挣扎。")
            lines.append("建议：① 找到增量突破口 ② 优化成本结构 ③ 关注现金储备。")
        else:
            lines.append(f"经营健康，保本线 {breakeven:.0f} 元已稳，目标 {target:.0f} 元。")
            lines.append("建议：考虑适度扩张或提升客单价。")
        lines.append("(提示：在设置页填入 API Key 后可获得个性化 AI 经营诊断)")
        return " ".join(lines)
    prompt = (
        "你是街边小店的经营顾问，用勇哥的方法论帮老板诊断店铺。\n"
        "核心原则：现金流比故事重要，保本线先行，三维交叉验证。\n"
        f"单店模型计算结果：{json.dumps(model_result, ensure_ascii=False, default=str)}\n"
        + (f"上次诊断参考：{prev_diagnosis}\n" if prev_diagnosis else "")
        + "请输出：① 一句话总结店铺健康状况 ② 最紧迫的问题 ③ 具体的行动计划（3条）。\n"
        "口语化，像老掌柜跟新老板聊天。直接输出正文。"
    )
    return chat([{"role": "user", "content": prompt}], temperature=0.5, max_tokens=500).strip()