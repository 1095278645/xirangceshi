"""team_domain_review.py — 掌柜每日复盘域（员工配置 + 降级函数 + 生成入口）

## 为什么单独一个域

原先的每日复盘是**把三个数字拼成一句话**（今天收 X / 本月收 Y / 单店一句话），
读的只有账本和单店模型 —— 熟客、库存、赊账、发票、预算、报税、账目更正全都
没进掌柜的眼睛。"掌柜"因此退化成一块只显示收支的仪表盘。

这里复用文案域已经跑通的编排（员工并行发言 → 掌柜取舍融合 → 采纳归因），
把「全店经营快照」交给一组**按经营维度分工**的员工，再由掌柜裁决。

## 员工分工的设计原则

- **一位员工只盯一个经营维度**（人 / 货 / 钱 / 票 / 险），职责不重叠 ——
  重叠会让候选趋同，"竞争"就白设了。
- 每人产出**一条判断 + 一个具体动作**，动作要能今天就做（点名到人、到货、到钱）。
- **没数据也是结论**："没建库存档案"本身就是要提的动作，别为此沉默。
- 口吻是店里的伙计，不是顾问：不写"提升运营效率"这种话。

## 关于 import ai

这里**不在顶层 import ai**：ai.py 的末尾有 `from team_domains import ...`，
而 team_domains 又 import 本模块 —— 顶层 import 会踩循环导入
（同目录的 copy/store 域是靠 import 顺序侥幸躲过的，不该继续依赖这种巧合）。
所以在函数内部惰性 import。
"""
from __future__ import annotations

# ---------------- 员工配置 ----------------
# 共同约束：都拿到同一份"全店快照"，但各自只看自己那一摊，避免五人说同一句话。

_COMMON_TAIL = (
    "\n你只负责自己这一块，不要替别的岗位操心。\n"
    "只讲两点：① 你这一块今天最要紧的一个事实（带数字）；② 一个具体动作"
    "（点名到人/货/钱，今天就能做）。\n"
    "口语、短句，像店里的伙计跟老板说话。不写\"提升/优化/加强\"这类空话，"
    "不堆排比，不要客套。全文不超过 120 字。"
)

_REVIEW_EMPLOYEES = [
    {"role": "账房先生", "temperature": 0.4, "max_tokens": 300,
     "system": "你是店里的账房先生，管钱和账。你盯的是：今天/本月的收支结构、"
       "客单价、毛利率有没有异常，成本和费用里哪一项涨得不正常，跟这家店的经营水平"
       "（日均流水、毛利率）比是不是偏离了。\n"
       "算得出来就说数字，算不出来就别编。"
       + _COMMON_TAIL},
    {"role": "熟客管家", "temperature": 0.6, "max_tokens": 300,
     "system": "你是熟客管家，管人。你盯的是：谁最久没来了、谁来得勤、"
       "有没有该回访的老主顾，以及今天有没有值得记一笔的人情细节。\n"
       "提动作时要说出名字和由头（比如\"张叔上次说老伴住院了\"），"
       "不要泛泛说\"维护老客户\"。如果库里没有熟客，就直说该开始记了。"
       + _COMMON_TAIL},
    {"role": "采买师傅", "temperature": 0.4, "max_tokens": 300,
     "system": "你是采买师傅，管货。你盯的是：库存货值、哪些货见底要补、"
       "哪些临期要赶紧出手，以及进货花得是不是太多或太少。\n"
       "要具体到货名和数量。如果店里还没建库存档案，就提\"该把进货出货记起来\"，"
       "并说清记了有什么用（能算货值、能防临期浪费）。"
       + _COMMON_TAIL},
    {"role": "税务管事", "temperature": 0.3, "max_tokens": 300,
     "system": "你是管票管税的。你盯的是：这个月开票收票情况、有没有该要的进项票、"
       "报税日历上最近的节点、有没有税务上的风险苗头（比如收入快碰到免征额）。\n"
       "话要白话，别背法条。没票没税事就直说\"这块今天没事\"，不要硬凑。"
       + _COMMON_TAIL},
    {"role": "经营监察", "temperature": 0.3, "max_tokens": 300,
     "system": "你是管规矩的，负责挑毛病。你盯的是：有没有改过的账、作废过的单、"
       "退货冲销，赊账有没有逾期的，预算有没有被超。\n"
       "你的价值在于**发现问题**，所以宁可指出一个可疑的地方，也不要空泛肯定。"
       "如果一切正常，就明确说\"账没被改过、没有逾期\"，让人放心。"
       + _COMMON_TAIL},
]


