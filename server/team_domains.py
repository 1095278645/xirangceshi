"""多 agent 业务域编排：朋友圈文案协作流水线 + 单店诊断竞争融合

从 ai.py 抽取，遵循项目「按业务域拆分」的架构（参照 team.py 引擎原语）。
本模块只负责把 team 引擎（并行竞争 / 融合裁决 / 采纳成长）接到具体业务域上，
单 agent 能力（记账解析、熟客画像、洞察等）仍留在 ai.py。

设计：
  - 复用 ai.chat / ai.ai_available / ai._extract_json（模块内 import，避免循环引用）。
  - 无 API Key 时走规则「团队过程」，业务文本与降级路径完全不变（测试保障）。
  - 有 Key 时：员工并行竞争产出 → 掌柜 _decide 融合裁决 → 采纳归因沉淀进 team 域。
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
    except Exception:  # noqa: BLE001 —— 融合失败退化为拼接
        return {"verdict": "", "adopted": [], "final": fail.strip()}


# ---------------- 朋友圈文案：协作流水线（创意/熟客 → 合规 → 掌柜融合） ----------------
_COPY_EMPLOYEES = [
    {"role": "创意文案师", "temperature": 0.9, "max_tokens": 300,
     "system": "你是烟火气的文案师，不套网红词，像真人老板随手发的朋友圈。口语、真诚、偶尔自嘲。"},
    {"role": "熟客运营", "temperature": 0.8, "max_tokens": 300,
     "system": "你懂老主顾的人情味，知道怎么把文案写到老熟人心里，记得住细节、不套路。"},
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
    # 1) 并行产出两个候选（竞争）
    adoption = team.adoption_brief("copy")
    task = (f"店铺：{shop_name}；场景：{scene}；补充：{extra}\n"
            + (f"熟客：{customer_name}，可自然带一句（不硬凑）\n" if customer_name else "")
            + (f"经营上下文（参考不照抄）：{context}\n" if context else ""))
    def produce(emp):
        sys = emp["system"] + "（请输出一条可直接发的朋友圈文案正文，不超过 80 字，只输出正文。）"
        return ai.chat([{"role": "system", "content": sys},
                        {"role": "user", "content": task}],
                       temperature=emp["temperature"], max_tokens=emp["max_tokens"]).strip()
    cands = team.run_parallel([lambda e=e: produce(e) for e in _COPY_EMPLOYEES])
    pairs = list(zip([e["role"] for e in _COPY_EMPLOYEES], cands))

    # 2) 协作下游：合规审核对候选做评审（挑违禁词/夸大，给修改意见）
    review_task = "\n\n".join(f"【{name}】{out}" for name, out in pairs)
    review_out = ""
    try:
        review_out = ai.chat(
            [{"role": "system", "content": _COPY_REVIEWER["system"]},
             {"role": "user", "content": f"请评审下面两条文案，指出违规点并给修改建议（2-3句）：\n{review_task}"}],
            temperature=_COPY_REVIEWER["temperature"], max_tokens=_COPY_REVIEWER["max_tokens"]).strip()
    except Exception:  # noqa: BLE001
        review_out = "未发现问题（评审降级跳过）。"

    # 3) 掌柜融合：候选 + 合规意见 → 最终文案
    cands_with_review = pairs + [(_COPY_REVIEWER["role"], review_out)]
    judge = _decide("写一条朋友圈文案（结合两位文案候选与合规审核意见，融合成一条可直接发的正文）",
                    cands_with_review, adoption, fail=cands[0])
    if judge["adopted"]:
        team.record_adoption("copy", judge["adopted"])
    process = {"mode": "collaborative",
               "employees": [{"role": name, "output": out} for name, out in cands_with_review],
               "verdict": judge["verdict"], "adopted": judge["adopted"]}
    if not return_process:
        return judge["final"]
    return judge["final"], process


# ---------------- 单店经营诊断：多人竞争 → 掌柜融合 ----------------
_STORE_EMPLOYEES = [
    {"role": "财务顾问", "temperature": 0.4, "max_tokens": 320,
     "system": "你是谨慎的财务顾问，眼里只有现金流：保本线、实际支出、回本周期、现金储备。先算账再说话，用大白话。"},
    {"role": "经营顾问", "temperature": 0.5, "max_tokens": 320,
     "system": "你是懂街边生意的经营顾问，看重客流、复购、客单价，给的是明天就能做的小动作。"},
    {"role": "风控顾问", "temperature": 0.5, "max_tokens": 260,
     "system": "你是专泼冷水的风控，专挑别人没敢说的坑：现金断档、回本太慢、成本结构不合理、盲目扩张。"},
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
    adoption = team.adoption_brief("store")
    task = (f"这家店的经营诊断，数据如下：\n{json.dumps(brief, ensure_ascii=False)}\n"
            + (f"上次诊断（参考不照抄）：{prev_diagnosis}\n" if prev_diagnosis else ""))

    def produce(emp):
        sys = emp["system"] + "（你是这家店的一位员工，观点要具体、口语、直接，2-4 句。）"
        return ai.chat([{"role": "system", "content": sys},
                        {"role": "user", "content": task + f"请站在「{emp['role']}」的视角，给出你最要紧的判断。"}],
                       temperature=emp["temperature"], max_tokens=emp["max_tokens"]).strip()

    cands = team.run_parallel([lambda e=e: produce(e) for e in _STORE_EMPLOYEES])
    pairs = list(zip([e["role"] for e in _STORE_EMPLOYEES], cands))
    judge = _decide("这家店的经营诊断与下一步行动（融合财务/经营/风控，别给空洞的话，要有取舍）",
                    pairs, adoption, prev_diagnosis,
                    fail=" ".join(out for _, out in pairs))
    if judge["adopted"]:
        team.record_adoption("store", judge["adopted"])
    process = {"mode": "competitive",
               "employees": [{"role": name, "output": out} for name, out in pairs],
               "verdict": judge["verdict"], "adopted": judge["adopted"]}
    if not return_process:
        return judge["final"]
    return judge["final"], process