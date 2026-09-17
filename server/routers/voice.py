"""语音接口：录音上传 → 本地 vosk 转写（纯本地优先；失败时前端降级手动输入）

与旧版 Web Speech API（浏览器/Google 服务）的区别：识别完全发生在本机，
不依赖手机浏览器、不依赖外网与大模型 Key，局域网演示断网也能用。
"""
import base64
import binascii

from fastapi import APIRouter, HTTPException

import asr
from schemas import VoiceIn

router = APIRouter(prefix="/api", tags=["voice"])

_MAX_AUDIO_BYTES = 4 * 1024 * 1024   # 单条录音上限 4MB（16kHz 16bit WAV ≈ 2 分钟）


@router.post("/voice/transcribe")
def transcribe(data: VoiceIn):
    """前端录音 base64 WAV → 本地 vosk 转写文字；失败返回 5xx，前端 toast 后转手动输入。"""
    audio = (data.audio or "").strip()
    if audio.startswith("data:"):   # 容错：剥掉录音库可能带的 data URI 前缀
        audio = audio.split(",", 2)[-1]
    try:
        raw = base64.b64decode(audio, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "音频数据不是合法的 base64")
    if not raw:
        raise HTTPException(400, "音频内容为空，请重试或手动输入")
    if len(raw) > _MAX_AUDIO_BYTES:
        raise HTTPException(413, "录音太长，请控制在 2 分钟以内")
    try:
        text = asr.transcribe(raw)
    except Exception as e:  # noqa: BLE001
        # 模型未安装 / 加载失败 → 503，前端降级手动输入
        raise HTTPException(503, f"语音转写不可用，请手动输入（{e}）")
    return {"text": text}
