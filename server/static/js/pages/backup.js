// 数据备份 / 导出 / 恢复页
// 依赖 core.js 的 state/api/toast/render/esc/fmt
//
// 两条语义不同的路径，别搞混（小程序端第一版就搞混过）：
//   POST /api/backup/export           → 在服务器上**生成** zip 包，返回文件名
//   GET  /api/backup/download/{name}  → 真正把包下载下来
// 恢复是危险操作，后端强制要求 confirm=true，否则 400。
'use strict';

async function loadBackup() {
  state.backup.loading = true;
  render();
  try {
    const r = await api('/api/backup/list');
    state.backup.list = r.backups || [];
  } catch (e) { toast(e.message); state.backup.list = []; }
  state.backup.loading = false;
  render();
}

async function createBackup() {
  if (state.backup.busy) return;
  state.backup.busy = true;
  render();
  try {
    const r = await api('/api/backup/create', 'POST', { kind: 'manual' });
    toast('已备份：' + (r.backup ? r.backup.name : ''));
    await loadBackup();
  } catch (e) { toast(e.message); }
  state.backup.busy = false;
  render();
}

async function exportBundle() {
  try {
    const r = await api('/api/backup/export', 'POST', {});
    const name = r.export && r.export.name;
    if (!name) { toast('导出失败：后端未返回文件名'); return; }
    // 用 fetch+blob 下载：<a href> 无法带访问令牌头
    await downloadFile('/api/backup/download/' + encodeURIComponent(name),
                       name.endsWith('.zip') ? name : name + '.zip');
  } catch (e) { toast(e.message); }
}

async function restoreBackup(name) {
  if (!confirm(`用「${name}」覆盖当前全部数据？\n\n恢复前系统会自动把当前数据另存一份（pre_restore 快照），恢复错了可以退回。`)) return;
  try {
    // confirm=true 是后端强制的（防误操作）
    await api(`/api/backup/restore/${encodeURIComponent(name)}?confirm=true`, 'POST', {});
    toast('已恢复，正在重新加载');
    await loadBackup();
    await loadHome();
  } catch (e) { toast(e.message); }
}

async function deleteBackup(name) {
  if (!confirm(`删除备份「${name}」？此操作不可撤销。`)) return;
  try {
    await api('/api/backup/' + encodeURIComponent(name), 'DELETE');
    toast('已删除');
    await loadBackup();
  } catch (e) { toast(e.message); }
}

// 从本地选一个备份包上传恢复（zip / db）
async function importBundle(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  if (!confirm(`用「${file.name}」覆盖当前全部数据？\n\n恢复前会自动备份当前数据。`)) {
    input.value = '';
    return;
  }
  const fd = new FormData();
  fd.append('file', file);
  try {
    const res = await fetch(
      `/api/backup/import?confirm=true&filename=${encodeURIComponent(file.name)}`,
      { method: 'POST', headers: authHeaders(), body: file });   // 后端读原始请求体
    if (!res.ok) {
      let msg = '恢复失败 ' + res.status;
      try { const e = await res.json(); msg = e.detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    toast('恢复完成');
    await loadBackup();
    await loadHome();
  } catch (e) { toast(e.message); }
  input.value = '';
}

function _kb(n) {
  const v = Number(n || 0);
  if (v < 1024) return v + ' B';
  if (v < 1024 * 1024) return (v / 1024).toFixed(0) + ' KB';
  return (v / 1024 / 1024).toFixed(1) + ' MB';
}

// ---------------- 渲染 ----------------
function renderBackup() {
  const b = state.backup;
  const rows = b.list.length === 0
    ? '<div class="empty">还没有备份，点上面「立即备份」创建一份</div>'
    : b.list.map(x => `
      <div class="pay-row">
        <div class="pay-row-main">
          <div class="pay-type-badge">${x.kind === 'auto' ? '自动' : (x.kind === 'pre_restore' ? '恢复前快照' : '手动')}</div>
          <div class="txn-sub">${esc(x.name)}</div>
          <div class="pay-mchid">${_kb(x.size)} · ${esc(x.created_at || '')}${x.note ? ' · ' + esc(x.note) : ''}</div>
        </div>
        <div class="pay-row-actions">
          <button class="btn-mini" onclick="downloadFile('/api/backup/download/${encodeURIComponent(x.name)}','${esc(x.name)}')">下载</button>
          <button class="btn-mini" onclick="restoreBackup('${esc(x.name)}')">恢复</button>
          <button class="btn-mini btn-danger" onclick="deleteBackup('${esc(x.name)}')">删除</button>
        </div>
      </div>`).join('');

  return `
  <div class="hero"><div class="hero-title">数据备份</div>
    <div class="hero-sub">系统每天自动快照（保留最近 14 份），也可以随时手动备份</div></div>

  <div class="card">
    <div class="card-title">🛡 立即备份 / 导出</div>
    <div class="acct-note">备份是「一致性快照」（VACUUM INTO），生成时不会读到写了一半的数据。</div>
    <div class="pay-actions">
      <button class="btn-primary" style="flex:1" onclick="createBackup()" ${b.busy ? 'disabled' : ''}>
        ${b.busy ? '备份中…' : '立即备份'}</button>
    </div>
    <div class="pay-actions">
      <button class="btn-mini" onclick="exportBundle()">导出全量数据包</button>
      <label class="btn-mini" style="cursor:pointer">导入备份包恢复
        <input type="file" accept=".zip,.db" style="display:none"
               onchange="importBundle(this)" /></label>
    </div>
    <div class="acct-note">导出包默认**不含** API Key，方便存网盘或发给别人。</div>
  </div>

  <div class="card">
    <div class="card-title">📦 备份列表（${b.list.length}）</div>
    ${b.loading ? '<div class="empty">加载中…</div>' : rows}
  </div>

  <div class="card">
    <div class="card-title">⚠️ 恢复须知</div>
    <div class="acct-note">恢复会**覆盖当前全部数据**。系统会在恢复前自动把当前数据
      另存为 pre_restore 快照并出现在上面的列表里，恢复错了可以再恢复回去。</div>
  </div>`;
}
