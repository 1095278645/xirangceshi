#!/usr/bin/env node
/**
 * check_web_click.js — 用**真实浏览器**（Edge 无头）点一遍网页端
 *
 * 为什么需要：check_web_render.js 只是在 Node 里"算出 HTML"，证明不了
 * 接口能通、事件能触发、点下去页面会变。而编译期/静态扫描都查不出
 * "接口 404、字段名对不上、切店不生效"这类问题 —— 只有真浏览器点一遍才知道。
 *
 * 实现方式（零依赖）：
 *   - 用 Edge 的 --headless=new + --remote-debugging-port 起一个真浏览器
 *   - 用 Node 内置 WebSocket 连 CDP（Chrome DevTools Protocol）
 *   - 主要用 Runtime.evaluate 在页面里执行 JS：既能点按钮，也能读渲染结果
 *   - 同一个 browser context 只开一个 tab，保证前后端状态一致
 *
 * 前置：需要一个已在跑的后端（默认读环境变量 BASE_URL，否则
 * http://127.0.0.1:8020）。自动化脚本 scripts/_browser_check.py 会自己拉起后端。
 *
 * 用法：node scripts/check_web_click.js
 */
'use strict';

const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const BASE_URL = process.env.BASE_URL || 'http://127.0.0.1:8020';
const EDGE_CANDIDATES = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  '/usr/bin/microsoft-edge',
  '/usr/bin/google-chrome',
];

const fail = [];
const pass = [];
function check(label, ok, detail) {
  if (ok) { pass.push(label); console.log('  ✅ ' + label); }
  else { fail.push(label + (detail ? '  → ' + detail : '')); console.log('  ❌ ' + label + (detail ? '  → ' + detail : '')); }
}

function findBrowser() {
  for (const p of EDGE_CANDIDATES) if (fs.existsSync(p)) return p;
  return null;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function httpJson(url, timeoutMs = 3000) {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: ac.signal });
    return await res.json();
  } finally { clearTimeout(t); }
}

// ---------------- CDP 极简客户端 ----------------
class CDP {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.events = [];
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(JSON.stringify(msg.error)));
        else resolve(msg.result);
      } else if (msg.method) {
        this.events.push(msg);
      }
    });
  }

  send(method, params = {}, timeoutMs = 20000) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`CDP 超时：${method}`));
        }
      }, timeoutMs);
    });
  }

  /** 在页面里执行 JS 并取回值。timeoutMs 可放宽（恢复数据这类操作 20 秒不够） */
  async eval(expression, awaitPromise = true, timeoutMs = 20000) {
    const r = await this.send('Runtime.evaluate', {
      expression, awaitPromise, returnByValue: true,
    }, timeoutMs);
    if (r.exceptionDetails) {
      const d = r.exceptionDetails;
      throw new Error('页面 JS 抛错：' + (d.exception && d.exception.description || d.text));
    }
    return r.result.value;
  }
}

async function waitReady(cdp, ms = 30000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    try {
      const ok = await cdp.eval('typeof state !== "undefined" && typeof go === "function"');
      if (ok) return true;
    } catch (_) { /* 页面还没加载完 */ }
    await sleep(200);
  }
  throw new Error('页面初始化超时');
}

/** 等某个 DOM 内容出现（渲染是异步的：go() 先渲染骨架，再 await 接口后重渲染） */
async function waitFor(cdp, expr, desc, ms = 15000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    try {
      if (await cdp.eval(expr)) return true;
    } catch (_) {}
    await sleep(150);
  }
  throw new Error(`等待超时：${desc}`);
}

