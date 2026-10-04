#!/usr/bin/env node
/**
 * capture_shots.js — 用**真实浏览器**把演示动线逐屏截图（供演示视频 / 设计原型）
 *
 * 与 check_web_click.js 的分工：
 *   - check_web_click.js 是"点了要通"（功能性回归）；
 *   - 本脚本是"点了要好看"——按演示动线走一遍，把每一屏存成 PNG。
 *
 * 前置：一个已在跑的后端（BASE_URL，默认 http://127.0.0.1:8020）。
 * 强烈建议用 `python scripts/run_shot_capture.py` 调起：它会拿**演示库的副本**
 * 起一个隔离后端 —— 截图过程里的写操作（记一笔、建二号店、生成收款码）
 * 不会弄脏真实演示库。
 *
 * 用法：
 *   node scripts/capture_shots.js
 *   BASE_URL=http://127.0.0.1:8000 SHOT_DIR=D:\shots node scripts/capture_shots.js
 */
'use strict';

const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const BASE_URL = process.env.BASE_URL || 'http://127.0.0.1:8020';
const OUT_DIR = process.env.SHOT_DIR ||
  path.join(__dirname, '..', '..', 'deliverables', 'video', 'shots');
// 手机视口：与真人手机接近（412×915 是常见安卓尺寸）。
// 2 倍像素密度：截图进入 1080p 视频时不会糊。
const VW = 412, VH = 915, DSF = 2;
// 只重拍指定几屏（逗号分隔），留空 = 全部
const ONLY = (process.env.SHOT_ONLY || '').split(',').map(s => s.trim()).filter(Boolean);

const EDGE_CANDIDATES = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  '/usr/bin/microsoft-edge',
  '/usr/bin/google-chrome',
];

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

// ---------------- CDP 极简客户端（与 check_web_click.js 同款） ----------------
class CDP {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(JSON.stringify(msg.error)));
        else resolve(msg.result);
      }
    });
  }

  send(method, params = {}, timeoutMs = 30000) {
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

  async eval(expression, awaitPromise = true, timeoutMs = 60000) {
    const r = await this.send('Runtime.evaluate', {
      expression, awaitPromise, returnByValue: true,
    }, timeoutMs);
    if (r.exceptionDetails) {
      const d = r.exceptionDetails;
      throw new Error('页面 JS 抛错：' + ((d.exception && d.exception.description) || d.text));
    }
    return r.result.value;
  }
}

async function waitReady(cdp, ms = 40000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    try {
      if (await cdp.eval('typeof state !== "undefined" && typeof go === "function"')) return true;
    } catch (_) { /* 还没加载完 */ }
    await sleep(200);
  }
  throw new Error('页面初始化超时');
}

async function waitFor(cdp, expr, desc, ms = 25000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    try { if (await cdp.eval(expr)) return true; } catch (_) {}
    await sleep(150);
  }
  throw new Error(`等待超时：${desc}`);
}

/** 把页面滚回顶部（或指定位置）再截图，否则会截到上次操作留下的滚动位置 */
async function capture(cdp, name, scrollY = 0) {
  // 截图前把浮层 toast 摘掉：它常挡住正在讲的那块内容（"掌柜已复盘"这类
  // 提示是真实交互的一部分，但留在定格里只会遮住数据）
  await cdp.eval(`(() => {
    document.querySelectorAll('.toast').forEach(e => e.remove());
    window.scrollTo(0, ${scrollY});
    document.querySelectorAll('*').forEach(e => { if (e.scrollTop > 0) e.scrollTop = 0; });
    window.scrollTo(0, ${scrollY});
    return true;
  })()`).catch(() => {});
  await sleep(500);
  const r = await cdp.send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(path.join(OUT_DIR, name), Buffer.from(r.data, 'base64'));
  const kb = (fs.statSync(path.join(OUT_DIR, name)).size / 1024).toFixed(0);
  console.log(`  📸 ${name}  (${kb} KB)`);
}

