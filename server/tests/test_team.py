# -*- coding: utf-8 -*-
"""「一人团队」AI 引擎测试：并行竞争 / 融合裁决 / 采纳归因成长 / 降级团队过程

运行：cd server && python -m unittest tests.test_team -v
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai
import db
import store as storelib
import team


class _TempDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "test_team.db"
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        with db.get_conn() as conn:
            conn.execute("DELETE FROM domain_context")


class TestParallelCompetition(_TempDB):
    """并行竞争扇出：多员工各给一角度"""

    def test_run_parallel_order(self):
        outs = team.run_parallel([lambda: "财务", lambda: "经营", lambda: "风控"])
        self.assertEqual(outs, ["财务", "经营", "风控"])

    def test_run_parallel_empty(self):
        self.assertEqual(team.run_parallel([]), [])


class TestAdoptionGrowth(_TempDB):
    """Self-Grown：采纳归因沉淀 + 采纳率提示"""

    def test_record_and_load(self):
        team.record_adoption("store", ["财务顾问", "风控顾问"])
        team.record_adoption("store", ["财务顾问"])
        ad = team.load_adoption("store")
        self.assertEqual(ad, {"财务顾问": 2, "风控顾问": 1})

    def test_adoption_brief(self):
        team.record_adoption("copy", ["创意文案师"])
        team.record_adoption("copy", ["创意文案师"])
        brief = team.adoption_brief("copy")
        self.assertIn("创意文案师", brief)
        self.assertIn("100%", brief)

    def test_adoption_brief_empty(self):
        self.assertEqual(team.adoption_brief("nope"), "")

    def test_adoption_isolated_by_domain(self):
        team.record_adoption("store", ["财务顾问"])
        self.assertEqual(team.load_adoption("copy"), {})


class TestStoreDiagnosisTeam(_TempDB):
    """单店诊断：竞争融合（无 Key 降级走规则团队过程）"""

    def _result(self, daily_revenue=300):
        return storelib.calc_store_model(
            daily_revenue=daily_revenue, rent=6000, salary=8000,
            utilities=2000, total_investment=100000, cash_on_hand=30000)

    def test_degraded_process_structure(self):
        text, process = ai.generate_store_diagnosis(self._result(), return_process=True)
        self.assertIn("止损", text)          # 危险场景降级话术仍保留
        self.assertEqual(process["mode"], "competitive")
        self.assertEqual(len(process["employees"]), 3)   # 三位员工
        roles = {e["role"] for e in process["employees"]}
        self.assertIn("财务顾问", roles)
        self.assertIn("风控顾问", roles)
        self.assertTrue(process["verdict"])

    def test_degraded_default_returns_str(self):
        # 兼容性：不传 return_process 时仍返回纯文本
        text = ai.generate_store_diagnosis(self._result())
        self.assertIsInstance(text, str)
        self.assertIn("止损", text)

    def test_degraded_healthy_process(self):
        r = storelib.calc_store_model(
            daily_revenue=2000, rent=5000, salary=5000,
            utilities=2000, total_investment=50000, cash_on_hand=100000)
        text, process = ai.generate_store_diagnosis(r, return_process=True)
        self.assertIn("健康", text)
        self.assertIn("扩张", text)


class TestCopyTeamPipeline(_TempDB):
    """朋友圈文案：协作流水线（创意/熟客 → 合规 → 融合）降级团队过程"""

    def test_degraded_process_structure(self):
        text, process = ai.generate_copy("老王面馆", "今日营业", "新出卤面", return_process=True)
        self.assertIn("老王面馆", text)
        self.assertEqual(process["mode"], "collaborative")
        roles = {e["role"] for e in process["employees"]}
        self.assertIn("创意文案师", roles)
        self.assertIn("合规审核", roles)     # 协作下游评审在场

    def test_degraded_default_returns_str(self):
        text = ai.generate_copy("老王面馆", "今日营业", "新出卤面")
        self.assertIsInstance(text, str)
        self.assertIn("新出卤面", text)


class TestLiveTeamWithMock(_TempDB):
    """有 API Key 时的真实竞争融合 / 协作流水线（mock chat，不依赖真实大模型）"""

    def _result(self):
        return storelib.calc_store_model(
            daily_revenue=300, rent=6000, salary=8000,
            utilities=2000, total_investment=100000, cash_on_hand=30000)

    def test_store_diagnosis_competitive(self):
        """三位员工竞争产出 → 掌柜融合，采纳归因沉淀进 team 域"""
        def fake_chat(messages, temperature=0.7, max_tokens=1024):
            first = messages[0]
            if isinstance(first, dict) and first.get("role") == "system":
                sys_text = first["content"]
                if "财务顾问" in sys_text:
                    return "现金流吃紧，日销不到保本线，先降固定成本。"
                if "经营顾问" in sys_text:
                    return "做一个月的整改窗口，日销追到目标线。"
                if "风控顾问" in sys_text:
                    return "现金只够扛三个月，先保命再扩张。"
            # 掌柜融合裁决
            return ('{"verdict":"采纳财务与风控的核心判断","adopted":["财务顾问","风控顾问"],'
                    '"final":"最终诊断：现金流危险，日销不到保本线，先把固定成本降下来，做一个月的整改窗口。"}')
        with mock.patch.object(ai, "ai_available", return_value=True), \
             mock.patch.object(ai, "chat", side_effect=fake_chat):
            text, process = ai.generate_store_diagnosis(self._result(), return_process=True)
        self.assertIn("最终诊断", text)
        self.assertEqual(process["mode"], "competitive")
        self.assertEqual(process["adopted"], ["财务顾问", "风控顾问"])
        # 采纳已归因沉淀（Self-Grown）
        self.assertEqual(team.load_adoption("store"), {"财务顾问": 1, "风控顾问": 1})

    def test_copy_collaborative_with_reviewer(self):
        """文案：创意/熟客竞争 → 合规评审 → 掌柜融合（协作流水线）"""
        def fake_chat(messages, temperature=0.7, max_tokens=1024):
            # 员工/评审调用都带 system 消息；掌柜融合裁决只有一个 user 消息 → 命中末行返回 JSON
            first = messages[0]
            if isinstance(first, dict) and first.get("role") == "system":
                sys_text = first["content"]
                user = messages[-1]["content"] if messages else ""
                if "评审" in user:
                    return "无绝对化用语，注意别用'最'字。"
                if "创意文案师" in sys_text:
                    return "新出卤面，香得很。"
                if "熟客运营" in sys_text:
                    return "王阿姨常来，给她留一碗。"
            return ('{"verdict":"合并创意与熟客","adopted":["创意文案师","熟客运营"],'
                    '"final":"新出卤面香得很，王阿姨来一碗不？"}')
        with mock.patch.object(ai, "ai_available", return_value=True), \
             mock.patch.object(ai, "chat", side_effect=fake_chat):
            text, process = ai.generate_copy("老王面馆", "今日营业", "新出卤面",
                                             "王阿姨", return_process=True)
        self.assertIn("新出卤面", text)
        self.assertEqual(process["mode"], "collaborative")
        roles = {e["role"] for e in process["employees"]}
        self.assertIn("合规审核", roles)     # 协作下游评审在场
        print("DEBUG adoption=", team.load_adoption("copy"), "process.adopted=", process["adopted"])
        self.assertEqual(team.load_adoption("copy")["创意文案师"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)