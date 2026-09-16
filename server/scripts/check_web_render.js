#!/usr/bin/env node
/**
 * check_web_render.js — 真跑一遍网页端的渲染函数（Node 里模拟浏览器环境）
 *
 * 为什么需要：静态扫描只能查"函数存不存在"，查不出渲染函数里的运行时错误 ——
 * 模板字符串括号没配平、state 里的字段拼错、`location.origin` 用在 Node 里……
 * 这些在浏览器里表现为"页面一片空白"，最难排查。这里把 20 个 js 按 index.html
 * 的顺序加载（**不是** ES module，就是普通脚本拼接），打桩最小 DOM/浏览器 API，
 * 然后逐个调用 renderXxx()，断言返回非空字符串。
 *
 * 用法：node scripts/check_web_render.js      （仓库根目录或 server 目录都可）
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SERVER = path.resolve(__dirname, '..');
const STATIC = path.join(SERVER, 'static');

// 与 index.html 的加载顺序保持一致（core 必须最先，页面文件随后）
const ORDER = [
  'js/core.js',
  'js/speech.js',
  'js/pages/home.js',
  'js/pages/customers.js',
  'js/pages/copy.js',
  'js/pages/books.js',
  'js/pages/store.js',
  'js/pages/store_render.js',
  'js/pages/finance.js',
  'js/pages/finance_render.js',
  'js/pages/stock.js',
  'js/pages/invoice.js',
  'js/pages/accounting.js',
  'js/pages/backup.js',
  'js/pages/notify.js',
  'js/pages/collect.js',
  'js/pages/shops.js',
  'js/pages/settings.js',
  'js/pages/settings_pay.js',
  'js/init.js',
];

// ---------- 最小浏览器环境打桩 ----------
function makeEl() {
  const el = {
    innerHTML: '', value: '', textContent: '', style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, removeChild() {}, remove() {}, select() {},
    addEventListener() {}, querySelectorAll() { return []; },
    getContext() { return {}; }, setAttribute() {}, focus() {}, blur() {},
  };
  return el;
}

const sandbox = {
  console,
  setTimeout, clearTimeout, setInterval, clearInterval,
  JSON, Math, Date, Number, String, Boolean, Object, Array, RegExp, Error,
  parseFloat, parseInt, isNaN, encodeURIComponent, decodeURIComponent,
  document: {
    getElementById: () => makeEl(),
    querySelector: () => makeEl(),
    querySelectorAll: () => [],
    createElement: () => makeEl(),
    body: makeEl(),
    addEventListener() {},
    execCommand() { return true; },
  },
  location: { hash: '', origin: 'http://192.168.1.5:8000', href: '', pathname: '/', reload() {} },
  navigator: { clipboard: { writeText: () => Promise.resolve() }, userAgent: 'node' },
  localStorage: {
    _d: {},
    getItem(k) { return Object.prototype.hasOwnProperty.call(this._d, k) ? this._d[k] : null; },
    setItem(k, v) { this._d[k] = String(v); },
    removeItem(k) { delete this._d[k]; },
  },
  alert() {}, confirm() { return true; }, prompt() { return ''; },
  fetch: () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('') }),
  FormData: class { append() {} },
  URL: { createObjectURL: () => 'blob:x', revokeObjectURL() {} },
  Blob: class {},
  addEventListener() {},
  requestAnimationFrame(fn) { return setTimeout(fn, 0); },
  window: null,
  globalThis: null,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
// 让 location 的赋值在 vm 里生效（core.js 会写 location.hash）
sandbox.window.location = sandbox.location;

const ctx = vm.createContext(sandbox);

// ---------- 加载脚本 ----------
const problems = [];
for (const rel of ORDER) {
  const p = path.join(STATIC, rel);
  if (!fs.existsSync(p)) {
    problems.push(`缺少脚本：${rel}`);
    continue;
  }
  const code = fs.readFileSync(p, 'utf8');
  try {
    vm.runInContext(code, ctx, { filename: rel });
  } catch (e) {
    problems.push(`${rel} 执行失败（语法/顶层错误）：${e.message}`);
  }
}

// ---------- 逐个跑渲染函数 ----------
const RENDERS = [
  'renderHome', 'renderCustomers', 'renderCopy', 'renderBooks',
  'renderStore', 'renderFinance', 'renderStock', 'renderInvoice',
  'renderAccounting', 'renderBackup', 'renderNotify', 'renderCollect',
  'renderShops', 'renderSettings',
];

console.log(`已在 Node 里加载 ${ORDER.length} 个脚本，准备试跑 ${RENDERS.length} 个渲染函数`);
const ran = [];
for (const fn of RENDERS) {
  const f = ctx[fn];
  if (typeof f !== 'function') {
    problems.push(`渲染函数不存在：${fn}（页面会空白）`);
    continue;
  }
  try {
    const html = f();
    if (typeof html !== 'string' || html.length < 40) {
      problems.push(`${fn}() 返回内容异常（长度 ${typeof html === 'string' ? html.length : typeof html}）`);
    } else if (!/<\w+/.test(html)) {
      problems.push(`${fn}() 没有产出任何 HTML 标签`);
    } else {
      ran.push(`${fn}(${html.length})`);
    }
  } catch (e) {
    problems.push(`${fn}() 抛异常：${e.message}`);
  }
}

// renderCustDetail 在没有选中熟客时**故意**返回空串（`if (!c) return ''`），
// 所以不能按"必须产出 HTML"来断言；这里给它塞一份数据再试一次，
// 才能真正覆盖这个页面的渲染路径。
//
// 注意：core.js 里是 `const state = {...}`，顶层 const 不会挂到 sandbox 上，
// 所以不能写 ctx.state —— 必须把代码放进同一个 context 里执行。
function evalInCtx(code) {
  return vm.runInContext(code, ctx, { filename: 'harness' });
}
try {
  evalInCtx(`state.custDetail = {
    name: '王阿姨', phone: '13800000000', tags: '常客', favorite: '肉包',
    last_visit: '2026-09-15',
    memories: [{ content: '孙子考上了一中' }],
    transactions: [{ item: '肉包', amount: 6, created_at: '2026-09-15 08:10' }],
  }`);
  const html = evalInCtx('renderCustDetail()');
  if (typeof html !== 'string' || !/<\w+/.test(html)) {
    problems.push('renderCustDetail() 在有数据时仍没产出 HTML');
  } else {
    ran.push(`renderCustDetail(${html.length})`);
  }
} catch (e) {
  problems.push(`renderCustDetail() 抛异常：${e.message}`);
}

// ---------- 空数据下也应能渲染（真实的首屏就是空数据） ----------
console.log('渲染通过：' + (ran.join(' ') || '（无）'));

if (problems.length) {
  console.log('\n发现问题：');
  problems.forEach(p => console.log('  ✗ ' + p));
  process.exit(1);
}
console.log('✅ 所有页面渲染函数都能正常产出 HTML');