// ---------------- 演示动线（顺序即视频顺序） ----------------
const SHOTS = [
  {
    name: '01-home.png', desc: '首页：按住说话记账 + 账本速览',
    async run(cdp) {
      await cdp.eval('go("home")');
      await waitFor(cdp, 'document.querySelector(".tabbar") !== null', '首页骨架');
      await waitFor(cdp, 'document.body.innerText.includes("账本速览")', '账本速览', 30000);
    },
  },
  {
    name: '00-review.png', scrollY: 620, desc: '掌柜今日复盘（多 agent 裁决）',
    async run(cdp) {
      await cdp.eval('go("home")');
      await waitFor(cdp, 'document.querySelector(".tabbar") !== null', '首页骨架');
      // 掌柜复盘：真实多 agent 编排（员工并行 → 掌柜裁决，约 10~40 秒）
      await cdp.eval('reviewNow()', true, 180000).catch(() => {});
      await waitFor(cdp, 'state.review && state.review.length > 4', '复盘正文', 60000);
      await waitFor(cdp, 'document.body.innerText.includes("掌柜今日复盘")', '复盘卡片', 20000);
    },
  },
  {
    name: '02-recorded.png', desc: '记完当场核对（我这么记的，对吗？）',
    async run(cdp) {
      await cdp.eval('go("home")');
      await waitFor(cdp, 'typeof state.parsed !== "undefined"', '首页就绪');
      await cdp.eval(`submitOrder('王阿姨买了一个肉包一杯豆浆，6块')`, true, 120000);
      await waitFor(cdp, 'state.recorded !== null || state.amountDraft !== null',
                    '记账结果返回', 120000);
      if (await cdp.eval('state.amountDraft !== null')) {
        await cdp.eval('onAmountInput("6")');
        await cdp.eval('confirmAmount()', true, 60000);
      }
      await waitFor(cdp, 'state.recorded !== null', '核对卡', 60000);
      await waitFor(cdp, 'document.body.innerText.includes("我这么记的")', '核对卡文案', 20000);
    },
  },
  {
    name: '03-customers.png', desc: '熟客 + 增长动作（拉新/复购/选品）',
    async run(cdp) {
      await cdp.eval('go("customers")');
      await waitFor(cdp, 'state.customers.length > 0', '熟客列表');
      // 增长动作：真实多 agent 出稿（约 5~10 秒）
      await cdp.eval('genGrowth()', true, 120000).catch(() => {});
      await waitFor(cdp, 'document.body.innerText.includes("增长动作")', '增长动作卡片', 30000);
    },
  },
  {
    name: '04-customer-detail.png', desc: '一位熟客的完整档案（含 AI 画像分析）',
    async run(cdp) {
      await cdp.eval('go("customers")');
      await waitFor(cdp, 'state.customers.length > 0', '熟客列表');
      const cid = await cdp.eval(
        '(state.customers.find(c => c.name === "王阿姨") || state.customers[0]).id');
      await cdp.eval(`viewCustomer(${cid})`, true, 60000);
      await waitFor(cdp, 'document.body.innerText.includes("画像分析")', '熟客详情', 40000);
      // 画像分析是 AI 出的（无 Key 时是规则兜底文案），等它落地再截
      await waitFor(cdp, 'state.custInsight !== null', '画像分析结果', 90000).catch(() => {});
    },
  },
  {
    name: '05-copy.png', desc: '多 AI 员工竞争出稿（朋友圈文案）',
    async run(cdp) {
      await cdp.eval('go("copy")');
      await waitFor(cdp, 'document.body.innerText.includes("朋友圈")', '文案页');
      await cdp.eval('generateCopy()', true, 180000).catch(() => {});
      await waitFor(cdp, 'state.copyResult && state.copyResult.length > 4', '文案出稿', 60000);
    },
  },
  {
    name: '06-books.png', desc: '账本：流水（含借贷凭证）',
    async run(cdp) {
      await cdp.eval('go("books")');
      await waitFor(cdp, 'state.books.txns.length > 0', '流水列表');
      await waitFor(cdp, 'document.body.innerText.includes("流水")', '流水 tab');
    },
  },
  {
    name: '07-tax.png', desc: '算税：四种税 + 报税建议',
    async run(cdp) {
      await cdp.eval('go("books")');
      await waitFor(cdp, 'typeof state.books !== "undefined"', '账本页');
      await cdp.eval('switchBookTab(1)');
      await cdp.eval('state.books.vatRevenue = "350000"; render();');
      await cdp.eval('calcVat()', true, 60000).catch(() => {});
      await waitFor(cdp, 'state.books.vatResult !== null', '增值税结果', 60000);
      // 报税建议走 AI（首次约 30~65 秒），失败也不影响这一屏的主体
      await cdp.eval('loadTaxAdvice()', true, 180000).catch(() => {});
      await waitFor(cdp, 'document.body.innerText.includes("附加税")', '附加税', 20000);
    },
  },
  {
    name: '08-store.png', desc: '单店：从账本反推保本线（健康）',
    async run(cdp) {
      await cdp.eval('go("store")');
      await waitFor(cdp, 'typeof state.store !== "undefined"', '单店页');
      await cdp.eval('loadStoreLedger()', true, 60000).catch(() => {});
      await sleep(600);
      await cdp.eval(`state.store.form.rent = '6000';
                      state.store.form.salary = '8000';
                      state.store.form.utilities = '2000';
                      state.store.form.total_investment = '180000';
                      state.store.form.cash_on_hand = '50000';
                      render();`);
      await cdp.eval('calcStoreModel()', true, 120000).catch(() => {});
      await waitFor(cdp, 'state.store.result !== null', '算账结果', 60000);
    },
  },
  {
    name: '09-store-danger.png', desc: '日销掉到 600 → 判定「危险」',
    async run(cdp) {
      await cdp.eval(`state.store.form.daily_revenue = '600'; render();`);
      await cdp.eval('calcStoreModel()', true, 120000).catch(() => {});
      await sleep(800);
      await waitFor(cdp, 'state.store.result !== null', '危险结论', 30000);
    },
  },
  {
    name: '10-metrics.png', desc: '运行指标 + ROI（省了多少）',
    async run(cdp) {
      await cdp.eval('go("metrics")');
      await waitFor(cdp, 'typeof _mxData !== "undefined"', '指标页');
      await waitFor(cdp, '_mxData !== null', '指标数据', 40000);
    },
  },
  {
    name: '11-collect.png', desc: '收款：二维码 + 一键入账',
    async run(cdp) {
      await cdp.eval('go("collect")');
      await waitFor(cdp, 'document.body.innerText.includes("新建收款")', '收款页');
      await cdp.eval(`state.collect.form.amount = '12.5';
                      state.collect.form.item = '豆浆两杯'; render();`);
      await cdp.eval('createCollection()', true, 60000);
      await waitFor(cdp, 'state.collect.current && state.collect.current.token', '收款单', 30000);
      await waitFor(cdp, 'document.querySelector(".qr-holder svg") !== null', '二维码', 30000);
    },
  },
  {
    name: '12-accounting.png', desc: '会计闭环：三表 + 期末结转',
    async run(cdp) {
      await cdp.eval('go("accounting")');
      await waitFor(cdp, 'state.accounting.bs !== null', '三张表', 60000);
      await waitFor(cdp, 'document.body.innerText.includes("资产负债表")', '资产负债表', 30000);
    },
  },
  {
    name: '13-hq-overview.png', desc: '连锁总部视图（一店一库 + 跨店汇总）',
    async run(cdp) {
      // 先在隔离副本里造一家二号店：截图要的是"跨店汇总长什么样"，
      // 而不是"默认店长什么样"。副本用完即弃，不碰真实演示库。
      await cdp.eval(`api('/api/shops','POST',{name:'二号店'}).then(r => r.shop.id)`, true, 60000)
        .catch(() => {});
      await cdp.eval('go("shops")');
      await waitFor(cdp, 'state.shops.overview && state.shops.overview.length > 0',
                    '总部视图', 40000);
      await sleep(600);
    },
  },
];

