"""copy_review.py — 文案硬检查 / 配图方案 / 交付自检报告

## 为什么需要这一层

参考的生成型 skill 有两条我们缺的纪律：

1. **合规与 AI 味要做成"可测的硬检查"，而不是"再过一次模型"**。
   我们的合规审核是个人工位（一个员工），它靠模型再读一遍，既不可复现、也无法单测。
   这里把能写死的部分写成规则表：违禁词、绝对化用语、医疗/金融承诺、渠道特有禁忌、
   字数上限、具体度、空话套话 —— 命中即报，**且不改写正文**。
2. **交付要附"这次为什么这么写"的自检报告**（对应参考 skill 的 completion report）。

## 明确不做的事（照搬参考 skill 的一条硬约束）

**发现违禁词不自动替换、不静默改写正文。** 只标记 + 给修改方向，让生成方重出或人工决定。
理由：静默替换会让人以为文案已经合规，而替换后的句子可能更糟（比如把"绝不"改成"不"，
语义就飘了）。这条与项目里 `plain_language` 的静默术语替换不同 —— 那层是**翻译**，
这层是**合规判定**，判定结果不能偷偷改结论。

## 交付报告里有什么

- `combo`：这次用的渠道/骨架/语气/配方 + 人话理由（来自 copy_playbook.select_combo）
- `texts`：正文候选（第一条是主文案）
- `diagnostics`：字数/行数/具体度/emoji 数
- `checks`：逐条硬检查结果（pass/fail/skip + 人话说明）
- `verdict`：pass / warn / fail
- `image_plan`：配图方案（封面/内容/结尾，每张给标题、画面、图上文字、比例、提示词）
- `next_actions`：只有 fail/warn 时才给的"改哪儿"清单
"""
from __future__ import annotations

import re

import copy_playbook as pb
import config

__all__ = ['SEVERITY_ORDER', 'HARD_RULES', 'SLOP_AND_ABSOLUTE', 'CHANNEL_BANNED',
           'review', 'build_image_plan', 'build_report', 'concrete_ratio',
           'count_lines', 'count_emoji', 'normalize_for_match', 'effective_limit',
           'punctuation_pileup', 'HARD_RULES']

# 兼容旧引用：合规词表现在是 `copy_playbook` 的**声明**（判定逻辑仍在本模块）。
# 放这里是因为 copy_review 曾因此超 400 行硬限（arch_check LINE_FAIL）。
from copy_rules import HARD_RULES  # noqa: E402,F401

# ---------------- 规则表 ----------------
# 说明：这里只放**能靠字符串判定**的规则。语义类判断（有没有说清卖点）交给 AI 员工，
# 但结论同样进报告，不让它变成一句没人看的评语。

# AI 味 + 空话：词表来自 copy_playbook（唯一真源），这里只管判定与计数
SLOP_AND_ABSOLUTE = pb.NO_SLOP_WORDS

# 渠道特有禁忌
# 注意：**不要放纯标点词**（如「，」）—— 归一化后为空，逐字匹配会变成"永远命中"
# （独立复核抓到的 P1：招牌渠道所有文案被判 fail）。标点类改用 `punctuation_pileup` 判定。
CHANNEL_BANNED = {
    'moments': ('@全体成员', 'http://', 'https://', '会员日', '感恩回馈'),
    'xiaohongshu': ('家人们', '宝子们', '求三连', '一键三连'),
    'douyin': ('点击下方', '关注我', '链接在评论区'),
    'wechat_group': ('各位老板', '亲们', 'http://', 'https://'),
    'signboard': (),
    'groupbuy': ('最终解释权', '解释权归'),
    'reply': (),
}
# 只对"短句/回复"渠道生效的标点堆砌判定（纯标点没法当词表用，见上）
_PUNCT_CHANNELS = ('signboard', 'reply', 'douyin')
# 链接类禁忌统一走正则（词表只能穷举 http://，实测 `www.a.com`／`a.com` 绕过）
_URL_RE = re.compile(r'(https?://|www\.|\b[\w-]+\.(?:com|cn|net|org|top|vip)\b)', re.I)

SEVERITY_ORDER = {'fail': 0, 'warn': 1, 'pass': 2, 'skip': 3}

_EMOJI_RE = re.compile(
    '[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\u2b00-\u2bff]')
