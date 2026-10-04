"""copy_playbook.py — 文案打法库（声明式）：渠道 × 骨架 × 语气 → 组合出可解释的文案

## 为什么需要这一层

原先的文案能力是「两个员工 + 一段长提示词 + 碰运气出 3 条」：
配方、渠道、结构、禁用词全糊在提示词字符串里，改一个词要动源码，也没法回答
「这条为什么这么写」。而成熟的生成型 skill（如 baoyu-xhs-images 的
12 样式 × 8 布局 × 3 配色 + 预设 + 自动选型）之所以稳定，是因为它把
**「怎么写」拆成了可自由组合的正交维度**，并给每个场景准备了配方缩写。

这一层把那套方法论搬到文案上（换维度的**内容**，不是换形式）：

    渠道 channel（发到哪：朋友圈/小红书/抖音/微信群/招牌/团购/回评）
  × 骨架 skeleton（怎么组织：场景移植/痛点开场/清单/对比反转/数字钩子/问答/反常识）
  × 语气 tone（用什么口气：街坊口语/种草活力/群通知干脆/叫卖直接/回评诚恳/专业克制）
  = 一条可解释的文案；配方 recipe 是「常用组合」的缩写。

## 设计约定

- **纯数据 + 纯函数**：不调 AI、不联网、不读库。选型与拼装提示词都是可单测的确定性逻辑。
- **有限取值一律常量元组**（铁律5）：`CHANNELS` / `SKELETONS` / `TONES` / `RECIPES`，
  `schemas.py` 的 Literal 与之一致，由 `tests/test_copy_playbook.py` 回归。
- **不改既有降级文案**：本模块只产出「提示词片段 + 交付报告」，落笔仍在 `team_domain_copy`。
- **阈值不写魔法数**：字数上限等走 `config.COPY_*`（这里只声明结构与纪律）。
- 本文件里的内容字符串统一用单引号，避免中文语境下频繁出现的内嵌双引号。
"""
from __future__ import annotations

import re

__all__ = [
    'HARD_RULES', 'CHANNELS', 'SKELETONS', 'TONES', 'RECIPES', 'CONTENT_INDUSTRIES',
    'CHANNEL_SPECS', 'SKELETON_SPECS', 'TONE_SPECS', 'RECIPE_SPECS',
    'PALETTE_SPECS', 'NO_SLOP_WORDS', 'channel_spec', 'skeleton_spec',
    'tone_spec', 'recipe_spec', 'select_combo', 'build_brief', 'combo_summary',
    'strip_slop', 'count_concrete', 'infer_biz_type',
]

# ---------------- 维度取值（唯一真源） ----------------

CHANNELS = ('moments', 'xiaohongshu', 'douyin', 'wechat_group', 'signboard',
            'groupbuy', 'reply')
SKELETONS = ('scene_transplant', 'pain_open', 'listicle', 'contrast',
             'number_hook', 'dialogue', 'counter_intuitive')
TONES = ('neighbor', 'promo_bright', 'group_notice', 'shout', 'reply_sincere',
         'pro_plain')
RECIPES = ('moments_daily', 'moments_soft_ad', 'xhs_note', 'xhs_guide',
           'douyin_shout', 'group_notice', 'group_urge', 'board_one_line',
           'reply_sincere', 'review_answer')
# 业态：只影响「举什么例子、避什么坑」，不改变结构
CONTENT_INDUSTRIES = ('餐饮', '零售', '生鲜', '服务', '饮品', '摆摊')

# ---------------- 渠道（发到哪 = 平台硬约束） ----------------
# 上限阈值走 config.COPY_*_MAX_CHARS；这里只声明结构与纪律。

