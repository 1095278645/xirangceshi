"""AI 掌柜「一人团队」引擎 —— 并行竞争 + 融合裁决 + 经验成长

借鉴 OpenClaw / OpenOPC（港大 HKUDS 开源的一人团队 OPC 项目）的三机制，
为「巷子里的AI掌柜」提供轻量级、可复用的多员工编排能力：

  Self-Built   员工装配：调用方（ai.py 各业务域）定义「员工」角色与各自提示词，
                本引擎不绑定具体角色，只提供编排原语。
  Self-Run     协作与竞争：run_parallel 并行产出（竞争出多个候选）；
                掌柜融合裁决把多个候选收敛成一个最终答案（融合）。
  Self-Grown   经验沉淀：融合裁决把「谁的判断被掌柜采纳」记入 team 领域上下文，
                累计成采纳率；下次裁决时掌柜把采纳偏好作为参考（经验权重，不迷信）。

设计约束（面向小微店主、免配置）：
  - 不引入外部多 agent 框架，复用 ai.chat / ai._extract_json；
  - 无 API Key 时引擎本身不负责产出，由调用方提供"规则多角度过程"，仍可展示团队结构；
  - 员工并行用线程池，低频调用场景下安全且能体现"竞争扇出"。
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import db

MAX_WORKERS = 4
_ADOPT_PREFIX = "adoption_"


# ---------------- Self-Run：并行产出（竞争扇出） ----------------
def run_parallel(producers: list, max_workers: int = MAX_WORKERS) -> list:
    """并行执行多个员工产出函数。producers: list[callable()->str]"""
    if not producers:
        return []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        return list(ex.map(lambda fn: fn(), producers))


# ---------------- Self-Grown：采纳归因沉淀 ----------------
def _adopt_key(domain: str) -> str:
    return f"{_ADOPT_PREFIX}{domain}"


def load_adoption(domain: str) -> dict:
    """读取该域的历史采纳统计 {员工名: 被采纳次数}"""
    item = db.get_domain_context("team", _adopt_key(domain))
    if not item or not item.get("value"):
        return {}
    val = item["value"]
    if isinstance(val, dict):      # db 对 dict 值直接返回对象
        return val
    return json.loads(val)


def record_adoption(domain: str, roles: list) -> dict:
    """记下本轮被掌柜采纳的员工，返回更新后的采纳表"""
    ad = load_adoption(domain)
    for r in roles or []:
        ad[r] = ad.get(r, 0) + 1
    db.set_domain_context("team", _adopt_key(domain), ad)
    return ad


def adoption_brief(domain: str) -> str:
    """把采纳史拼成给掌柜的一段"经验参考"（权重随历史漂移，对应 Self-Grown）"""
    ad = load_adoption(domain)
    if not ad:
        return ""
    total = sum(ad.values()) or 1
    parts = "、".join(f"{k}({round(v / total * 100)}%)"
                      for k, v in sorted(ad.items(), key=lambda x: -x[1]))
    return f"（经验参考——过往被掌柜采纳较多的员工：{parts}，可作为先参考，但仍以本店本次数据为准。）"