_NUMBER_RE = re.compile(r'\d')
_LIST_MARK_RE = re.compile(r'[①②③④⑤⑥⑦⑧⑨⑩]|^\s*[\d]+[.、)]|^\s*[-·•]', re.M)
_PROMO_TAIL_RE = re.compile(r'期待(您的|你的)?(光临|惠顾)|欢迎光临|谢谢惠顾')

# 匹配前的归一化：违禁词靠"插入空格/标点/emoji"就能绕过（实测"最 好 吃"漏过），
# 所以判定前先去掉这些噪声，再在**压缩后的文本**上找词。
# \ufe0e/\ufe0f 是 emoji 的变体选择符（不可见但要一起去掉，否则"最好❤️"会漏）、
# \u200d 是零宽连接符、\U0001F3FB-\U0001F3FF 是肤色修饰符。
_NOISE_RE = re.compile(
    r'[\s\u3000\u200b-\u200d\ufe0e\ufe0f\U0001F3FB-\U0001F3FF'
    r'·・,，.。、;；:：!！?？~～\-—_/\\|()（）\[\]【】"\'`]+')
# 常见繁体 → 简体（只收与规则表相关的字，不做完整转换）
_TRAD_TO_SIMP = str.maketrans({
    '絕': '绝', '對': '对', '網': '网', '價': '价', '獨': '独', '無': '无',
    '敵': '敌', '極': '极', '頂': '顶', '級': '级', '療': '疗', '藥': '药',
    '減': '减', '肥': '肥', '賺': '赚', '險': '险', '漲': '涨', '搶': '抢',
    '團': '团', '規': '规', '則': '则', '關': '关', '注': '注',
})


def normalize_for_match(text: str) -> str:
    """归一化：NFKC（全角→半角、全角@等）+ 去空格/标点/emoji + 繁体转简 + 小写。

    **只用于匹配，不用于输出**（不改写正文）。NFKC 是挡"变体绕过"的关键：
    `１００％`→`100%`、`＠全体成员`→`@全体成员`、`YYDS`→`yyds`。
    """
    if not text:
        return ''
    import unicodedata
    cleaned = unicodedata.normalize('NFKC', text)
    cleaned = _NOISE_RE.sub('', cleaned)
    cleaned = _EMOJI_RE.sub('', cleaned)
    return cleaned.translate(_TRAD_TO_SIMP).casefold()


def count_lines(text: str) -> int:
    return len([l for l in (text or '').splitlines() if l.strip()])


def count_emoji(text: str) -> int:
    return len(_EMOJI_RE.findall(text or ''))


def concrete_ratio(text: str) -> float:
    """具体度 = 具体物个数 / 句子数（1.0 表示每句都有一个具体物）。"""
    sentences = [s for s in re.split(r'[。！？!?\n]', text or '') if s.strip()]
    if not sentences:
        return 0.0
    return round(pb.count_concrete(text) / len(sentences), 2)


# ---------------- 单条检查 ----------------

def _hit(words, text) -> list[str]:
    """在归一化文本里找词（挡住"插空格/标点/emoji"的绕过），返回**原始词形**便于提示。

    两个边界（独立复核抓到过真 bug，别改回去）：

    1. **规则词自己的归一化结果可能为空**：招牌渠道的禁忌是「，」「；」这种标点，
       归一化后变成空串，而 `'' in flat` 恒真 → 任何非空文案都被判 fail。
       所以归一化后为空的词直接跳过，交给下面的标点堆砌判定。
    2. **全角/大小写**：`１００％`／`＠全体成员`／`YYDS` 这类要靠 NFKC + casefold 归一，
       否则换个写法就绕过了。
    """
    flat = normalize_for_match(text)
    if not flat:
        return []
    hits = []
    for w in words:
        nw = normalize_for_match(w)
        if not nw:
            continue                      # 纯标点词走 punctuation_pileup
        if nw in flat:
            hits.append(w)
    return hits


def punctuation_pileup(text: str) -> bool:
    """标点堆砌判定（招牌/短句渠道用）：连续两个以上标点或全篇标点占比过高。

    这是「，」「；」这类"通道禁词"的正确判法 —— 逐字匹配标点既无意义（文字里本来就可能有），
    又会被归一化吃掉（见 `_hit` 的边界 1）。
    """
    if not text:
        return False
    if re.search(r'[，,；;。.！!？?、]{2,}', text):
        return True
    puncts = len(re.findall(r'[，,；;。.！!？?、：:—…]', text))
    return puncts >= 4 and puncts / max(len(text), 1) > 0.25


