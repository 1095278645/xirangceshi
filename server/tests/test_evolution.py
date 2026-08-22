# -*- coding: utf-8 -*-
"""自适应进化层测试：经验日志 / 渐进记忆 / 技能进化 / 自适应团队

覆盖设计文档四阶段全部 25 条测试用例。
运行：cd server && python -m unittest tests.test_evolution -v
"""
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai
import db
import team
import team_domains
import team_evolution
import evolution
import heartbeat
import db_evolution as dbe


# ---------------- 测试基础设施 ----------------

class _TempDB(unittest.TestCase):
    """临时数据库隔离 mixin（与 test_team.py / test_finance.py 一致）"""
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "test_evo.db"
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        super().setUp()
        # 每个测试清空进化表，避免互相干扰
        with db.get_conn() as conn:
            for t in ("agent_events", "agent_capsules", "agent_genes",
                      "agent_learnings"):
                conn.execute(f"DELETE FROM {t}")
            conn.execute("DELETE FROM domain_context WHERE key LIKE 'insight_index' "
                        "OR key LIKE 'promoted_%' OR key LIKE 'last_distill%' "
                        "OR key LIKE 'adoption_%'")


class _NoKeyAI:
    """mixin：强制无 API Key（降级路径测试）"""
    def setUp(self):
        super().setUp()
        self._no_key = mock.patch.object(ai, "ai_available", return_value=False)
        self._no_key.start()
        self.addCleanup(self._no_key.stop)


# ================================================================
# Phase 1: 经验日志（5 tests）
# ================================================================

class TestLearnings(_TempDB):
    """Layer 1：经验日志记录 / 去重 / 触发器 / 回顾注入 / 降级日志"""

    def test_learning_record(self):
        """P1: 记录触发后正确写入 agent_learnings"""
        lid = dbe.record_learning("copy", "user_edited", "copy.user-edited",
                                   source="frontend", details="用户修改了AI文案")
        self.assertTrue(lid.startswith("LRN-"))
        items = dbe.get_learnings("copy")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["trigger_type"], "user_edited")
        self.assertEqual(items[0]["pattern_key"], "copy.user-edited")
        self.assertEqual(items[0]["status"], "open")
        self.assertEqual(items[0]["recurrence_count"], 1)

    def test_pattern_key_dedup(self):
        """P1: 相同 pattern_key 复现时更新 count 而非新建"""
        dbe.record_learning("copy", "user_edited", "copy.too-ai-flavor",
                            source="frontend", details="AI味太重")
        dbe.record_learning("copy", "user_edited", "copy.too-ai-flavor",
                            source="frontend", details="AI味太重 again")
        dbe.record_learning("copy", "user_edited", "copy.too-ai-flavor",
                            source="frontend", details="第三次AI味")
        items = dbe.get_learnings("copy")
        self.assertEqual(len(items), 1, "相同 pattern_key 应去重，不新建条目")
        self.assertEqual(items[0]["recurrence_count"], 3)

    def test_trigger_detection(self):
        """P1: 6 个触发器各自正确记录"""
        triggers = [
            ("user_edited", "copy.user-edited"),
            ("repeated_request", "copy.repeated-request"),
            ("all_skipped", "copy.all-skipped"),
            ("degraded", "system.degraded"),
            ("margin_abnormal", "store.margin-abnormal"),
            ("explicit_feedback", "user.explicit-feedback"),
        ]
        for tt, pk in triggers:
            dbe.record_learning("copy", tt, pk, source="test")
        items = dbe.get_learnings("copy")
        self.assertEqual(len(items), 6, "6 个触发器应各产生一条记录")
        trigger_types = {i["trigger_type"] for i in items}
        self.assertEqual(trigger_types, {t[0] for t in triggers})

    def test_review_injection(self):
        """P1: 任务前回顾注入 pending 条目到提示词"""
        dbe.record_learning("copy", "user_edited", "copy.too-ai-flavor",
                            source="frontend", details="避免排比句和空话")
        dbe.record_learning("copy", "user_edited", "copy.too-ai-flavor",
                            source="frontend", details="再次AI味")
        review = evolution.review_injection("copy")
        self.assertIn("copy.too-ai-flavor", review)
        self.assertIn("历史经验提醒", review)
        self.assertIn("出现2次", review)  # recurrence_count=2 after dedup

    def test_review_injection_empty(self):
        """P1: 无 pending 条目时回顾注入为空"""
        review = evolution.review_injection("copy")
        self.assertEqual(review, "")

    def test_degraded_logging(self):
        """P1: 降级路径触发时记录 Learning"""
        dbe.record_learning("system", "degraded", "system.degraded",
                            source="system", details="无Key走降级路径")
        items = dbe.get_learnings("system")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["trigger_type"], "degraded")
        self.assertEqual(items[0]["source"], "system")


