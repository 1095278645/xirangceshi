// 主动触达页（推送通道 / 订阅 / 测试发送 / 投递记录）
// 依赖 core.js 的 state/api/toast/render/esc/fmt
//
// 演示要点：企业微信群机器人**不需要正式 AppID**，贴一个 webhook key 就能真的
// 收到消息；没配置时用本地记录通道（mock），消息会记到 data/notifications.jsonl，
// 页面下方「本地收件箱」能直接看到"推送了什么"。
'use strict';

async function loadNotify() {
  const n = state.notify;
  n.loading = true;
  render();
  const q = (p) => api(p).catch(() => null);
  const [prov, subs, logs, inbox] = await Promise.all([
    q('/api/notify/providers'), q('/api/notify/subscriptions'),
    q('/api/notify/logs'), q('/api/notify/mock-inbox'),
  ]);
  n.providers = (prov && prov.providers) || [];
  n.subs = (subs && subs.subscriptions) || [];
  n.logs = (logs && logs.logs) || [];
  n.inbox = (inbox && inbox.messages) || [];
  if (!n.form.channel && n.providers.length) n.form.channel = n.providers[0].id;
  n.loading = false;
  render();
}

function notifyPickChannel(id) {
  state.notify.form.channel = id;
  render();
}

async function saveSubscription() {
  const f = state.notify.form;
  if (!f.channel) { toast('先选一个通道'); return; }
  const needTarget = ['wecom_bot', 'wecom_app', 'wechat_subscribe'].includes(f.channel);
  if (needTarget && !f.target.trim()) {
    toast('这个通道需要填接收目标（如 webhook key）');
    return;
  }
  try {
    await api('/api/notify/subscriptions', 'POST', {
      channel: f.channel, target: f.target.trim(),
      name: f.name.trim(), events: f.events, enabled: true,
    });
    toast('已保存订阅');
    await loadNotify();
  } catch (e) { toast(e.message); }
}

async function testPush() {
  const f = state.notify.form;
  if (!f.channel) { toast('先选一个通道'); return; }
  try {
    const r = await api('/api/notify/test', 'POST', {
      channel: f.channel, target: f.target.trim(),
      title: '测试推送', content: '这是一条来自掌柜的测试消息',
    });
    const res = r.result || {};
    toast(res.ok ? '测试消息已发出' : ('发送失败：' + (res.error || '未知原因')));
    await loadNotify();
  } catch (e) { toast(e.message); }
}

async function delSubscription(sid) {
  if (!confirm('删除这条订阅？')) return;
  try { await api(`/api/notify/subscriptions/${sid}`, 'DELETE'); await loadNotify(); }
  catch (e) { toast(e.message); }
}

async function testSubscription(sid) {
  try {
    const r = await api(`/api/notify/subscriptions/${sid}/test`, 'POST', {});
    const res = r.result || {};
    toast(res.ok ? '测试消息已发出' : ('发送失败：' + (res.error || '未知原因')));
    await loadNotify();
  } catch (e) { toast(e.message); }
}

async function setupWecomBot() {
  const key = prompt('粘贴企业微信群机器人的 Webhook 地址（或只贴 key）\n\n微信：群设置 → 群机器人 → 添加 → 复制 Webhook 地址');
  if (!key) return;
  try {
    const r = await api('/api/notify/wecom-bot', 'POST', { key: key.trim() });
    toast(r.message || '已配置');
    await loadNotify();
  } catch (e) { toast(e.message); }
}

function notifyToggleEvent(ev) {
  const f = state.notify.form;
  const i = f.events.indexOf(ev);
  if (i >= 0) f.events.splice(i, 1); else f.events.push(ev);
  render();
}

// ---------------- 渲染 ----------------
const NOTIFY_EVENT_LABEL = {
  daily_review: '每日经营复盘',
  customer_reminder: '熟客提醒',
  payment_received: '收款到账',
  revenue_warning: '流水低于保本线预警',
};