def effective_limit(channel: str, hard_max: int | None = None) -> int:
    """该渠道的**实际**字数上限：取「config 阈值」与「打法库声明」里更严的那个。

    为什么取更严：两处口径都可能被单独调整（config 给运维，打法库给产品），
    取严的能保证"任何一处的收紧都生效"，不会出现改了一处却不生效的静默失灵。

    边界：config 值 ≤ 0 视为"没配"（用 `or` 会把 0 当假值吞掉，实测过；
    而负数会让任何文案必 fail），因此只在 > 0 时才参与取严。
    """
    ch = int(hard_max if hard_max is not None else pb.channel_spec(channel)['hard_max'])
    cfg_key = {'moments': 'COPY_MOMENTS_MAX_CHARS', 'xiaohongshu': 'COPY_XHS_BODY_MAX_CHARS',
               'douyin': 'COPY_DOUYIN_MAX_CHARS', 'wechat_group': 'COPY_GROUP_MAX_CHARS',
               'signboard': 'COPY_SIGNBOARD_MAX_CHARS', 'groupbuy': 'COPY_GROUPBUY_MAX_CHARS',
               'reply': 'COPY_REPLY_MAX_CHARS'}.get(channel, '')
    if not cfg_key:
        return ch
    try:
        cfg_max = int(getattr(config, cfg_key, ch))
    except (TypeError, ValueError):
        cfg_max = ch
    return min(cfg_max, ch) if cfg_max > 0 else ch


def _check_channel_length(text: str, channel: str, hard_max: int) -> dict:
    """字数：上限口径见 `effective_limit`。"""
    limit = effective_limit(channel, hard_max)
    n = len(text or '')
    if n <= limit:
        return {'id': 'length', 'ok': True, 'severity': 'pass',
                'detail': f'{n} 字，在 {limit} 字以内'}
    if n <= limit * 1.1:
        return {'id': 'length', 'ok': False, 'severity': 'warn',
                'detail': f'{n} 字，略超 {limit} 字的渠道上限，删掉一个修饰词就够'}
    return {'id': 'length', 'ok': False, 'severity': 'fail',
            'detail': f'{n} 字，超过 {limit} 字的渠道上限太多，建议切成两条'}


def _check_slop(text: str) -> dict:
    # 走归一化匹配：否则"匠 心 甄 选"这种拆字能绕过（独立复核指出的实现漏洞）
    hits = _hit(pb.NO_SLOP_WORDS, text)
    if not hits:
        return {'id': 'ai_slop', 'ok': True, 'severity': 'pass', 'detail': '没有空话套话'}
    return {'id': 'ai_slop', 'ok': False, 'severity': 'warn',
            'detail': '出现空话套话：' + '、'.join(hits[:6]) + '；换成具体的事或数'}


def _check_generic_tail(text: str) -> dict:
    m = _PROMO_TAIL_RE.search(text or '')
    if not m:
        return {'id': 'generic_tail', 'ok': True, 'severity': 'pass',
                'detail': '结尾不是通用客套话'}
    return {'id': 'generic_tail', 'ok': False, 'severity': 'warn',
            'detail': f'结尾是通用客套话「{m.group(0)}」；改成具体信息（剩几份/几点收摊）'}


def _check_concreteness(text: str, min_ratio: float) -> dict:
    ratio = concrete_ratio(text)
    if ratio >= min_ratio:
        return {'id': 'concreteness', 'ok': True, 'severity': 'pass',
                'detail': f'具体度 {ratio}（每句 {ratio} 个具体物）'}
    return {'id': 'concreteness', 'ok': False, 'severity': 'warn',
            'detail': f'具体度只有 {ratio}，低过 {min_ratio}：通篇没有数字/时间/数量，'
                      '读起来像广告不像人话'}