CHANNEL_SPECS: dict[str, dict] = {
    'moments': {
        'name': '朋友圈',
        'scene_hint': '老客刷手机时随口一看',
        'soft_max': 100, 'hard_max': 220,
        'line_hint': '1 段，最多 3 行',
        'emoji': '可 0~2 个，不许堆',
        'structure': '第一行必须让人停一下（具体的事或具体的数）；'
                     '第二行给理由或口味细节；结尾给一个可核对的硬信息（剩几份/几点收摊/今天的价）',
        'avoid': '不许活动预告腔（感恩回馈、会员日）；不许 @ 全体；不许放链接；'
                 '不许把店名当第一句（开头要是事，不是招牌）',
        'best_for': '日常营业、上新、熟客互动',
    },
    'xiaohongshu': {
        'name': '小红书正文',
        'scene_hint': '陌生人搜索或刷到时决定要不要看',
        'soft_max': 600, 'hard_max': 1000,
        'line_hint': '短段落，每段 1~2 行；小标题最多 6 行一段',
        'emoji': '每个小标题前最多 1 个，正文里不撒',
        'structure': '开头 2 行说清「这店在哪、吃什么买什么、多少钱」；'
                     '中段 2~3 个小标题讲具体体验（口味/分量/等多久/老板怎么做）；'
                     '结尾一句店铺信息（位置+营业时间），不放促销电话',
        'avoid': '不许「家人们」「宝子们」式套话；不许编造排队盛况；'
                 '不许医疗或功效暗示；不许绝对化用语',
        'best_for': '新客种草、把路边店讲给陌生人',
    },
    'douyin': {
        'name': '抖音口播/视频文案',
        'scene_hint': '手指一划就过去的 3 秒',
        'soft_max': 90, 'hard_max': 200,
        'line_hint': '1~2 行，前 8 个字定生死',
        'emoji': '0 个（口播不看表情）',
        'structure': '开头 8 字内抛出钩子（数字/反差/疑问）；中段一句话说清卖点；'
                     '结尾给动作（几点来、在哪、今天多少份）',
        'avoid': '不许堆平台话术（点击下方、关注我）；不许标题党式虚假承诺',
        'best_for': '短视频文案、口播稿',
    },
    'wechat_group': {
        'name': '微信群发',
        'scene_hint': '已经被拉进群的老客，一句话扫过',
        'soft_max': 90, 'hard_max': 180,
        'line_hint': '1~2 行，不刷屏（一次只说一件事）',
        'emoji': '最多 1 个',
        'structure': '直接给事：什么、多少、什么时候；语气像在群里喊一声',
        'avoid': '不许「各位老板」「亲们」；不许连发多条；不许放外链',
        'best_for': '到货通知、开团、临时闭店',
    },
    'signboard': {
        'name': '招牌/灯箱/贴纸短句',
        'scene_hint': '路过 2 秒扫一眼',
        'soft_max': 12, 'hard_max': 20,
        'line_hint': '1 行，最多两行（大字优先）',
        'emoji': '0 个',
        'structure': '只有「卖什么+多少/几点」两件事，动词开头最好',
        'avoid': '不许标点堆砌；不许写需要读第二遍才懂的句子',
        'best_for': '门口牌、价签、摊位招牌',
    },
    'groupbuy': {
        'name': '团购/拼团说明',
        'scene_hint': '已进店、正在看商品详情',
        'soft_max': 180, 'hard_max': 300,
        'line_hint': '3~5 行：内容 → 价格 → 使用规则',
        'emoji': '0 个',
        'structure': '第一行说清包含什么；第二行价格与对比；第三行使用规则（有效期/时段/可否叠加）',
        'avoid': '不许留模糊规则；不许虚构原价；不许绝对化用语（广告法风险最高的渠道）',
        'best_for': '上团购、写套餐说明',
    },
    'reply': {
        'name': '评价回复',
        'scene_hint': '陌生人在看你有没有把差评当回事',
        'soft_max': 100, 'hard_max': 160,
        'line_hint': '2~3 句：一句承认、一句解释、一句动作',
        'emoji': '0 个',
        'structure': '先认事实（不辩解）→ 说清原因或已经改了什么 → 给下次的具体安排',
        'avoid': '不许怼顾客；不许「已了解情况」式模板话；不许承诺兑现不了的补偿',
        'best_for': '差评回复、好评致谢',
    },
}

# ---------------- 骨架（怎么组织 = 内容结构） ----------------

