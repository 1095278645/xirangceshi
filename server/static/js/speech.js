// 语音识别（Web Speech API，依赖 core.js 的 state/toast/render）
'use strict';

let recognition = null;
let _recognizeTimer = null;
// 识别超时（毫秒）：浏览器长期无结果/无结束事件时强制停止，避免 UI 卡在"识别中"
const RECOGNIZE_TIMEOUT = 10000;

function _clearRecognizeTimer() {
  if (_recognizeTimer) {
    clearTimeout(_recognizeTimer);
    _recognizeTimer = null;
  }
}

function _startRecognizeTimer() {
  _clearRecognizeTimer();
  _recognizeTimer = setTimeout(() => {
    _recognizeTimer = null;
    state.recognizing = false;
    toast('语音识别超时，已停止，请重试或手动输入');
    // 尝试强制结束浏览器识别会话，避免其持续占用麦克风
    try { if (recognition) recognition.abort(); } catch (e) { /* 忽略 */ }
    render();
  }, RECOGNIZE_TIMEOUT);
}

function initSpeech() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    state.voiceSupported = false;
    return;
  }
  state.voiceSupported = true;
  recognition = new SR();
  recognition.lang = 'zh-CN';
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.onresult = (e) => {
    let txt = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      txt += e.results[i][0].transcript;
    }
    state.result = txt;
    // 中间结果只更新结果文本，避免全量 render 打断按住的按钮/丢失焦点
    const el = document.querySelector('.voice-result');
    if (el) el.textContent = txt;
    else render();
  };
  recognition.onend = () => {
    _clearRecognizeTimer();
    state.recognizing = false;
    if (state.result) {
      submitOrder(state.result);
    }
    render();
  };
  recognition.onerror = () => {
    _clearRecognizeTimer();
    state.recognizing = false;
    toast('语音不可用（非 HTTPS/localhost 下浏览器可能禁用），请手动输入');
    render();
  };
}

function startRecord() {
  if (!recognition) {
    toast('当前浏览器不支持语音，请手动输入');
    return;
  }
  state.result = '';
  state.parsed = null;
  try {
    recognition.start();
    state.recognizing = true;
    _startRecognizeTimer();
    render();
  } catch (e) {
    toast('语音启动失败，请手动输入');
  }
}

function stopRecord() {
  _clearRecognizeTimer();
  if (recognition && state.recognizing) recognition.stop();
}