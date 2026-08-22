// 朋友圈文案（依赖 core.js 的 state/api/toast/render/esc/copyText）
'use strict';

// ---------- 文案 ----------
async function generateCopy() {
  state.copyLoading = true;
  state.copyResult = '';
  state.copyVariants = [];
  render();
  try {
    const r = await api('/api/copy', 'POST', state.copyForm);
    state.copyResult = r.text;
    state.copyVariants = (r.variants && r.variants.length > 1) ? r.variants : [r.text];
  } catch (e) { toast(e.message); }
  state.copyLoading = false;
  render();
}

function copyVariant(i) {
  const t = state.copyVariants[i];
  if (!t) return;
  navigator.clipboard.writeText(t).then(() => toast('已复制第' + (i + 1) + '条')).catch(() => {
    const ta = document.createElement('textarea');
    ta.value = t; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove(); toast('已复制第' + (i + 1) + '条');
  });
}

// ---------- 渲染 ----------
function renderCopy() {
  const v = state.copyVariants;
  const loading = state.copyLoading;
  const labels = ['场景移植', '宜忌体', '反向克制'];
  return `
  <div class="hero"><div class="hero-title">朋友圈文案</div><div class="hero-sub">3 条风格各异，挑一条直接发</div></div>
  <div class="card">
    <div class="form-item">
      <label class="form-label">店名</label>
      <input class="form-input" value="${esc(state.copyForm.shop_name)}" oninput="state.copyForm.shop_name=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">场景</label>
      <input class="form-input" value="${esc(state.copyForm.scene)}" oninput="state.copyForm.scene=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">补充信息</label>
      <textarea class="form-textarea" placeholder="比如：新到了土鸡蛋、今天包子买一送一"
        oninput="state.copyForm.extra=this.value">${esc(state.copyForm.extra)}</textarea>
    </div>
    <div class="form-item">
      <label class="form-label">想带的熟客名（可选）</label>
      <input class="form-input" value="${esc(state.copyForm.customer_name)}" oninput="state.copyForm.customer_name=this.value" />
    </div>
    <button class="btn-primary" onclick="generateCopy()" ${loading ? 'disabled' : ''}>${loading ? '生成中…' : '生成文案'}</button>
  </div>
  ${loading ? '<div class="card"><div class="card-title">正在生成 3 条文案…</div><div class="review-box">两位文案师各出方案 → 合规审核 → 掌柜挑选打磨 3 条</div></div>' : ''}
  ${!loading && v.length > 0 ? `
  <div class="card">
    <div class="card-title">📝 挑一条直接发</div>
    ${v.map((t, i) => `
    <div class="copy-variant">
      <div class="copy-variant-label">${labels[i] || ('风格' + (i + 1))}</div>
      <div class="result-text">${esc(t)}</div>
      <button class="btn-ghost" onclick="copyVariant(${i})">复制这条</button>
    </div>`).join('')}
  </div>` : ''}`;
}
