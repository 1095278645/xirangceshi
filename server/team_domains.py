"""多 agent 业务域编排：朋友圈文案协作流水线 + 单店诊断竞争融合

从 ai.py 抽取，遵循项目「按业务域拆分」的架构（参照 team.py 引擎原语）。
本模块只负责把 team 引擎（并行竞争 / 融合裁决 / 采纳成长）接到具体业务域上，
单 agent 能力（记账解析、熟客画像、洞察等）仍留在 ai.py。

设计：
  - 复用 ai.chat / ai.ai_available / ai._extract_json（模块内 import，避免循环引用）。
  - 无 API Key 时走规则「团队过程」，业务文本与降级路径完全不变（测试保障）。
  - 有 Key 时：员工并行竞争产出 → 掌柜 _decide 融合裁决 → 采纳归因沉淀进 team 域。

扩展指引（后续增减能力不破坏架构）：
  1. 增/删员工：只改 TEAM_DOMAINS 里对应域的 employees 列表（增一行或删一行），
     引擎、裁决、采纳沉淀全部自动适配，无需改任何流程代码。
  2. 新增业务域：三步——
     ① 定义员工列表（3 名左右，各给 role/system/temperature/max_tokens）；
     ② 在 TEAM_DOMAINS 登记（mode=competitive 或 collaborative，judge 一句话，
        可选 reviewer 加协作评审；无 Key 降级函数写进 degraded）；
     ③ 写一个薄壳入口函数（负责无 Key 降级 + 组装 task，然后一行调用 _run_team）。
     流程、采纳沉淀、process 结构都由 _run_team 统一提供，不会出现复制粘贴走样。
  3. 删整个域：删除注册表项 + 薄壳函数即可；tests/test_team.py 的注册表自检
     会自动确认所有已注册域都结构完整、降级可跑。
"""
from __future__ import annotations

import json

import ai
import team


# ---------------- 掌柜融合裁决（Self-Run 的"决策者仲裁"） ----------------
def _decide(task_desc: str, cands: list, adoption_brief: str = "",
            prev: str = "", fail: str = "", temperature: float = 0.3,
            max_tokens: int = 900) -> dict:
    """掌柜融合裁决：多个员工候选 → 取舍 + 归因 + 融合成一个最终答案

    返回 {"verdict": 取舍说明, "adopted": [被采纳员工], "final": 最终融合文本}。
    """
    cand_txt = "\n\n".join(f"【{role}】{out}" for role, out in cands)
    prompt = (
        "你是「AI掌柜」，一家街边小店唯一的老板，下面有几位 AI 员工对同一个问题各自给了方案。\n"
        "你作为最终决策者要：\n"
        "1) 判断谁说得在理、谁在臆测或重复，把合理的判断挑出来，驳掉/修正不合适的；\n"
        "2) 融合成一段连贯、口语化、可直接照做的最终答案（像老掌柜跟老板交代事情）；\n"
        "3) 明确归因：你最后采纳了哪几位员工的核心判断。\n"
        f"{adoption_brief}\n"
        f"要解决的问题：{task_desc}\n"
        + (f"上次结论（参考不照抄）：{prev}\n" if prev else "")
        + f"员工们的方案：\n{cand_txt}\n"
        "只输出 JSON，不要任何多余文字。格式："
        "{\"verdict\":\"你如何取舍的一句话说明\",\"adopted\":[\"被采纳的员工名\",...],\"final\":\"融合后的最终答案\"}"
    )
    try:
        out = ai._extract_json(ai.chat([{"role": "user", "content": prompt}],
                                       temperature=temperature, max_tokens=max_tokens))
        return {
            "verdict": out.get("verdict", ""),
            "adopted": out.get("adopted", []),
            "final": out.get("final", "").strip(),
        }
    except Exception:  # noqa: BLE001 —— 融合失败退化为拼接候选
        return {"verdict": "", "adopted": [], "final": fail.strip()}