SKELETON_SPECS: dict[str, dict] = {
    'scene_transplant': {
        'name': '场景移植',
        'concept': '让店里的某样东西开口说话，读者自己推断你在干嘛',
        'shape': '物件/招牌/收银台的一句 → 事实 → 落点',
        'best_for': '日常营业、门口小景、招牌菜',
        'example': '收银台说：今天第 50 次听到「随便看看」。',
        'watch': '必须真有这物件，不能编；一句就够，别铺陈',
    },
    'pain_open': {
        'name': '痛点开场',
        'concept': '先说顾客此刻的具体麻烦，再说你怎么解决',
        'shape': '具体麻烦（时间/场景/身体感受）→ 你做了什么 → 现在可以怎么办',
        'best_for': '早餐、加班餐、雨天、带娃、赶时间',
        'example': '早上七点半，等公交的人最怕迟到——包子 5 分钟出一笼。',
        'watch': '麻烦要是真痛点（迟到、饿、冷），不是「想吃点好的」这种伪需求',
    },
    'listicle': {
        'name': '清单罗列',
        'concept': '把内容拆成 3~5 条短清单，一条一个点',
        'shape': '一句总起 → 3~5 条（每条 ≤15 字，带数字）→ 一句收尾',
        'best_for': '今日有什么、菜单、排行榜、价目',
        'example': '今天出这几样：①卤面 ②牛肉面 ③小笼 ④豆浆',
        'watch': '条数别超 5；每条都要有具体名或数，不许「多种选择」',
    },
    'contrast': {
        'name': '对比反转',
        'concept': '先立一个大家都会的旧做法，再用你的做法反转',
        'shape': '别人怎么做 → 我怎么做 → 为什么值得多等一下或多花一点',
        'best_for': '手艺、现做、用料、慢工',
        'example': '别人用高汤粉，我们熬四小时——贵两块，你自己尝。',
        'watch': '不许贬低同行到具体人或具体店；对比要落在可验证的事上（时间/材料/克数）',
    },
    'number_hook': {
        'name': '数字钩子',
        'concept': '用一个真数字当锚，让人有画面',
        'shape': '数字 + 它对应的具体物 → 一句解释 → 动作',
        'best_for': '现做数量、斤两、价格、时长',
        'example': '6 斤肉，包 120 个，卖完收摊。',
        'watch': '数字必须真（能对上库存或流水口径），不许凑整好看',
    },
    'dialogue': {
        'name': '问答对话',
        'concept': '模拟老板和顾客的一问一答，把卖点写成对话',
        'shape': '问一句 → 答一句（2~3 轮），最后一行给硬信息',
        'best_for': '熟客口味、常被问的问题、解释误会',
        'example': '「你家辣椒辣吗？」——「你自己试，不行我给你换。」',
        'watch': '对白要像真说的，别写成客服问答；问答里也要有具体数字或细节',
    },
    'counter_intuitive': {
        'name': '反常识',
        'concept': '一句让人意外的话，再解释为什么这么做',
        'shape': '反常识一句 → 理由（具体到做法或成本）→ 落点',
        'best_for': '主动劝退、限量、不接单、不降价',
        'example': '今天不接 20 份以上的单——一个人做不过来，焦了我不赔。',
        'watch': '反常识要真有理由，不能只是标题党',
    },
}

# ---------------- 语气（用什么口气 = 调色板） ----------------

TONE_SPECS: dict[str, dict] = {
    'neighbor': {
        'name': '街坊口语',
        'feel': '像街坊隔着柜台说话',
        'rules': '短句为主；用「你」不用「您」；可以自嘲；不喊口号；一句只讲一件事；'
                 '不用感叹号堆情绪（最多 1 个）',
        'banned_register': '客服腔、广告腔、公文腔',
    },
    'promo_bright': {
        'name': '种草活力',
        'feel': '像熟人推荐给朋友，带点小兴奋但不夸张',
        'rules': '可以用 1~2 个轻 emoji；多给「怎么吃/怎么用」的具体画面；'
                 '形容词只留最能说明问题的那个；不喊「绝了」「封神」',
        'banned_register': '网红黑话、夸张承诺',
    },
    'group_notice': {
        'name': '群通知干脆',
        'feel': '群里喊一声，一秒扫完',
        'rules': '信息前置：时间/数量/价格放最前；不用修饰语；一条只说一件事',
        'banned_register': '寒暄、打扰大家',
    },
    'shout': {
        'name': '叫卖直接',
        'feel': '老式叫卖，短促有力',
        'rules': '动词开头；一句话写完；不解释；允许重复关键词一次',
        'banned_register': '书面语、从句',
    },
    'reply_sincere': {
        'name': '回评诚恳',
        'feel': '把顾客的话当真，先说事实再给安排',
        'rules': '先承认具体问题（不复述恶意评论）；不辩解；结尾给可执行的下一步',
        'banned_register': '官方声明腔、非常重视',
    },
    'pro_plain': {
        'name': '专业克制',
        'feel': '懂行的人把事说明白，不推销',
        'rules': '只讲事实与做法；不形容口味（让顾客自己判断）；数字准确到个位',
        'banned_register': '夸饰、感官堆砌',
    },
}

