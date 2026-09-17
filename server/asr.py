"""asr.py — 本地语音转写引擎（vosk 离线识别，无 Key / 无外网依赖）

分层定位：与 store.py / tax.py 同级的「计算引擎」层，被 routers/voice.py 调用。
设计原则（与项目一致）：
  - 纯本地优先：识别全部在本机完成，断网也能用，符合「纯本地算法优先」；
  - 渐进披露：模型懒加载，首次转写时才占用内存（约 100MB 级）；
  - 优雅降级：模型未安装 / 解包失败 → 抛 RuntimeError，路由层转 503，前端降级手动输入。

模型放置：server/models/vosk-model-small-cn-0.22/（解压后目录，git 已忽略）。
下载：https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip
"""
import json
import logging
import os
from pathlib import Path
from threading import Lock

import config

log = logging.getLogger("asr")

_MODEL_DIR_NAME = "vosk-model-small-cn-0.22"
_SAMPLE_RATE = 16000          # 与前端 speech.js 录音的下行采样率一致
_CHUNK = 4096                 # 喂识别器的块大小（字节）

_model = None                 # 进程内懒加载单例（vosk Model 线程安全，可并发共用）
_model_lock = Lock()


def _model_dir() -> Path:
    return config.BASE_DIR / "models" / _MODEL_DIR_NAME


def _vosk_loadable_path(d: Path) -> str:
    """生成 vosk 实际能打开的路径字符串。

    vosk 的 Python 侧把路径按 UTF-8 编码传入，C++ 底层却用 ANSI（中文系统=GBK）
    方式 fopen，因此 Windows 上含中文等非 ASCII 字符的绝对路径必然打开失败
    （本机实测：`C:\\...\\新建文件夹\\...` 报 Failed to create a model）。
    退化策略：8.3 短路径 → 相对当前工作目录的相对路径（两者均需为纯 ASCII）。
    模型文件在 Model 构造时全部载入内存，构造完成后路径不再被使用，
    因此相对路径只需在加载那一刻有效。
    """
    s = str(d)
    if s.isascii():
        return s
    if os.name == "nt":  # 尝试 8.3 短路径（部分环境可用）
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(2048)
            if ctypes.windll.kernel32.GetShortPathNameW(s, buf, 2048) and buf.value.isascii():
                return buf.value
        except Exception:  # noqa: BLE001
            pass
    try:  # 相对当前工作目录（uvicorn 常以 server/ 或项目根为 cwd，两者均不含中文）
        rel = os.path.relpath(s)
        if rel.isascii() and os.path.exists(rel):
            return rel
    except Exception:  # noqa: BLE001
        pass
    return s   # 兜底：按原路径交给 vosk，失败则由调用链降级手动输入


def model_ready() -> bool:
    """本地语音模型是否已就位（供健康检查 / 前端提示用，不触发加载）"""
    d = _model_dir()
    return (d / "conf" / "model.conf").exists() or (d / "final.mdl").exists()


def _get_model():
    """懒加载并复用 vosk 模型；缺失时抛 RuntimeError（前端降级手动输入）"""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        d = _model_dir()
        if not model_ready():
            raise RuntimeError(
                f"本地语音模型未安装：请将 {_MODEL_DIR_NAME} 解压到 server/models/ 后重启"
                f"（下载地址 https://alphacephei.com/vosk/models/{_MODEL_DIR_NAME}.zip）")
        from vosk import Model  # 惰性 import：未装 vosk 包时不影响其它功能启动
        _model = Model(_vosk_loadable_path(d))
        log.info("vosk model loaded from %s", d)
        return _model


def transcribe(wav_bytes: bytes) -> str:
    """把一段 16kHz 16bit 单声道 WAV 转写成中文文本；识别不到内容返回空串。

    整段识别（非流式按键说话场景足够快：10 秒音频在 i5 级 CPU 上约 1-3 秒出结果）。
    AcceptWaveform 返回 True 表示说话人停顿分段，该段 Result() 需收集；
    最后用 FinalResult() 收尾剩余部分。
    """
    model = _get_model()
    from vosk import KaldiRecognizer, SetLogLevel
    SetLogLevel(-1)                                   # 静默 Kaldi 内部日志刷屏
    rec = KaldiRecognizer(model, _SAMPLE_RATE)
    parts = []
    for i in range(0, len(wav_bytes), _CHUNK):
        if rec.AcceptWaveform(wav_bytes[i:i + _CHUNK]):
            seg = json.loads(rec.Result()).get("text", "")
            if seg:
                parts.append(seg)
    tail = json.loads(rec.FinalResult()).get("text", "")
    if tail:
        parts.append(tail)
    return " ".join(p for p in parts if p).strip()
