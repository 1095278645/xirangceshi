// 语音记账：录音上传 → 服务端本地 vosk 转写（不走浏览器 Web Speech / Google 服务）
// 依赖 core.js 的 state/toast/render/api、home.js 的 submitOrder。
// 降级策略：麦克风不可用 / 转写失败 → toast 提示，用户改用页面下方手动输入。
'use strict';

const VOICE_TARGET_RATE = 16000;      // 下行采样率：16kHz 16bit 单声道，转写够用且省流量
const VOICE_MAX_MS = 60000;           // 最长录音 60 秒自动停，防长按忘松
const VOICE_MIN_MS = 300;             // 短于 0.3 秒视为误触
const VOICE_TRANSCRIBE_TIMEOUT_MS = 30000;  // 转写请求超时

let _mediaStream = null;
let _audioCtx = null;
let _recorder = null;                 // ScriptProcessorNode
let _pcmChunks = [];                  // Float32 采样片段
let _recordTimer = null;
let _transcribing = false;

function initSpeech() {
  const ok = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)
    && window.isSecureContext !== false;
  state.voiceSupported = ok;
}

// ---------- 录音 ----------

async function startRecord() {
  if (!state.voiceSupported) { toast('当前环境不支持录音，请手动输入'); return; }
  if (state.recognizing || _transcribing) return;
  state.result = '';
  state.parsed = null;
  try {
    _mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    toast('麦克风不可用或未授权，请手动输入');
    return;
  }
  _pcmChunks = [];
  _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const source = _audioCtx.createMediaStreamSource(_mediaStream);
  _recorder = _audioCtx.createScriptProcessor(4096, 1, 1);
  _recorder.onaudioprocess = (e) => {
    _pcmChunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  };
  source.connect(_recorder);
  _recorder.connect(_audioCtx.destination);
  state.recognizing = true;
  _recordTimer = setTimeout(stopRecord, VOICE_MAX_MS);
  render();
}

function _cleanupRecord() {
  if (_recordTimer) { clearTimeout(_recordTimer); _recordTimer = null; }
  try { if (_recorder) _recorder.disconnect(); } catch (e) { /* 忽略 */ }
  try { if (_audioCtx) _audioCtx.close(); } catch (e) { /* 忽略 */ }
  try { if (_mediaStream) _mediaStream.getTracks().forEach(t => t.stop()); } catch (e) { /* 忽略 */ }
  _recorder = null; _audioCtx = null; _mediaStream = null;
}

async function stopRecord() {
  if (!state.recognizing) return;
  const rate = _audioCtx ? _audioCtx.sampleRate : 0;
  const chunks = _pcmChunks;
  _pcmChunks = [];
  _cleanupRecord();
  state.recognizing = false;
  render();

  let merged = new Float32Array(0);
  let total = 0;
  for (const c of chunks) total += c.length;
  if (total) {
    merged = new Float32Array(total);
    let off = 0;
    for (const c of chunks) { merged.set(c, off); off += c.length; }
  }
  if (!rate || merged.length < VOICE_TARGET_RATE * VOICE_MIN_MS / 1000) {
    toast('没听到声音，请重试或手动输入');
    return;
  }
  await _transcribe(_encodeWav(_resample(merged, rate, VOICE_TARGET_RATE), VOICE_TARGET_RATE));
}

// ---------- 转写（上传大模型） ----------

async function _transcribe(wavBytes) {
  _transcribing = true;
  const el = document.querySelector('.voice-result');
  if (el) el.textContent = 'AI 转写中…';
  try {
    const body = { audio: _b64FromBytes(wavBytes), format: 'wav' };
    const res = await _withTimeout(
      api('/api/voice/transcribe', 'POST', body),
      VOICE_TRANSCRIBE_TIMEOUT_MS, '语音转写超时');
    const text = (res.text || '').trim();
    if (!text) { toast('没听清，请重试或手动输入'); return; }
    submitOrder(text);
  } catch (e) {
    toast((e.message || '语音转写失败') + '，请手动输入');
  } finally {
    _transcribing = false;
  }
}

function _withTimeout(promise, ms, msg) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(msg)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

// ---------- 音频处理工具 ----------

function _resample(input, fromRate, toRate) {
  if (fromRate === toRate || !input.length) return input;
  const ratio = fromRate / toRate;
  const out = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const pos = i * ratio, idx = Math.floor(pos), frac = pos - idx;
    const a = input[idx] || 0, b = idx + 1 < input.length ? input[idx + 1] : a;
    out[i] = a + (b - a) * frac;
  }
  return out;
}

function _encodeWav(samples, sampleRate) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const ws = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  ws(0, 'RIFF'); v.setUint32(4, 36 + samples.length * 2, true); ws(8, 'WAVE');
  ws(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * 2, true);
  v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  ws(36, 'data'); v.setUint32(40, samples.length * 2, true);
  let o = 44;
  for (let i = 0; i < samples.length; i++, o += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }
  return new Uint8Array(buf);
}

function _b64FromBytes(bytes) {
  let bin = '';
  const CHUNK = 0x8000;   // 分块拼接，避免 apply 参数栈溢出
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(bin);
}
