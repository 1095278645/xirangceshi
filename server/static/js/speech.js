// 语音识别（Web Speech API，依赖 core.js 的 state/toast/render）
'use strict';

let recognition = null;

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
    render();
  };
  recognition.onend = () => {
    state.recognizing = false;
    if (state.result) {
      submitOrder(state.result);
    }
    render();
  };
  recognition.onerror = () => {
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
    render();
  } catch (e) {
    toast('语音启动失败，请手动输入');
  }
}

function stopRecord() {
  if (recognition && state.recognizing) recognition.stop();
}