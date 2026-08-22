"""ai_parsing.py — 文本记账解析辅助函数，从 ai.py 拆出。

中文数字转换、金额提取、顾客称呼提取。
被 ai.parse_transaction 调用；无外部依赖。
"""
import re

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}
_CN_CHARS = "零一二两三四五六七八九十百千万"


def cn_to_int(s: str) -> int:
    """中文数字转整数：一百二十→120，三千五→3500，十块→10"""
    total = section = num = 0
    last_unit = 0
    for ch in s:
        if ch in _CN_DIGITS:
            num = _CN_DIGITS[ch]
            if ch == "零":
                last_unit = 0
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if unit == _CN_UNITS["万"]:
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
        return total + section + num * last_unit // 10
    return total + section + num


def extract_amount(text: str):
    """从大白话里提取金额：优先'X块/X元'，其次'一共/花了/付了+X'，再支持中文数字"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:块|元|块钱)", text)
    if m:
        return float(m.group(1))
    m = re.search(rf"[{_CN_CHARS}]+\s*(?:块|元|块钱)", text)
    if m:
        return float(cn_to_int(re.search(rf"[{_CN_CHARS}]+", m.group(0)).group(0)))
    m = re.search(rf"(?:一共|总共|共|花了|花掉|付了|付掉|收了|收进|到账|赚了)\s*"
                  rf"(\d+(?:\.\d+)?|[零一二两三四五六七八九十百千万]+)", text)
    if m:
        g = m.group(1)
        return float(g) if g.replace(".", "", 1).isdigit() else float(cn_to_int(g))
    return None


def extract_customer(text: str) -> str:
    """从大白话里提取顾客称呼（兜底用）：王阿姨、李师傅、张大爷、陈哥……"""
    m = re.search(
        r"([\u4e00-\u9fa5]{1,3}?"
        r"(?:老板娘|师傅|阿姨|大爷|大妈|奶奶|叔叔|大姐|小妹|老板|经理|老师|"
        r"老李|老王|老张|老陈|老赵|老刘|小张|小李|小王|"
        r"哥|姐|叔|婶|伯|娘|爷|奶))",
        text,
    )
    return m.group(1) if m else ""
