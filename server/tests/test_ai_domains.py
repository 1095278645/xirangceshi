# -*- coding: utf-8 -*-
"""全业务域 AI 接入测试：5 域降级路径 + domain_context 读写

测试覆盖：
1. ai.generate_insights（账本洞察）— 降级返回模板
2. ai.generate_customer_insight（客户画像）— 降级返回规则标签
3. ai.generate_tax_advice（报税建议）— 降级返回免征判断
4. ai.generate_store_diagnosis（经营诊断）— 降级返回评分话术
5. ai.generate_copy（文案增强）— 降级含 context 信息
6. domain_context 各域读写往返

运行：cd server && python -m unittest tests.test_ai_domains -v
"""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai
import db
import store as storelib
import tax as taxcalc


class _NoKeyAI:
    """mixin：强制 ai_available()=False，降级路径测试与外部是否配置 Key 解耦"""

    def setUp(self):
        super().setUp()
        self._no_key = mock.patch.object(ai, "ai_available", return_value=False)
        self._no_key.start()
        self.addCleanup(self._no_key.stop)


class _TempDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "test_ai.db"
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        with db.get_conn() as conn:
            conn.execute("DELETE FROM domain_context")
            conn.execute("DELETE FROM transactions")
            conn.execute("DELETE FROM customers")


class TestInsightsDegraded(_NoKeyAI, _TempDB):
    """账本洞察 — 无 API Key 降级"""

    def test_degraded_returns_template(self):
        """降级时返回含收支数据的模板"""
        monthly = {"income": 5000, "expense": 3000, "balance": 2000, "categories": []}
        text = ai.generate_insights(monthly)
        self.assertIn("5000", text)
        self.assertIn("3000", text)
        self.assertIn("提示", text)  # 降级提示

    def test_degraded_negative_balance(self):
        """入不敷出时降级提示开源节流"""
        monthly = {"income": 1000, "expense": 3000, "balance": -2000, "categories": []}
        text = ai.generate_insights(monthly)
        self.assertIn("入不敷出", text)

    def test_degraded_with_top_category(self):
        """有分类明细时降级展示最高支出"""
        cats = [{"category": "进货", "friendly": "进货", "trans_type": "expense", "total": 2000}]
        monthly = {"income": 5000, "expense": 3000, "balance": 2000, "categories": cats}
        text = ai.generate_insights(monthly)
        self.assertIn("进货", text)


class TestCustomerInsightDegraded(_NoKeyAI, _TempDB):
    """客户画像 — 无 API Key 降级"""

    def test_degraded_returns_tags(self):
        """降级时返回基于规则的客户标签"""
        customer = {"name": "王阿姨", "memories": [{"content": "孙子考了一百分"}]}
        txns = [{"amount": 30} for _ in range(5)]
        text = ai.generate_customer_insight(customer, txns)
        self.assertIn("王阿姨", text)
        self.assertIn("5", text)  # 5 笔交易
        self.assertIn("回头客", text)  # >=3 笔

    def test_degraded_high_spend(self):
        """高消费客户降级标签"""
        customer = {"name": "李老板", "memories": []}
        txns = [{"amount": 80} for _ in range(3)]
        text = ai.generate_customer_insight(customer, txns)
        self.assertIn("高消费", text)

    def test_degraded_with_memories(self):
        """降级时包含记忆点"""
        customer = {"name": "张师傅", "memories": [{"content": "爱喝浓茶"}]}
        text = ai.generate_customer_insight(customer, [])
        self.assertIn("爱喝浓茶", text)


class TestTaxAdviceDegraded(_NoKeyAI, _TempDB):
    """报税建议 — 无 API Key 降级"""

    def test_degraded_exempt(self):
        """季度≤30万降级提示免征"""
        vat = taxcalc.calc_vat(200000)
        text = ai.generate_tax_advice(200000, vat)
        self.assertIn("免征", text)
        self.assertIn("200000", text)

    def test_degraded_not_exempt(self):
        """季度>30万降级提示应缴"""
        vat = taxcalc.calc_vat(400000)
        text = ai.generate_tax_advice(400000, vat)
        self.assertIn("400000", text)