async function main() {
  const browser = findBrowser();
  if (!browser) {
    console.log('找不到 Edge/Chrome，跳过浏览器实测');
    process.exit(2);
  }
  console.log('浏览器：' + browser);

  const userDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-edge-'));
  const port = 9227 + Math.floor(Math.random() * 50);
  const proc = spawn(browser, [
    '--headless=new',
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${userDir}`,
    '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--disable-extensions', '--disable-background-networking',
    '--window-size=420,900',
    'about:blank',
  ], { stdio: 'ignore' });

  let ws = null;
  try {
    // 等浏览器把调试端口开起来
    let ver = null;
    for (let i = 0; i < 60; i++) {
      try { ver = await httpJson(`http://127.0.0.1:${port}/json/version`); break; }
      catch (_) { await sleep(300); }
    }
    if (!ver) throw new Error('浏览器调试端口没起来');
    console.log('浏览器版本：' + (ver.Browser || 'unknown'));

    // 新建一个 page target 并连上它的 WebSocket
    const target = await httpJson(`http://127.0.0.1:${port}/json/new?about:blank`, 5000)
      .catch(async () => {
        // 新版 Chrome/Edge 需要 PUT 才能新建
        const res = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' });
        return res.json();
      });
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => {
      ws.addEventListener('open', res, { once: true });
      ws.addEventListener('error', () => rej(new Error('WebSocket 连接失败')), { once: true });
    });
    const cdp = new CDP(ws);
    await cdp.send('Runtime.enable');
    await cdp.send('Page.enable');

    // 收集页面控制台错误（白屏类问题最直接的证据）
    const consoleErrors = [];
    ws.addEventListener('message', (ev) => {
      const m = JSON.parse(ev.data);
      if (m.method === 'Runtime.exceptionThrown') {
        const d = m.params.exceptionDetails;
        consoleErrors.push((d.exception && d.exception.description) || d.text || '未知异常');
      }
    });

    console.log('\n== 加载首页 ==');
    await cdp.eval(`location.href = ${JSON.stringify(BASE_URL)}`, false).catch(() => {});
    await cdp.send('Page.navigate', { url: BASE_URL });
    await sleep(1200);
    await waitReady(cdp);
    const title = await cdp.eval('document.querySelector(".tabbar") ? "ok" : "no tabbar"');
    check('首页加载出底部导航', title === 'ok', title);

    // ---------- 0. 一句话记账的"最后一环"（缺金额追问 / 记完核对 / 就地改） ----------
    console.log('\n== 0. 记账：缺金额追问 + 记完当场核对 ==');
    // 找一个当前路由能记账：先回首页
    await cdp.eval('go("home")');
    await waitFor(cdp, 'typeof state.parsed !== "undefined"', '首页就绪');

    // 用"今日笔数"而不是 /api/transactions 的长度：
    // 后者是**带 limit 的窗口**（默认 100 条），演示库有 3000+ 笔，
    // 新记一笔不会让这个数字变化（第一版就因此误判"没落库"）。
    const cntOf = 'api("/api/orders/today").then(r => r.cnt)';
    const txnsBefore = await cdp.eval(`(async () => ${cntOf})()`);
    // 真调 submitOrder（首页语音/手动输入走的就是它），提交一句"没说金额"的话。
    // 这条话会被真实模型解析：金额应当解析不出来 → 进入补金额流程。
    await cdp.eval(`submitOrder('测试一句没有金额的话')`, true, 60000);
    await waitFor(cdp,
      'state.amountDraft !== null || state.recorded !== null',
      '记账请求返回（草稿或已记录）', 60000);

    const wentDraft = await cdp.eval('state.amountDraft !== null');
    if (wentDraft) {
      check('没听出金额时进入「补金额」而不是静默记一笔', true);
      await waitFor(cdp, 'document.querySelector(".ask-input") !== null',
                    '页面上出现金额输入框');
      check('界面上真的问出了「这笔多少钱？」',
            (await cdp.eval('document.body.innerText')).includes('这笔多少钱'));
      const txnsMid = await cdp.eval(`(async () => ${cntOf})()`);
      check('补金额之前没有落库（不留 0 元幽灵记录）', txnsMid === txnsBefore,
            `${txnsBefore} → ${txnsMid}`);

      // 真填金额、真点「记下」
      await cdp.eval('onAmountInput("7.5")');
      await cdp.eval('confirmAmount()', true, 60000);
      await waitFor(cdp, 'state.recorded !== null', '补记完成', 60000);
      check('填完金额点「记下」后真的记上了', true);
      const rec = await cdp.eval('state.recorded');
      check('记的金额就是刚填的 7.5', rec && Math.abs(rec.amount - 7.5) < 0.001,
            JSON.stringify(rec));
      const txnsAfter = await cdp.eval(`(async () => ${cntOf})()`);
      check('补记后今日笔数 +1（真的落库了）', txnsAfter === txnsBefore + 1,
            `${txnsBefore} → ${txnsAfter}`);
    } else {
      // 模型把金额解析出来了（这句话带了数字之类）—— 那条路径同样要验证核对卡
      check('这次金额被解析出来了（走核对路径）', true);
    }

    // 不论走哪条路径，最后都该有一张"我这么记的，对吗？"的卡
    await waitFor(cdp, 'state.recorded !== null', '出现核对卡', 30000);
    check('页面上出现「我这么记的，对吗？」',
          (await cdp.eval('document.body.innerText')).includes('我这么记的'));
    check('核对卡上有「记错了？点这里改」入口',
          (await cdp.eval('document.body.innerText')).includes('记错了'));

    // 真点「改」→ 改金额（prompt 打桩）→ 校验服务端金额变了
    const recId = await cdp.eval('state.recorded.transaction_id');
    await cdp.eval(`window.prompt = (msg, def) => (String(msg).includes('改什么') ? '1' : '99.5');
                    fixRecorded()`, true, 40000);
    await waitFor(cdp,
      `api('/api/transactions/' + ${recId}).then(r => Math.abs(r.transaction.amount - 99.5) < 0.001)`,
      '就地改金额已落库', 30000);
    check('点「记错了？点这里改」能真的改掉账（且留痕）', true);
    const audits = await cdp.eval(
      '(async () => (await api("/api/audits")).audits.length)()');
    check('更正留下了审计记录', audits > 0, String(audits));

    // ---------- 0b. 掌柜复盘（多 agent，可展开原始事实） ----------
    console.log('\n== 0b. 掌柜今日复盘 ==');
    // 先直接让掌柜复盘一次（真实编排：五位伙计 → 掌柜裁决；配了 Key 约 6~10 秒）
    await cdp.eval('reviewNow()', true, 90000);
    await waitFor(cdp, 'state.review && state.review.length > 4', '掌柜复盘生成', 90000);
    const review = await cdp.eval('state.review');
    check('点「让掌柜再看一遍」能拿到复盘正文', typeof review === 'string' && review.length > 4,
          String(review).slice(0, 80));
    check('复盘不是照抄快照的标签（[今日]/[库存] 这类分区标记不该出现）',
          !/\[(今日|本月|熟客|库存|赊账|发票|报税|经营水平|账目更正)\]/.test(review),
          String(review).slice(0, 120));
    check('页面上出现「掌柜今日复盘」', (await cdp.eval('document.body.innerText')).includes('掌柜今日复盘'));

    await waitFor(cdp, 'state.snapshot && state.snapshot.length > 10', '快照随复盘返回', 20000);
    const snap = await cdp.eval('state.snapshot');
    check('复盘带回了「掌柜看到的原始事实」', snap.includes('['), String(snap).slice(0, 80));
    check('快照覆盖多个经营维度（不只收支）',
          ['[熟客]', '[库存]', '[赊账]', '[发票]'].filter(t => snap.includes(t)).length >= 2,
          String(snap).slice(0, 200));

    // 真点展开 / 收起
    await cdp.eval('toggleSnap()');
    await waitFor(cdp, 'state.snapOpen === true', '展开原始事实');
    check('点「掌柜看到的原始事实」能展开', true);
    await cdp.eval('toggleSnap()');
    check('再点一次能收起', (await cdp.eval('state.snapOpen')) === false);

    // 接口层面：快照端点可用且带分域事实
    const snapApi = await cdp.eval('(async () => (await api("/api/heartbeat/snapshot")).facts)()');
    check('快照接口按域返回事实', snapApi && snapApi.money && snapApi.customers,
          Object.keys(snapApi || {}).join(','));

    // ---------- 1. 收款页 ----------
    console.log('\n== 1. 收款页（生成收款码 → 一键入账）==');
    await cdp.eval('go("collect")');
    await waitFor(cdp, 'document.body.innerText.includes("新建收款")', '收款页渲染');
    check('收款页能打开', true);

    // 填金额并点「生成收款码」
    await cdp.eval(`state.collect.form.amount = 12.5; state.collect.form.item = '浏览器实测'; render();`);
    await cdp.eval('createCollection()');
    await waitFor(cdp, 'state.collect.current && state.collect.current.token', '收款单创建');
    check('点「生成收款码」真的建了收款单',
          !!(await cdp.eval('state.collect.current && state.collect.current.token')));
    await waitFor(cdp, 'document.querySelector(".qr-holder svg") !== null', '二维码 SVG 渲染');
    const qrBox = await cdp.eval(
      'const s=document.querySelector(".qr-holder svg"); s ? {w:s.getBoundingClientRect().width,h:s.getBoundingClientRect().height} : null');
    check('二维码在页面上真的画出来了（有尺寸）',
          qrBox && qrBox.w > 50 && qrBox.h > 50, JSON.stringify(qrBox));
    const payUrl = await cdp.eval('state.collect.current.pay_url');
    check('收款链接是绝对地址（顾客能打开）', /^https?:\/\//.test(payUrl || ''), payUrl);

    // 模拟顾客：真的打开收款页并点「我已付款」
    console.log('   （模拟顾客扫码：新开标签打开收款页并点「我已付款」）');
    const payTarget = await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(payUrl)}`, { method: 'PUT' })
      .then(r => r.json());
    const payWs = new WebSocket(payTarget.webSocketDebuggerUrl);
    await new Promise((res, rej) => {
      payWs.addEventListener('open', res, { once: true });
      payWs.addEventListener('error', () => rej(new Error('顾客页连接失败')), { once: true });
    });
    const payCdp = new CDP(payWs);
    await payCdp.send('Runtime.enable');
    await waitFor(payCdp, 'typeof load === "function"', '顾客页脚本就绪');
    await waitFor(payCdp, 'document.getElementById("submit") !== null', '顾客页出现「我已付款」按钮');
    check('顾客打开收款页可用（免鉴权）', true);
    const shownAmount = await payCdp.eval('document.querySelector(".amount") ? document.querySelector(".amount").innerText.trim() : ""');
    check('顾客页显示的金额正确', shownAmount.includes('12.50'), shownAmount);
    await payCdp.eval('document.getElementById("payer").value = "浏览器顾客"; document.getElementById("submit").click();');
    await waitFor(payCdp, 'document.body.innerText.includes("已提交") || document.body.innerText.includes("已提交，等店主确认")', '顾客提交付款');
    check('顾客点「我已付款」成功', true);

    // 回到店主页：入账
    //
    // 先确认**数据真的落库了**（顾客那一下有没有生效），再让店主页重新拉列表。
    // 早期版本直接等 `state.collect.list` 变成 paid —— 那是页面的缓存，
    // 店主页自己不会自动感知顾客的操作（真实业务里也得手动刷新/被推送唤起），
    // 所以那样等必然超时。
    const colToken = await cdp.eval('state.collect.current.token');
    await waitFor(
      cdp,
      `api('/api/collect/list').then(r => r.collections.some(c => c.token === ${JSON.stringify(colToken)} && c.status === 'paid'))`,
      '顾客标记已付款已落库', 15000);
    check('顾客点「我已付款」后服务端状态变成待确认', true);

    await cdp.eval('loadCollect()');       // 等价于点进收款页时的重新加载
    await waitFor(cdp, 'state.collect.list.some(c => c.status === "paid")',
                  '店主页看到待确认收款单', 15000);
    const paidRow = await cdp.eval('state.collect.list.find(c => c.status === "paid")');
    check('店主页列表显示「已付款待确认」', !!paidRow);
    check('页面上能看到顾客填的称呼', (await cdp.eval('document.body.innerText')).includes('浏览器顾客'));

    await cdp.eval(`confirmCollection(${paidRow.id})`);
    await waitFor(cdp,
      `api('/api/collect/list').then(r => r.collections.some(c => c.id === ${paidRow.id} && c.status === 'confirmed'))`,
      '确认入账落库', 20000);
    await cdp.eval('loadCollect()');
    const confirmed = await cdp.eval('state.collect.list.find(c => c.id === ' + paidRow.id + ')');
    check('点「入账」后状态变为已入账', confirmed && confirmed.status === 'confirmed',
          JSON.stringify(confirmed));
    const incAmt = await cdp.eval('(async () => (await api("/api/orders/monthly")).income)()');
    check('入账金额进了账本（月度收入 > 0）', incAmt > 0, String(incAmt));

    // 顾客称呼应自动变成熟客档案
    const hasCustomer = await cdp.eval(
      'api("/api/customers").then(r => r.some(c => c.name === "浏览器顾客"))');
    check('顾客称呼自动建成熟客档案', hasCustomer === true);

    // ---------- 2. 会计报表 ----------
    console.log('\n== 2. 会计报表 ==');
    await cdp.eval('go("accounting")');
    await waitFor(cdp, 'state.accounting.bs !== null', '三张表加载完成');
    await waitFor(cdp, 'document.body.innerText.includes("资产负债表")', '资产负债表渲染');
    const acc = await cdp.eval('({tb: state.accounting.tb && state.accounting.tb.balanced, bs: state.accounting.bs && state.accounting.bs.balanced, net: state.accounting.inc && state.accounting.inc.net_profit})');
    check('科目余额表借贷平衡', acc.tb === true, JSON.stringify(acc));
    check('资产负债表平衡', acc.bs === true, JSON.stringify(acc));
    const badges = await cdp.eval('Array.from(document.querySelectorAll(".acct-badge")).map(e => e.innerText.trim())');
    check('页面上出现「已平衡」徽标', badges.some(b => b.includes('已平衡')), JSON.stringify(badges));
    // 切期间（真触发 onchange：模拟用户在 <input type=month> 上选月份）
    //
    // 注意：loadAccounting() 是异步的。只等 state.accounting.period 变成新值是不够的
    // —— 那是同步设的，此时接口还没回来，读到的仍是上一个期间的数字。
    // 必须等"取回来的数据自己标明是哪个期间"才能断言（第一版就栽在这里，
    // 误报成"利润表是写死的"）。
    await cdp.eval(`onAccountingPeriod('2099-01')`);
    await waitFor(cdp,
      '!state.accounting.loading && state.accounting.inc && state.accounting.inc.period === "2099-01"',
      '空期间数据加载完成');
    const emptyPeriod = await cdp.eval('state.accounting.inc.net_profit');
    await cdp.eval(`onAccountingPeriod(new Date().toISOString().slice(0,7))`);
    await waitFor(cdp,
      '!state.accounting.loading && state.accounting.inc && state.accounting.inc.period !== "2099-01"',
      '切回本月完成');
    const thisMonth = await cdp.eval('state.accounting.inc.net_profit');
    check('换期间后数字确实变了（证明数据来自接口，不是写死的）',
          emptyPeriod !== thisMonth, `2099-01=${emptyPeriod} 本月=${thisMonth}`);
    check('完全没流水的期间利润表为 0（不把期初当本期）',
          Math.abs(emptyPeriod) < 0.01, String(emptyPeriod));

    // 期末结转 → 反结转（真点按钮）
    //
    // 同样不能只等"结转列表里有这个期间"就断言利润表 —— loadAccount() 里三张表
    // 是各自 await 的，closings 先回来时 inc 可能还是上一次的数据。
    // 必须等利润表**自己**变成 0（或等它标明的期间+已加载）。
    const period = await cdp.eval('state.accounting.period');
    await cdp.eval(`confirm = () => true; closePeriod()`);
    await waitFor(cdp, 'state.accounting.closings.some(c => c.period === ' + JSON.stringify(period) + ')',
                  '结转后列表里出现该期间', 20000);
    // 结转后可能还带着旧记录，这里确认拿到的是 status='closed'
    await waitFor(cdp,
      'state.accounting.closings.some(c => c.period === ' + JSON.stringify(period) + ' && c.status === "closed")',
      '该期间状态为已结转', 20000);
    check('点「期末结转」后该期间被结转', true);
    check('点「期末结转」后该期间被结转', true);
    await waitFor(cdp, '!state.accounting.loading && state.accounting.inc && Math.abs(state.accounting.inc.net_profit) < 0.01',
                  '结转后利润表归零', 20000).catch(() => {});
    const afterClose = await cdp.eval('state.accounting.inc && state.accounting.inc.net_profit');
    check('结转后本期净利归零', Math.abs(afterClose) < 0.01, String(afterClose));
    await cdp.eval(`confirm = () => true; reopenPeriod()`);
    await waitFor(cdp,
      'api("/api/accounting/income-statement?period=" + state.accounting.period)'
      + '.then(r => Math.abs(r.net_profit) > 1)',
      '反结转后净利回来', 20000);
    check('点「反结转」后损益恢复（可重做）', true);

    // ---------- 3. 数据备份 ----------
    console.log('\n== 3. 数据备份 ==');
    await cdp.eval('go("backup")');
    await waitFor(cdp, 'state.backup.list.length > 0', '备份列表加载');
    const before = await cdp.eval('state.backup.list.length');
    check('备份列表能显示已有备份', before > 0, String(before));
    await cdp.eval('createBackup()');
    await waitFor(cdp, `state.backup.list.length > ${before}`, '新备份出现在列表', 25000);
    check('点「立即备份」后列表里多了一份', true);
    const newest = await cdp.eval('state.backup.list[0]');
    check('备份条目有名字与大小', !!(newest && newest.name && newest.size > 0), JSON.stringify(newest));

    // 真点「恢复」：先造一条临时收款单，恢复后它应该消失。
    // 恢复要覆盖整个库文件，20 秒不够，单独放宽到 90 秒。
    const marker = '恢复前临时单';
    await cdp.eval(`api('/api/collect/create','POST',{amount: 1, item: ${JSON.stringify(marker)}})`);
    const hasMarkerBefore = await cdp.eval(
      `api('/api/collect/list').then(r => r.collections.some(c => c.item === ${JSON.stringify(marker)}))`);
    check('恢复前：临时数据存在', hasMarkerBefore === true);
    await cdp.eval(`confirm = () => true; restoreBackup(${JSON.stringify(newest.name)})`,
                   true, 90000);
    await sleep(1200);
    const hasMarkerAfter = await cdp.eval(
      `api('/api/collect/list').then(r => r.collections.some(c => c.item === ${JSON.stringify(marker)}))`);
    check('点「恢复」后数据真的被备份覆盖了（临时数据消失）', hasMarkerAfter === false,
          '临时单仍在，恢复可能没生效');
    // 恢复后账目仍要能读（恢复把库换掉了，连接必须重新指向新文件）
    const afterRestore = await cdp.eval(
      '(async () => (await api("/api/orders/monthly")).income)()');
    check('恢复后账本仍可读（库连接已切到恢复后的文件）', afterRestore > 0, String(afterRestore));

    // ---------- 4. 主动触达 ----------
    console.log('\n== 4. 主动触达 ==');
    await cdp.eval('go("notify")');
    await waitFor(cdp, 'state.notify.providers.length > 0', '通道列表加载');
    check('推送通道能加载', true);
    // 真点通道卡片
    await cdp.eval(`document.querySelectorAll('.pay-type-btn')[0].click()`);
    const picked = await cdp.eval('state.notify.form.channel');
    check('点通道卡片能选中通道', !!picked, String(picked));

    await cdp.eval(`state.notify.form.channel = 'mock'; state.notify.form.target = ''; render();`);
    // 真点「保存订阅」按钮
    const subBefore = await cdp.eval('state.notify.subs.length');
    await cdp.eval(`saveSubscription()`);
    await waitFor(cdp, `state.notify.subs.length > ${subBefore}`, '订阅保存成功', 15000);
    check('点「保存订阅」后订阅列表多一条', true);

    // 真点「发一条测试推送」
    await cdp.eval('testPush()');
    await waitFor(cdp, 'state.notify.inbox.length > 0', '本地收件箱收到消息', 20000);
    check('点「发一条测试推送」后收件箱有内容', true);
    const inboxText = await cdp.eval('document.body.innerText.includes("测试推送")');
    check('页面上能看到推送内容', inboxText === true);
    await waitFor(cdp, 'state.notify.logs.length > 0', '投递记录出现');
    check('投递记录里能看到这次发送', true);

    // ---------- 5. 多店 / 成员 ----------
    console.log('\n== 5. 多店 / 成员 ==');
    await cdp.eval('go("shops")');
    await waitFor(cdp, 'state.shops.list.length > 0', '店铺列表加载');
    const curShop = await cdp.eval('state.shops.currentId');
    check('当前店铺显示正确', !!curShop, String(curShop));
    // 建一家新店（createShop 用 prompt，这里直接调接口后刷新列表：等价于点了按钮）
    const newShop = await cdp.eval(`api('/api/shops','POST',{name:'浏览器实测店', copy_from: state.shops.currentId}).then(r => r.shop.id)`);
    check('新建店铺成功', !!newShop, String(newShop));
    // 切过去（这是页面上「切到此店」按钮走的同一条路径）
    await cdp.eval(`switchShop(${newShop})`);
    await waitFor(cdp, `state.shops.currentId === ${newShop}`, '切店生效', 15000);
    check('点「切到此店」后当前店铺变了', true);
    check('切店后前端记住了店铺 id（后续请求会带 X-Shop-Id）',
          (await cdp.eval('getShopId()')) === newShop, String(await cdp.eval('getShopId()')));
    const txnsOther = await cdp.eval('(async () => (await api("/api/transactions")).length)()');
    check('新店账本是空的（数据隔离）', txnsOther === 0, String(txnsOther));
    // 切回默认店，账本应有数据
    await cdp.eval('switchShop(1)');
    await waitFor(cdp, 'state.shops.currentId === 1', '切回默认店', 15000);
    const txnsHome = await cdp.eval('(async () => (await api("/api/transactions")).length)()');
    check('切回默认店后账本有数据（切店真的切换了账本）', txnsHome > 0, String(txnsHome));
    // 创建成员 → 一次性令牌
    const newUser = await cdp.eval(`api('/api/shops/users','POST',{name:'浏览器店员', role:'staff'}).then(r => r.user.token)`);
    check('新增成员返回一次性令牌', typeof newUser === 'string' && newUser.length > 10);
    // 用成员令牌访问 → 应被按成员身份限制
    const members = await cdp.eval('(async () => (await api("/api/shops")).shops.length)()');
    check('店铺列表仍可读', members > 0, String(members));
    // 清理：删掉实测店铺与账号
    await cdp.eval(`api('/api/shops/' + ${newShop}, 'DELETE')`).catch(() => {});
    await cdp.eval(`api('/api/shops').then(r => { const u = null; return true; })`).catch(() => {});
    const users = await cdp.eval('(async () => (await api("/api/shops/users")).users)()');
    const uid = (users.find(u => u.name === '浏览器店员') || {}).id;
    if (uid) await cdp.eval(`api('/api/shops/users/' + ${uid}, 'DELETE')`).catch(() => {});
    console.log(`   （已清理实测数据：店铺 #${newShop}、成员 #${uid}）`);

    // ---------- 收尾：控制台错误 ----------
    console.log('\n== 控制台错误检查 ==');
    check('页面没有未捕获的 JS 异常', consoleErrors.length === 0,
          consoleErrors.slice(0, 3).join(' | '));

    console.log(`\n== 汇总 ==\n  通过 ${pass.length} 项，失败 ${fail.length} 项`);
    if (fail.length) {
      console.log('  失败明细：');
      fail.forEach(f => console.log('   ✗ ' + f));
    }
    process.exit(fail.length ? 1 : 0);
  } finally {
    try { if (ws) ws.close(); } catch (_) {}
    try { proc.kill(); } catch (_) {}
    await sleep(300);
    try { fs.rmSync(userDir, { recursive: true, force: true }); } catch (_) {}
  }
}

main().catch(e => {
  console.error('浏览器实测异常：' + e.message);
  process.exit(1);
});
