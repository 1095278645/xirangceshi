"""AI 能力层：记账解析、文案生成、熟客提醒 —— 基于 DeepSeek（OpenAI 兼容接口）"""
import json
import re

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

_client = None


def get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    return _client


def ai_available():
    return bool(DEEPSEEK_API_KEY)


def chat(messages, temperature=0.7, max_tokens=1024):
    resp = get_client().chat.completions.create(
        model=DEEPSEEK_MODEL, messages=messages, temperature=temperature, max_tokens=max_tokens
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
def parse_transaction(text: str) -> dict:
    """把一句大白话转成结构化记账，如：'王阿姨买了两个肉包和一杯豆浆，6块'"""
    if not ai_available():
        # 无 API Key 时的兜底：朴素提取
        amount = None
        m = re.search(r"(\d+(?:\.\d+)?)\s*(块|元)", text)
        if m:
            amount = float(m.group(1))
        return {
            "customer": "", "item": text, "amount": amount, "note": "",
            "tags": "", "fallback": True,
        }
    prompt = (
        "你是一家街边小店的AI掌柜，负责把店主随口说的记账话翻译成结构化数据。\n"
        "规则：只输出JSON，不要多余文字。字段：customer(顾客称呼,没有则空串)、"
        "item(买的东西)、amount(金额,数字,没提到则null)、note(补充说明)、"
        "tags(适合给客户打的标签数组，如常客/爱喝豆浆，没有则空数组)。\n"
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
        }
    except Exception:
        return {"customer": "", "item": text, "amount": None, "note": "", "tags": ""}


# ---------------- 2. 朋友圈文案生成 ----------------
def generate_copy(shop_name: str, scene: str, extra: str, customer_name: str = "") -> str:
    """生成有烟火气、口语化的朋友圈文案"""
    if not ai_available():
        return (f"【{shop_name}】{extra}\n—— 今日份营业，欢迎光临！"
                "(提示：在 server/config.local.json 填入 DeepSeek API Key 后即可生成真实文案)")
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