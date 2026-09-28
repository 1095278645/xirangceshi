#!/usr/bin/env node
/**
 * check_mp_runtime.js — 小程序端"运行时"冒烟：在 Node 里用桩把每个页面 JS 真正加载一遍
 *
 * 为什么需要：小程序此前只有**静态**检查（页面是否注册、bindtap 是否有对应函数），
 * 但加载期错误（顶层代码抛错、require 路径写错、Page 内部字段拼错）静态查不出来，
 * 现场表现为"点开页面白屏"。这里用最小 wx/getApp/Page 桩把每个页面真实 require 一遍，
 * 并断言 Page({...}) 的 data 与方法形状存在。
 *
 * 用法：node scripts/check_mp_runtime.js   （仓库根或 server 目录都可）
 */
'use strict';
const fs = require('fs');
const path = require('path');

const MP = path.resolve(__dirname, '..', '..', 'miniprogram');
const problems = [];

// ---------- 最小小程序环境桩 ----------
const pages = [];
function makeWx() {
  const ok = (opt) => { if (opt && typeof opt.success === 'function') opt.success({ statusCode: 200, data: {} }); };
  return {
    request: ok, uploadFile: ok, downloadFile: ok, setClipboardData: ok,
    getStorageSync: () => '', setStorageSync: () => {}, removeStorageSync: () => {},
    showToast: () => {}, hideToast: () => {}, showModal: (o) => { if (o && o.success) o.success({ confirm: true }); },
    showLoading: () => {}, hideLoading: () => {},
    navigateTo: () => {}, redirectTo: () => {}, reLaunch: () => {}, switchTab: () => {}, navigateBack: () => {},
    getSystemInfoSync: () => ({ platform: 'devtools', system: 'node' }),
    getRecorderManager: () => ({ onStop: () => {}, onError: () => {}, start: () => {}, stop: () => {} }),
    createSelectorQuery: () => ({ select: () => ({ boundingClientRect: () => ({ exec: () => {} }) }), exec: () => {} }),
    nextTick: (fn) => fn && fn(),
  };
}
globalThis.wx = makeWx();
globalThis.getApp = () => ({ globalData: { shopName: '冒烟店', baseUrl: 'http://127.0.0.1:8000' } });
globalThis.getCurrentPages = () => [];
globalThis.App = (o) => { globalThis.__app = o; };
globalThis.Component = (o) => { globalThis.__component = o; };
globalThis.Page = (o) => { pages.push(o); };
globalThis.requirePlugin = () => { throw new Error('插件未声明（预期：页面应降级）'); };

function load(rel) {
  const abs = path.join(MP, rel);
  if (!fs.existsSync(abs)) { problems.push(`缺少文件：${rel}`); return null; }
  const before = pages.length;
  try {
    delete require.cache[require.resolve(abs)];
    require(abs);
  } catch (e) {
    problems.push(`${rel} 加载失败：${e.message}`);
    return null;
  }
  if (/pages\//.test(rel) && pages.length === before) {
    problems.push(`${rel} 没有调用 Page({...})`);
    return null;
  }
  return pages[pages.length - 1] || true;
}

// ---------- 逐个加载页面 ----------
const pageDirs = fs.readdirSync(path.join(MP, 'pages'))
  .filter((d) => fs.statSync(path.join(MP, 'pages', d)).isDirectory());

let loaded = 0;
for (const d of pageDirs) {
  const rel = path.join('pages', d, `${d}.js`);
  const cfg = load(rel);
  if (!cfg || cfg === true) continue;
  loaded++;
  if (!cfg.data || typeof cfg.data !== 'object') {
    problems.push(`${rel} 的 Page 缺少 data 对象`);
  }
  const fns = Object.keys(cfg).filter((k) => typeof cfg[k] === 'function');
  if (fns.length === 0) problems.push(`${rel} 的 Page 没有任何方法`);
}
load('app.js');
load('utils/api.js');

// ---------- 顺带：共享契约在小程序侧可用（与其他检查同源） ----------
try {
  const fc = require(path.join(MP, 'shared', 'frontend_contract.js'));
  if (typeof fc.labelStockMovement !== 'function') problems.push('共享契约在小程序 require 下不可用');
  if (fc.STORAGE_KEYS.token !== 'shop_access_token') problems.push('共享契约存储键异常');
} catch (e) {
  problems.push('共享契约加载失败：' + e.message);
}

console.log(`已加载页面 ${loaded} 个（Page 配置捕获 ${pages.length} 个）`);
if (problems.length) {
  console.log('\n发现问题：');
  problems.forEach((p) => console.log('  ✗ ' + p));
  process.exit(1);
}
console.log('✅ 小程序页面在 Node 运行时桩下均可加载，且 Page 结构完整');