async function main() {
  const browser = findBrowser();
  if (!browser) { console.log('找不到 Edge/Chrome，无法截图'); process.exit(2); }
  fs.mkdirSync(OUT_DIR, { recursive: true });
  console.log('浏览器：' + browser);
  console.log('输出目录：' + OUT_DIR);
  console.log('后端：' + BASE_URL);
  // 把「这次拍哪些屏」显式打出来：否则"跳过了全部却报成功"会被当成正常
  console.log('拍摄范围：' + (ONLY.length ? '仅 ' + ONLY.join(', ') : '全部 ' + SHOTS.length + ' 屏'));

  const userDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-shot-'));
  const port = 9333 + Math.floor(Math.random() * 50);
  const proc = spawn(browser, [
    '--headless=new',
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${userDir}`,
    '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--disable-extensions', '--disable-background-networking',
    `--window-size=${VW},${VH}`,
    'about:blank',
  ], { stdio: 'ignore' });

  let ws = null;
  const failed = [];
  try {
    let ver = null;
    for (let i = 0; i < 60; i++) {
      try { ver = await httpJson(`http://127.0.0.1:${port}/json/version`); break; }
      catch (_) { await sleep(300); }
    }
    if (!ver) throw new Error('浏览器调试端口没起来');

    const target = await httpJson(`http://127.0.0.1:${port}/json/new?about:blank`, 5000)
      .catch(async () => {
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
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: VW, height: VH, deviceScaleFactor: DSF, mobile: true,
    });

    await cdp.send('Page.navigate', { url: BASE_URL });
    await sleep(1500);
    await waitReady(cdp);

    for (const s of SHOTS) {
      // 只重拍某一屏时：SHOT_ONLY=00-review.png（省掉整套动线的 AI 调用）
      // 注意必须判 ONLY.length：空数组 [] 在 JS 里是 truthy，
      // 写成 `if (ONLY && !ONLY.includes(...))` 会把**全部**截图都 continue 掉，
      // 而且 failed 为空 → 末尾照样报"成功 14/14"（实测踩过这个假成功）。
      if (ONLY.length && !ONLY.includes(s.name)) continue;
      console.log(`\n== ${s.name} · ${s.desc} ==`);
      try {
        await s.run(cdp);
        await capture(cdp, s.name, s.scrollY || 0);
      } catch (e) {
        // 单屏失败不拖垮整批：记下来，最后统一汇报
        console.log('  ❌ ' + e.message);
        failed.push(`${s.name}: ${e.message}`);
        await capture(cdp, s.name, s.scrollY || 0).catch(() => {});
      }
    }

    // 产出校验：把「本轮应该产出哪些文件」逐个核对一遍。
    // 只统计 failed 是不够的 —— 循环被跳过时 failed 也是空的，会报假成功（实测踩过）。
    const expected = SHOTS.filter(s => !ONLY.length || ONLY.includes(s.name));
    const missing = expected.filter(s => {
      const p = path.join(OUT_DIR, s.name);
      return !fs.existsSync(p) || fs.statSync(p).size < 1024;
    }).map(s => s.name);
    missing.forEach(n => {
      const m = `${n}: 没有产出（或文件过小）`;
      if (!failed.includes(m)) failed.push(m);
    });

    console.log(`\n== 汇总：成功 ${expected.length - missing.length} / ${expected.length} ==`);
    console.log(`   产出目录：${OUT_DIR}`);
    failed.forEach(f => console.log('   ✗ ' + f));
    // 用 exitCode 而不是 process.exit()：后者在 stdout 是管道时可能丢掉缓冲输出
    process.exitCode = failed.length ? 1 : 0;
  } finally {
    try { if (ws) ws.close(); } catch (_) {}
    try { proc.kill(); } catch (_) {}
    await sleep(300);
    try { fs.rmSync(userDir, { recursive: true, force: true }); } catch (_) {}
  }
}

main().catch(e => {
  console.error('截图异常：' + e.message);
  process.exit(1);
});