class TestStoreDiagnosisDegraded(_NoKeyAI, _TempDB):
    """经营诊断 — 无 API Key 降级"""

    def test_degraded_danger(self):
        """危险场景降级话术"""
        result = storelib.calc_store_model(
            daily_revenue=300, rent=6000, salary=8000,
            utilities=2000, total_investment=100000, cash_on_hand=30000)
        text = ai.generate_store_diagnosis(result)
        score = result["overall"]["score"]
        self.assertIn(str(score), text)
        self.assertIn("止损", text)  # 低分建议止损

    def test_degraded_healthy(self):
        """健康场景降级话术"""
        result = storelib.calc_store_model(
            daily_revenue=2000, rent=5000, salary=5000,
            utilities=2000, total_investment=50000, cash_on_hand=100000)
        text = ai.generate_store_diagnosis(result)
        self.assertIn("健康", text)
        self.assertIn("扩张", text)  # 高分建议扩张


class TestCopyWithContextDegraded(_NoKeyAI, _TempDB):
    """文案生成增强 — 无 API Key 降级，context 信息填入模板"""

    def test_degraded_without_context(self):
        """无 context 降级模板"""
        text = ai.generate_copy("老王面馆", "今日营业", "新出卤面", "")
        self.assertIn("老王面馆", text)
        self.assertIn("新出卤面", text)

    def test_degraded_with_context(self):
        """有 context 降级模板含上下文"""
        text = ai.generate_copy("老王面馆", "今日营业", "新出卤面", "", "今日收500元")
        self.assertIn("老王面馆", text)
        self.assertIn("今日收500元", text)


class TestDomainContextRoundtrip(_TempDB):
    """各业务域 domain_context 读写往返"""

    def test_ledger_domain(self):
        """账本域：写入月度洞察 → 读取"""
        db.set_domain_context("ledger", "monthly_insights", "本月收入5000元")
        item = db.get_domain_context("ledger", "monthly_insights")
        self.assertEqual(item["value"], "本月收入5000元")

    def test_customer_domain(self):
        """客户域：写入客户画像 → 读取"""
        db.set_domain_context("customer", "profile_1", "王阿姨是常客")
        item = db.get_domain_context("customer", "profile_1")
        self.assertEqual(item["value"], "王阿姨是常客")

    def test_tax_domain(self):
        """税务域：写入报税建议 → 读取"""
        db.set_domain_context("tax", "quarterly_advice", "本季度免征")
        item = db.get_domain_context("tax", "quarterly_advice")
        self.assertEqual(item["value"], "本季度免征")

    def test_store_domain(self):
        """单店域：写入经营诊断 → 读取"""
        db.set_domain_context("store", "diagnosis", "经营健康")
        item = db.get_domain_context("store", "diagnosis")
        self.assertEqual(item["value"], "经营健康")

    def test_copy_reads_context(self):
        """文案域读取 ledger + store 上下文"""
        db.set_domain_context("ledger", "daily_review", "今日收500元")
        db.set_domain_context("store", "diagnosis", "保本线920元")
        # 模拟 basic.py 的 context 拼接逻辑
        parts = []
        review = db.get_domain_context("ledger", "daily_review")
        if review and review.get("value"):
            parts.append(str(review["value"])[:200])
        diag = db.get_domain_context("store", "diagnosis")
        if diag and diag.get("value"):
            parts.append(str(diag["value"])[:200])
        context = " | ".join(parts) if parts else ""
        self.assertIn("今日收500元", context)
        self.assertIn("保本线920元", context)

    def test_upsert_across_domains(self):
        """不同域同名 key 不冲突"""
        db.set_domain_context("ledger", "k", "v1")
        db.set_domain_context("customer", "k", "v2")
        self.assertEqual(db.get_domain_context("ledger", "k")["value"], "v1")
        self.assertEqual(db.get_domain_context("customer", "k")["value"], "v2")


