// 巷子里的AI掌柜 · 网页端核心（状态 / API 封装 / 渲染分发 / 工具）
// 加载顺序：core.js 必须先于所有页面文件，最后加载 init.js 完成初始化。
'use strict';

// ---------- API 封装（同源，直接 fetch） ----------
async function api(path, method = 'GET', data = null) {
  const opt = { method, headers: { 'Content-Type': 'application/json' } };
  if (data) opt.body = JSON.stringify(data);
  let res;
  try {
    res = await fetch(path, opt);
  } catch (e) {
    throw new Error('无法连接小店服务，请确认后端已启动');
  }
  if (!res.ok) {
    let msg = '请求失败 ' + res.status;
    try { const e = await res.json(); msg = e.detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

// ---------- 全局状态 ----------
const state = {
  route: 'home',
  summary: { income: 0, expense: 0, balance: 0, cnt: 0 },
  month: { period: '', income: 0, expense: 0, balance: 0 },
  recognizing: false,
  result: '',
  submitting: false,
  parsed: null,
  voucher: null,
  friendlyCategory: '',
  manualText: '',
  voiceSupported: false,
  // 熟客
  customers: [],
  custDetail: null,
  custMemInput: '',
  custInsight: null,
  custInsightLoading: false,
  custInsightAiUsed: false,
  // 文案
  copyForm: { shop_name: '巷子里的早餐铺', scene: '今日营业', extra: '', customer_name: '' },
  copyResult: '',
  copyVariants: [],
  copyLoading: false,
  // 设置
  aiEnabled: false,
  hasKey: false,
  baseUrl: '',
  model: '',
  provider: '',
  providers: [],
  apiKeyInput: '',
  baseUrlInput: '',
  modelInput: '',
  // 收款账户（微信商户/聚合支付流水同步）
  paySources: [],
  payLogs: [],
  payForm: { source_type: 'wechat', name: '', mchid: '', appid: '',
    cert_path: '', private_key_path: '', api_v3_key: '', enabled: true },
  paySyncing: false,
  // 账本（省账通能力）
  books: { tab: 0, year: 0, month: 0, txns: [], summaryLoading: false,
    vatRevenue: '', vatResult: null, surtaxResult: null,
    pitSalary: '', pitSocial: '', pitSpecial: '', pitResult: null,
    citIncome: '', citSmall: true, citResult: null,
    calendar: null, accountCats: [], downloading: false,
    insight: null, insightLoading: false, insightAiUsed: false,
    taxAdvice: null, taxAdviceLoading: false, taxAdviceAiUsed: false },
  // 单店模型（勇哥方法论泛化）
  store: { presets: [], bizType: '餐饮', form: { daily_revenue: '', gross_margin: '',
    rent: '', salary: '', utilities: '', total_investment: '', cash_on_hand: '',
    traffic: '一般', competitor: '一般' },
    result: null, loading: false, ledgering: false, ledgerNote: '',
    diagnosis: null, diagnosisLoading: false, diagnosisAiUsed: false,
    profiles: [], profileName: '我的店', savingProfile: false, applyingProfile: null },
  // 掌柜今日复盘（心跳）
  review: '',
  // 财务（现金流 / 预算 / 应收应付）
  finance: { tab: 0, month: '', cash: { cash_on_hand: '', months: 6, result: null, loading: false },
    budForm: { month: '', scope: 'expense', amount: '', category: '', note: '' },
    budgets: [], budVs: null, budLoading: false,
    debtForm: { party: '', kind: 'receivable', amount: '', due_date: '', note: '' },
    debts: [], aging: null, debtLoading: false },
  // 库存（进销存）
  stock: { summary: null, loading: false,
    form: { name: '', category: '', unit: '', stock_qty: '', safety_stock: '',
      unit_cost: '', expiry_date: '', supplier: '', note: '' } },
  // 发票台账（销项/进项）
  invoice: { summary: null, loading: false,
    form: { kind: 'out', party: '', invoice_no: '', amount: '', rate: '', tax_amount: '',
      issued_date: '', note: '' } },
};

// ---------- 辅助 ----------
function fmt(n) { return Number(n || 0).toFixed(0); }
function pad2(n) { return n < 10 ? '0' + n : '' + n; }

// HTML 转义：用户输入 / AI 输出插入模板前统一转义，防 XSS 与属性注入
function esc(s) {
  if (s === null || s === undefined) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  // 在 textContent→innerHTML（只转义 < > &）基础上再转义引号，
  // 防止值被拼进 data-* / value="..." 等属性上下文时发生属性注入
  return d.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

let toastTimer = null;
function toast(msg) {
  let el = document.querySelector('.toast');
  if (el) el.remove();
  el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  document.body.appendChild(el);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), 2200);
}

function copyText() {
  navigator.clipboard.writeText(state.copyResult).then(() => toast('已复制')).catch(() => {
    const ta = document.createElement('textarea');
    ta.value = state.copyResult; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove(); toast('已复制');
  });
}

function copyLink(url) {
  navigator.clipboard.writeText(url).then(() => toast('链接已复制')).catch(() => {
    const ta = document.createElement('textarea');
    ta.value = url; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove(); toast('链接已复制');
  });
}

// ---------- 渲染分发（各页面渲染函数见 pages/*.js） ----------
function render() {
  const r = state.route;
  let html;
  if (r === 'customers') html = renderCustomers();
  else if (r === 'custDetail') html = renderCustDetail();
  else if (r === 'copy') html = renderCopy();
  else if (r === 'settings') html = renderSettings();
  else if (r === 'books') html = renderBooks();
  else if (r === 'store') html = renderStore();
  else if (r === 'finance') html = renderFinance();
  else if (r === 'stock') html = renderStock();
  else if (r === 'invoice') html = renderInvoice();
  else html = renderHome();
  document.getElementById('app').innerHTML = html;
  const inMore = (r === 'finance' || r === 'stock' || r === 'invoice' || r === 'settings');
  document.querySelectorAll('.tab-item').forEach(t => {
    const r2 = t.dataset.route;
    t.classList.toggle('active', inMore ? (r2 === 'more') : (r2 === r));
    t.style.display = (r === 'custDetail' && r2 !== 'customers') ? 'none' : 'flex';
  });
}

// ---------- 更多抽屉 ----------
function openMore() {
  document.getElementById('moreMask').classList.add('show');
  document.getElementById('moreDrawer').classList.add('open');
}

function closeMore() {
  document.getElementById('moreMask').classList.remove('show');
  document.getElementById('moreDrawer').classList.remove('open');
}

// ---------- 路由 ----------
function go(route) {
  if (route === 'more') { openMore(); return; }
  state.route = route;
  if (location.hash.slice(1) !== route) location.hash = route;
  render();
  if (route === 'home') loadHome();
  else if (route === 'customers') loadCustomers();
  else if (route === 'settings') loadSettings();
  else if (route === 'books') loadBooks();
  else if (route === 'store') loadStore();
  else if (route === 'finance') loadFinance();
  else if (route === 'stock') loadStock();
  else if (route === 'invoice') loadInvoice();
}