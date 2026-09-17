# -*- coding: utf-8 -*-
"""语音转写接口测试：录音上传 → 本地 vosk 转写（mock 转写引擎，不需真模型）

覆盖：参数校验（空/非法/超长）、data URI 前缀容错、转写成功、
模型未安装时 503 降级（前端据此转手动输入）、注册表登记。
运行：cd server && python -m unittest tests.test_voice -v
"""
import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.voice as voice_mod
from routers import registry
from routers.voice import router as voice_router

# 只挂 voice 路由的轻量 app：不拉起 main.py（避免心跳循环/真实 DB）
_app = FastAPI()
_app.include_router(voice_router)
_client = TestClient(_app)

_B64 = base64.b64encode(b"fake-wav-bytes").decode()


class TestVoiceValidation(unittest.TestCase):
    """入参校验：坏输入 4xx，且不触发大模型调用"""

    def test_empty_audio_rejected(self):
        r = _client.post("/api/voice/transcribe", json={"audio": ""})
        self.assertEqual(r.status_code, 400)

    def test_invalid_base64_rejected(self):
        with patch.object(voice_mod.asr, "transcribe") as m:
            r = _client.post("/api/voice/transcribe", json={"audio": "!!!not-base64!!!"})
            self.assertEqual(r.status_code, 400)
            m.assert_not_called()

    def test_oversize_audio_rejected(self):
        big = base64.b64encode(b"x" * (4 * 1024 * 1024 + 1)).decode()
        r = _client.post("/api/voice/transcribe", json={"audio": big})
        self.assertEqual(r.status_code, 413)

    def test_data_uri_prefix_stripped(self):
        """前端若带上 data:audio/wav;base64, 前缀也能正常转写"""
        with patch.object(voice_mod.asr, "transcribe", return_value="今天卖了300块") as m:
            r = _client.post("/api/voice/transcribe",
                             json={"audio": f"data:audio/wav;base64,{_B64}", "format": "wav"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["text"], "今天卖了300块")
            m.assert_called_once()


class TestVoiceTranscribe(unittest.TestCase):
    """转写主链路 + 本地模型不可用降级"""

    def test_transcribe_ok(self):
        with patch.object(voice_mod.asr, "transcribe", return_value="王阿姨买了两个肉包，6块"):
            r = _client.post("/api/voice/transcribe", json={"audio": _B64, "format": "wav"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["text"], "王阿姨买了两个肉包，6块")

    def test_asr_unavailable_returns_503(self):
        """模型未安装 / 加载失败 → 503，前端 toast 后转手动输入"""
        with patch.object(voice_mod.asr, "transcribe",
                          side_effect=RuntimeError("本地语音模型未安装")):
            r = _client.post("/api/voice/transcribe", json={"audio": _B64})
            self.assertEqual(r.status_code, 503)
            self.assertIn("手动输入", r.json()["detail"])

    def test_empty_transcription_returns_empty_text(self):
        """识别成功但没听出内容 → 200 + 空串（前端提示重试/手动输入）"""
        with patch.object(voice_mod.asr, "transcribe", return_value=""):
            r = _client.post("/api/voice/transcribe", json={"audio": _B64})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["text"], "")


class TestVoiceRegistered(unittest.TestCase):
    """语音域必须已在注册表登记且可挂载（声明式注册表自检）"""

    def test_voice_domain_declared(self):
        names = [d["name"] for d in registry.BUSINESS_DOMAINS]
        self.assertIn("voice", names)

    def test_voice_router_mountable(self):
        r = registry._load_router("voice")
        self.assertTrue(any(getattr(x, "path", "") == "/api/voice/transcribe" for x in r.routes))


if __name__ == "__main__":
    unittest.main()