class TestEndpointLogic(_NoKeyAI, _TempDB):
    """端点逻辑集成测试（不经过 HTTP，直接调底层函数模拟端点行为，AI 一律走降级路径）"""

    def test_insights_flow(self):
        """洞察端点流程：月度汇总 → AI(降级) → 落盘 domain_context"""
        # 插入一笔今日流水
        today = date.today().isoformat()
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO transactions(trans_type, category, item, amount, created_at) "
                "VALUES('income','主营业务收入','卖面',100,?)", (f"{today} 10:00:00",))
        monthly = db.monthly_summary()
        prev = db.get_domain_context("ledger", "monthly_insights")
        prev_text = prev["value"] if prev else ""
        text = ai.generate_insights(monthly, prev_text)
        db.set_domain_context("ledger", "monthly_insights", text)
        # 验证落盘
        stored = db.get_domain_context("ledger", "monthly_insights")
        self.assertEqual(stored["value"], text)
        self.assertIn("100", text)

    def test_customer_insight_flow(self):
        """客户画像端点流程：取客户 → AI(降级) → 落盘"""
        cid, _ = db.find_or_create_customer("赵阿姨", favorite="肉包")
        customer = db.get_customer(cid)
        txns = customer.get("transactions", [])
        text = ai.generate_customer_insight(customer, txns)
        db.set_domain_context("customer", f"profile_{cid}", text)
        stored = db.get_domain_context("customer", f"profile_{cid}")
        self.assertEqual(stored["value"], text)
        self.assertIn("赵阿姨", text)

    def test_tax_advice_flow(self):
        """报税建议端点流程：算增值税 → AI(降级) → 落盘"""
        vat = taxcalc.calc_vat(250000)
        prev = db.get_domain_context("tax", "quarterly_advice")
        text = ai.generate_tax_advice(250000, vat, prev["value"] if prev else "")
        db.set_domain_context("tax", "quarterly_advice", text)
        stored = db.get_domain_context("tax", "quarterly_advice")
        self.assertEqual(stored["value"], text)
        self.assertIn("免征", text)

    def test_store_diagnosis_flow(self):
        """经营诊断端点流程：跑模型 → AI(降级) → 落盘"""
        result = storelib.calc_store_model(
            daily_revenue=500, rent=6000, salary=8000,
            utilities=2000, total_investment=100000, cash_on_hand=30000)
        prev = db.get_domain_context("store", "diagnosis")
        text = ai.generate_store_diagnosis(result, prev["value"] if prev else "")
        db.set_domain_context("store", "diagnosis", text)
        stored = db.get_domain_context("store", "diagnosis")
        self.assertEqual(stored["value"], text)