# ---------------- 降级函数（无 Key 兜底） ----------------

def _strip_tag(line: str) -> str:
    """去掉快照的 [标签] 前缀 —— 那些是给员工看的分区标记，不是人话。"""
    import re
    return re.sub(r"^\[[^\]]+\]\s*", "", line or "").strip()


def _review_degraded_process(snapshot: str) -> dict:
    """无 Key 时的「团队过程」：五个岗位按快照各挑一条，掌柜规则融合。

    刻意不复用 AI 文本，而是**从快照里挑选**最要紧的一两条 —— 这样无 Key 也有
    真正的"掌柜取舍"，不是把整份快照原样吐回去。

    注意输出要是**人话**：快照里的 `[今日]` `[库存]` 是给员工看的分区标记，
    直接吐出来会变成"掌柜在念标签"。这里统一剥掉前缀。
    """
    lines = [l.strip() for l in (snapshot or "").splitlines() if l.strip()]
    urgent = [l for l in lines
              if any(k in l for k in ("没建", "没设", "逾期", "临期", "见底",
                                      "快过期", "作废", "退货", "该"))]
    pick = [_strip_tag(x) for x in (urgent[:2] or lines[:1])]
    pick = [x for x in pick if x]

    def _grab(prefixes, default):
        for l in lines:
            if l.startswith(tuple(prefixes)):
                return _strip_tag(l)
        return default

    money = _grab(("[今日]",), "")
    todos = []
    for l in lines:
        if any(k in l for k in ("没建", "没设", "逾期", "临期", "见底", "快过期")):
            t = _strip_tag(l)
            if t and t not in todos:
                todos.append(t)
        if len(todos) >= 2:
            break
    if money and todos:
        final = f"今天{money}。有件事得动手：{todos[0]}。"
        if len(todos) > 1:
            final += f"顺带{todos[1]}。"
    elif money:
        final = f"今天{money}，账上没别的异常，照常做。"
    elif todos:
        final = "账上还没什么数据。先动手两件：" + "；".join(todos) + "。"
    else:
        final = "今天还没有可复盘的经营数据。"

    return {
        "mode": "competitive",
        "employees": [
            {"role": "账房先生",
             "output": _grab(("[今日]", "[本月]"), "今天账上没有新动静。")},
            {"role": "熟客管家",
             "output": _grab(("[熟客]",), "还没人建档，先攒熟客。")},
            {"role": "采买师傅",
             "output": _grab(("[库存]", "[待补货]", "[临期]"), "库存这块还没记。")},
            {"role": "税务管事",
             "output": _grab(("[发票]", "[报税]"), "票税这块没数据。")},
            {"role": "经营监察",
             "output": _grab(("[账目更正]", "[赊账]", "[已逾期]"), "账目干净，没有异常。")},
        ],
        "verdict": "规则融合：从全店快照里挑最要紧的一两条动手（没配 Key 时按信号强弱挑选）。",
        "adopted": ["账房先生", "经营监察"],
        "final": final,
    }


# ---------------- 生成入口 ----------------

def generate_daily_review(snapshot: str, prev: str = "",
                          return_process: bool = False):
    """生成掌柜每日复盘：五位伙计各管一摊 → 掌柜挑最要紧的动手。

    有 AI Key 时走真实多 agent 编排；无 Key 时按快照信号规则挑选（业务文本可用）。
    """
    import ai  # 惰性导入，避开 ai.py ↔ team_domains 的循环（见模块 docstring）
    if not ai.ai_available():
        text = _review_degraded_process(snapshot)["final"]
        if not return_process:
            return text
        return text, _review_degraded_process(snapshot)

    from team_domains import _run_team
    task = (
        "这是这家店今天的全店经营快照（各岗位据此发言）：\n"
        f"{snapshot}\n\n"
        "请各位从自己负责的那一块出发，说最要紧的一件事。"
    )
    result = _run_team(
        "review", task, prev=prev,
        sys_suffix=("（掌柜口吻：像老掌柜跟老板交代事情。只挑**今天最该动手的一两件**，"
                    "其余一句话带过或直接不提。先给最要紧的那个数字，再给动作，"
                    "要具体到人/货/钱。总长不超过 200 字，不用小标题，不要客套话。）"))
    if not return_process:
        return result[0]
    final, process = result
    return final, process