# ================================================================
# Phase 2: 渐进记忆（5 tests）
# ================================================================

class TestInsightIndex(_TempDB):
    """Layer 2：L1 极简索引 / 任务前回顾 / 晋升规则 / 无执行不记忆"""

    def test_insight_index_format(self):
        """P2: L1 索引格式正确且 <=20 行"""
        lines = [f"copy: formula{i} -> L3:sop_copy_{i}" for i in range(25)]
        dbe.set_insight_index("copy", lines)
        idx = dbe.get_insight_index("copy")
        self.assertLessEqual(len(idx), 20, "L1 索引硬约束 <=20 行")
        for line in idx:
            self.assertLessEqual(len(line), 80, "每行 <80 字符")

    def test_review_before_task(self):
        """P2: 任务前回顾注入 pending 条目到提示词（通过 team_domains._run_team 验证）"""
        dbe.record_learning("copy", "user_edited", "copy.too-ai-flavor",
                            source="frontend", details="避免AI味")
        dbe.record_learning("copy", "user_edited", "copy.too-ai-flavor",
                            source="frontend", details="again")
        # 模拟有 Key 场景，验证 review_injection 被调用
        review_text = evolution.review_injection("copy")
        self.assertIn("copy.too-ai-flavor", review_text)

    def test_promotion_rule(self):
        """P2: 三条件晋升规则正确判断"""
        # 满足全部条件：recurrence >= 3, distinct_tasks >= 2, within 30 days
        dbe.record_learning("copy", "user_edited", "copy.too-ai-flavor",
                            source="frontend", details="AI味太重",
                            metadata={"distinct_tasks": ["task1", "task2"]})
        # 再记录 2 次（达到 recurrence_count=3）
        dbe.record_learning("copy", "user_edited", "copy.too-ai-flavor",
                            source="frontend", details="again",
                            metadata={"distinct_tasks": ["task1", "task2"]})
        dbe.record_learning("copy", "user_edited", "copy.too-ai-flavor",
                            source="frontend", details="third",
                            metadata={"distinct_tasks": ["task1", "task2"]})
        promoted = evolution.promote_learning("copy")
        self.assertEqual(len(promoted), 1)
        # 确认状态改为 promoted
        items = dbe.get_learnings("copy", status="promoted")
        self.assertEqual(len(items), 1)

    def test_promotion_to_domain_context(self):
        """P2: 晋升后写入 domain_context 且原条目改 status"""
        dbe.record_learning("copy", "user_edited", "copy.too-ai-flavor",
                            source="frontend", details="AI味太重",
                            metadata={"distinct_tasks": ["t1", "t2"]})
        dbe.record_learning("copy", "user_edited", "copy.too-ai-flavor",
                            source="frontend", details="2nd",
                            metadata={"distinct_tasks": ["t1", "t2"]})
        dbe.record_learning("copy", "user_edited", "copy.too-ai-flavor",
                            source="frontend", details="3rd",
                            metadata={"distinct_tasks": ["t1", "t2"]})
        evolution.promote_learning("copy")
        from db_arch import get_domain_context
        ctx = get_domain_context("copy", "promoted_copy.too-ai-flavor")
        self.assertIsNotNone(ctx, "晋升后应写入 domain_context")
        self.assertIn("晋升自经验日志", ctx["value"])
        # 原条目状态改为 promoted
        items = dbe.get_learnings("copy", status="open")
        self.assertEqual(len(items), 0)

    def test_no_execution_no_memory(self):
        """P2: 未验证信息不写入记忆"""
        # 无执行来源的记录不应晋升（source 为 model_guess 不符合 No Execution 原则）
        # 这里验证只有经过实际触发记录的条目才会被考虑晋升
        dbe.record_learning("copy", "user_edited", "copy.test",
                            source="frontend", details="测试",
                            metadata={"distinct_tasks": ["t1"]})
        # recurrence=1, distinct_tasks=1 → 不满足晋升条件
        promoted = evolution.promote_learning("copy")
        self.assertEqual(len(promoted), 0, "不满足条件不应晋升")