class TestChatTokenBudget(unittest.TestCase):
    """思考型模型的 max_tokens 预算问题（回归测试）。

    背景：deepseek-flash / deepseek-v4-pro 这类思考型模型的 reasoning_content 与
    正文**共享** max_tokens 预算。各业务点的 max_tokens 是按非思考模型定的
    （260~500），预算被推理吃光后正文返回空串 —— 表现为"AI 功能没反应"。

    ai.chat 必须在这种情况自动放大预算重试，而不是把空串传给业务层。
    """

    @staticmethod
    def _resp(content, reasoning=""):
        msg = mock.Mock()
        msg.content = content
        msg.reasoning_content = reasoning
        choice = mock.Mock()
        choice.message = msg
        resp = mock.Mock()
        resp.choices = [choice]
        return resp

    def _fake_client(self, responses):
        """按顺序返回预设响应，并记录每次请求的 max_tokens。"""
        calls = []

        def create(**kw):
            calls.append(kw["max_tokens"])
            return responses[min(len(calls) - 1, len(responses) - 1)]

        client = mock.Mock()
        client.chat.completions.create.side_effect = create
        return client, calls

    def _patch(self, client):
        return [
            mock.patch.object(ai, "get_client", return_value=client),
            mock.patch.object(ai, "load_settings",
                              return_value={"model": "deepseek-flash", "api_key": "k",
                                            "base_url": "u"}),
        ]

    def test_empty_content_retries_with_larger_budget(self):
        """推理吃光预算（正文为空）→ 放大 max_tokens 重试并拿到正文。"""
        client, calls = self._fake_client([
            self._resp("", reasoning="想" * 500),      # 预算不足：正文空
            self._resp("老板，本季度要交 10194 元。"),   # 放大后成功
        ])
        patches = self._patch(client)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        out = ai.chat([{"role": "user", "content": "算税"}], max_tokens=400)
        self.assertIn("10194", out)
        self.assertEqual(len(calls), 2, "应当重试一次")
        self.assertGreater(calls[1], calls[0], "重试时 max_tokens 必须放大")

    def test_starting_budget_has_floor(self):
        """起始预算有下限：思考型模型用 400 起步几乎必然空返回，应自动抬高。"""
        client, calls = self._fake_client([self._resp("正常正文")])
        patches = self._patch(client)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        ai.chat([{"role": "user", "content": "x"}], max_tokens=400)
        self.assertGreaterEqual(calls[0], ai._CHAT_TOKEN_MIN)

    def test_raises_when_budget_exhausted(self):
        """放大到上限仍无正文 → 抛清晰异常（由业务层走兜底），不返回空串。"""
        client, calls = self._fake_client([self._resp("", reasoning="想" * 9000)])
        patches = self._patch(client)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with self.assertRaises(RuntimeError) as cm:
            ai.chat([{"role": "user", "content": "x"}], max_tokens=400)
        self.assertIn("max_tokens", str(cm.exception))
        # 必须真的递增到上限，而不是固定次数就放弃
        self.assertEqual(calls[-1], ai._CHAT_TOKEN_CAP)
        self.assertLessEqual(calls[-1], ai._CHAT_TOKEN_CAP)

    def test_normal_content_no_retry(self):
        """正文正常时只调一次，不产生额外开销。"""
        client, calls = self._fake_client([self._resp("正常")])
        patches = self._patch(client)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.assertEqual(ai.chat([{"role": "user", "content": "x"}]), "正常")
        self.assertEqual(len(calls), 1)


class TestThinkingParams(unittest.TestCase):
    """思考模式参数构造（回归测试）。

    背景：deepseek-flash 是思考型模型（DeepSeek-V4.1-Flash），默认 effort=high。
      - 思考模式下 temperature 被服务端忽略，多 agent 依赖 temperature 区分角色，
        所以默认应关闭思考；
      - 但把 DeepSeek 专有的 thinking/reasoning_effort 发给 OpenAI 等其它
        OpenAI 兼容服务可能 400，本项目支持自定义 base_url，必须按模型精确判断。
    """

    def test_default_disables_thinking(self):
        p = ai._thinking_params("deepseek-flash", None)
        self.assertEqual(p.get("extra_body"), {"thinking": {"type": "disabled"}})
        self.assertNotIn("reasoning_effort", p)

    def test_explicit_effort_enables_thinking(self):
        for eff in ("low", "high", "max"):
            p = ai._thinking_params("deepseek-flash", eff)
            self.assertEqual(p["reasoning_effort"], eff)
            self.assertEqual(p["extra_body"], {"thinking": {"type": "enabled"}})

    def test_invalid_effort_falls_back(self):
        """非法档位必须收敛，否则服务端会拒绝请求。"""
        p = ai._thinking_params("deepseek-flash", "ultra")
        self.assertEqual(p["reasoning_effort"], ai._DEFAULT_REASONING_EFFORT)

    def test_legacy_and_new_model_names_supported(self):
        for m in ("deepseek-flash", "deepseek-v4-flash", "deepseek-v4-pro",
                  "deepseek-v4-flash-vision-exp"):
            self.assertTrue(ai._thinking_params(m, "low"), f"{m} 应支持思考参数")

    def test_non_deepseek_models_get_no_thinking_params(self):
        """关键：绝不能把 DeepSeek 专有参数发给其它服务（可能 400）。"""
        for m in ("deepseek-chat", "gpt-4o-mini", "qwen-turbo", "glm-4-flash",
                  "moonshot-v1-8k", "custom-model", ""):
            self.assertEqual(ai._thinking_params(m, "high"), {},
                             f"{m} 不该收到思考参数")

    def test_model_name_matching_is_exact(self):
        """前缀相似但不是官方模型名的不能放行（如自建代理）。"""
        self.assertEqual(ai._thinking_params("deepseek-flash-proxy", "low"), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