# ---------------- 通用团队流水线（各业务域共用的编排骨架） ----------------
def _run_team(domain: str, task: str, prev: str = "",
              sys_suffix: str = "", user_tail: str = ""):
    """域级编排骨架：员工并行竞争 →（可选协作评审）→ 掌柜融合裁决 → 采纳归因。

    参数全部来自 TEAM_DOMAINS[domain] 的登记，保证新增域时只有一处声明。
    prev：上次结论（参考不照抄）；sys_suffix：附加给每位员工的系统提示；
    user_tail：附加给每位员工的提问模板（{role} 会替换为员工名）。
    """
    cfg = TEAM_DOMAINS[domain]
    employees, reviewer, judge_desc = cfg["employees"], cfg["reviewer"], cfg["judge"]

    def produce(emp):
        sys = emp["system"] + sys_suffix
        user = task + (user_tail.format(role=emp["role"]) if user_tail else "")
        return ai.chat([{"role": "system", "content": sys},
                        {"role": "user", "content": user}],
                       temperature=emp["temperature"], max_tokens=emp["max_tokens"]).strip()

    cands = team.run_parallel([lambda e=e: produce(e) for e in employees])
    cands = list(zip([e["role"] for e in employees], cands))
    if reviewer:
        # 协作下游：评审专家对候选挑毛病、给修改建议（评审失败降级跳过）
        review_task = "\n\n".join(f"【{name}】{out}" for name, out in cands)
        try:
            review_out = ai.chat(
                [{"role": "system", "content": reviewer["system"]},
                 {"role": "user", "content": f"请评审下面几份内容，指出违规问题和修改建议（2-3句）：\n{review_task}"}],
                temperature=reviewer["temperature"], max_tokens=reviewer["max_tokens"]).strip()
        except Exception:  # noqa: BLE001
            review_out = "未发现问题（评审降级跳过）。"
        cands = cands + [(reviewer["role"], review_out)]

    fail = " ".join(out for _, out in cands)
    judge = _decide(judge_desc, cands, team.adoption_brief(domain), prev, fail=fail)
    if judge["adopted"]:
        team.record_adoption(domain, judge["adopted"])
    process = {
        "mode": "collaborative" if reviewer else "competitive",
        "employees": [{"role": name, "output": out} for name, out in cands],
        "verdict": judge["verdict"], "adopted": judge["adopted"],
    }
    return judge["final"], process


# ---------------- 域：朋友圈文案（协作流水线：创意/熟客 → 合规 → 掌柜融合） ----------------
_COPY_EMPLOYEES = [
    {"role": "创意文案师", "temperature": 0.9, "max_tokens": 300,
     "system": "你是烟火气的文案师，像真人老板随手发的朋友圈，用勇哥那套表达DNA：短句、先说具体的东西再谈感觉，"
              "数字比形容词更能打动人。口语、真诚、偶尔自嘲。"
              "不套网红词，更不碰\"赋能/闭环/底层逻辑/品效合一\"这类黑话。"
      "方案要具体到颗粒度——不是\"欢迎光临优惠多多\"，而是\"买面送卤蛋，下午3点前到店还加一碟小菜\"这样的实打实。"},
    {"role": "熟客运营", "temperature": 0.8, "max_tokens": 300,
     "system": "你懂老主顾的人情味，能把文案写到老熟人心里，记得住细节、不套路。"
      "像街坊聊天一样带一句只有熟客才懂的梗（比如他常点的那道、上次提过的一件小事），不要泛泛\"感谢新老顾客\"。"},
]
_COPY_REVIEWER = {"role": "合规审核", "temperature": 0.2, "max_tokens": 250,
                  "system": "你是平台审核搭档，专挑广告法违禁词、绝对化用语、虚假优惠，给出修改意见。"}


def _copy_degraded(shop_name: str, scene: str, extra: str, context: str = "") -> str:
    ctx_part = f"（{context}）" if context else ""
    return (f"【{shop_name}】{extra}{ctx_part}\n—— 今日份营业，欢迎光临！"
            "(提示：在设置页填入 API Key 后即可生成真实文案)")


def _copy_degraded_process(shop_name: str, scene: str, extra: str) -> dict:
    """无 Key 时的「团队过程」：三个员工用规则各给角度 + 掌柜规则融合（文本不变）"""
    return {
        "mode": "collaborative",
        "employees": [
            {"role": "创意文案师", "output": f"主打：{shop_name} · {extra}，突出烟火气、口语化。"},
            {"role": "熟客运营", "output": f"可带一句老主顾语境，让文案有人情味、像对熟人说话。"},
            {"role": "合规审核", "output": "核对：避免爆款、限时抢购、绝对化用语等广告法敏感词。"},
        ],
        "verdict": "规则融合：创意为主、熟客语境加持、合规把关，合并成一条可直接发的朋友圈文案。",
        "adopted": ["创意文案师", "熟客运营", "合规审核"],
    }


def generate_copy(shop_name: str, scene: str, extra: str, customer_name: str = "",
                  context: str = "", return_process: bool = False):
    """生成有烟火气的朋友圈文案（多人协作：创意/熟客竞争 → 合规评审 → 掌柜融合）"""
    if not ai.ai_available():
        text = _copy_degraded(shop_name, scene, extra, context)
        if not return_process:
            return text
        return text, _copy_degraded_process(shop_name, scene, extra)
    task = (f"店铺：{shop_name}；场景：{scene}；补充：{extra}\n"
            + (f"熟客：{customer_name}，可自然带一句（不硬凑）\n" if customer_name else "")
            + (f"经营上下文（参考不照抄）：{context}\n" if context else ""))
    final, process = _run_team("copy", task,
                               sys_suffix="（请输出一条可直接发的朋友圈文案正文，不超过 80 字，只输出正文。）")
    if not return_process:
        return final
    return final, process


