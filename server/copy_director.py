"""copy_director.py — 文案导演层：选型 / 写法说明 / 组合摘要

从 `copy_playbook` 外移（架构自检 L1：单文件 >400 行要按既有职责边界拆）。
分工：

  - `copy_playbook`（数据层）：渠道 / 骨架 / 语气 / 配方 / 词表的**声明**与规格读取。
  - `copy_director`（导演层，本文件）：**拿这些声明做决策** —— 按内容信号选组合、
    把组合拼成给模型的"写法说明"、把组合压成给报告的摘要。

## 依赖方向

单向：director → playbook。但 playbook 也要支持 `copy_playbook.select_combo(...)` 这种
便捷写法，所以它的末尾用模块级 `__getattr__`（PEP 562）惰性转发到本模块。
两边**都不在加载期互相 import**，否则会出现 "partially initialized module"（实测踩到）。

本文件里对 playbook 的访问统一走 `_load_pb()` 填充的全局 `pb`，函数体写法与顶层 import 一致。
"""
from __future__ import annotations

import sys as _sys

__all__ = ['select_combo', 'build_brief', 'combo_summary', 'detect_intent',
           'recipe_combo', 'spec_of', 'pb']

# 首次调用时填充；填充后 `pb.CHANNELS` 这类写法与顶层 import 完全等价。
pb = None

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

# 渠道 → 该渠道的常规写法（只给了 channel、既无配方也无信号时用它补骨架/语气）
# 为什么需要：以前这种情况会套用硬编码的 scene_transplant + neighbor（其实是**朋友圈**的写法），
# 结果是"选抖音却按朋友圈的骨架和语气写"，理由还写着"按你选定的渠道/骨架/语气直接出"
# （独立复核抓到的自相矛盾）。
_CHANNEL_DEFAULT_RECIPE = {
    'moments': 'moments_daily', 'xiaohongshu': 'xhs_note', 'douyin': 'douyin_shout',
    'wechat_group': 'group_notice', 'signboard': 'board_one_line',
    'groupbuy': 'review_answer', 'reply': 'reply_sincere',
}


def _load_pb():
    """延迟导入 copy_playbook 并填充全局 `pb`（只做一次），避免与它的惰性转发成环。"""
    global pb
    if pb is None:
        import copy_playbook as _module
        _sys.modules.setdefault('pb', _module)     # 便于调试/打桩时按短名访问
        pb = _module
    return pb


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
    _load_pb()
    for name, spec in pb.RECIPE_SPECS.items():
        if (spec['channel'], spec['skeleton'], spec['tone']) == (channel, skeleton, tone):
            return name
    return f'custom:{channel}+{skeleton}+{tone}'


def spec_of(recipe: str) -> tuple:
    """配方对应的 (渠道, 骨架, 语气)；自定义或未注册配方返回空元组。"""
    _load_pb()
    spec = pb.RECIPE_SPECS.get(recipe)
    if not spec:
        return ()
    return (spec['channel'], spec['skeleton'], spec['tone'])


def recipe_combo(recipe: str) -> dict:
    """把一个配方展开成完整组合（不含选型理由）。

    也接受 `select_combo` 产出的 `custom:<渠道>+<骨架>+<语气>` 形态 —— 那种不是注册配方，
    硬查 `RECIPE_SPECS` 会抛错（自测踩到：报告里带出 custom 组合时崩）。
    """
    _load_pb()
    if recipe in pb.RECIPE_SPECS:
        spec = pb.recipe_spec(recipe)
        return {'channel': spec['channel'], 'skeleton': spec['skeleton'],
                'tone': spec['tone'], 'recipe': recipe,
                'reason': f'按指定配方「{spec["name"]}」直接出。', 'matched': ''}
    if recipe.startswith('custom:'):
        parts = recipe.split(':', 1)[1].split('+')
        if len(parts) == 3:
            combo = select_combo(channel=parts[0], skeleton=parts[1], tone=parts[2])
            combo['recipe'] = recipe
            return combo
    raise ValueError(
        f'非法配方：{recipe!r}（合法值：{pb.RECIPES}，或 custom:<渠道>+<骨架>+<语气>）')


