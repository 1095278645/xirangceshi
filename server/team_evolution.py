"""team_evolution.py — 进化层桥接模块（基因种子 + 结果记录 + 状态摘要）

从 team_domains.py 和 evolution.py 拆出，避免单文件超过 400 行（arch_check L1 约束）。
职责：初始基因种子入库、用户行为结果记录转发、进化状态摘要生成。

依赖方向：team_evolution → evolution → db_evolution（无循环依赖）。
"""
import db_evolution as dbe
import evolution

__all__ = ["seed_initial_genes", "record_outcome", "get_evolution_summary",
           "update_insight_index", "extract_common_signals", "extract_content_pattern"]


# ---------------- 初始基因种子 ----------------

_INITIAL_GENES = [
    {
        "gene_id": "gene_copy_scene_transplant",
        "domain": "copy",
        "trigger_signals": ["开业", "新店", "促销", "上新"],
        "system_prompt_addon": "使用场景移植公式：让店里某个物件开口说话，读者自行推断。",
        "category": "innovate",
    },
    {
        "gene_id": "gene_copy_yiji",
        "domain": "copy",
        "trigger_signals": ["日常", "营业", "今日", "宜忌"],
        "system_prompt_addon": "使用宜忌体公式：老黄历格式，四字为佳，极低制作成本，极易栏目化。",
        "category": "innovate",
    },
    {
        "gene_id": "gene_copy_reverse_restraint",
        "domain": "copy",
        "trigger_signals": ["节假日", "简约", "朴素", "真诚"],
        "system_prompt_addon": "使用反向克制公式：不耍花活说真话，朴素一句话比十句花活有人情味。",
        "category": "innovate",
    },
    {
        "gene_id": "gene_copy_number_pun",
        "domain": "copy",
        "trigger_signals": ["热点", "数字", "双关", "节日"],
        "system_prompt_addon": "使用数字双关公式：热点自带数字，数字在你的语境里另有含义。",
        "category": "innovate",
    },
    {
        "gene_id": "gene_store_diagnose",
        "domain": "store",
        "trigger_signals": ["诊断", "经营", "评分", "保本"],
        "system_prompt_addon": "综合现金流/经营/风控三维视角进行单店经营诊断。",
        "category": "reinforce",
    },
    {
        "gene_id": "gene_store_margin_alert",
        "domain": "store",
        "trigger_signals": ["毛利率", "异常", "成本", "利润"],
        "system_prompt_addon": "毛利率异常预警：>90% 或 <5% 时标记为异常，检查成本结构。",
        "category": "repair",
    },
]


def seed_initial_genes():
    """初始化基因库：将静态公式/策略导入 agent_genes 表。
    幂等：已存在的基因不覆盖统计（只更新 prompt_addon）。
    """
    for g in _INITIAL_GENES:
        existing = dbe.get_gene(g["gene_id"])
        if existing:
            dbe.save_gene(
                gene_id=g["gene_id"], domain=g["domain"],
                trigger_signals=g["trigger_signals"],
                system_prompt_addon=g["system_prompt_addon"],
                confidence=existing.get("confidence", 0.5),
                success_count=existing.get("success_count", 0),
                failure_count=existing.get("failure_count", 0),
                consecutive_inert=existing.get("consecutive_inert", 0),
                status=existing.get("status", "active"),
                category=g["category"],
                is_distilled=existing.get("is_distilled", 0),
            )
        else:
            dbe.save_gene(
                gene_id=g["gene_id"], domain=g["domain"],
                trigger_signals=g["trigger_signals"],
                system_prompt_addon=g["system_prompt_addon"],
                category=g["category"],
            )
    return len(_INITIAL_GENES)