# ---------------- 配方（渠道+骨架+语气的常用组合，一键选） ----------------

RECIPE_SPECS: dict[str, dict] = {
    'moments_daily': {
        'name': '朋友圈·日常营业', 'channel': 'moments',
        'skeleton': 'scene_transplant', 'tone': 'neighbor',
        'best_for': '每天一条日常，不推销',
    },
    'moments_soft_ad': {
        'name': '朋友圈·软性上新', 'channel': 'moments',
        'skeleton': 'number_hook', 'tone': 'neighbor',
        'best_for': '上新、今日限量',
    },
    'xhs_note': {
        'name': '小红书·探店笔记', 'channel': 'xiaohongshu',
        'skeleton': 'pain_open', 'tone': 'promo_bright',
        'best_for': '让陌生人知道在哪吃、买什么',
    },
    'xhs_guide': {
        'name': '小红书·攻略清单', 'channel': 'xiaohongshu',
        'skeleton': 'listicle', 'tone': 'pro_plain',
        'best_for': '菜单攻略、避坑清单',
    },
    'douyin_shout': {
        'name': '抖音·口播钩子', 'channel': 'douyin',
        'skeleton': 'number_hook', 'tone': 'shout',
        'best_for': '短视频文案与口播稿',
    },
    'group_notice': {
        'name': '群通知·到货开团', 'channel': 'wechat_group',
        'skeleton': 'listicle', 'tone': 'group_notice',
        'best_for': '到货、开团、临时闭店',
    },
    'group_urge': {
        'name': '群通知·催一下', 'channel': 'wechat_group',
        'skeleton': 'number_hook', 'tone': 'group_notice',
        'best_for': '剩得不多、快收摊',
    },
    'board_one_line': {
        'name': '招牌·一句话', 'channel': 'signboard',
        'skeleton': 'number_hook', 'tone': 'shout',
        'best_for': '门口牌、价签',
    },
    'reply_sincere': {
        'name': '回评·诚恳', 'channel': 'reply',
        'skeleton': 'dialogue', 'tone': 'reply_sincere',
        'best_for': '差评或好评回复',
    },
    'review_answer': {
        'name': '团购·套餐说明', 'channel': 'groupbuy',
        'skeleton': 'listicle', 'tone': 'pro_plain',
        'best_for': '上团购、写规则',
    },
}

# ---------------- 文案公式（句式，供提示词引用；不是渠道） ----------------

PALETTE_SPECS: dict[str, str] = {
    'scene_transplant': '让物件开口：「收银台说：…」——读者自己推断',
    'yiji': '宜忌体：「宜|加辣 忌|减肥」，四字为佳，极易栏目化',
    'reverse_restraint': '反向克制：不搞花活，「今天店开着，随时来」',
    'number_pun': '数字双关：热点自带数字，在你的语境里另有含义',
    'countdown': '倒计时：「还有 8 份」「5 点收摊」——把库存讲成时间',
    'detail_first': '先具体后感觉：先说「肉包 6 块」，再说「刚出笼」',
}

# ---------------- AI 味禁用词（硬规则，见 copy_review） ----------------

NO_SLOP_WORDS = (
    '开启', '新体验', '魅力', '遇见', '美好', '甄选', '匠心', '极致',
    '私享', '赋能', '闭环', '底层逻辑', '品效合一', '绝了', '封神', 'yyds',
    '家人们', '宝子们', '各位老板', '亲们', '感恩回馈', '重磅来袭', '震撼',
    '致力于', '旨在', '为您', '尊享', '一站式',
)