def select_combo(scene: str = '', extra: str = '', biz_type: str = '',
                 channel: str = '', skeleton: str = '', tone: str = '',
                 recipe: str = '') -> dict:
    """选型：显式参数优先，其次意图信号，最后业态兜底。

    返回 `{channel, skeleton, tone, recipe, reason, matched}` —— `reason` 是**人话**，
    会原样出现在交付报告里（回答「为什么这么写」，对应参考 skill 的 style_reason）。

    **两条不变量**（自测各抓到一个反例，故写进 docstring）：

    1. 显式传了某一维就**只补空缺**，不覆盖调用方的选择；
    2. `recipe` 必须与最终三维一致 —— 只传了 `channel` 时不会保留"原名配方"，
       而是记成与三维相符的（必要时是 `custom:...`），否则会出现
       "理由说是小红书、实际发的是招牌"这种自相矛盾的报告。
    """
    _load_pb()
    for name, value, legal in (('channel', channel, pb.CHANNELS),
                               ('skeleton', skeleton, pb.SKELETONS),
                               ('tone', tone, pb.TONES),
                               ('recipe', recipe, pb.RECIPES)):
        if value and value not in legal:
            raise ValueError(f'非法 {name}：{value!r}（合法值：{legal}）')

    explicit = {'channel': channel, 'skeleton': skeleton, 'tone': tone}
    reason_bits: list[str] = []
    matched = ''
    padded: list[str] = []          # 由信号补上的维度（把理由说准要用）
    picked = ''

    if recipe:
        spec = pb.RECIPE_SPECS[recipe]
        # 配方与显式维度冲突时，**以显式维度为准**，并把冲突写进理由（不许悄悄换掉用户的选择）
        conflicts = [k for k in ('channel', 'skeleton', 'tone')
                     if explicit[k] and explicit[k] != spec[k]]
        channel = channel or spec['channel']
        skeleton = skeleton or spec['skeleton']
        tone = tone or spec['tone']
        if conflicts:
            zh = {'channel': '渠道', 'skeleton': '骨架', 'tone': '语气'}
            reason_bits.append(f'你指定了{"/".join(zh[c] for c in conflicts)}，'
                               f'与配方「{spec["name"]}」不同，以你的选择为准')
        else:
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
            for key in ('channel', 'skeleton', 'tone'):
                if not explicit[key]:
                    padded.append(key)
            channel = channel or spec['channel']
            skeleton = skeleton or spec['skeleton']
            tone = tone or spec['tone']
            if matched:
                reason_bits.append(f'从「{matched}」判断是{spec["name"]}的场景')

    if not (channel or skeleton or tone):
        # 极端兜底：完全没线索时给最常用的「朋友圈·日常营业」
        recipe = recipe or 'moments_daily'
        spec = pb.RECIPE_SPECS[recipe]
        channel, skeleton, tone = spec['channel'], spec['skeleton'], spec['tone']
        reason_bits.append('没有任何线索，按最常用的「朋友圈·日常营业」出')
    if channel and not (skeleton and tone):
        # 只指定了渠道、既没配方也没信号：按**该渠道的常规写法**补齐，别拿朋友圈的默认值硬套。
        canon = _CHANNEL_DEFAULT_RECIPE.get(channel)
        if canon:
            spec = pb.RECIPE_SPECS[canon]
            skeleton, tone = skeleton or spec['skeleton'], tone or spec['tone']
            picked = picked or canon
            reason_bits.append(f'只指定了渠道「{pb.CHANNEL_SPECS[channel]["name"]}」，'
                               f'其余按该渠道的常规写法（{spec["name"]}）补齐')
    channel = channel or (pb.RECIPE_SPECS[recipe]['channel'] if recipe else 'moments')
    skeleton = skeleton or (pb.RECIPE_SPECS[recipe]['skeleton'] if recipe
                            else 'scene_transplant')
    tone = tone or (pb.RECIPE_SPECS[recipe]['tone'] if recipe else 'neighbor')

    if not reason_bits:
        reason_bits.append('按你选定的渠道/骨架/语气直接出')

    # 不变量 2：recipe 与最终三维对齐
    final_recipe = _recipe_of(channel, skeleton, tone)
    if recipe and not any(explicit.values()) and spec_of(recipe) == (channel, skeleton, tone):
        final_recipe = recipe
    # 显式选了渠道、其余维度由信号补上时，理由要说准：骨架/语气来自信号，渠道是你选的。
    if matched and explicit['channel'] and padded:
        spec_name = pb.RECIPE_SPECS.get(picked, {}).get('name', '该场景')
        reason_bits = [b for b in reason_bits if '判断是' not in b]
        reason_bits.append(f'你指定了渠道「{pb.CHANNEL_SPECS[channel]["name"]}」，'
                           f'骨架与语气按「{matched}」的信号（{spec_name}的写法）补齐')

    return {
        'channel': channel, 'skeleton': skeleton, 'tone': tone,
        'recipe': final_recipe,
        'reason': '；'.join(reason_bits) + '。',
        'matched': matched,
    }


def build_brief(combo: dict, shop_name: str = '', biz_type: str = '',
                customer_name: str = '', extra: str = '') -> str:
    """把选中的组合拼成一段人话「写法说明」，作为提示词的一部分。

    只描述**怎么写**，不替模型写内容 —— 内容要靠调用方给的场景与事实。
    """
    _load_pb()
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
    _load_pb()
    ch = pb.channel_spec(combo['channel'])
    return {
        'channel': combo['channel'], 'channel_name': ch['name'],
        'skeleton': combo['skeleton'],
        'skeleton_name': pb.skeleton_spec(combo['skeleton'])['name'],
        'tone': combo['tone'], 'tone_name': pb.tone_spec(combo['tone'])['name'],
        'recipe': combo['recipe'],
        'recipe_name': pb.RECIPE_SPECS.get(combo.get('recipe') or '', {}).get('name', ''),
        'reason': combo.get('reason', ''),
        # limits 要给**实际生效**的上限（config 可能比打法库更严），
        # 否则报告里的字数口径和检查用的口径会打架（独立复核抓到过）。
        'limits': {'soft': ch['soft_max'], 'hard': _effective_hard(combo['channel'], ch)},
    }


def _effective_hard(channel: str, ch_spec: dict) -> int:
    """实际生效的硬上限：交给 copy_review.effective_limit 算（含 config 覆盖）。"""
    try:
        from copy_review import effective_limit
        return effective_limit(channel, ch_spec['hard_max'])
    except Exception:  # noqa: BLE001 —— 取不到就退回打法库声明值，不让报告整体失败
        return ch_spec['hard_max']