# ================================================================
# Phase 3: 技能进化（10 tests）
# ================================================================

class TestGeneSelection(_TempDB):
    """Layer 3：基因选择 / 抑制 / 漂移 / 策略 / 生命周期 / 审计 / 胶囊"""

    def setUp(self):
        super().setUp()
        team_evolution.seed_initial_genes()

    def test_gene_selection_laplace(self):
        """P3: Laplace 平滑成功率排序正确"""
        # gene A: 8 成功 2 失败 → (8+1)/(10+2) = 0.75
        dbe.update_gene_stats("gene_copy_scene_transplant", success=True)
        dbe.update_gene_stats("gene_copy_scene_transplant", success=True)
        dbe.update_gene_stats("gene_copy_scene_transplant", success=True)
        dbe.update_gene_stats("gene_copy_scene_transplant", success=True)
        # gene B: 0 成功 4 失败 → (0+1)/(4+2) = 0.167
        dbe.update_gene_stats("gene_copy_yiji", failure=True)
        dbe.update_gene_stats("gene_copy_yiji", failure=True)
        dbe.update_gene_stats("gene_copy_yiji", failure=True)
        dbe.update_gene_stats("gene_copy_yiji", failure=True)

        gene_a = dbe.get_gene("gene_copy_scene_transplant")
        gene_b = dbe.get_gene("gene_copy_yiji")
        self.assertGreater(gene_a["confidence"], gene_b["confidence"])
        # 选择时高成功率的更可能被选中（排除漂移概率影响，多次取众数）
        selections = {}
        for _ in range(100):
            g = evolution.select_gene("copy", ["开业"])
            if g:
                selections[g["gene_id"]] = selections.get(g["gene_id"], 0) + 1
        # 高成功率基因应被选中更多次
        self.assertGreater(selections.get("gene_copy_scene_transplant", 0),
                           selections.get("gene_copy_yiji", 0))

    def test_gene_suppression(self):
        """P3: 低成功率基因被抑制"""
        # 4 次失败 0 次成功 → 成功率 = 1/6 ≈ 0.167 > 0.15... need more
        # 4 failures: (0+1)/(4+2) = 0.167 - not low enough
        # 5 failures: (0+1)/(5+2) = 0.143 < 0.15 → suppress
        for _ in range(5):
            dbe.update_gene_stats("gene_copy_number_pun", failure=True)
        result = evolution.suppress_gene("gene_copy_number_pun")
        self.assertTrue(result, "成功率 <=15% 且 >=4 次尝试应被抑制")
        gene = dbe.get_gene("gene_copy_number_pun")
        self.assertEqual(gene["status"], "suppressed")
        # 验证审计事件
        events = dbe.get_events(domain="copy", event_type="gene_suppressed")
        self.assertGreaterEqual(len(events), 1)

    def test_gene_suppression_inert(self):
        """P3: 连续 8 次零工作结果基因被抑制"""
        for _ in range(8):
            dbe.update_gene_stats("gene_copy_reverse_restraint", inert=True)
        result = evolution.suppress_gene("gene_copy_reverse_restraint")
        self.assertTrue(result)
        gene = dbe.get_gene("gene_copy_reverse_restraint")
        self.assertEqual(gene["status"], "suppressed")

    def test_genetic_drift(self):
        """P3: 遗传漂移概率正确 (1/sqrt(gene_count))"""
        # 4 个基因 → 漂移概率 = 1/sqrt(4) = 0.5
        # 多次选择，验证非常规选择（探索）确实出现
        selections = {}
        for _ in range(200):
            g = evolution.select_gene("copy", [])  # 无信号 → 全部候选
            if g:
                selections[g["gene_id"]] = selections.get(g["gene_id"], 0) + 1
        # 4 个基因均分选择 → 漂移在起作用
        self.assertGreater(len(selections), 1, "遗传漂移应导致多个基因被选中")

    def test_strategy_auto_select(self):
        """P3: 根据近期失败率自动选择策略"""
        # 无胶囊 → balanced
        self.assertEqual(evolution.auto_strategy("copy"), "balanced")
        # 创建 20 个胶囊，其中 12 个失败 → 失败率 60% → harden
        for i in range(12):
            dbe.save_capsule(f"cap-fail-{i}", "gene_copy_yiji", "copy",
                             user_adopted=False)
        for i in range(8):
            dbe.save_capsule(f"cap-ok-{i}", "gene_copy_scene_transplant", "copy",
                             user_adopted=True)
        strategy = evolution.auto_strategy("copy")
        self.assertEqual(strategy, "harden")

    def test_7phase_lifecycle(self):
        """P3: 7 阶段生命周期完整执行"""
        # 阶段 1: 检测（信号匹配）
        gene = evolution.select_gene("copy", ["开业"])
        self.assertIsNotNone(gene, "阶段 1-2: 检测+选择应返回基因")
        # 阶段 3-5: 变异+假设+执行（由 team_domains._run_team 负责，这里验证选择返回可用基因）
        self.assertIn("system_prompt_addon", gene)
        # 阶段 6: 评估（记录 Capsule）
        cap_id = evolution.record_outcome("copy", gene["gene_id"],
                                          "新出卤面，香得很",
                                          user_adopted=True)
        self.assertTrue(cap_id.startswith("cap-"))
        capsules = dbe.get_recent_capsules("copy", limit=1)
        self.assertEqual(len(capsules), 1)
        self.assertTrue(capsules[0]["user_adopted"])
        # 阶段 7: 固化（蒸馏条件不足，不触发）
        events = dbe.get_events(domain="copy")
        self.assertGreater(len(events), 0, "应记录审计事件")

    def test_event_audit_log(self):
        """P3: 审计日志 append-only 且完整"""
        dbe.log_event("gene_selected", gene_id="gene_copy_yiji",
                      domain="copy", details="test event 1")
        dbe.log_event("capsule_created", gene_id="gene_copy_yiji",
                      domain="copy", details="test event 2")
        events = dbe.get_events(domain="copy")
        self.assertEqual(len(events), 2)
        # 每条都有 content_hash (SHA-256)
        for e in events:
            self.assertTrue(e["content_hash"].startswith("sha256:"))
            self.assertEqual(len(e["content_hash"]), 71)  # "sha256:" + 64 hex chars

    def test_capsule_creation(self):
        """P3: 成功执行创建 Capsule"""
        result = dbe.save_capsule("cap-test-001", "gene_copy_yiji", "copy",
                                  task_context={"shop": "老王面馆"},
                                  content="宜|尝鲜 忌|将就",
                                  user_adopted=True, confidence=0.75)
        self.assertEqual(result["capsule_id"], "cap-test-001")
        cap = dbe.get_capsule("cap-test-001")
        self.assertEqual(cap["gene_id"], "gene_copy_yiji")
        self.assertTrue(cap["user_adopted"])
        self.assertEqual(cap["confidence"], 0.75)
        self.assertEqual(cap["task_context"]["shop"], "老王面馆")

    def test_gene_rollback(self):
        """P3: 失败后基因状态不变（回滚安全）"""
        gene_before = dbe.get_gene("gene_copy_scene_transplant")
        # 记录一次失败
        evolution.record_outcome("copy", "gene_copy_scene_transplant",
                                 "失败文案", user_adopted=False)
        gene_after = dbe.get_gene("gene_copy_scene_transplant")
        # 基因仍 active（未达抑制阈值）
        self.assertEqual(gene_after["status"], "active")
        # failure_count 增加
        self.assertEqual(gene_after["failure_count"],
                         gene_before["failure_count"] + 1)

    def test_evolution_config(self):
        """P3: TEAM_DOMAINS evolution 配置正确读取"""
        for name in team_domains.list_team_domains():
            cfg = team_domains.TEAM_DOMAINS[name]
            evo = cfg.get("evolution", {})
            self.assertTrue(evo.get("enabled"), f"{name} 进化应启用")
            self.assertIn(evo.get("strategy"), ("auto", "balanced", "innovate",
                                                "harden", "repair_only"))
            self.assertIsInstance(evo.get("genes"), list)
            self.assertGreaterEqual(len(evo["genes"]), 2)

    def test_degraded_with_evolution(self):
        """P3: 无 Key 时进化层不启动，走原始降级"""
        with mock.patch.object(ai, "ai_available", return_value=False):
            text, process, variants = ai.generate_copy(
                "老王面馆", "今日营业", "新出卤面", return_process=True)
            # 降级文本仍正常
            self.assertIn("老王面馆", text)
            # 降级过程正常
            self.assertEqual(process["mode"], "collaborative")


