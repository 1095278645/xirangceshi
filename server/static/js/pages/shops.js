// 多店 / 成员页（开店、切店、角色与令牌）
// 依赖 core.js 的 state/api/toast/render/esc/fmt
//
// 一店一库：切店后前端会在每个请求上带 X-Shop-Id，后端据此切换账本。
// 演示动线：新建二号店 → 切过去 → 账本是空的 → 切回来原样。
'use strict';

async function loadShops() {
  const s = state.shops;
  s.loading = true;
  render();
  const q = (p) => api(p).catch(() => null);
  const [ctx, list, users] = await Promise.all([
    q('/api/shops/context'), q('/api/shops'), q('/api/shops/users'),
  ]);
  s.ctx = ctx || {};
  s.list = (list && list.shops) || [];
  s.users = (users && users.users) || [];
  s.currentId = (ctx && ctx.shop_id) || (s.list[0] && s.list[0].id) || null;
  s.canManage = true;   // 具体权限由后端判定，越权会返回 403 并在页面上提示
  const target = s.memberShopId || s.currentId;
  if (target) {
    const m = await q(`/api/shops/${target}/members`);
    s.members = (m && m.members) || [];
    s.memberShopId = target;
  }
  s.loading = false;
  render();
}

function pickShopForMembers(sid) {
  state.shops.memberShopId = sid;
  loadShops();
}

async function switchShop(sid) {
  try {
    const r = await api('/api/shops/switch', 'POST', { shop_id: sid });
    setShopId(sid);          // 之后所有请求都会带 X-Shop-Id
    toast('已切到「' + ((r.shop && r.shop.name) || sid) + '」');
    await loadShops();
  } catch (e) { toast(e.message); }
}

async function createShop() {
  const name = prompt('新店铺名称（如：巷口二号店）');
  if (!name || !name.trim()) return;
  try {
    // copy_from 沿用当前店的**科目结构**（不复制流水），新店开张即是标准账套
    await api('/api/shops', 'POST', {
      name: name.trim(), copy_from: state.shops.currentId || undefined,
    });
    toast('店铺已创建');
    await loadShops();
  } catch (e) { toast(e.message); }
}

async function renameShop(sid, old) {
  const name = prompt('新的店名', old);
  if (!name || !name.trim() || name === old) return;
  try {
    await api(`/api/shops/${sid}`, 'PUT', { name: name.trim() });
    toast('已改名');
    await loadShops();
  } catch (e) { toast(e.message); }
}

async function removeShop(sid) {
  if (!confirm('从列表移除这家店？\n\n数据文件会保留（可联系技术支持找回），只是不再出现在列表里。')) return;
  try {
    await api(`/api/shops/${sid}`, 'DELETE');
    toast('已移除');
    await loadShops();
  } catch (e) { toast(e.message); }
}

async function createUser() {
  const name = prompt('成员姓名（如：小李）');
  if (!name || !name.trim()) return;
  const roleLabel = prompt('角色：填 1=店员（可记账）  2=店长（可管账+管人）  3=店主（全权）', '1');
  const role = { '1': 'staff', '2': 'admin', '3': 'owner' }[String(roleLabel).trim()] || 'staff';
  const sid = state.shops.memberShopId;
  try {
    const r = await api('/api/shops/users', 'POST', {
      name: name.trim(), role, shop_ids: sid ? [sid] : [],
    });
    const token = r.user && r.user.token;
    // 令牌只回传一次，必须让店主当场复制
    await copyLink(token || '');
    alert('创建成功！\n\n一次性令牌（已复制到剪贴板）：\n' + token +
          '\n\n请把它交给该成员，填到「设置 → 访问令牌」里。');
    await loadShops();
  } catch (e) { toast(e.message); }
}

async function removeUser(uid, name) {
  if (!confirm(`把「${name}」从系统中移除？其令牌会立即失效。`)) return;
  try { await api(`/api/shops/users/${uid}`, 'DELETE'); toast('已移除'); await loadShops(); }
  catch (e) { toast(e.message); }
}

async function changeUserRole(uid, name) {
  const v = prompt(`「${name}」的角色：填 1=店员  2=店长`, '1');
  const role = { '1': 'staff', '2': 'admin' }[String(v).trim()];
  if (!role) return;
  try { await api(`/api/shops/users/${uid}`, 'PUT', { role }); toast('已改角色'); await loadShops(); }
  catch (e) { toast(e.message); }
}

async function grantMember(uid) {
  const sid = state.shops.memberShopId;
  if (!sid) return;
  try {
    await api(`/api/shops/${sid}/members`, 'POST', { user_id: uid, role: 'staff' });
    toast('已加入本店');
    await loadShops();
  } catch (e) { toast(e.message); }
}