def _check_ai_smell_lines(text: str, lines: list[str]) -> list[dict]:
    """结构类硬检查：段落数、开头是不是招牌、排比堆砌、感叹号。"""
    out = []
    n_lines = count_lines(text)
    if n_lines > 6:
        out.append({'id': 'too_many_lines', 'ok': False, 'severity': 'warn',
                    'detail': f'{n_lines} 行，太长；朋友圈/群发的读者不会读完'})
    lines = [l.strip() for l in (lines or []) if l.strip()]
    if lines and re.match(r'^(【|\[).{0,10}(】|\])', lines[0]):
        out.append({'id': 'brand_first', 'ok': False, 'severity': 'warn',
                    'detail': '第一句是店名/括号头，读者会当成广告划走；把"事"放最前面'})
    if len(re.findall(r'！', text or '')) >= 3:
        out.append({'id': 'exclaim_stack', 'ok': False, 'severity': 'warn',
                    'detail': '感叹号 3 个以上，像叫卖喇叭；留一个就够'})
    if re.search(r'(.{4,8})\1', text or ''):
        out.append({'id': 'repeat_phrase', 'ok': False, 'severity': 'warn',
                    'detail': '有连续重复的短语，读起来卡；删掉一处'})
    return out


def _check_planning(text: str, combo: dict) -> dict:
    """副题检查：有没有行动指引（去哪/几点/多少钱/怎么买）。"""
    has_action = bool(re.search(
        r'\d+\s*(?:点|元|块|份|个|斤)|今天|明天|早上|下午|晚上|门店|门口|摊位|'
        r'扫码|到店|来找我|地址|营业', text or ''))
    if has_action:
        return {'id': 'action_hint', 'ok': True, 'severity': 'pass',
                'detail': '有可执行的信息（时间/价格/地点其一）'}
    return {'id': 'action_hint', 'ok': False, 'severity': 'warn',
            'detail': '没告诉顾客"下一步做什么/去哪/几点"；补一个硬信息'}


def _check_skeleton_fit(text: str, combo: dict) -> dict:
    """骨架契合度：选了骨架却完全没按它的形状写，就报出来（可解释性的一部分）。"""
    sk = combo.get('skeleton')
    if sk == 'listicle':
        marks = len(_LIST_MARK_RE.findall(text or ''))
        if marks >= 3:
            return {'id': 'skeleton_fit', 'ok': True, 'severity': 'pass',
                    'detail': f'清单有 {marks} 条'}
        return {'id': 'skeleton_fit', 'ok': False, 'severity': 'warn',
                'detail': '选了「清单罗列」但没看到 3 条以上编号项目'}
    if sk in ('number_hook', 'contrast', 'counter_intuitive'):
        if _NUMBER_RE.search(text or ''):
            return {'id': 'skeleton_fit', 'ok': True, 'severity': 'pass',
                    'detail': '骨架要求的数字/对比锚点在场'}
        return {'id': 'skeleton_fit', 'ok': False, 'severity': 'warn',
                'detail': '这个骨架靠数字或反差立住，但正文里一个数字都没有'}
    if sk == 'dialogue':
        if '？' in (text or '') or '?' in (text or '') or '——' in (text or ''):
            return {'id': 'skeleton_fit', 'ok': True, 'severity': 'pass',
                    'detail': '有问答痕迹'}
        return {'id': 'skeleton_fit', 'ok': False, 'severity': 'warn',
                'detail': '选了「问答对话」但正文里没有一问一答的形状'}
    return {'id': 'skeleton_fit', 'ok': True, 'severity': 'pass',
            'detail': '该骨架没有额外的形状硬要求'}


def _check_forbidden_terms(text: str) -> list[dict]:
    out = []
    for rule_id, label, words in HARD_RULES:
        hits = _hit(words, text or '')
        if hits:
            out.append({'id': rule_id, 'ok': False, 'severity': 'fail',
                        'detail': f'{label}：命中「{"、".join(hits[:5])}」，必须改掉'})
    return out


# ---------------- 主检查入口 ----------------

