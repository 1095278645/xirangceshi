"""多 agent 业务域编排：团队流水线基础设施 + 域注册表

从 ai.py 抽取，遵循项目「按业务域拆分」的架构。
本模块只负责：掌柜融合裁决(_decide)、通用编排骨架(_run_team)、域注册表(TEAM_DOMAINS)。
具体域的员工配置/降级函数/生成入口已拆到：
  - team_domain_copy.py  （朋友圈文案域）
  - team_domain_store.py （单店经营诊断域）

设计：
  - 复用 ai.chat / ai.ai_available / ai._extract_json（模块内 import）。
  - 无 API Key 时走规则降级兜底，业务文本不变（测试保障）。
  - 有 Key 时：员工并行竞争产出 → 掌柜裁决融合 → 采纳归因沉淀。

扩展指引（后续增减能力不破坏架构）：
  1. 增/删员工：只改子模块里的员工列表，TEAM_DOMAINS 自动适配。
  2. 新增业务域：在子模块定义员工列表+降级函数+生成入口，在 TEAM_DOMAINS 登记。
  3. 删整个域：删除注册表项 + 子模块文件即可。
"""
from __future__ import annotations

import ai
import team
import evolution
import db_evolution as dbe

# 域数据/降级函数/生成入口从子模块导入
from team_domain_copy import (
    _COPY_EMPLOYEES, _COPY_REVIEWER,
    _copy_degraded, _copy_degraded_process,
    generate_copy,
)
from team_domain_store import (
    _STORE_EMPLOYEES,
    _store_diagnosis_degraded, _store_degraded_process,
    generate_store_diagnosis,
)

# 向后兼容 re-export（调用方仍可 team_domains.generate_copy 等）
__all__ = [
    "generate_copy", "generate_store_diagnosis",
    "TEAM_DOMAINS", "list_team_domains",
]


# ---------------- 掌柜融合裁决（竞争→裁决→融合→归因） ----------------

def _decide(task_desc: str, cands: list, adoption_brief: str = "",
            prev: str = "", fail: str = "", temperature: float = 0.3,
            max_tokens: int = 900, variants: bool = False) -> dict:
    """掌柜融合裁决：多个员工候选 → 取舍 + 归因 + 融合成最终答案

    返回 {"verdict": 取舍说明, "adopted": [被采纳员工], "final": 最终融合文本}。
    variants=True 时额外返回 {"variants": [3条风格各异的文案]}。
    """
    cand_txt = "\n\n".join(f"【{role}】{out}" for role, out in cands)
    prompt = (
        "你是「AI掌柜」，一家街边小店唯一的老板，下面有几位 AI 员工对同一个问题各自给了方案。\n"
        "你作为最终决策者要：\n"
        "1) 判断谁说得在理、谁在臆测或重复，把合理的判断挑出来，驳掉/修正不合适的；\n"
    )
    if variants:
        prompt += (
            "2) 从所有候选中挑选并打磨出 3 条可直接发的朋友圈文案，保证 3 条风格各异"
            "（不同公式/角度/语气），不要是同一条的变体；\n"
            "3) 明确归因：你最后采纳了哪几位员工的核心判断。\n"
        )
    else:
        prompt += (
            "2) 融合成一段连贯、口语化、可直接照做的最终答案（像老掌柜跟老板交代事情）；\n"
            "3) 明确归因：你最后采纳了哪几位员工的核心判断。\n"
        )
    prompt += (
        f"{adoption_brief}\n"
        f"要解决的问题：{task_desc}\n"
        + (f"上次结论（参考不照抄）：{prev}\n" if prev else "")
        + f"员工们的方案：\n{cand_txt}\n"
    )
    if variants:
        prompt += (
            "只输出 JSON，不要任何多余文字。格式："
            "{\"verdict\":\"你如何取舍的一句话说明\",\"adopted\":[\"被采纳的员工名\",...],"
            "\"final\":\"最推荐的一条\",\"variants\":[\"第一条\",\"第二条\",\"第三条\"]}"
        )
    else:
        prompt += (
            "只输出 JSON，不要任何多余文字。格式："
            "{\"verdict\":\"你如何取舍的一句话说明\",\"adopted\":[\"被采纳的员工名\",...],\"final\":\"融合后的最终答案\"}"
        )
    try:
        out = ai._extract_json(ai.chat([{"role": "user", "content": prompt}],
                                       temperature=temperature, max_tokens=max_tokens))
        result = {
            "verdict": out.get("verdict", ""),
            "adopted": out.get("adopted", []),
            "final": out.get("final", "").strip(),
        }
        if variants:
            v = out.get("variants", [])
            if not isinstance(v, list) or len(v) < 2:
                v = [result["final"]] if result["final"] else [fail.strip()]
            result["variants"] = [x.strip() for x in v if x and x.strip()]
            if not result["variants"]:
                result["variants"] = [fail.strip()]
        return result
    except Exception:  # noqa: BLE001 —— 融合失败退化为拼接候选（降级兜底）
        result = {"verdict": "", "adopted": [], "final": fail.strip()}
        if variants:
            result["variants"] = [fail.strip()]
        return result


# ---------------- 通用团队流水线（员工并行竞争 → 掌柜裁决融合 → 采纳归因） ----------------

