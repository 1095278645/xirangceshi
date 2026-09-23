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
                     "/api/store/model", "/api/collect/create",
                     "/api/backup/list", "/api/shops", "/api/genes"):
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
        self.assertEqual(core, 92)
        self.assertEqual(full, 140)

    def test_invalid_profile_rejected(self):
        with self.assertRaises(ValueError):
            registry.get_routers("advanced")


if __name__ == "__main__":
    unittest.main()
