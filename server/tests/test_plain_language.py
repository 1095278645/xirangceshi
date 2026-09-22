"""test_plain_language.py — 术语翻译层 + "所以呢"过滤器 单元测试

覆盖：
  - 常见财务术语替换（保本点/应收账款/毛利率/现金跑道等）
  - 长词优先替换（"保本点"不被"保本"截断）
  - 空文本/None 安全
  - has_action 行动建议检测
  - so_what_check 自动补行动建议
  - polish 一站式处理
"""

from plain_language import translate, has_action, so_what_check, polish, TERM_MAP


class TestTranslate:
    def test_basic_replacement(self):
        assert translate("你的保本点是340元") == "你的每天至少要卖多少钱才不亏是340元"

    def test_multiple_terms(self):
        text = "应收账款逾期，毛利率下降"
        out = translate(text)
        assert "别人还欠你的钱" in out
        assert "每卖 100 块赚多少" in out
        assert "应收账款" not in out
        assert "毛利率" not in out

    def test_long_term_priority(self):
        """保本日销 应优先于 保本 被替换"""
        out = translate("保本日销340元")
        assert "每天至少要卖的金额" in out

    def test_empty_and_none(self):
        assert translate("") == ""
        assert translate(None) is None

    def test_no_terms_unchanged(self):
        text = "今天卖了580块，不错"
        assert translate(text) == text

    def test_cash_runway(self):
        out = translate("现金跑道还剩3个月")
        assert "手里的钱还能撑几个月" in out

    def test_tax_terms(self):
        out = translate("进项税可以抵扣销项税")
        assert "你买东西时可以抵的税" in out
        assert "你卖东西要交的税" in out

    def test_term_map_not_empty(self):
        assert len(TERM_MAP) >= 20


class TestHasAction:
    def test_has_action(self):
        assert has_action("建议明天催票据")
        assert has_action("要不要打个电话？")
        assert has_action("赶紧补录")
        assert has_action("先核对一下账")

    def test_no_action(self):
        assert not has_action("今天卖了580块")
        assert not has_action("毛利率为42%")

    def test_empty(self):
        assert not has_action("")
        assert not has_action(None)


class TestSoWhatCheck:
    def test_with_action_unchanged(self):
        text = "进货缺票据。建议明天催供应商。"
        assert so_what_check(text) == text

    def test_without_action_adds_hint(self):
        text = "今天卖了580块，赚了230块。"
        out = so_what_check(text)
        assert "你要不要" in out or "建议" in out
        assert len(out) > len(text)

    def test_empty_safe(self):
        assert so_what_check("") == ""


class TestPolish:
    def test_full_pipeline(self):
        text = "你的保本点是340元，今天卖了580元。"
        out = polish(text)
        assert "保本点" not in out
        assert "每天至少要卖多少钱才不亏" in out
        # should have action hint appended
        assert has_action(out)

    def test_already_good_text(self):
        text = "别人欠你200块三周了。建议找个理由打个电话催一下。"
        out = polish(text)
        assert out == text  # no terms, has action → unchanged

    def test_none_safe(self):
        assert polish(None) is None
        assert polish("") == ""