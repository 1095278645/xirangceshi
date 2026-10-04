#!/usr/bin/env node
/**
 * _repro_copy_uncached.js —— 用唯一 payload 绕过缓存，测真实等待时间与界面反馈。
 */
'use strict';
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const URL_ = process.env.XIRANG_BASE || 'https://124.222.74.116';
const TOKEN = process.env.XIRANG_TOKEN || '';
const EDGE = ['C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe'].find(p => fs.existsSync(p));
const sleep = ms => new Promise(r => setTimeout(r, ms));

class CDP {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map();
    ws.addEventListener('message', ev => {
      const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) {
        const { resolve, reject } = this.pending.get(m.id);
        this.pending.delete(m.id);
        m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
      }
    });
  }
  send(method, params = {}, t = 180000) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      this.pending.set(id, { resolve: res, reject: rej });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => { if (this.pending.has(id)) { this.pending.delete(id); rej(new Error('timeout ' + method)); } }, t);
    });
  }
  async eval(e, ap = true, t = 180000) {
    const r = await this.send('Runtime.evaluate', { expression: e, awaitPromise: ap, returnByValue: true }, t);
    if (r.exceptionDetails) return { __err: r.exceptionDetails.exception?.description || r.exceptionDetails.text };
    return r.result.value;
  }
}

async function main() {
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'cu-'));
  const port = 9851 + Math.floor(Math.random() * 40);
  const proc = spawn(EDGE, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${ud}`,
    '--no-first-run', '--disable-gpu', '--ignore-certificate-errors', '--window-size=412,915', 'about:blank'],
    { stdio: 'ignore' });
  let ws = null;
  const errors = [], api = [];
  try {
    for (let i = 0; i < 60; i++) { try { await (await fetch(`http://127.0.0.1:${port}/json/version`)).json(); break; } catch { await sleep(300); } }
    const t = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
    ws = new WebSocket(t.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.addEventListener('open', res, { once: true }); ws.addEventListener('error', () => rej(new Error('ws')), { once: true }); });
    const cdp = new CDP(ws);
    ws.addEventListener('message', ev => {
      const m = JSON.parse(ev.data);
      if (m.method === 'Runtime.exceptionThrown') errors.push((m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text || '').split('\n')[0]);
      if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push('console.error: ' + m.params.args.map(a => a.value || a.description || '').join(' ').split('\n')[0]);
      if (m.method === 'Network.responseReceived' && /\/api\/insights/.test(m.params.response.url)) api.push(m.params.response.status + ' ' + m.params.response.url.replace(URL_, ''));
    });
    await cdp.send('Runtime.enable'); await cdp.send('Page.enable'); await cdp.send('Network.enable');
    await cdp.send('Page.navigate', { url: URL_ });
    await sleep(4000);
    await cdp.eval(`localStorage.setItem('shop_access_token', ${JSON.stringify(TOKEN)})`);
    await cdp.send('Page.navigate', { url: URL_ });
    await sleep(5000);
    await cdp.eval(`go('copy')`);
    await sleep(2000);

    // 唯一补充信息 → 绕过服务端分析缓存
    const uniq = '今天新到了' + Date.now() % 100000 + '号土鸡蛋';
    await cdp.eval(`state.copyForm.extra = ${JSON.stringify(uniq)}; render(); 'ok'`);
    console.log('唯一 payload extra =', uniq);

    const t0 = Date.now();
    await cdp.eval(`document.querySelector('.btn-primary').click(); 'clicked'`);
    const marks = [];
    for (let i = 0; i < 24; i++) {
      await sleep(2500);
      const st = await cdp.eval(`JSON.stringify({btn:(document.querySelector('.btn-primary')||{}).innerText,
        card:(document.body.innerText.includes('正在生成')?'生成中卡片':'-'),
        variants:(state.copyVariants||[]).length, has:.length})`.replace('has:.length', `has:!!document.querySelector('.copy-variant')`));
      const s = JSON.parse(st);
      const el = ((Date.now() - t0) / 1000).toFixed(1);
      console.log(`  T+${el}s  按钮=${s.btn}  ${s.card}  结果=${s.has ? '已出' : '未出'}`);
      if (s.has) { marks.push(el); break; }
    }
    const r = await cdp.send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(__dirname, '_copy_uncached.png'), Buffer.from(r.data, 'base64'));
    console.log('\n浮出耗时:', marks[0] ? marks[0] + 's' : '未出（超时 60s）');
    console.log('控制台异常:', errors.length ? errors.slice(0, 5) : '（无）');
    console.log('/api/insights 响应:', api.length ? api : '（无）');
  } finally {
    try { ws && ws.close(); } catch (_) {}
    try { proc.kill(); } catch (_) {}
    await sleep(300);
    try { fs.rmSync(ud, { recursive: true, force: true }); } catch (_) {}
  }
}
main().catch(e => { console.error('异常：' + e.message); process.exit(1); });