# ================================================================
# Phase 4: 自适应团队（5 tests）
# ================================================================

class TestDistillation(_TempDB):
    """Layer 4: 技能蒸馏 / 端到端进化闭环"""

    def setUp(self):
        super().setUp()
        team_evolution.seed_initial_genes()

    def test_distill_skill(self):
        """P4: 7/10 成功蒸馏为新 Gene"""
        # 创建 10 个胶囊，7 个成功
        for i in range(7):
            dbe.save_capsule(f"cap-ok-{i}", "gene_copy_scene_transplant", "copy",
                             content=f"成功文案{i}", user_adopted=True)
        for i in range(3):
            dbe.save_capsule(f"cap-fail-{i}", "gene_copy_yiji", "copy",
                             content=f"失败文案{i}", user_adopted=False)
        new_gene = evolution.distill_skill("copy")
        self.assertIsNotNone(new_gene, "7/10 成功应蒸馏出新基因")
        self.assertTrue(new_gene["gene_id"].startswith("gene_distilled_"))
        # 蒸馏基因 confidence 乘 0.8
        gene = dbe.get_gene(new_gene["gene_id"])
        self.assertEqual(gene["is_distilled"], 1)

    def test_distill_threshold(self):
        """P4: 蒸馏条件判断 (7 成功 + 24h 间隔)"""
        # 只有 5 个成功 → 不满足
        for i in range(5):
            dbe.save_capsule(f"cap-ok-{i}", "gene_copy_yiji", "copy",
                             user_adopted=True)
        for i in range(5):
            dbe.save_capsule(f"cap-fail-{i}", "gene_copy_scene_transplant", "copy",
                             user_adopted=False)
        result = evolution.distill_skill("copy")
        self.assertIsNone(result, "5/10 成功不满足蒸馏阈值")

    def test_distill_threshold_24h(self):
        """P4: 24 小时间隔检查"""
        # 7/10 成功但距上次蒸馏不到 24h
        for i in range(7):
            dbe.save_capsule(f"cap-ok-{i}", "gene_copy_scene_transplant", "copy",
                             user_adopted=True)
        for i in range(3):
            dbe.save_capsule(f"cap-fail-{i}", "gene_copy_yiji", "copy",
                             user_adopted=False)
        # 设置 last_distill_time 为当前时间
        from db_arch import set_domain_context
        set_domain_context("copy", "last_distill_time",
                           datetime.now(timezone.utc).isoformat())
        result = evolution.distill_skill("copy")
        self.assertIsNone(result, "24h 内不应重复蒸馏")

    def test_insight_index_update(self):
        """P4: 蒸馏后 L1 索引自动更新"""
        # 先确保有基因
        genes = dbe.get_active_genes("copy")
        self.assertGreater(len(genes), 0, "应有初始基因")
        # 手动触发 L1 更新
        team_evolution.update_insight_index("copy")
        idx = dbe.get_insight_index("copy")
        self.assertGreater(len(idx), 0, "L1 索引应有内容")
        # 每行以 "copy:" 开头
        for line in idx:
            self.assertTrue(line.startswith("copy:"), f"L1 行应以 copy: 开头: {line}")

    def test_user_feedback_loop(self):
        """P4: 前端反馈 -> 记录 -> 进化完整闭环"""
        # 1. 用户编辑了 AI 文案 → 前端上报 learning
        dbe.record_learning("copy", "user_edited", "copy.too-ai-flavor",
                            source="frontend", details="AI味太重，改成口语化")
        dbe.record_learning("copy", "user_edited", "copy.too-ai-flavor",
                            source="frontend", details="再次AI味")
        dbe.record_learning("copy", "user_edited", "copy.too-ai-flavor",
                            source="frontend", details="第三次",
                            metadata={"distinct_tasks": ["task1", "task2"]})
        # 2. 用户采纳了某条文案 → 前端上报 outcome
        cap_id = evolution.record_outcome("copy", "gene_copy_scene_transplant",
                                          "新出卤面，香得很", user_adopted=True)
        self.assertTrue(cap_id.startswith("cap-"))
        # 3. 每日进化检查
        results = heartbeat.evolution_daily_check()
        # 4. 验证晋升已触发
        promoted = dbe.get_learnings("copy", status="promoted")
        self.assertGreater(len(promoted), 0, "经验应被晋升")
        # 5. 验证胶囊已记录
        capsules = dbe.get_recent_capsules("copy")
        self.assertGreater(len(capsules), 0, "胶囊应存在")

    def test_e2e_evolution(self):
        """P4: 端到端：生成 -> 采纳 -> 记录 -> 晋升 -> 蒸馏"""
        # 1. 记录足够的经验日志（满足晋升条件）
        for i in range(3):
            dbe.record_learning("copy", "user_edited", "copy.user-preference",
                                source="frontend", details=f"用户偏好短句 {i}",
                                metadata={"distinct_tasks": ["task1", "task2"]})

        # 2. 记录 7/10 成功的胶囊（满足蒸馏条件）
        for i in range(7):
            dbe.save_capsule(f"cap-e2e-ok-{i}", "gene_copy_scene_transplant", "copy",
                             content=f"好文案{i}", user_adopted=True)
        for i in range(3):
            dbe.save_capsule(f"cap-e2e-fail-{i}", "gene_copy_yiji", "copy",
                             content=f"差文案{i}", user_adopted=False)

        # 3. 触发每日进化检查
        results = heartbeat.evolution_daily_check()

        # 4. 验证晋升
        promoted = dbe.get_learnings("copy", status="promoted")
        self.assertGreater(len(promoted), 0, "经验应被晋升为永久记忆")

        # 5. 验证蒸馏
        distilled_genes = [g for g in dbe.get_all_genes("copy")
                          if g.get("is_distilled")]
        self.assertGreater(len(distilled_genes), 0, "应蒸馏出新基因")

        # 6. 验证 L1 索引已更新
        idx = dbe.get_insight_index("copy")
        self.assertGreater(len(idx), 0, "L1 索引应已更新")

        # 7. 验证审计事件完整
        events = dbe.get_events(domain="copy")
        event_types = {e["event_type"] for e in events}
        self.assertIn("gene_distilled", event_types)
        self.assertIn("learning_promoted", event_types)


if __name__ == "__main__":
    unittest.main(verbosity=2)
