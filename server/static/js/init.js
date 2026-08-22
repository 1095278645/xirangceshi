// 初始化：hash 路由 / 底部导航绑定 / 语音初始化 / 首次渲染
// 必须在所有 js/core.js、js/speech.js、js/pages/*.js 之后加载。
'use strict';

// ---------- 初始化 ----------
window.addEventListener('hashchange', () => {
  const r = location.hash.slice(1) || 'home';
  if (r !== state.route) go(r);
});

document.querySelectorAll('.tab-item').forEach(t => {
  t.addEventListener('click', (e) => { e.preventDefault(); go(t.dataset.route); });
});

// 更多抽屉：遮罩点击关闭，功能项点击跳转并关闭
document.getElementById('moreMask').addEventListener('click', closeMore);
document.querySelectorAll('.more-item').forEach(it => {
  it.addEventListener('click', () => { closeMore(); go(it.dataset.route); });
});

initSpeech();
const initRoute = location.hash.slice(1) || 'home';
state.route = initRoute;
render();
go(initRoute);