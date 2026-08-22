"""team_domain_store.py — 单店经营诊断域（员工配置 + 降级函数 + 生成入口）

从 team_domains.py 拆出，避免单文件 >250 行。
依赖方向：team_domain_store → ai + json（无循环依赖）；_run_team 通过 late import 获取。
"""
from __future__ import annotations

import json

import ai


# ---------------- 员工配置 ----------------

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


# ---------------- 降级函数（无 Key 兜底） ----------------

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
    """无 Key 的「团队过程」：三个员工用规则各给角度 + 掌柜规则融合"""
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


# ---------------- 生成入口 ----------------

def generate_store_diagnosis(model_result: dict, prev_diagnosis: str = "",
                             return_process: bool = False):
    """基于单店模型生成经营诊断：财务/经营/风控三视角竞争产出 → 掌柜融合裁决"""
    if not ai.ai_available():
        text = _store_diagnosis_degraded(model_result)
        if not return_process:
            return text
        return text, _store_degraded_process(model_result)
    # late import 避免循环依赖
    from team_domains import _run_team
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
