// 发票台账页（销项/进项）依赖 core.js 的 state/api/toast/render/esc/fmt
'use strict';

async function loadInvoice() {
  state.invoice.loading = true;
  render();
  try {
    state.invoice.summary = await api('/api/invoices/summary');
  } catch (e) { toast(e.message); }
  state.invoice.loading = false;
  render();
}

async function saveInvoice() {
  const f = state.invoice.form;
  const amount = parseFloat(f.amount);
  if (!(amount > 0)) { toast('先填开票/收票金额'); return; }
  const ratePct = parseFloat(f.rate);
  const rate = isNaN(ratePct) ? 0 : ratePct / 100;   // 用户填%，后端存小数
  try {
    await api('/api/invoices', 'POST', {
      kind: f.kind, party: f.party, invoice_no: f.invoice_no, amount,
      rate, tax_amount: Math.round(amount * rate * 100) / 100,
      issued_date: f.issued_date || todayStr(), note: f.note });
    Object.assign(f, { party: '', invoice_no: '', amount: '', rate: '',
      issued_date: '', note: '' });
    toast('发票已记入台账');
    await loadInvoice();
  } catch (e) { toast(e.message); }
}

function todayStr() {
  const n = new Date();
  return `${n.getFullYear()}-${pad2(n.getMonth() + 1)}-${pad2(n.getDate())}`;
}

async function voidInvoice(iid) {
  if (!confirm('作废这张发票？')) return;
  try { await api(`/api/invoices/${iid}/void`, 'POST'); await loadInvoices(); }
  catch (e) { toast(e.message); }
}

// ---------------- 渲染 ----------------
function renderInvoice() {
  const iv = state.invoice;
  const s = iv.summary;
  const byKind = (k) => (s ? s.by_kind.find(x => x.kind === k) : null);

  const list = (!s || s.invoices.length === 0)
    ? '<div class="empty">还没有发票记录</div>'
    : s.invoices.map(v => `
      <div class="txn-row">
        <div class="txn-main">
          <div class="txn-item">${v.kind === 'out' ? '📤 开给 ' : '📥 收到 '}${esc(v.party || '未记名')}
            ${v.status === 'void' ? ' <span class="type-badge badge-expense">作废</span>' : ''}</div>
          <div class="txn-sub">${v.invoice_no ? '票号 ' + esc(v.invoice_no) + ' · ' : ''}${esc(v.issued_date || '')}${v.rate ? ' · 税率 ' + (v.rate * 100).toFixed(0) + '%' : ''}</div>
        </div>
        <div class="txn-amount ${v.kind === 'out' ? 'income' : ''}">${fmt(v.amount)}</div>
        ${v.status === 'issued' ? `<div class="pay-row-actions"><button class="btn-mini btn-danger" onclick="voidInvoice(${v.id})">作废</button></div>` : ''}
      </div>`).join('');

  return `
  <div class="hero"><div class="hero-title">发票</div><div class="hero-sub">开了多少票、收了多少票，心里有数</div></div>

  ${iv.loading ? '<div class="card"><div class="card-title">加载中…</div></div>' : !s ? '' : `
  <div class="store-grid">
    <div class="store-cell"><div class="store-cell-num income">${byKind('out').cnt || 0} 张</div><div class="store-cell-label">销项 ${fmt(byKind('out').total || 0)} 元</div></div>
    <div class="store-cell"><div class="store-cell-num">${byKind('in').cnt || 0} 张</div><div class="store-cell-label">进项 ${fmt(byKind('in').total || 0)} 元</div></div>
  </div>
  ${s.summary ? `<div class="card"><div class="review-box">${esc(s.summary)}</div></div>` : ''}`}

  <div class="card">
    <div class="card-title">➕ 记一笔发票</div>
    <div class="form-item"><label class="form-label">类型</label>
      <div class="pay-type-toggle">
        <div class="pay-type-btn ${iv.form.kind === 'out' ? 'on' : ''}" onclick="state.invoice.form.kind='out';render()">销项（开给客户）</div>
        <div class="pay-type-btn ${iv.form.kind === 'in' ? 'on' : ''}" onclick="state.invoice.form.kind='in';render()">进项（供应商给我）</div>
      </div>
    </div>
    <div class="form-item"><label class="form-label">对方（客户 / 供应商）</label>
      <input class="form-input" type="text" placeholder="如 某某公司" value="${esc(iv.form.party)}" oninput="state.invoice.form.party=this.value" /></div>
    <div class="form-item"><label class="form-label">票号</label>
      <input class="form-input" type="text" placeholder="选填" value="${esc(iv.form.invoice_no)}" oninput="state.invoice.form.invoice_no=this.value" /></div>
    <div class="form-item"><label class="form-label">不含税金额（元）</label>
      <input class="form-input" type="number" inputmode="decimal" placeholder="如 5000" value="${esc(iv.form.amount)}" oninput="state.invoice.form.amount=this.value" /></div>
    <div class="form-item"><label class="form-label">税率（%）</label>
      <input class="form-input" type="number" inputmode="decimal" placeholder="如 6（小规模常 1/3）" value="${esc(iv.form.rate)}" oninput="state.invoice.form.rate=this.value" /></div>
    <div class="form-item"><label class="form-label">开票日期</label>
      <input class="form-input" type="date" value="${esc(iv.form.issued_date || todayStr())}" onchange="state.invoice.form.issued_date=this.value" /></div>
    <button class="btn-primary" onclick="saveInvoice()">记入台账</button>
  </div>

  <div class="card">
    <div class="card-title">🧾 发票台账</div>
    ${list}
  </div>`;
}