function renderNotify() {
  const n = state.notify;
  const f = n.form;

  const chCards = n.providers.map(p => `
    <div class="pay-type-btn ${f.channel === p.id ? 'on' : ''}"
         style="text-align:left;padding:10px 12px;margin-bottom:6px"
         onclick="notifyPickChannel('${esc(p.id)}')">
      ${esc(p.name)} ${p.ready ? '<span class="pay-status">可用</span>' : '<span class="pay-status">待配置</span>'}
      <div class="pay-mchid">${esc(p.desc || p.requirement || '')}</div>
    </div>`).join('');

  const evBoxes = Object.keys(NOTIFY_EVENT_LABEL).map(ev => `
    <label class="acct-note" style="display:block;cursor:pointer">
      <input type="checkbox" ${f.events.includes(ev) ? 'checked' : ''}
             onchange="notifyToggleEvent('${ev}')" />
      ${NOTIFY_EVENT_LABEL[ev]}</label>`).join('');

  const subRows = n.subs.length === 0
    ? '<div class="empty">还没有订阅</div>'
    : n.subs.map(s => `
      <div class="pay-row">
        <div class="pay-row-main">
          <div class="pay-type-badge">${esc(s.channel)}</div>
          <div class="txn-sub">${esc(s.target || '默认目标')}</div>
          <div class="pay-mchid">${(s.events || []).map(e => NOTIFY_EVENT_LABEL[e] || e).join('、') || '未选事件'}</div>
        </div>
        <div class="pay-row-actions">
          <button class="btn-mini" onclick="testSubscription(${s.id})">测试</button>
          <button class="btn-mini btn-danger" onclick="delSubscription(${s.id})">删除</button>
        </div>
      </div>`).join('');

  const inbox = n.inbox.length === 0
    ? '<div class="empty">本地通道还没收到消息，点上面「发一条测试推送」试试</div>'
    : n.inbox.slice(0, 8).map(m => `
      <div class="review-box" style="margin-bottom:8px">
        <strong>${esc(m.title || '')}</strong><br/>${esc(m.content || '')}
        <div class="txn-time">${esc(m.created_at || '')}</div>
      </div>`).join('');

  const logs = n.logs.length === 0
    ? '<div class="empty">暂无投递记录</div>'
    : n.logs.slice(0, 12).map(l => `
      <div class="txn-row">
        <div class="txn-main">
          <div class="txn-item">${esc(NOTIFY_EVENT_LABEL[l.event] || l.event)}
            ${l.ok ? '<span class="pay-status">成功</span>' : '<span class="txn-amount expense">失败</span>'}</div>
          <div class="txn-sub">${esc(l.channel || '')} · ${esc(l.created_at || '')}${l.retries ? ' · 重试 ' + l.retries + ' 次' : ''}</div>
          ${l.error ? `<div class="pay-mchid">${esc(l.error)}</div>` : ''}
        </div>
      </div>`).join('');

  return `
  <div class="hero"><div class="hero-title">主动触达</div>
    <div class="hero-sub">复盘与提醒主动推到手机上，不用自己想起来打开</div></div>

  <div class="card">
    <div class="card-title">⚡ 最快接通：企业微信群机器人</div>
    <div class="acct-note">不需要正式小程序 AppID。手机企业微信建一个只有自己的群，
      群设置 → 群机器人 → 添加 → 复制 Webhook 里的 key 即可。</div>
    <button class="btn-primary" onclick="setupWecomBot()">一步配置并立即测试</button>
  </div>

  <div class="card">
    <div class="card-title">📡 推送通道</div>
    ${chCards || '<div class="empty">没有可用通道</div>'}
    <div class="form-item"><label class="form-label">接收目标（webhook key / 用户 ID）</label>
      <input class="form-input" type="text" placeholder="本地记录通道可留空"
             value="${esc(f.target)}" oninput="state.notify.form.target=this.value" /></div>
    <div class="form-item"><label class="form-label">订阅哪些消息</label>${evBoxes}</div>
    <div class="pay-actions">
      <button class="btn-mini" onclick="testPush()">发一条测试推送</button>
      <button class="btn-mini" onclick="saveSubscription()">保存订阅</button>
    </div>
  </div>

  <div class="card">
    <div class="card-title">🔔 已订阅（${n.subs.length}）</div>
    ${subRows}
  </div>

  <div class="card">
    <div class="card-title">📥 本地收件箱（演示用）</div>
    ${inbox}
  </div>

  <div class="card">
    <div class="card-title">📜 投递记录</div>
    ${logs}
  </div>`;
}
