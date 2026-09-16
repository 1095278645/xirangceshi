# -*- coding: utf-8 -*-
"""微信支付账单同步的输入校验护栏测试（证书路径 / 下载地址 SSRF）

覆盖审计确认的两个面：
  1. cert_path / private_key_path 直接 open() → 任意文件读取：
     现在要求扩展名合法 + 必须是已存在的普通文件；
  2. download_url 直接 requests.get() → SSRF + 攻击者可控内容：
     现在要求 https + 主机在微信官方域白名单内，且重定向逐跳校验。

运行：cd server && python -m unittest tests.test_wechat_pay_guard -v
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wechat_pay


class TestCertPathGuard(unittest.TestCase):
    """证书/私钥路径护栏"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_accepts_real_pem_file(self):
        p = self.tmp / "apiclient_cert.pem"
        p.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
        wechat_pay._check_cert_path(str(p), "商户证书")  # 不抛异常即通过

    def test_rejects_missing_file(self):
        with self.assertRaises(RuntimeError) as cm:
            wechat_pay._check_cert_path(str(self.tmp / "nope.pem"), "商户证书")
        self.assertIn("不存在", str(cm.exception))

    def test_rejects_arbitrary_extension(self):
        """核心：不能把任意文件（如系统文件）当证书读。"""
        p = self.tmp / "passwords.txt"
        p.write_text("root:x:0:0", encoding="utf-8")
        with self.assertRaises(RuntimeError) as cm:
            wechat_pay._check_cert_path(str(p), "商户证书")
        self.assertIn("扩展名不允许", str(cm.exception))

    def test_rejects_empty_path(self):
        with self.assertRaises(RuntimeError) as cm:
            wechat_pay._check_cert_path("", "商户私钥")
        self.assertIn("未配置", str(cm.exception))

    def test_rejects_directory(self):
        d = self.tmp / "certdir.pem"
        d.mkdir()
        with self.assertRaises(RuntimeError) as cm:
            wechat_pay._check_cert_path(str(d), "商户证书")
        self.assertIn("不是普通文件", str(cm.exception))

    def test_fetch_real_bill_checks_paths_before_open(self):
        """整链路：配置了非法证书路径时，_fetch_real_bill 必须在校验阶段就拒绝。"""
        cfg = {
            "mchid": "1900000109", "cert_path": "C:/Windows/System32/drivers/etc/hosts",
            "private_key_path": "C:/keys/k.pem", "api_v3_key": "x" * 32,
        }
        with self.assertRaises(RuntimeError) as cm:
            wechat_pay._fetch_real_bill(cfg, "2026-09-01")
        self.assertIn("扩展名不允许", str(cm.exception))


class TestDownloadUrlGuard(unittest.TestCase):
    """账单下载地址 SSRF 护栏"""

    def test_accepts_official_host(self):
        u = "https://mch.wechatpay.com/bill/abc.tar.gz"
        self.assertEqual(wechat_pay._check_download_url(u), u)

    def test_accepts_official_subdomain(self):
        u = "https://cdn.mch.wechatpay.com/bill/abc.tar.gz"
        self.assertEqual(wechat_pay._check_download_url(u), u)

    def test_rejects_plain_http(self):
        with self.assertRaises(RuntimeError) as cm:
            wechat_pay._check_download_url("http://mch.wechatpay.com/x")
        self.assertIn("https", str(cm.exception))

    def test_rejects_internal_address(self):
        """核心：不能把请求引向内网/云元数据地址。"""
        for bad in ("https://127.0.0.1/x",
                    "https://169.254.169.254/latest/meta-data/",
                    "https://192.168.1.1/admin",
                    "https://evil.example.com/x"):
            with self.assertRaises(RuntimeError, msg=bad) as cm:
                wechat_pay._check_download_url(bad)
            self.assertIn("白名单", str(cm.exception))

    def test_rejects_lookalike_host(self):
        """后缀匹配不能把 notwechatpay.com 误判为官方域。"""
        with self.assertRaises(RuntimeError) as cm:
            wechat_pay._check_download_url("https://notwechatpay.com/x")
        self.assertIn("白名单", str(cm.exception))

    def test_rejects_file_scheme(self):
        with self.assertRaises(RuntimeError) as cm:
            wechat_pay._check_download_url("file:///etc/passwd")
        self.assertIn("https", str(cm.exception))

    def test_rejects_empty(self):
        with self.assertRaises(RuntimeError) as cm:
            wechat_pay._check_download_url("")
        self.assertIn("未返回", str(cm.exception))

    def test_env_hosts_extends_allowlist(self):
        with mock.patch.dict("os.environ",
                             {"WECHAT_PAY_DOWNLOAD_HOSTS": "proxy.internal.example"}):
            u = "https://proxy.internal.example/bill.gz"
            self.assertEqual(wechat_pay._check_download_url(u), u)


class TestRedirectValidation(unittest.TestCase):
    """重定向逐跳校验：允许官方 CDN 跳转，但跳向内网必须被拦下。"""

    def test_redirect_to_internal_is_blocked(self):
        import requests
        # 直接替换基类方法（而非 patch super()），避免 override 自我递归
        orig = requests.sessions.Session.resolve_redirects

        def fake_resolve(self, resp, req, **kwargs):
            r = requests.Response()
            r.status_code = 302
            r.url = "https://169.254.169.254/latest/meta-data/"
            r.request = req
            yield r

        sess = wechat_pay._validating_redirect_session()
        self.addCleanup(sess.close)
        requests.sessions.Session.resolve_redirects = fake_resolve
        self.addCleanup(setattr, requests.sessions.Session, "resolve_redirects", orig)

        req = requests.Request("GET", "https://mch.wechatpay.com/x").prepare()
        with self.assertRaises(RuntimeError) as cm:
            list(sess.resolve_redirects(None, req))
        self.assertIn("白名单", str(cm.exception))

    def test_redirect_to_official_cdn_allowed(self):
        import requests
        orig = requests.sessions.Session.resolve_redirects

        def fake_resolve(self, resp, req, **kwargs):
            r = requests.Response()
            r.status_code = 302
            r.url = "https://cdn.mch.wechatpay.com/bill/a.tar.gz"
            r.request = req
            yield r

        sess = wechat_pay._validating_redirect_session()
        self.addCleanup(sess.close)
        requests.sessions.Session.resolve_redirects = fake_resolve
        self.addCleanup(setattr, requests.sessions.Session, "resolve_redirects", orig)

        req = requests.Request("GET", "https://mch.wechatpay.com/x").prepare()
        out = list(sess.resolve_redirects(None, req))
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
