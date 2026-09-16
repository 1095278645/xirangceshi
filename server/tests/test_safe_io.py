"""safe_io 模块测试：受保护路径白名单（Pattern 20）+ 原子写入（Pattern 21）。

L9 Engineering Robustness — 验证：
  - is_protected() 正确识别受保护文件（源码/数据库/配置）和放行可写文件（config.local.json/报表）；
  - atomic_write_* 写入后不留 .tmp 残留，能覆盖已有文件，能自动创建父目录；
  - safe_write_* 对受保护文件抛 PermissionError，对非受保护文件正常写入。
"""
import json
import tempfile
import unittest
from pathlib import Path

from safe_io import (
    is_protected,
    atomic_write_text,
    atomic_write_json,
    atomic_write_bytes,
    safe_write_text,
    safe_write_json,
)


class TestProtectedPaths(unittest.TestCase):
    """Pattern 20: 受保护路径白名单"""

    def test_protected_source_files(self):
        """.py 源码文件受保护"""
        self.assertTrue(is_protected("main.py"))
        self.assertTrue(is_protected("config.py"))
        self.assertTrue(is_protected("db.py"))
        self.assertTrue(is_protected("server/ai.py"))
        self.assertTrue(is_protected("routers/registry.py"))

    def test_protected_database_files(self):
        """数据库文件受保护"""
        self.assertTrue(is_protected("data/ai_shopkeeper.db"))
        self.assertTrue(is_protected("test.sqlite"))
        self.assertTrue(is_protected("backup.sqlite3"))

    def test_protected_config_files(self):
        """关键配置/清单文件受保护"""
        self.assertTrue(is_protected(".gitignore"))
        self.assertTrue(is_protected("README.md"))
        self.assertTrue(is_protected("initial_genes.json"))
        self.assertTrue(is_protected("project.config.json"))

    def test_protected_directories(self):
        """受保护目录下的文件禁止写入"""
        self.assertTrue(is_protected("routers/orders.py"))
        self.assertTrue(is_protected("tests/test_smoke.py"))
        self.assertTrue(is_protected("static/index.html"))
        self.assertTrue(is_protected("miniprogram/app.js"))

    def test_unprotected_files(self):
        """可写文件（运行时配置、报表输出）不受保护"""
        self.assertFalse(is_protected("config.local.json"))
        self.assertFalse(is_protected("reports/收支报表_2026年8月.xlsx"))
        self.assertFalse(is_protected("data/reports/report.xlsx"))
        self.assertFalse(is_protected("data/sync_log.txt"))

    def test_case_insensitive_evasion(self):
        """大小写变体不能绕过保护（Windows/macOS 大小写不敏感）"""
        # 扩展名大小写变体
        self.assertTrue(is_protected("main.PY"))
        self.assertTrue(is_protected("DB.py"))
        self.assertTrue(is_protected("backup.SQLite"))
        # 目录名大小写变体
        self.assertTrue(is_protected("Static/index.html"))
        self.assertTrue(is_protected("TESTS/test_smoke.py"))
        self.assertTrue(is_protected("MINIPROGRAM/app.js"))
        self.assertTrue(is_protected("Routers/orders.py"))
        # 文件名大小写变体
        self.assertTrue(is_protected("README.MD"))
        self.assertTrue(is_protected(".GITIGNORE"))

    def test_path_traversal_evasion(self):
        """路径穿越（..）不能绕过保护：resolve 规范化后仍识别为受保护目录"""
        self.assertTrue(is_protected("static/../static/index.html"))
        self.assertTrue(is_protected("data/../../server/main.py"))
        # 通过子目录跳到受保护目录
        self.assertTrue(is_protected("whatever/../tests/test_smoke.py"))

    def test_safe_write_blocks_protected(self):
        """safe_write 对受保护文件抛 PermissionError"""
        with self.assertRaises(PermissionError):
            safe_write_text("main.py", "malicious code")
        with self.assertRaises(PermissionError):
            safe_write_json("db.py", {"hack": True})

    def test_safe_write_allows_unprotected(self):
        """safe_write 对非受保护文件正常写入"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "output.txt"
            safe_write_text(p, "hello")
            self.assertEqual(p.read_text(encoding="utf-8"), "hello")
            # 不留 .tmp 残留
            self.assertFalse(Path(str(p) + ".tmp").exists())


class TestAtomicWrite(unittest.TestCase):
    """Pattern 21: 原子写入"""

    def test_atomic_write_json(self):
        """原子写入 JSON：内容正确，不留 .tmp"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            atomic_write_json(p, {"api_key": "sk-test", "model": "gpt-4"})
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")),
                             {"api_key": "sk-test", "model": "gpt-4"})
            self.assertFalse(Path(str(p) + ".tmp").exists())

    def test_atomic_write_text(self):
        """原子写入文本：内容正确，不留 .tmp"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "notes.txt"
            atomic_write_text(p, "hello world")
            self.assertEqual(p.read_text(encoding="utf-8"), "hello world")
            self.assertFalse(Path(str(p) + ".tmp").exists())

    def test_atomic_write_bytes(self):
        """原子写入二进制：内容正确"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "report.xlsx"
            data = b"fake excel content"
            atomic_write_bytes(p, data)
            self.assertEqual(p.read_bytes(), data)

    def test_atomic_write_creates_parent_dir(self):
        """原子写入自动创建父目录"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "subdir" / "deep" / "config.json"
            atomic_write_json(p, {"a": 1})
            self.assertTrue(p.exists())
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"a": 1})

    def test_atomic_write_overwrites_existing(self):
        """原子写入覆盖已有文件"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            atomic_write_json(p, {"old": True})
            atomic_write_json(p, {"new": True})
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"new": True})
            self.assertFalse(Path(str(p) + ".tmp").exists())

    def test_atomic_write_json_ensure_ascii_false(self):
        """原子写入 JSON 保证中文不被转义"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            atomic_write_json(p, {"name": "巷子里的AI掌柜"})
            content = p.read_text(encoding="utf-8")
            self.assertIn("巷子里的AI掌柜", content)
            self.assertNotIn("\\u", content)


class TestConfigAtomicWrite(unittest.TestCase):
    """验证 config.py save_settings 使用原子写入"""

    def test_save_settings_writes_atomically(self):
        """save_settings 写入后不留 .tmp 残留"""
        import config
        with tempfile.TemporaryDirectory() as d:
            # 临时替换 _LOCAL_CONFIG 路径
            orig = config._LOCAL_CONFIG
            config._LOCAL_CONFIG = Path(d) / "config.local.json"
            try:
                result = config.save_settings(api_key="sk-test123", base_url="https://api.test.com", model="test-model")
                self.assertEqual(result["api_key"], "sk-test123")
                self.assertTrue(config._LOCAL_CONFIG.exists())
                # 不留 .tmp 残留
                tmp = Path(str(config._LOCAL_CONFIG) + ".tmp")
                self.assertFalse(tmp.exists())
                # 内容正确
                saved = json.loads(config._LOCAL_CONFIG.read_text(encoding="utf-8"))
                self.assertEqual(saved["api_key"], "sk-test123")
                self.assertEqual(saved["base_url"], "https://api.test.com")
                self.assertEqual(saved["model"], "test-model")
            finally:
                config._LOCAL_CONFIG = orig


class TestReportExportGuard(unittest.TestCase):
    """受保护路径护栏必须真正接在请求可达的写入点上。

    回归背景：is_protected() 原先只被 safe_write_* 调用，而 safe_write_*
    在生产代码里零调用 → 护栏是死代码。报表导出是唯一受请求参数影响落盘
    位置的写入点，现在在此处强制执行。
    """

    def setUp(self):
        import db
        import report as reportlib
        self.reportlib = reportlib
        self.db = db
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._orig_db = db.DB_PATH
        db.DB_PATH = self.tmp / "guard.db"
        db.init_db()
        db.add_transaction(None, "测试", 100, "income", "主营业务收入")

    def tearDown(self):
        self.db.DB_PATH = self._orig_db
        self._tmp.cleanup()

    def test_normal_export_allowed(self):
        r = self.reportlib.get_monthly_report(2026, 9, out_dir=str(self.tmp / "reports"))
        self.assertNotIn("error", r, r)
        self.assertTrue(Path(r["file"]).exists())

    def test_export_into_protected_dir_refused(self):
        """把报表导出到 tests/ 这类受保护目录必须被拒绝，而不是覆盖源码。"""
        protected = self.tmp / "server" / "tests"
        protected.mkdir(parents=True)
        victim = protected / "收支报表_2026年9月.xlsx"
        victim.write_text("原始源码内容", encoding="utf-8")
        r = self.reportlib.get_monthly_report(2026, 9, out_dir=str(protected))
        self.assertIn("error", r)
        self.assertIn("受保护", r["error"])
        # 原文件未被覆盖
        self.assertEqual(victim.read_text(encoding="utf-8"), "原始源码内容")

    def test_export_path_traversal_resolved(self):
        """`..` 穿越到受保护目录同样被拦（is_protected 内部已 resolve 规范化）。"""
        safe_dir = self.tmp / "server" / "reports"
        safe_dir.mkdir(parents=True)
        escape = safe_dir / ".." / "tests"
        (self.tmp / "server" / "tests").mkdir(parents=True, exist_ok=True)
        r = self.reportlib.get_monthly_report(2026, 9, out_dir=str(escape))
        self.assertIn("error", r)
        self.assertIn("受保护", r["error"])


if __name__ == "__main__":
    unittest.main()