def review(text: str, combo: dict | None = None, biz_type: str = '',
           min_concrete: float | None = None) -> dict:
    """对单条文案做硬检查，返回 `{checks, verdict, diagnostics, violations, next_actions}`。

    **只判定、不改写**：违禁词只标记并给方向，正文原样返回（见模块 docstring）。
    """
    combo = combo or pb.select_combo(biz_type=biz_type)
    text = text or ''
    floor = (float(min_concrete) if min_concrete is not None
             else float(getattr(config, 'COPY_MIN_CONCRETE_RATIO', 0.5) or 0.5))

    checks = [_check_channel_length(text, combo['channel'],
                                    pb.channel_spec(combo['channel'])['hard_max'])]
    checks.append(_check_slop(text))
    checks.append(_check_generic_tail(text))
    checks.append(_check_concreteness(text, floor))
    checks.append(_check_planning(text, combo))
    checks.append(_check_skeleton_fit(text, combo))
    checks.extend(_check_ai_smell_lines(text, text.splitlines()))

    # 渠道特有禁忌：词表 + 链接正则 + （短句渠道的）标点堆砌
    banned = _hit(CHANNEL_BANNED.get(combo['channel'], ()), text)
    if _URL_RE.search(text or ''):
        banned.append('链接/网址')
    if combo['channel'] in _PUNCT_CHANNELS and punctuation_pileup(text):
        banned.append('标点堆砌')
    if banned:
        checks.append({'id': 'channel_banned', 'ok': False, 'severity': 'fail',
                       'detail': f'这个渠道不能出现：「{"、".join(banned)}」'})
    else:
        checks.append({'id': 'channel_banned', 'ok': True, 'severity': 'pass',
                       'detail': '没有渠道特有禁忌'})
    checks.extend(_check_forbidden_terms(text))

    violations = [c for c in checks if not c['ok']]
    if any(c['severity'] == 'fail' for c in violations):
        verdict = 'fail'
    elif violations:
        verdict = 'warn'
    else:
        verdict = 'pass'
    diagnostics = {
        'chars': len(text), 'lines': count_lines(text), 'emoji': count_emoji(text),
        'concrete_count': pb.count_concrete(text), 'concrete_ratio': concrete_ratio(text),
        # 用**实际生效**的上限（config 与打法库取更严），否则 config 收紧后诊断还显示旧值
        'limit': effective_limit(combo['channel']),
    }
    return {
        'checks': checks, 'verdict': verdict, 'diagnostics': diagnostics,
        'violations': [{'id': c['id'], 'severity': c['severity'], 'detail': c['detail']}
                       for c in violations],
        'next_actions': _next_actions(violations),
        'policy': '只标记不改写：命中的词不会被自动替换，请重出或人工改。',
    }


def _next_actions(violations: list[dict]) -> list[str]:
    """把问题翻成"改哪儿"的动作清单（最多 3 条，按严重度）。"""
    hints = {
        'absolute_terms': '把绝对化用语换成可核对的事实（如"最好吃"→"开了 6 年，回头客占七成"）',
        'medical_claims': '删掉所有功效/疗效表述，只讲食材和做法',
        'finance_promises': '删掉收益承诺，或改成"具体怎么算"的口径',
        'traffic_fake': '删掉诱导话术，把"排队盛况"换成今天的实际出餐量',
        'urgency_fake': '删掉假紧迫感，如果真有限量就写真实份数',
        'channel_banned': '删掉该渠道的禁忌词（外链/全体@/模板称呼）',
        'length': '砍到一个核心信息，其余挪到下一条',
        'ai_slop': '把空话换成具体的事：谁、几块、几点、剩几份',
        'concreteness': '至少加一个真数字或时间',
        'action_hint': '补一句"几点/在哪/多少钱"',
        'generic_tail': '结尾改成具体信息，不用"期待光临"',
        'brand_first': '把第一句改成"事"，店名放到结尾',
        'skeleton_fit': '按选定的骨架补上它要求的形状（编号/数字/问答）',
        'too_many_lines': '压到 3 行以内，只留最想说的一件事',
        'exclaim_stack': '感叹号删到 1 个以内',
        'repeat_phrase': '删掉重复的短语',
    }
    out, seen = [], set()
    for v in sorted(violations, key=lambda x: SEVERITY_ORDER.get(x['severity'], 9)):
        hint = hints.get(v['id'])
        if hint and hint not in seen:
            seen.add(hint)
            out.append(hint)
        if len(out) >= 3:
            break
    return out




# 配图方案与交付报告见 copy_report.py（搬家，API 不变）。
# 用模块级 __getattr__ 惰性转发，**不**顶层 import —— copy_report 需要本模块的 review()，
# 两边顶层互相 import 会 "partially initialized module"（本仓库已踩过两次，故统一用这个写法）。
_REPORT_EXPORTS = ("build_image_plan", "build_report")


def __getattr__(name):  # noqa: D401  —— 模块级惰性转发（PEP 562）
    if name in _REPORT_EXPORTS:
        from copy_report import __dict__ as _d
        return _d[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