# ---------------- 域：单店经营诊断（员工竞争 → 掌柜融合） ----------------
_STORE_EMPLOYEES = [
    {"role": "财务顾问", "temperature": 0.4, "max_tokens": 320,
     "system": "你是谨慎的财务顾问，眼里只有现金流：保本线、实际支出、回本周期、现金储备。先算账再说话，用大白话。"
      "牢记\"餐饮是现金流游戏，不是故事游戏\"，保本线就是店的命线——低于保本线开门一天亏一天。"},
    {"role": "经营顾问", "temperature": 0.5, "max_tokens": 320,
     "system": "你是懂街边生意的经营顾问，看重客流、复购、客单价，给的是明天就能做的小动作。"
      "方案要具体到颗粒度：不说\"提升运营\"，要说\"免费续面+加微信送卤蛋\"这种实打实的差异化动作。"},
    {"role": "风控顾问", "temperature": 0.5, "max_tokens": 260,
     "system": "你是专泼冷水的风控，专挑别人没敢说的坑：现金断档、回本太慢、成本结构不合理、盲目扩张。"
      "带点餐饮反诈的眼力——若店是加盟的，警惕\"零加盟费\"\"6个月回本\"\"总部全包\"这三类快招话术，"
      "别让老板被割了还以为是经营问题。"},
]


def _store_diagnosis_degraded(model_result: dict) -> str:
    """无 Key 时的降级话术（保持既有文本，供测试与无 Key 兜底）"""
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


def _store_degraded_process(model_result: dict) -> dict:
    """无 Key 的「团队过程」：三个员工用规则各给角度 + 掌柜规则融合（业务文本不变）"""
    d = model_result.get("dimensions", {})
    a, b, c = d.get("a", {}), d.get("b", {}), d.get("c", {})
    return {
        "mode": "competitive",
        "employees": [
            {"role": "财务顾问", "output": f"现金流{a.get('level', '?')}（日销/保本/目标），月利润与回本需盯紧。"},
            {"role": "经营顾问", "output": f"回本维度{b.get('level', '?')}，商圈客流{c.get('level', '?')}，先从客流/复购找增量。"},
            {"role": "风控顾问", "output": f"现金储备{b.get('payback_months')}个月，警惕断档；回本超期是主要风险点。"},
        ],
        "verdict": "规则融合：综合现金流、经营、风控三维视角，得出一条诊断与整改动作（见上方诊断）。",
        "adopted": ["财务顾问", "经营顾问", "风控顾问"],
    }


def generate_store_diagnosis(model_result: dict, prev_diagnosis: str = "",
                             return_process: bool = False):
    """基于单店模型生成经营诊断：财务/经营/风控三视角竞争产出 → 掌柜融合裁决"""
    if not ai.ai_available():
        text = _store_diagnosis_degraded(model_result)
        if not return_process:
            return text
        return text, _store_degraded_process(model_result)

    # 构造一个给员工看的精炼经营简报
    d = model_result
    brief = {
        "业态": d["preset"]["name"], "实际日销": d["inputs"]["daily_revenue"],
        "保本日销": d["model"]["break_even_day"], "目标日销": d["model"]["target_day"],
        "月利润": d["model"]["month_profit"], "回本周期(月)": d["model"]["payback_months"],
        "现金可扛(月)": d["model"]["cash_months"],
        "综合评分": d["overall"]["score"], "判定": d["overall"]["level"],
        "经营现金流": d["dimensions"]["a"]["level"], "投资回本": d["dimensions"]["b"]["level"],
        "商圈客流": d["dimensions"]["c"]["level"], "现金预警": d.get("cash_flags", []),
    }
    task = (f"这家店的经营诊断，数据如下：\n{json.dumps(brief, ensure_ascii=False)}\n"
            + (f"上次诊断（参考不照抄）：{prev_diagnosis}\n" if prev_diagnosis else ""))
    final, process = _run_team("store", task, prev=prev_diagnosis,
                               sys_suffix="（你是这家店的一位员工，观点要具体、口语、直接，2-4 句。）",
                               user_tail="请站在「{role}」的视角，给出你最要紧的判断。")
    if not return_process:
        return final
    return final, process


# ---------------- 域注册表（增删能力的唯一入口，test_team 自检其完整性） ----------------
TEAM_DOMAINS = {
    "copy": {
        "mode": "collaborative",
        "employees": _COPY_EMPLOYEES,
        "reviewer": _COPY_REVIEWER,
        "judge": "写一条朋友圈文案（结合两位文案候选与合规审核意见，融合成一条可直接发的正文）"
                 "。风格要口语、短句、有具体细节，不套网红词和黑话，像真人老板随手发的朋友圈",
        "degraded": _copy_degraded_process,
    },
    "store": {
        "mode": "competitive",
        "employees": _STORE_EMPLOYEES,
        "reviewer": None,
        "judge": "这家店的经营诊断与下一步行动（融合财务/经营/风控，别给空洞的话，要有取舍）"
                 "。先给结论——可做/整改窗口(1个月)/果断劝退，再给一两个具体颗粒度的动作，别写\"提升运营\"这类空话",
        "degraded": _store_degraded_process,
    },
}


def list_team_domains() -> list:
    """列出已注册的团队业务域（供自检 / 前端菜单 / 后续扩展）"""
    return sorted(TEAM_DOMAINS)