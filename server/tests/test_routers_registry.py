# -*- coding: utf-8 -*-
"""业务域注册表测试：验证「增减能力不影响整体组装」的声明式注册表机制

运行：cd server && python -m unittest tests.test_routers_registry -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import APIRouter
from routers import registry


class TestRoutersRegistry(unittest.TestCase):
    """注册表自检：结构与挂载一致性（仿 test_team.TestTeamRegistry）"""

    def test_domains_structure(self):
        """每个域声明都必须含 name / module / desc / enabled 字段"""
        self.assertTrue(registry.BUSINESS_DOMAINS, "注册表不应为空")
        for d in registry.BUSINESS_DOMAINS:
            for key in ("name", "module", "desc", "enabled"):
                self.assertIn(key, d, f"域 {d} 缺少字段 {key}")
            self.assertIsInstance(d["name"], str)
            self.assertIsInstance(d["module"], str)
            self.assertIsInstance(d["desc"], str)
            self.assertIsInstance(d["enabled"], bool)

    def test_all_enabled_modules_load_router(self):
        """每个已启用域对应的模块都必须可 import 且暴露 APIRouter 实例"""
        for d in registry.get_enabled_domains():
            r = registry._load_router(d["module"])
            self.assertIsInstance(r, APIRouter, f"{d['module']} 未暴露 APIRouter")
            self.assertTrue(r.routes, f"{d['module']} 的 router 不应为空")

    def test_get_routers_matches_enabled(self):
        """挂载列表 = 已启用域，且顺序与注册表声明一致（用 tags 校验域身份）"""
        routers = registry.get_routers()
        expected = registry.get_enabled_domains()
        self.assertEqual(len(routers), len(expected))
        self.assertEqual([d["name"] for d in expected],
                         [r.tags[0] for r in routers])
        # 每个 router 都挂在一个 /api 前缀上（与原 main.py 一致）
        for r in routers:
            self.assertEqual(r.prefix, "/api")

    def test_disabling_one_domain_does_not_break_others(self):
        """停用一个域只剔除它，其余域照常挂载（验证「增减能力不影响整体架构」）"""
        target = registry.BUSINESS_DOMAINS[1]["name"]  # 例如 basic
        original = registry.BUSINESS_DOMAINS[1]["enabled"]
        try:
            registry.BUSINESS_DOMAINS[1]["enabled"] = False
            enabled = registry.get_enabled_domains()
            names = [d["name"] for d in enabled]
            self.assertNotIn(target, names)
            # 其余域仍全部可用
            self.assertEqual(len(enabled), len(registry.BUSINESS_DOMAINS) - 1)
            routers = registry.get_routers()
            self.assertEqual(len(routers), len(enabled))
            self.assertIsInstance(routers[0], APIRouter)
        finally:
            registry.BUSINESS_DOMAINS[1]["enabled"] = original

    def test_list_domains_returns_metadata(self):
        """list_domains 返回副本，不改动注册表；停用域也保留在清单里"""
        listed = registry.list_domains()
        self.assertEqual(len(listed), len(registry.BUSINESS_DOMAINS))
        # 改返回副本不应影响注册表
        listed[0]["name"] = "mutated"
        self.assertNotEqual(registry.BUSINESS_DOMAINS[0]["name"], "mutated")


if __name__ == "__main__":
    unittest.main()