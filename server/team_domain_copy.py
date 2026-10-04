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
                  context: str = "", return_process: bool = False, *,
                  channel: str = "", skeleton: str = "", tone: str = "",
                  recipe: str = "", return_report: bool = False,
                  biz_type: str = ""):
    """生成有烟火气的文案（多人协作：创意/熟客竞争 → 合规评审 → 掌柜融合）。

    - 有 AI Key 时返回 3 条风格各异的变体；无 Key 降级走模板（**降级文本一字未改**）。
    - 新增关键词参数（渠道/骨架/语气/配方）走 `copy_playbook` 的声明式打法库：
      都不传时按场景信号自动选型，选型理由进交付报告（回答"为什么这么写"）。
    - `return_report=True` 时返回 (正文, 交付报告)，报告含配图方案与硬规则自检；
      报告失败不影响文案本身（降级成一份最简报告）。
    """
    # 选型先算（纯本地）：即使无 Key 也能给出"这条该按什么渠道/骨架写"
    import copy_playbook as pb
    if not biz_type:
        biz_type = pb.infer_biz_type(f'{scene} {extra} {context}')
    combo = pb.select_combo(scene=scene, extra=extra, biz_type=biz_type,
                            channel=channel, skeleton=skeleton, tone=tone, recipe=recipe)

    if not ai.ai_available():
        text = _copy_degraded(shop_name, scene, extra, context)
        process = _copy_degraded_process(shop_name, scene, extra)
        if return_report:
            return text, _report(text, combo, process, shop_name, extra, biz_type)
        if not return_process:
            return text
        return text, process, [text]
    brief = pb.build_brief(combo, shop_name, biz_type, customer_name, extra)
    task = (f"店铺：{shop_name}；场景：{scene}；补充：{extra}\n"
            + (f"熟客：{customer_name}，可自然带一句（不硬凑）\n" if customer_name else "")
            + (f"经营上下文（参考不照抄）：{context}\n" if context else "")
            + f"\n【这次怎么写】\n{brief}\n")
    settings = ai.load_settings()
    if settings.get("ai_pipeline") != "team":
        from team_domains import _run_fast
        final, process, variants = _run_fast(
            "copy", task,
            system=("为街边小店写可发布的文案。严格按【这次怎么写】里的渠道纪律、"
                    "结构与语气来写；短句、具体细节、口语自然；不用广告腔、网红词、排比。"
                    "禁止出现复盘、账本、看这笔账等无关提醒。"
                    "输出3条不同角度的正文，每条控制在渠道字数上限内，用 ||| 分隔。"),
            temperature=0.8, max_tokens=600, variants=True)
        if return_report:
            return final, _report(final, combo, process, shop_name, extra, biz_type, variants)
        return (final, process, variants) if return_process else final

    from team_domains import _run_team
    final, process, variants = _run_team(
        "copy", task,
        sys_suffix=("（严格按【这次怎么写】的渠道纪律与结构写，输出3条角度各异的正文，"
                    "每条不超过该渠道上限，只输出正文，用 ||| 分隔3条。）"),
        variants=True)
    if return_report:
        return final, _report(final, combo, process, shop_name, extra, biz_type, variants)
    if not return_process:
        return final
    return final, process, variants


def _report(final: str, combo: dict, process: dict, shop_name: str, extra: str,
            biz_type: str, variants: list | None = None) -> dict:
    """组装交付报告；报告自身出任何问题都降级成最简版（不能拖垮文案）。"""
    cands = [t for t in (variants or []) if (t or '').strip()]
    if final and final not in cands:
        cands.insert(0, final)
    texts = cands or ([final] if final else [])
    try:
        import copy_review
        return copy_review.build_report(combo, texts, team=process, shop_name=shop_name,
                                        extra=extra, biz_type=biz_type,
                                        candidates=cands)
    except Exception as e:  # noqa: BLE001 —— 报告是附加物，失败也要让文案能用
        import logging
        logging.getLogger("team_domain_copy").warning("交付报告生成失败（文案照常返回）：%s", e)
        return {"combo": {"reason": combo.get("reason", "")}, "texts": texts,
                "candidates": cands or texts, "primary": texts[0] if texts else "",
                "verdict": "unknown", "error": str(e)[:200]}
