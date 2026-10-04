# -*- coding: utf-8 -*-
"""core/full API 能力边界测试：默认做减法，完整模式不丢功能。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import registry


def _paths(profile):
    return {
        path
        for router in registry.get_routers(profile)
        for route in router.routes
        for path in [getattr(route, "path", "")]
        if path
    }


class TestAPIProfile(unittest.TestCase):
    def test_core_profile_keeps_main_path_and_evolution(self):
        paths = _paths("core")
        for path in ("/api/orders", "/api/customers", "/api/insights",
                     "/api/store/model", "/api/breakeven/today", "/api/collect/create",
                     "/api/backup/list", "/api/shops", "/api/evolution/summary"):
            self.assertIn(path, paths)

    def test_core_profile_hides_advanced_domains(self):
        paths = _paths("core")
        for path in ("/api/payment/sources", "/api/cashflow", "/api/products",
                     "/api/invoices", "/api/accounting/trial-balance",
                     "/api/notify/providers", "/api/metrics/ai"):
            self.assertNotIn(path, paths)

    def test_profile_counts(self):
        core = sum(len(router.routes) for router in registry.get_routers("core"))
        full = sum(len(router.routes) for router in registry.get_routers("full"))
        # 基线：撤旧/收敛(A/C/B) → 候选处理/提醒投递/进化真值 → 连锁总部视图 → 统一保本线
        #       → /api/ledger/periods（月初账本默认期间回退，见 db.list_active_periods）
        #       → 知识资产治理（knowledge 域共 8 个端点）与跨域关系索引（relations 域 4 个）
        #       → /api/health/ai（AI 真实探活：配了 Key 但调不通不再静默降级）
        self.assertEqual(core, 92)
        self.assertEqual(full, 140)

    def test_invalid_profile_rejected(self):
        with self.assertRaises(ValueError):
            registry.get_routers("advanced")


if __name__ == "__main__":
    unittest.main()