# 业态关键词（从场景/补充里猜业态，只为选兜底配方与举例）
_BIZ_KEYWORDS = {
    '餐饮': ('面', '饭', '包子', '馒', '饺', '菜', '汤', '小吃', '快餐', '食堂'),
    '饮品': ('奶茶', '咖啡', '豆浆', '柠檬茶', '果茶', '饮品', '冰'),
    '生鲜': ('菜市场', '蔬菜', '水果', '肉', '鱼', '海鲜', '生鲜', '鸡蛋'),
    '零售': ('超市', '便利', '日用', '烟酒', '五金', '服装', '鞋', '文具'),
    '服务': ('理发', '修', '洗', '按摩', '美甲', '维修', '照', '家政'),
    '摆摊': ('摊', '推车', '夜市', '赶集', '早市'),
}


# ---------------- 取规格 ----------------

def channel_spec(channel: str) -> dict:
    """取渠道规格；未知渠道抛 ValueError（不静默兜底，铁律5）。"""
    if channel not in CHANNEL_SPECS:
        raise ValueError(f'非法渠道：{channel!r}（合法值：{CHANNELS}）')
    return CHANNEL_SPECS[channel]


def skeleton_spec(skeleton: str) -> dict:
    if skeleton not in SKELETON_SPECS:
        raise ValueError(f'非法骨架：{skeleton!r}（合法值：{SKELETONS}）')
    return SKELETON_SPECS[skeleton]


def tone_spec(tone: str) -> dict:
    if tone not in TONE_SPECS:
        raise ValueError(f'非法语气：{tone!r}（合法值：{TONES}）')
    return TONE_SPECS[tone]


def recipe_spec(recipe: str) -> dict:
    if recipe not in RECIPE_SPECS:
        raise ValueError(f'非法配方：{recipe!r}（合法值：{RECIPES}）')
    return dict(RECIPE_SPECS[recipe])


def infer_biz_type(text: str) -> str:
    """从文本里猜业态（猜不出返回空串）。

    命中多个业态时**取命中最长关键词的那个** —— 短词会误伤：实测「菜市场的青菜」
    同时命中餐饮的「菜」与生鲜的「菜市场」，按字典顺序会错判成餐饮。
    """
    haystack = text or ''
    best, best_len = '', 0
    for biz, words in _BIZ_KEYWORDS.items():
        for w in words:
            if w and w in haystack and len(w) > best_len:
                best, best_len = biz, len(w)
    return best


def strip_slop(text: str) -> list[str]:
    """返回文本里命中的空话套话词（供硬检查用，**不改写**正文）。"""
    if not text:
        return []
    return [w for w in NO_SLOP_WORDS if w in text]


def count_concrete(text: str) -> int:
    """数一数文本里有几个「具体物」（数字/金额/时间/数量）——具体度是文案的生命线。"""
    if not text:
        return 0
    hits = re.findall(
        r'\d+(?:\.\d+)?\s*(?:元|块|点|分|个|份|斤|两|杯|碗|笼|包|人|天|小时|分钟|号|折)?',
        text)
    hits += re.findall(r'(?:早上|中午|下午|晚上|今天|明天|本周|周末|点半)', text)
    return len(hits)

# 导演层（选型/写法说明/组合摘要）见 copy_director.py（搬家，API 不变）。
# 这里用模块级 __getattr__ **惰性转发**，而不是顶层 import —— 顶层 import 会成环
# （director 依赖本模块的声明，本模块又要在加载期导入 director，实测报
#  "partially initialized module"）。PEP 562 的 __getattr__ 在首次访问时才解析，
# 因此 `copy_playbook.select_combo(...)` 照旧可用，`mock.patch` 也能正常工作。
_DIRECTOR_EXPORTS = ("select_combo", "build_brief", "combo_summary",
                     "detect_intent", "recipe_combo")


def __getattr__(name):  # noqa: D401  —— 模块级惰性转发（PEP 562）
    if name in _DIRECTOR_EXPORTS:
        from copy_director import __dict__ as _d
        return _d[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# 合规硬规则词表见 copy_rules.py（声明层，独立文件便于按平台规则迭代）。
from copy_rules import HARD_RULES  # noqa: E402,F401
