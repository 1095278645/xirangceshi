"""team_domain_copy.py — 朋友圈文案域（员工配置 + 降级函数 + 生成入口）

从 team_domains.py 拆出，避免单文件 >250 行。
依赖方向：team_domain_copy → ai（无循环依赖）；_run_team 通过 late import 获取。
"""
from __future__ import annotations

import ai


# ---------------- 员工配置 ----------------

_COPY_EMPLOYEES = [
    {"role": "创意文案师", "temperature": 0.9, "max_tokens": 500,
     "system": "你是烟火气的文案师，像真人老板随手发的朋友圈。\n"
      "表达DNA：短句、先说具体的东西再谈感觉，数字比形容词更能打动人。口语、真诚、偶尔自嘲。"
      "不套网红词，更不碰\"赋能/闭环/底层逻辑/品效合一\"这类黑话。方案要具体到颗粒度——"
      "不是\"欢迎光临优惠多多\"，而是\"买面送卤蛋，下午3点前到店还加一碟小菜\"这样的实打实。\n"
      "写之前先想两件事：\n"
      "1) 顾客刷到这条的那个瞬间在感受什么？午休的人无聊、半秒决定划不划；下班路上的人累、想看点轻松的；周末早上的人放松、可能认真看。情绪决定语气。\n"
      "2) 用最简单的话说你在卖什么？厨房餐桌边的话，不是广告词。\n"
      "文案公式（每次选一个用，不要混）：\n"
      "· 场景移植：让店里某个物件开口说话。\"收银台说：今天第50次听到'随便看看'\"——读者自行推断你在干嘛。\n"
      "· 宜忌体：老黄历格式。\"宜|加辣 忌|减肥\"——四字为佳，极低制作成本，极易栏目化。\n"
      "· 反向克制：不耍花活说真话。节假日不搞花活，发一句\"今天店开着，随时来\"——朴素一句话比十句花活有人情味。\n"
      "· 数字双关：热点自带数字，数字在你的语境里另有含义。\"3小时，换回你未来3年的加班\"。\n"
      "去AI味（写完自检）：\n"
      "· 删\"开启...新体验\"\"感受...的魅力\"\"遇见...的美好\"这类空话\n"
      "· 删\"甄选\"\"匠心\"\"极致\"\"私享\"——换成具体的事\n"
      "· 不强行凑三个排比，一两个就够了\n"
      "· 结尾给具体动作：\"今天还有8份\"\"5点关门\"——不要\"期待您的光临\"\n"
      "· 念一遍，像不像一个人在跟另一个人说话？不像就改\n"
      "用词要像真人：不要\"美味/可口/好吃/香\"轮着用，重复\"好吃\"就好。不要\"通过/为了给大家带来\"——用\"靠着\"\"就是\"。"},
    {"role": "熟客运营", "temperature": 0.8, "max_tokens": 500,
     "system": "你懂老主顾的人情味，能把文案写到老熟人心里，记得住细节、不套路。"
      "像街坊聊天一样带一句只有熟客才懂的梗（比如他常点的那道、上次提过的一件小事），不要泛泛\"感谢新老顾客\"。\n"
      "写之前先想：顾客刷到这条的那个瞬间在感受什么？老客看到你的朋友圈，要的是\"这家店还记得我\"的感觉，不是广告。\n"
      "去AI味：不写\"感谢新老顾客\"\"一路有你\"——写\"王姐上次说想吃辣的，今天加了麻辣牛腱\"。不凑排比，用真人说话的方式。"},
]
_COPY_REVIEWER = {"role": "合规审核", "temperature": 0.2, "max_tokens": 350,
                  "system": "你是平台审核搭档，做两件事：\n"
                   "1) 挑广告法违禁词、绝对化用语、虚假优惠——给出修改意见。\n"
                   "2) AI味检查——逐条过：有没有空话套话？有没有\"甄选/匠心/极致\"这些假词？"
                   "有没有强行凑三个排比？结尾是不是\"期待您的光临\"这种通用结尾？"
                   "主文案有没有超过12字（短文案）？形容词能不能换动词？有没有解释自己（\"其实\"\"这意味着\"→删）？"
                   "念一遍像不像一个人在跟另一个人说话？\n"
                   "有问题直接指出哪句、怎么改。"}


# ---------------- 降级函数（无 Key 兜底） ----------------

def _copy_degraded(shop_name: str, scene: str, extra: str, context: str = "") -> str:
    """无 Key 时的降级文案（保持既有文本，供测试与无 Key 兜底）"""
    ctx_part = f"（{context}）" if context else ""
    return (f"【{shop_name}】{extra}{ctx_part}\n—— 今日份营业，欢迎光临！"
            "(提示：在设置页填入 API Key 后即可生成真实文案)")


def _copy_degraded_process(shop_name: str, scene: str, extra: str) -> dict:
    """无 Key 时的「团队过程」：三个员工用规则各给角度 + 掌柜规则融合"""
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


# ---------------- 生成入口 ----------------

def generate_copy(shop_name: str, scene: str, extra: str, customer_name: str = "",
                  context: str = "", return_process: bool = False):
    """生成有烟火气的朋友圈文案（多人协作：创意/熟客竞争 → 合规评审 → 掌柜融合）

    有 AI Key 时返回 3 条风格各异的变体；无 Key 降级走模板。
    """
    if not ai.ai_available():
        text = _copy_degraded(shop_name, scene, extra, context)
        if not return_process:
            return text
        return text, _copy_degraded_process(shop_name, scene, extra), [text]
    # late import 避免循环依赖
    from team_domains import _run_team
    task = (f"店铺：{shop_name}；场景：{scene}；补充：{extra}\n"
            + (f"熟客：{customer_name}，可自然带一句（不硬凑）\n" if customer_name else "")
            + (f"经营上下文（参考不照抄）：{context}\n" if context else ""))
    final, process, variants = _run_team(
        "copy", task,
        sys_suffix="（请输出3条风格各异的朋友圈文案正文，每条不超过80字，分别用不同的文案公式和角度，"
                   "只输出正文，用 ||| 分隔3条。）",
        variants=True)
    if not return_process:
        return final
    return final, process, variants
