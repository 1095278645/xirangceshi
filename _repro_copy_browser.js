#!/usr/bin/env node
/**
 * _repro_copy_browser.js —— 真浏览器点线上「生成文案」，抓控制台异常与网络失败。
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
  send(method, params = {}, t = 120000) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      this.pending.set(id, { resolve: res, reject: rej });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => { if (this.pending.has(id)) { this.pending.delete(id); rej(new Error('timeout ' + method)); } }, t);
    });
  }
  async eval(e, ap = true, t = 120000) {
    const r = await this.send('Runtime.evaluate', { expression: e, awaitPromise: ap, returnByValue: true }, t);
    if (r.exceptionDetails) return { __err: r.exceptionDetails.exception?.description || r.exceptionDetails.text };
    return r.result.value;
  }
}

async function main() {
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'cp-'));
  const port = 9811 + Math.floor(Math.random() * 40);
  const proc = spawn(EDGE, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${ud}`,
    '--no-first-run', '--disable-gpu', '--ignore-certificate-errors', '--window-size=412,915', 'about:blank'],
    { stdio: 'ignore' });
  let ws = null;
  const errors = [], failedReqs = [], responses = [];
  try {
    for (let i = 0; i < 60; i++) { try { await (await fetch(`http://127.0.0.1:${port}/json/version`)).json(); break; } catch { await sleep(300); } }
    const t = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
    ws = new WebSocket(t.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.addEventListener('open', res, { once: true }); ws.addEventListener('error', () => rej(new Error('ws')), { once: true }); });
    const cdp = new CDP(ws);
    ws.addEventListener('message', ev => {
      const m = JSON.parse(ev.data);
      if (m.method === 'Runtime.exceptionThrown') {
        const d = m.params.exceptionDetails;
        errors.push((d.exception && d.exception.description) || d.text || '?');
      }
      if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
        errors.push('console.error: ' + m.params.args.map(a => a.value || a.description || '').join(' '));
      }
      if (m.method === 'Network.loadingFailed') failedReqs.push(m.params.errorText);
      if (m.method === 'Network.responseReceived') {
        const r = m.params.response;
        if (/\/api\//.test(r.url)) responses.push(`${r.status} ${r.url.replace(URL_, '')}`);
      }
    });
    await cdp.send('Runtime.enable'); await cdp.send('Page.enable'); await cdp.send('Network.enable');

    // 先写入令牌再进站
    await cdp.send('Page.navigate', { url: URL_ });
    await sleep(4000);
    const setTok = await cdp.eval(`localStorage.setItem('shop_access_token', ${JSON.stringify(TOKEN)}); 'ok'`);
    console.log('写入令牌:', setTok);
    await cdp.send('Page.navigate', { url: URL_ });
    await sleep(5000);

    console.log('页面标题:', await cdp.eval('document.querySelector(".hero-title")?.innerText'));
    // 进入「文案」页
    const nav = await cdp.eval(`(() => { if (typeof go === 'function') { go('copy'); return 'go(copy)'; }
        const b = Array.from(document.querySelectorAll('*')).find(e => e.innerText === '文案');
        if (b) { b.click(); return 'clicked 文案'; } return '未找到入口'; })()`);
    console.log('导航:', nav);
    await sleep(2500);
    console.log('当前页标题:', await cdp.eval('document.querySelector(".hero-title")?.innerText'));
    console.log('按钮存在:', await cdp.eval(`!!document.querySelector('.btn-primary')`));
    console.log('state.copyForm:', JSON.stringify(await cdp.eval('JSON.stringify(state && state.copyForm)')));

    // 点「生成文案」
    const clicked = await cdp.eval(`(() => { const b=document.querySelector('.btn-primary');
        if(!b) return '无按钮'; b.click(); return 'clicked'; })()`);
    console.log('点击生成:', clicked);

    // 观察 25 秒
    for (const s of [3, 8, 15, 25]) {
      await sleep(s === 3 ? 3000 : (s === 8 ? 5000 : 7000));
      const st = await cdp.eval(`JSON.stringify({
        loading: state && state.copyLoading,
        variants: (state && state.copyVariants || []).length,
        btn: document.querySelector('.btn-primary')?.innerText,
        hasResult: !!document.querySelector('.copy-variant'),
        bodyHas: document.body.innerText.includes('挑一条直接发'),
        toast: document.querySelector('.toast')?.innerText || null
      })`);
      console.log(`  T+${s}s:`, st);
    }

    const r = await cdp.send('Page.captureScreenshot', { format: 'png' });
    const shot = path.join(__dirname, '_copy_proof.png');
    fs.writeFileSync(shot, Buffer.from(r.data, 'base64'));

    console.log('\n=== 控制台异常 ===');
    console.log(errors.length ? errors.slice(0, 8).map(e => '  ' + e.split('\n')[0]).join('\n') : '  （无）');
    console.log('=== 失败请求 ===');
    console.log(failedReqs.length ? failedReqs.slice(0, 8).map(e => '  ' + e).join('\n') : '  （无）');
    console.log('=== /api/ 响应 ===');
    console.log(responses.length ? responses.slice(-12).map(e => '  ' + e).join('\n') : '  （无）');
    console.log('截图: ' + shot);
  } finally {
    try { ws && ws.close(); } catch (_) {}
    try { proc.kill(); } catch (_) {}
    await sleep(300);
    try { fs.rmSync(ud, { recursive: true, force: true }); } catch (_) {}
  }
}
main().catch(e => { console.error('异常：' + e.message); process.exit(1); });
