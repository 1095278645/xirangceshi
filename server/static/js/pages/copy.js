// 朋友圈文案（依赖 core.js 的 state/api/toast/render/copyText）
'use strict';

// ---------- 文案 ----------
async function generateCopy() {
  state.copyResult = '';
  render();
  try {
    const r = await api('/api/copy', 'POST', state.copyForm);
    state.copyResult = r.text;
  } catch (e) { toast(e.message); }
  render();
}

// ---------- 渲染 ----------
function renderCopy() {
  return `
  <div class="hero"><div class="hero-title">朋友圈文案</div><div class="hero-sub">老板语气，烟火气，随手发</div></div>
  <div class="card">
    <div class="form-item">
      <label class="form-label">店名</label>
      <input class="form-input" value="${state.copyForm.shop_name}" oninput="state.copyForm.shop_name=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">场景</label>
      <input class="form-input" value="${state.copyForm.scene}" oninput="state.copyForm.scene=this.value" />
    </div>
    <div class="form-item">
      <label class="form-label">补充信息</label>
      <textarea class="form-textarea" placeholder="比如：新到了土鸡蛋、今天包子买一送一"
        oninput="state.copyForm.extra=this.value">${state.copyForm.extra}</textarea>
    </div>
    <div class="form-item">
      <label class="form-label">想带的熟客名（可选）</label>
      <input class="form-input" value="${state.copyForm.customer_name}" oninput="state.copyForm.customer_name=this.value" />
    </div>
    <button class="btn-primary" onclick="generateCopy()">生成文案</button>
  </div>
  ${state.copyResult ? `<div class="card"><div class="result-text">${state.copyResult}</div><button class="btn-ghost" onclick="copyText()">复制到剪贴板</button></div>` : ''}`;
}