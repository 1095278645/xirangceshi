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
            if unit == 10000:
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
def generate_copy(shop_name: str, scene: str, extra: str, customer_name: str = "") -> str:
    """生成有烟火气、口语化的朋友圈文案"""
    if not ai_available():
        return (f"【{shop_name}】{extra}\n—— 今日份营业，欢迎光临！"
                "(提示：在设置页填入 API Key 后即可生成真实文案)")
    prompt = (
        f"你是{shop_name}的老板，文化不高但特别真诚，说话带点本地烟火气，偶尔自嘲和幽默。\n"
        "请写一条不超过80字的朋友圈文案，不要用'亲''家人们''爆款''限时抢购'这类网红词，"
        "要像真人老板随手发的。"
        f"场景：{scene}\n补充信息：{extra}\n"
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