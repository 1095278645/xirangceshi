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
           'count_lines', 'count_emoji']

# ---------------- 规则表 ----------------
# 说明：这里只放**能靠字符串判定**的规则。语义类判断（有没有说清卖点）交给 AI 员工，
# 但结论同样进报告，不让它变成一句没人看的评语。

# (规则 id, 人话说明, 词表 / 类别)
HARD_RULES = (
    ('absolute_terms', '绝对化用语（广告法高风险）',
     ('最好', '第一', '最佳', '最优', '最便宜', '最快', '最强', '国家级', '世界级',
      '顶级', '极品', '完美', '绝对', '永远', '唯一', '100%', '百分百', '全网最低',
      '史上最低', '独一无二', '无敌', '首个', '独家')),
    ('medical_claims', '医疗/功效暗示（食品、日用都不能说）',
     ('治疗', '治愈', '根治', '药效', '疗效', '消炎', '抗癌', '降血压', '降血糖',
      '减肥', '瘦身', '排毒', '养生', '增强免疫', '包治', '祖传秘方')),
    ('finance_promises', '收益承诺（理财/贷款类不能承诺）',
     ('保本', '保收益', '稳赚', '包赚', '无风险', '高回报', '零风险', '必涨')),
    ('traffic_fake', '诱导或虚假流量话术',
     ('转发抽奖', '集赞', '砍一刀', '私信我', '关注我', '点击下方', '刷屏',
      '排队两小时', '天天爆满', '全网疯抢')),
    ('urgency_fake', '虚假紧迫感',
     ('最后一天', '最后机会', '仅此一次', '错过不再', '限时秒杀', '马上涨')),
)

# AI 味 + 空话：词表来自 copy_playbook（唯一真源），这里只管判定与计数
SLOP_AND_ABSOLUTE = pb.NO_SLOP_WORDS

# 渠道特有禁忌（渠道规则来自打法库，这里落成可判定的词）
CHANNEL_BANNED = {
    'moments': ('@全体成员', 'http://', 'https://', '会员日', '感恩回馈'),
    'xiaohongshu': ('家人们', '宝子们', '求三连', '一键三连'),
    'douyin': ('点击下方', '关注我', '链接在评论区'),
    'wechat_group': ('各位老板', '亲们', 'http://', 'https://'),
    'signboard': ('，', '；'),
    'groupbuy': ('最终解释权', '解释权归'),
    'reply': ('亲，', '尊敬的顾客'),
}

SEVERITY_ORDER = {'fail': 0, 'warn': 1, 'pass': 2, 'skip': 3}

_EMOJI_RE = re.compile(
    '[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\u2b00-\u2bff]')
_NUMBER_RE = re.compile(r'\d')
_LIST_MARK_RE = re.compile(r'[①②③④⑤⑥⑦⑧⑨⑩]|^\s*[\d]+[.、)]|^\s*[-·•]', re.M)
_PROMO_TAIL_RE = re.compile(r'期待(您的|你的)?(光临|惠顾)|欢迎光临|谢谢惠顾')


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
    return [w for w in words if w and w in text]


def _check_channel_length(text: str, channel: str, hard_max: int) -> dict:
    """字数：渠道上限取「配置阈值」与「打法库声明」里更严的那个（避免两处口径打架）。"""
    cfg_key = {'moments': 'COPY_MOMENTS_MAX_CHARS', 'xiaohongshu': 'COPY_XHS_BODY_MAX_CHARS',
               'douyin': 'COPY_DOUYIN_MAX_CHARS', 'wechat_group': 'COPY_GROUP_MAX_CHARS',
               'signboard': 'COPY_SIGNBOARD_MAX_CHARS', 'groupbuy': 'COPY_GROUPBUY_MAX_CHARS',
               'reply': 'COPY_REPLY_MAX_CHARS'}.get(channel, '')
    cfg_max = int(getattr(config, cfg_key, hard_max) or hard_max) if cfg_key else hard_max
    limit = min(cfg_max, hard_max)
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
    hits = pb.strip_slop(text)
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

    banned = _hit(CHANNEL_BANNED.get(combo['channel'], ()), text)
    if banned:
        checks.append({'id': 'channel_banned', 'ok': False, 'severity': 'fail',
                       'detail': f'这个渠道不能出现：「{"、".join(banned)}」'})
    else:
        checks.append({'id': 'channel_banned', 'ok': True, 'severity': 'pass',
                       'detail': '没有渠道特有禁忌词'})
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
        'limit': pb.channel_spec(combo['channel'])['hard_max'],
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


# ---------------- 配图方案（把参考 skill 的"封面/内容/结尾"搬过来） ----------------
# 只产出**可执行的图片提示词与排版建议**，不调用任何图片生成能力。

