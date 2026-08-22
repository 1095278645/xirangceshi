'use strict';
// 库存页（存档案 / 入库出库盘点 / 补货过期预警）依赖 core.js 的 state/api/toast/render/esc/fmt

async function loadStock() {
  state.stock.loading = true;
  render();
  try {
    state.stock.summary = await api('/api/stock');
  } catch (e) { toast(e.message); }
  state.stock.loading = false;
  render();
}

async function saveProduct() {
  const f = state.stock.form;
  if (!f.name.trim()) { toast('请填商品/材料名'); return; }
  try {
    await api('/api/products', 'POST', {
      name: f.name, category: f.category, unit: f.unit,
      stock_qty: parseFloat(f.stock_qty) || 0,
      safety_stock: parseFloat(f.safety_stock) || 0,
      unit_cost: parseFloat(f.unit_cost) || 0,
      expiry_date: f.expiry_date, supplier: f.supplier, note: f.note });
    Object.assign(f, { name: '', category: '', unit: '', stock_qty: '',
      safety_stock: '', unit_cost: '', expiry_date: '', supplier: '', note: '' });
    toast('已登记入库');
    await loadStock();
  } catch (e) { toast(e.message); }
}

async function deleteProduct(id) {
  if (!confirm('删除这个商品档案（库存流水一并删）？')) return;
  try { await api(`/api/products/${id}`, 'DELETE'); await loadStock(); }
  catch (e) { toast(e.message); }
}

// type: in 入库 / out 出库 / adj 盘点
function promptMove(pid, name, type) {
  const label = { in: '入库', out: '出库', adj: '盘点' }[type];
  const qty = prompt(`${name}：输入${label}数量`, type === 'adj' ? '' : '1');
  if (qty === null) return;
  const n = parseFloat(qty);
  if (isNaN(n) || n < 0) { toast('数量不对'); return; }
  doMove(pid, type, n);
}

async function doMove(pid, movement, qty) {
  try {
    await api(`/api/products/${pid}/move`, 'POST', { movement, qty, note: '' });
    toast('库存已更新');
    await loadStock();
  } catch (e) { toast(e.message); }
}

// ---------------- 渲染 ----------------
function renderStock() {
  const s = state.stock;
  const sum = s.summary;
  const over = (p) => (p.stock_qty <= p.safety_stock && p.safety_stock > 0);

  const productsHtml = (!sum || sum.products.length === 0)
    ? '<div class="empty">还没有商品，在下面登记</div>'
    : sum.products.map(p => {
        const sub = [
          `库存 ${p.stock_qty}${esc(p.unit || '')}`,
          `成本 ${fmt(p.unit_cost)} 元`,
          p.supplier ? `供应商 ${esc(p.supplier)}` : '',
          p.expiry_date ? `保质期 ${esc(p.expiry_date)}` : '',
        ].filter(Boolean).join(' · ');
        return `
        <div class="txn-row">
          <div class="txn-main">
            <div class="txn-item">${esc(p.name)}${over(p) ? ' <span class="type-badge badge-expense">快没了</span>' : ''}</div>
            <div class="txn-sub">${sub}</div>
          </div>
          <div class="txn-amount ${over(p) ? 'expense' : ''}">${fmt(p.stock_qty)}${esc(p.unit || '')}</div>
          <div class="pay-row-actions">
            <button class="btn-mini" onclick="promptMove(${p.id},'${esc(p.name)}','in')">进</button>
            <button class="btn-mini" onclick="promptMove(${p.id},'${esc(p.name)}','out')">出</button>
            <button class="btn-mini" onclick="promptMove(${p.id},'${esc(p.name)}','adj')">盘</button>
            <button class="btn-mini btn-danger" onclick="deleteProduct(${p.id})">删</button>
          </div>
        </div>`;
      }).join('');

  return `
  <div class="hero"><div class="hero-title">库存</div><div class="hero-sub">进了多少、还剩多少、该补货</div></div>

  ${s.loading ? '<div class="card"><div class="card-title">加载中…</div></div>' : ''}
  ${sum ? `
  <div class="store-grid">
    <div class="store-cell"><div class="store-cell-num">${sum.total_items}</div><div class="store-cell-label">在库品类</div></div>
    <div class="store-cell"><div class="store-cell-num">${fmt(sum.total_value)}</div><div class="store-cell-label">库存货值(元)</div></div>
  </div>
  ${sum.summary ? `<div class="card"><div class="review-box">${esc(sum.summary)}</div></div>` : ''}
  ${sum.flags.length ? `
  <div class="card">
    <div class="card-title">⚠️ 该处理了</div>
    ${sum.flags.map(f => `<div class="note warn-box">${esc(f)}</div>`).join('')}
  </div>` : ''}` : ''}

  <div class="card">
    <div class="card-title">📦 商品 / 材料</div>
    ${productsHtml}
  </div>

  <div class="card">
    <div class="card-title">➕ 登记新商品 / 入库</div>
    <div class="form-item"><label class="form-label">名称 *</label>
      <input class="form-input" type="text" placeholder="如 面粉" value="${esc(s.form.name)}" oninput="state.stock.form.name=this.value" /></div>
    <div class="form-item"><label class="form-label">分类</label>
      <input class="form-input" type="text" placeholder="如 原料" value="${esc(s.form.category)}" oninput="state.stock.form.category=this.value" /></div>
    <div class="form-item"><label class="form-label">单位</label>
      <input class="form-input" type="text" placeholder="如 斤" value="${esc(s.form.unit)}" oninput="state.stock.form.unit=this.value" /></div>
    <div class="form-item"><label class="form-label">初始库存</label>
      <input class="form-input" type="number" inputmode="decimal" placeholder="0" value="${esc(s.form.stock_qty)}" oninput="state.stock.form.stock_qty=this.value" /></div>
    <div class="form-item"><label class="form-label">补货线（低于就提醒）</label>
      <input class="form-input" type="number" inputmode="decimal" placeholder="如 20" value="${esc(s.form.safety_stock)}" oninput="state.stock.form.safety_stock=this.value" /></div>
    <div class="form-item"><label class="form-label">单位成本（元）</label>
      <input class="form-input" type="number" inputmode="decimal" placeholder="如 25" value="${esc(s.form.unit_cost)}" oninput="state.stock.form.unit_cost=this.value" /></div>
    <div class="form-item"><label class="form-label">保质期</label>
      <input class="form-input" type="date" value="${esc(s.form.expiry_date)}" onchange="state.stock.form.expiry_date=this.value" /></div>
    <div class="form-item"><label class="form-label">供应商</label>
      <input class="form-input" type="text" placeholder="如 城东粮油" value="${esc(s.form.supplier)}" oninput="state.stock.form.supplier=this.value" /></div>
    <button class="btn-primary" onclick="saveProduct()">登记 / 入库</button>
  </div>`;
}