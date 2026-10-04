"""copy_director.py — 文案导演层：选型 / 写法说明 / 组合摘要

从 `copy_playbook` 外移（架构自检 L1：单文件 >400 行要按既有职责边界拆）。
分工：

  - `copy_playbook`（数据层）：渠道 / 骨架 / 语气 / 配方 / 词表的**声明**与规格读取。
  - `copy_director`（导演层，本文件）：**拿这些声明做决策** —— 按内容信号选组合、
    把组合拼成给模型的"写法说明"、把组合压成给报告的摘要。

依赖方向单向：director → playbook（playbook 不认识 director，无环）。
"""
from __future__ import annotations

import copy_playbook as pb

__all__ = ['select_combo', 'build_brief', 'combo_summary', 'detect_intent',
           'recipe_combo']

# 意图信号（从上往下第一个命中的赢）
_INTENT_SIGNALS: tuple[tuple[tuple[str, ...], str], ...] = (
    (('团购', '套餐', '拼团', '上架', '规则'), 'review_answer'),
    (('差评', '评价', '回复', '好评'), 'reply_sincere'),
    (('招牌', '门头', '价签', '灯箱', '贴纸'), 'board_one_line'),
    (('群', '通知', '到货', '闭店', '开团'), 'group_notice'),
    (('抖音', '短视频', '口播', '视频'), 'douyin_shout'),
    (('小红书', '种草', '笔记', '探店'), 'xhs_note'),
    (('攻略', '清单', '排行', '价目'), 'xhs_guide'),
    (('加班', '迟到', '赶时间', '早餐', '雨天', '带娃'), 'xhs_note'),
    (('上新', '新品', '新出', '限量', '今天有'), 'moments_soft_ad'),
    (('催', '快没了', '收摊', '剩'), 'group_urge'),
)

# 业态兜底（没信号时用）：餐饮/饮品卖给熟客看朋友圈，零售/生鲜更靠群与招牌
_INDUSTRY_FALLBACK = {
    '餐饮': 'moments_daily', '饮品': 'moments_daily', '服务': 'xhs_note',
    '零售': 'group_notice', '生鲜': 'group_notice', '摆摊': 'board_one_line',
}


def detect_intent(text: str) -> tuple[str, str]:
    """从场景与补充里找意图信号，返回 (配方名, 命中信号)；没命中返回空串。"""
    haystack = text or ''
    for words, recipe in _INTENT_SIGNALS:
        for w in words:
            if w and w in haystack:
                return recipe, w
    return '', ''


def _recipe_of(channel: str, skeleton: str, tone: str) -> str:
    """反查最接近的配方名（让报告里有一个可复用的缩写；没有就写自定义组合）。"""
    for name, spec in pb.RECIPE_SPECS.items():
        if (spec['channel'], spec['skeleton'], spec['tone']) == (channel, skeleton, tone):
            return name
    return f'custom:{channel}+{skeleton}+{tone}'


def recipe_combo(recipe: str) -> dict:
    """把一个配方展开成完整组合（不含选型理由）。"""
    spec = pb.recipe_spec(recipe)
    return {'channel': spec['channel'], 'skeleton': spec['skeleton'],
            'tone': spec['tone'], 'recipe': recipe,
            'reason': f'按指定配方「{spec["name"]}」直接出。', 'matched': ''}