def _task_signals(domain: str, task: str) -> list:
    """从任务文本中提取命中的基因触发信号（子串匹配）。

    无命中时返回 []，select_gene 会自动回退到域内全部 active 基因。
    """
    if not task:
        return []
    signals = []
    for g in dbe.get_active_genes(domain):
        for s in g.get("trigger_signals", []):
            if s and s in task and s not in signals:
                signals.append(s)
    return signals


def _run_team(domain: str, task: str, prev: str = "",
              sys_suffix: str = "", user_tail: str = "", variants: bool = False):
    """域级编排骨架：员工并行竞争 →（可选协作评审）→ 掌柜裁决融合 → 采纳归因。

    参数全部来自 TEAM_DOMAINS[domain] 的登记。
    variants=True 时返回 (final, process, variants_list)。
    """
    cfg = TEAM_DOMAINS[domain]
    employees, reviewer, judge_desc = cfg["employees"], cfg["reviewer"], cfg["judge"]
    evo_cfg = cfg.get("evolution", {})

    # 进化层：任务前回顾 + 基因选择（启用且 AI 可用时）
    gene_id = None
    if evo_cfg.get("enabled") and ai.ai_available():
        review = evolution.review_injection(domain)
        if review:
            sys_suffix = (sys_suffix or "") + "\n" + review
        # 基因选择：按任务命中的触发信号挑一个基因，把其策略注入员工提示词
        gene = evolution.select_gene(domain, _task_signals(domain, task),
                                     evo_cfg.get("strategy", "auto"))
        if gene:
            gene_id = gene["gene_id"]
            addon = (gene.get("system_prompt_addon") or "").strip()
            if addon:
                sys_suffix = (sys_suffix or "") + "\n本局策略参考（来自近期验证有效的基因，可借鉴不必照搬）：\n" + addon

    def produce(emp):
        sys = emp["system"] + sys_suffix
        user = task + (user_tail.format(role=emp["role"]) if user_tail else "")
        return ai.chat([{"role": "system", "content": sys},
                        {"role": "user", "content": user}],
                       temperature=emp["temperature"], max_tokens=emp["max_tokens"]).strip()

    # 员工并行竞争产出
    cands = team.run_parallel([lambda e=e: produce(e) for e in employees])
    cands = list(zip([e["role"] for e in employees], cands))
    if reviewer:
        # 协作下游：评审专家对候选挑毛病（评审失败降级跳过）
        review_task = "\n\n".join(f"【{name}】{out}" for name, out in cands)
        try:
            review_out = ai.chat(
                [{"role": "system", "content": reviewer["system"]},
                 {"role": "user", "content": f"请评审下面几份内容，指出违规问题和修改建议（2-3句）：\n{review_task}"}],
                temperature=reviewer["temperature"], max_tokens=reviewer["max_tokens"]).strip()
        except Exception:  # noqa: BLE001 —— 评审降级兜底
            review_out = "未发现问题（评审降级跳过）。"
        cands = cands + [(reviewer["role"], review_out)]

    fail = " ".join(out for _, out in cands)
    # 掌柜裁决融合
    judge = _decide(judge_desc, cands, team.adoption_brief(domain), prev, fail=fail,
                    variants=variants)
    if judge["adopted"]:
        team.record_adoption(domain, judge["adopted"])
    process = {
        "mode": "collaborative" if reviewer else "competitive",
        "employees": [{"role": name, "output": out} for name, out in cands],
        "verdict": judge["verdict"], "adopted": judge["adopted"],
        "gene_id": gene_id,
    }
    if variants:
        return judge["final"], process, judge.get("variants", [judge["final"]])
    return judge["final"], process


# ---------------- 域注册表（增删能力的唯一入口，test_team 自检其完整性） ----------------

TEAM_DOMAINS = {
    "copy": {
        "mode": "collaborative",
        "employees": _COPY_EMPLOYEES,
        "reviewer": _COPY_REVIEWER,
        "judge": "写一条朋友圈文案（结合两位文案候选与合规审核意见，融合成一条可直接发的正文）"
                 "。风格要口语、短句、有具体细节，不套网红词和黑话，像真人老板随手发的朋友圈",
        "degraded": _copy_degraded_process,
        "evolution": {
            "enabled": True,
            "strategy": "auto",
            "genes": [
                "gene_copy_scene_transplant",
                "gene_copy_yiji",
                "gene_copy_reverse_restraint",
                "gene_copy_number_pun",
            ],
            "distill_threshold": 0.7,
            "suppress_threshold": 0.15,
        },
    },
    "store": {
        "mode": "competitive",
        "employees": _STORE_EMPLOYEES,
        "reviewer": None,
        "judge": "这家店的经营诊断与下一步行动（融合财务/经营/风控，别给空洞的话，要有取舍）"
                 "。先给结论——可做/整改窗口(1个月)/果断劝退，再给一两个具体颗粒度的动作，别写\"提升运营\"这类空话",
        "degraded": _store_degraded_process,
        "evolution": {
            "enabled": True,
            "strategy": "harden",
            "genes": [
                "gene_store_diagnose",
                "gene_store_margin_alert",
            ],
            "distill_threshold": 0.7,
            "suppress_threshold": 0.15,
        },
    },
}


def list_team_domains() -> list:
    """列出已注册的团队业务域（供自检 / 前端菜单 / 后续扩展）"""
    return sorted(TEAM_DOMAINS)