async function revokeMember(uid) {
  const sid = state.shops.memberShopId;
  if (!sid) return;
  try {
    await api(`/api/shops/${sid}/members/${uid}`, 'DELETE');
    toast('已移出本店');
    await loadShops();
  } catch (e) { toast(e.message); }
}

const ROLE_LABEL = { owner: '店主', admin: '店长', staff: '店员' };

// ---------------- 渲染 ----------------
function renderShops() {
  const s = state.shops;

  const shopRows = s.list.map(x => `
    <div class="pay-row ${x.id === s.currentId ? 'shop-current' : ''}">
      <div class="pay-row-main">
        <div class="pay-type-badge">${x.id === s.currentId ? '当前' : '#' + x.id}</div>
        <div class="txn-sub">${esc(x.name)}</div>
        <div class="pay-mchid">${x.status === 'active' ? '营业中' : '已归档'} ·
          ${x.member_count || 0} 位成员${x.my_role ? ' · 我的角色：' + (ROLE_LABEL[x.my_role] || x.my_role) : ''}</div>
      </div>
      <div class="pay-row-actions">
        ${x.id !== s.currentId ? `<button class="btn-mini" onclick="switchShop(${x.id})">切到此店</button>` : ''}
        <button class="btn-mini" onclick="renameShop(${x.id},'${esc(x.name)}')">改名</button>
        <button class="btn-mini" onclick="pickShopForMembers(${x.id})">成员</button>
        ${x.id !== 1 ? `<button class="btn-mini btn-danger" onclick="removeShop(${x.id})">删除</button>` : ''}
      </div>
    </div>`).join('');

  const memberRows = s.members.length === 0
    ? '<div class="empty">这家店还没有其他成员</div>'
    : s.members.map(m => `
      <div class="txn-row">
        <div class="txn-main">
          <div class="txn-item">${esc(m.name)}</div>
          <div class="txn-sub">${ROLE_LABEL[m.role] || m.role}${m.status === 'disabled' ? ' · 已停用' : ''}</div>
        </div>
        <div class="pay-row-actions">
          <button class="btn-mini" onclick="changeUserRole(${m.id},'${esc(m.name)}')">改角色</button>
          <button class="btn-mini" onclick="revokeMember(${m.id})">移出本店</button>
          <button class="btn-mini btn-danger" onclick="removeUser(${m.id},'${esc(m.name)}')">删账号</button>
        </div>
      </div>`).join('');

  const userRows = s.users.length === 0
    ? '<div class="empty">还没有其他账号</div>'
    : s.users.map(u => `
      <div class="txn-row">
        <div class="txn-main">
          <div class="txn-item">${esc(u.name)} <span class="pay-status">${ROLE_LABEL[u.role] || u.role}</span></div>
          <div class="txn-sub">令牌 ${esc(u.token_prefix)}… ${u.shop_ids && u.shop_ids.length ? '· 已在店 ' + u.shop_ids.join('、') : '· 未加入任何店'}</div>
        </div>
        <div class="pay-row-actions">
          <button class="btn-mini" onclick="grantMember(${u.id})">加入本店</button>
        </div>
      </div>`).join('');

  return `
  <div class="hero"><div class="hero-title">多店 / 成员</div>
    <div class="hero-sub">一家店一个独立账本，数据互不串；成员按角色分工</div></div>

  <div class="card">
    <div class="card-title">🏪 我的店铺（${s.list.length}）</div>
    ${s.loading ? '<div class="empty">加载中…</div>' : (shopRows || '<div class="empty">没有店铺</div>')}
    <div class="pay-actions">
      <button class="btn-primary" style="flex:1" onclick="createShop()">+ 新建店铺</button>
    </div>
    <div class="acct-note">切店后网页会在每个请求上带 X-Shop-Id，后端据此切换账本 ——
      切到二号店，账本是空的（两家数据物理隔离）。</div>
  </div>

  <div class="card">
    <div class="card-title">👥 店铺 #${s.memberShopId || '-'} 的成员（${s.members.length}）</div>
    ${memberRows}
    <div class="pay-actions">
      <button class="btn-mini" onclick="createUser()">+ 新增成员（返回一次性令牌）</button>
    </div>
  </div>

  <div class="card">
    <div class="card-title">🧑‍🤝‍🧑 全部账号（${s.users.length}）</div>
    <div class="acct-note">同一账号可以同时属于多家店，角色按店单独设置。</div>
    ${userRows}
  </div>

  <div class="card">
    <div class="card-title">🔑 角色权限</div>
    <div class="acct-note">
      <strong>店主</strong>：全权，可开店/删店/管人/管账<br/>
      <strong>店长</strong>：可管账、可管人，不能开店删店<br/>
      <strong>店员</strong>：可记账，不能管人<br/>
      令牌在「设置 → 访问令牌」里填写；不同令牌能看到的数据范围不同。
    </div>
  </div>`;
}