def select_combo(scene: str = '', extra: str = '', biz_type: str = '',
                 channel: str = '', skeleton: str = '', tone: str = '',
                 recipe: str = '') -> dict:
    """选型：显式参数优先，其次意图信号，最后业态兜底。

    返回 `{channel, skeleton, tone, recipe, reason, matched}` —— `reason` 是**人话**，
    会原样出现在交付报告里（回答「为什么这么写」，对应参考 skill 的 style_reason）。
    显式传了某一维时只补其余维度，不覆盖调用方的选择。
    """
    for name, value, legal in (('channel', channel, pb.CHANNELS),
                               ('skeleton', skeleton, pb.SKELETONS),
                               ('tone', tone, pb.TONES),
                               ('recipe', recipe, pb.RECIPES)):
        if value and value not in legal:
            raise ValueError(f'非法 {name}：{value!r}（合法值：{legal}）')

    reason_bits: list[str] = []
    matched = ''
    if recipe:
        spec = pb.RECIPE_SPECS[recipe]
        channel = channel or spec['channel']
        skeleton = skeleton or spec['skeleton']
        tone = tone or spec['tone']
        reason_bits.append(f'按指定配方「{spec["name"]}」')

    if not (channel and skeleton and tone):
        picked, matched = detect_intent(f'{scene} {extra}')
        if not picked:
            fallback = _INDUSTRY_FALLBACK.get(biz_type or '')
            if fallback:
                picked = fallback
                reason_bits.append(f'没有明显信号，按业态「{biz_type}」兜底')
        if picked:
            spec = pb.RECIPE_SPECS[picked]
            channel = channel or spec['channel']
            skeleton = skeleton or spec['skeleton']
            tone = tone or spec['tone']
            recipe = recipe or picked
            if matched:
                reason_bits.append(f'从「{matched}」判断是{spec["name"]}的场景')

    if not (channel or skeleton or tone):
        # 极端兜底：完全没线索时给最常用的「朋友圈·日常营业」
        recipe = recipe or 'moments_daily'
        spec = pb.RECIPE_SPECS[recipe]
        channel, skeleton, tone = spec['channel'], spec['skeleton'], spec['tone']
        reason_bits.append('没有任何线索，按最常用的「朋友圈·日常营业」出')
    channel = channel or (pb.RECIPE_SPECS[recipe]['channel'] if recipe else 'moments')
    skeleton = skeleton or (pb.RECIPE_SPECS[recipe]['skeleton'] if recipe
                            else 'scene_transplant')
    tone = tone or (pb.RECIPE_SPECS[recipe]['tone'] if recipe else 'neighbor')

    if not reason_bits:
        reason_bits.append('按你选定的渠道/骨架/语气直接出')
    return {
        'channel': channel, 'skeleton': skeleton, 'tone': tone,
        'recipe': recipe or _recipe_of(channel, skeleton, tone),
        'reason': '；'.join(reason_bits) + '。',
        'matched': matched,
    }


def build_brief(combo: dict, shop_name: str = '', biz_type: str = '',
                customer_name: str = '', extra: str = '') -> str:
    """把选中的组合拼成一段人话「写法说明」，作为提示词的一部分。

    只描述**怎么写**，不替模型写内容 —— 内容要靠调用方给的场景与事实。
    """
    ch = pb.channel_spec(combo['channel'])
    sk = pb.skeleton_spec(combo['skeleton'])
    tn = pb.tone_spec(combo['tone'])
    lines = [
        f'这次发到：{ch["name"]}（读者状态：{ch["scene_hint"]}）',
        f'结构用「{sk["name"]}」：{sk["concept"]}；大致形状：{sk["shape"]}。',
        f'语气用「{tn["name"]}」：{tn["feel"]}。{tn["rules"]}',
        f'渠道纪律：{ch["structure"]}。',
        f'渠道禁忌：{ch["avoid"]}。',
        f'这个结构的注意：{sk["watch"]}。',
        f'格式：{ch["line_hint"]}；表情：{ch["emoji"]}。',
        f'禁用词（出现即算不合格）：{"、".join(pb.NO_SLOP_WORDS[:14])} 等空话套话。',
    ]
    if shop_name:
        lines.append(f'店铺：{shop_name}（可以入正文，但别当第一句）')
    if biz_type:
        lines.append(f'业态：{biz_type}')
    if customer_name:
        lines.append(f'可以自然带一句给熟客「{customer_name}」的话（不硬凑）')
    if extra:
        lines.append(f'这次要说的具体事：{extra}')
    return '\n'.join(lines)


def combo_summary(combo: dict) -> dict:
    """给交付报告用的组合摘要（中文名一并给出，前端不再翻译）。"""
    ch = pb.channel_spec(combo['channel'])
    return {
        'channel': combo['channel'], 'channel_name': ch['name'],
        'skeleton': combo['skeleton'],
        'skeleton_name': pb.skeleton_spec(combo['skeleton'])['name'],
        'tone': combo['tone'], 'tone_name': pb.tone_spec(combo['tone'])['name'],
        'recipe': combo['recipe'],
        'recipe_name': pb.RECIPE_SPECS.get(combo.get('recipe') or '', {}).get('name', ''),
        'reason': combo.get('reason', ''),
        'limits': {'soft': ch['soft_max'], 'hard': ch['hard_max']},
    }