def record_outcome(domain, gene_id, content, user_adopted=False,
                   user_edited=False, edit_diff=None, task_context=None):
    """记录用户行为结果（采纳/修改/跳过）→ Capsule + Event + 基因统计更新。
    对接前端：用户选了一条变体 → user_adopted=True；修改了 → user_edited=True。"""
    return evolution.record_outcome(
        domain=domain, gene_id=gene_id, content=content,
        user_adopted=user_adopted, user_edited=user_edited,
        edit_diff=edit_diff, task_context=task_context,
    )


def get_evolution_summary(domain):
    """获取某域的进化状态摘要（供 API / 前端展示）"""
    genes = dbe.get_all_genes(domain)
    active = [g for g in genes if g.get("status") == "active"]
    suppressed = [g for g in genes if g.get("status") == "suppressed"]
    capsules = dbe.get_recent_capsules(domain, limit=20)
    adopted = [c for c in capsules if c.get("user_adopted")]
    learnings = dbe.get_learnings(domain)
    pending = [l for l in learnings if l.get("status") == "open"]
    promoted = [l for l in learnings if l.get("status") == "promoted"]

    return {
        "domain": domain,
        "genes_total": len(genes),
        "genes_active": len(active),
        "genes_suppressed": len(suppressed),
        "capsules_recent": len(capsules),
        "capsules_adopted": len(adopted),
        "adoption_rate": len(adopted) / len(capsules) if capsules else 0,
        "learnings_total": len(learnings),
        "learnings_pending": len(pending),
        "learnings_promoted": len(promoted),
        "strategy": evolution.auto_strategy(domain),
        "insight_index": dbe.get_insight_index(domain),
    }


# ---------------- L1 索引更新 + 蒸馏辅助（从 evolution.py 拆出，避免循环依赖） ----------------

def update_insight_index(domain):
    """重建某域的 L1 极简索引（<=20 行，每行 <80 字符）"""
    lines = []

    # 从基因库提取摘要
    genes = dbe.get_all_genes(domain)
    active_genes = [g for g in genes if g.get("status") == "active"]
    if active_genes:
        top = sorted(active_genes, key=lambda g: g.get("confidence", 0), reverse=True)[:3]
        for g in top:
            signals = ", ".join(g.get("trigger_signals", [])[:3])
            conf = g.get("confidence", 0)
            line = f"{domain}: {signals or g['gene_id']} | conf={conf:.2f}"
            lines.append(line[:80])

    # 从晋升的经验中提取
    promoted = dbe.get_learnings(domain, status="promoted", limit=5)
    for p in promoted:
        pk = p.get("pattern_key", "")
        line = f"{domain}: {pk} -> L2:promoted_{pk}"
        lines.append(line[:80])

    # 从 domain_context 中提取 SOP 引用
    from db_arch import list_domain_context
    ctx_items = list_domain_context(domain)
    for item in ctx_items:
        key = item.get("key", "")
        if key.startswith("sop_"):
            val = str(item.get("value", ""))[:40]
            line = f"{domain}: -> L3:{key} ({val})"
            lines.append(line[:80])

    # 硬约束 <=20 行
    lines = lines[:20]
    dbe.set_insight_index(domain, lines)


def extract_common_signals(capsules):
    """从成功胶囊中提取共性触发信号"""
    signal_counts = {}
    for c in capsules:
        gene = dbe.get_gene(c.get("gene_id"))
        if gene:
            for s in gene.get("trigger_signals", []):
                signal_counts[s] = signal_counts.get(s, 0) + 1
    threshold = len(capsules) * 0.5
    return [s for s, cnt in signal_counts.items() if cnt >= threshold]


def extract_content_pattern(capsules):
    """从成功胶囊内容中提取策略摘要"""
    contents = [c.get("content", "") for c in capsules if c.get("content")]
    if not contents:
        return "蒸馏策略：从近期成功文案中提取的模式"
    avg_len = sum(len(c) for c in contents) // max(len(contents), 1)
    return (f"蒸馏策略（{len(contents)} 条成功样本，平均{avg_len}字）："
            f"偏短句口语、具体细节、避免排比和空话。")