_IMAGE_STYLE_BY_CHANNEL = {
    'moments': '手机随手拍风格：自然光、真实桌面、轻微噪点，不要精修海报感',
    'xiaohongshu': '干净实物特写 + 大字标题：暖色、留白多、字像手写贴纸',
    'douyin': '竖屏封面：主体占 2/3，画面留出压字位置，高对比',
    'wechat_group': '实拍直出即可：看得清数量与状态，不用排版',
    'signboard': '纯文字排版：一个字也不能挤，远看可辨',
    'groupbuy': '商品平铺 + 价签：信息清晰优先，别做氛围图',
    'reply': '不用配图',
}


def build_image_plan(combo: dict, shop_name: str = '', extra: str = '',
                     texts: list[str] | None = None, count: int | None = None) -> dict:
    """按渠道给出配图方案（封面 / 内容 / 结尾），每张含画面、图上文字、比例、提示词。

    参考 baoyu-xhs-images 的"封面钩子 → 内容承载 → 结尾 CTA"三段式，
    但**不生成图**：给的是店主/摄影能直接照做的描述，以及可喂给绘图模型的提示词。
    小店的现实是"拿手机拍一张"，所以默认只要求 1~2 张，不做系列强制。
    """
    ch = combo['channel']
    style = _IMAGE_STYLE_BY_CHANNEL.get(ch, '真实手机实拍')
    texts = [t for t in (texts or []) if t]
    n = int(count) if count else (1 if ch in ('moments', 'wechat_group', 'reply', 'signboard') else 3)
    n = max(1, min(n, 3))
    plan = []
    if ch == 'reply':
        return {'required': False, 'count': 0, 'style': style, 'items': [],
                'note': '评价回复不需要配图；配了反而像模板回复。'}
    titles = ('封面', '内容', '结尾')[:n]
    for i, label in enumerate(titles):
        head = (extra or shop_name or '今天的东西').strip()
        if label == '封面':
            on_image = _cover_words(head)
            scene = f'主体：{head}；环境：{shop_name or "店里"} 的真实场景，不要摆拍道具'
        elif label == '内容':
            on_image = _content_words(head)
            scene = '细节特写：分量、切面、价签、手上正在做的动作（选一个）'
        else:
            on_image = ''
            scene = '收尾画面：摊位/店面全貌或收摊前的空筐，传递"今天就这样"'
        plan.append({
            'index': i + 1, 'role': label, 'on_image_words': on_image,
            'scene': scene, 'ratio': '1:1' if ch == 'xiaohongshu' else '3:4',
            'prompt': f'{style}。{scene}。图上文字：{on_image or "不加字"}。'
                      f'不要水印，不要过度修图，不要出现不存在的食材。',
        })
    return {
        'required': True, 'count': n, 'style': style, 'items': plan,
        'note': ('封面把"事"放在画面里；图上文字只放一个钩子，'
                 '别把正文全塞进图里（手机上看不清）。'),
        'text_hint': (texts[0][:40] + '…') if texts else '',
    }


def _cover_words(head: str) -> str:
    """封面图上文字：最多两个字的分量词 + 事由，避免整句塞进画面。"""
    head = (head or '').strip()
    if not head:
        return '今天有'
    return head[:10]


def _content_words(head: str) -> str:
    return '现做' if head else '分量'


# ---------------- 交付报告（对应参考 skill 的 completion report） ----------------

def build_report(combo: dict, texts: list[str], team: dict | None = None,
                 shop_name: str = '', extra: str = '', biz_type: str = '',
                 image_count: int | None = None,
                 candidates: list[str] | None = None) -> dict:
    """组装交付报告：用了什么组合、正文候选、逐条检查、配图方案、下一步。

    - `candidates` 是模型给出的**全部**稿子（含未进入检查的备选），原样保留 ——
      备选稿是给店主挑的，不能因为"只检查主文案"就丢掉（实测丢过一次，被既有用例抓到）。
    - `reviews` 只对 `texts`（默认 = 去重后的 candidates）逐条跑；`verdict` 取**最差**的一条，
      避免"三条里有一条不合规"被平均掉。
    """
    cands = [t for t in (candidates if candidates is not None else texts) or []
             if (t or '').strip()]
    texts = [t for t in (texts or []) if (t or '').strip()]
    if not texts:
        texts = cands[:1]
    reviews = [review(t, combo, biz_type=biz_type) for t in texts]
    worst = 'pass'
    for r in reviews:
        if SEVERITY_ORDER.get(r['verdict'], 9) < SEVERITY_ORDER.get(worst, 9):
            worst = r['verdict']
    return {
        'combo': pb.combo_summary(combo),
        'texts': texts,
        'candidates': cands or texts,
        'primary': texts[0] if texts else '',
        'reviews': reviews,
        'verdict': worst,
        'image_plan': build_image_plan(combo, shop_name, extra, texts, image_count),
        'team_adopted': (team or {}).get('adopted', []),
        'note': ('这是"发布方案"：正文 + 为什么这么写 + 配图建议 + 发布前要改的地方。'
                 '检查只标记不改写，违禁词需要你自己确认。'),
    }
