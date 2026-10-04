"""copy_report.py — 配图方案 + 交付自检报告（从 copy_review 外移）

为什么拆：`copy_review.py` 已到 400 行硬上限（arch_check LINE_FAIL）。这里只做"搬家"，
把**产出物**（配图方案、交付报告）与**判定逻辑**（review 的硬检查）分开：
判定要尽量小、好测；产出物会长，且改版频繁。

`copy_review.build_image_plan / build_report` 仍原样可用（末尾 re-export）。
"""
from __future__ import annotations

import copy_review as _review

__all__ = ['build_image_plan', 'build_report']

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
    text_hint = (texts[0][:40] + '…') if texts else ''      # 统一 schema：所有分支都带这个键
    plan = []
    if ch == 'reply':
        return {'required': False, 'count': 0, 'style': style, 'items': [],
                'text_hint': text_hint,
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
    reviews = [_review.review(t, combo, biz_type=biz_type) for t in texts]
    worst = 'pass'
    for r in reviews:
        if _review.SEVERITY_ORDER.get(r['verdict'], 9) < _review.SEVERITY_ORDER.get(worst, 9):
            worst = r['verdict']
    return {
        'combo': _review.pb.combo_summary(combo),
        'texts': texts,
        'candidates': cands or texts,
        'primary': texts[0] if texts else '',
        'reviews': reviews,
        'verdict': worst,
        'image_plan': build_image_plan(combo, shop_name, extra, texts, image_count),
        # team 与 gene_id 是**既有响应键名**（前端与旧调用读 team.employees），
        # 独立复核实测漏掉后 `/api/insights` 的 team 恒为 {}、gene_id 恒为 None —— 接口契约回归。
        'team': team or {},
        'team_adopted': (team or {}).get('adopted', []),
        'gene_id': (team or {}).get('gene_id'),
        'note': ('这是"发布方案"：正文 + 为什么这么写 + 配图建议 + 发布前要改的地方。'
                 '检查只标记不改写，违禁词需要你自己确认。'),
    }
