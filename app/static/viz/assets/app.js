import { api, store, startStream, stopStream, onUnauthorized, ApiError } from './api.js';
import { icon, esc, initial, toast, modal } from './ui.js';
import * as loginPage from './pages/login.js';
import * as overviewPage from './pages/overview.js';
import * as chatPage from './pages/chat.js';
import * as officePage from './pages/office.js';
import * as agentsPage from './pages/agents.js';
import * as settingsPage from './pages/settings.js';

const ROUTES = [
  { id: 'overview', label: '总览 · 链路', icon: 'overview', title: '总览与链路', sub: '一屏看清所有信箱与消息流向' },
  { id: 'chat', label: '通信台', icon: 'chat', title: '通信台', sub: '和任意信箱直接对话' },
  { id: 'office', label: '中继办公室', icon: 'office', title: '中继办公室', sub: '谁在活跃、谁静默、消息怎么走' },
  { id: 'agents', label: '信箱与成员', icon: 'agents', title: '信箱与成员', sub: '自动检测的信箱与它们的形象' },
  { id: 'settings', label: '设置与权限', icon: 'settings', title: '设置与权限', sub: '基础设置、账号与角色' },
];

const MODULES = {
  login: loginPage,
  overview: overviewPage,
  chat: chatPage,
  office: officePage,
  agents: agentsPage,
  settings: settingsPage,
};

const $ = (id) => document.getElementById(id);
let cleanup = null;
let currentRoute = '';
let booted = false;

export const ctx = {
  get user() { return store.user; },
  get settings() { return store.settings; },
  go: (route, params) => navigate(route, params),
  toast,
  modal,
  async reloadUser() {
    const data = await api.me();
    store.user = data.user;
    store.settings = data.settings;
    paintUser();
    return data.user;
  },
  async saveSettings(patch) {
    const data = await api.saveSettings(patch);
    store.settings = data.settings;
    return data.settings;
  },
};

/* ── 导航 ── */
function buildNav() {
  $('nav').innerHTML = ROUTES.map(
    (r) => `<a href="#/${r.id}" data-route="${r.id}">${icon(r.icon)}<span>${esc(r.label)}</span></a>`,
  ).join('') + `<a href="/viz/hq/" target="_blank" rel="noopener" class="ext" id="navHq" hidden>
    ${icon('overview', 16)}<span>战术大屏</span></a>`;
  // 主题包缺失时自动隐藏入口，避免留下一个 404 链接
  fetch('/viz/hq/index.html', { method: 'HEAD', credentials: 'same-origin' })
    .then((r) => { if (r.ok) document.getElementById('navHq').hidden = false; })
    .catch(() => {});
}

function markNav(route) {
  document.querySelectorAll('#nav a').forEach((a) => {
    a.classList.toggle('on', a.dataset.route === route);
  });
}

function paintUser() {
  const user = store.user;
  if (!user) return;
  const badge = user.role === 'admin' ? '' : `<span class="chip" style="font-size:10px;padding:0 5px">${esc(user.role_label || user.role)}</span>`;
  $('sideUser').innerHTML = `
    <div class="av">${esc(initial(user.username))}</div>
    <div class="meta"><b>${esc(user.username)}</b><span>${esc(user.role_label || user.role)}</span></div>
    ${badge}
    <button class="iconbtn" id="logoutBtn" title="退出登录">${icon('logout', 14)}</button>`;
  $('logoutBtn').onclick = async () => {
    try { await api.logout(); } catch { /* 忽略 */ }
    store.user = null;
    stopStream();
    location.hash = '#/login';
    navigate('login');
  };

  const name = store.settings?.site_name || 'Webhook';
  $('brandName').textContent = name;
  document.title = `${name} · 工作台`;
}

function paintLive(state) {
  const map = {
    open: ['online', '实时连接中'],
    reconnecting: ['idle', '连接中断，重连中'],
  };
  const [dot, text] = map[state] || ['online', '实时连接中'];
  $('liveState').innerHTML = `<span class="dot ${dot}"></span>${esc(text)}`;
}

function closeDrawer() {
  $('sidebar').classList.remove('open');
  document.querySelector('.scrim')?.remove();
}

function openDrawer() {
  $('sidebar').classList.add('open');
  const scrim = document.createElement('div');
  scrim.className = 'scrim';
  scrim.onclick = closeDrawer;
  document.body.appendChild(scrim);
}

/* ── 路由 ── */
function parseHash() {
  const raw = (location.hash || '').replace(/^#\/?/, '');
  return raw.split('?')[0] || '';
}

async function render(route, params = null) {
  const mod = MODULES[route];
  if (!mod) { navigate('overview'); return; }

  if (route === 'login') {
    $('app').classList.add('login-mode');
    $('pageTitle').textContent = '登录';
    $('pageSub').textContent = '';
    $('liveState').innerHTML = '';
  } else {
    $('app').classList.remove('login-mode');
    const meta = ROUTES.find((r) => r.id === route);
    $('pageTitle').textContent = meta?.title || '';
    $('pageSub').textContent = meta?.sub || '';
    paintLive('open');
  }

  if (booted) {
    $('boot').hidden = true;
    $('app').hidden = false;
  }

  if (typeof cleanup === 'function') {
    try { cleanup(); } catch (err) { console.error(err); }
  }
  cleanup = null;

  const view = $('view');
  view.innerHTML = '';
  view.dataset.page = route;
  currentRoute = route;
  markNav(route);
  closeDrawer();

  try {
    const result = await mod.mount(view, { ...ctx, params });
    if (typeof result === 'function') cleanup = result;
  } catch (err) {
    console.error(err);
    if (err instanceof ApiError && err.status === 401) return;
    view.innerHTML = `<div class="empty-state"><b>页面加载失败</b><span>${esc(err.message || err)}</span>
      <button class="btn ghost sm" id="retryBtn">重试</button></div>`;
    view.querySelector('#retryBtn').onclick = () => render(route, params);
  }
}

function navigate(route, params = null) {
  if (!store.user && route !== 'login') route = 'login';
  const target = `#/${route}`;
  if (location.hash === target) render(route, params);
  else {
    if (params) store.pending = params;
    location.hash = target;
  }
}

function onHashChange() {
  const route = parseHash() || 'overview';
  if (!store.user && route !== 'login') {
    location.hash = '#/login';
    return;
  }
  if (store.user && route === 'login') {
    location.hash = '#/overview';
    return;
  }
  const params = store.pending;
  store.pending = null;
  render(route, params);
}

/* ── 强制改密 ── */
function forcePasswordChange() {
  modal({
    title: '先设置一个新密码',
    okText: '保存并继续',
    body: `
      <p style="margin:0 0 12px;color:var(--ink2)">当前账号仍在用初始密码（即 API Key），继续使用有风险。</p>
      <label class="field"><span>新密码（至少 8 位）</span><input type="password" id="np1" autocomplete="new-password"></label>
      <label class="field"><span>再输一次</span><input type="password" id="np2" autocomplete="new-password"></label>
      <div id="npErr" style="color:var(--red);font-size:12px"></div>`,
    onOk: async (wrap) => {
      const a = wrap.querySelector('#np1').value;
      const b = wrap.querySelector('#np2').value;
      const err = wrap.querySelector('#npErr');
      if (a.length < 8) { err.textContent = '密码至少 8 位'; return false; }
      if (a !== b) { err.textContent = '两次输入不一致'; return false; }
      try {
        await api.changePassword(store.user.via === 'bearer' ? '' : '', a);
        store.user.must_change = false;
        toast('密码已更新');
        render(currentRoute || 'overview');
        return true;
      } catch (e) {
        err.textContent = e.message;
        return false;
      }
    },
  });
}

/* ── 启动 ── */
async function boot() {
  buildNav();
  $('menuBtn').innerHTML = icon('menu', 16);
  $('menuBtn').onclick = () => ($('sidebar').classList.contains('open') ? closeDrawer() : openDrawer());

  onUnauthorized(() => {
    if (!store.user) return;
    store.user = null;
    stopStream();
    toast('会话已过期，请重新登录', 'err');
    location.hash = '#/login';
  });

  store.on('stream-state', paintLive);

  let data = null;
  try {
    data = await api.me();
  } catch (err) {
    if (!(err instanceof ApiError) || err.status !== 401) {
      $('boot').innerHTML = `<div class="empty-state"><b>连不上服务</b><span>${esc(err.message)}</span>
        <button class="btn ghost sm" onclick="location.reload()">重新加载</button></div>`;
      return;
    }
  }

  if (data) {
    store.user = data.user;
    store.settings = data.settings;
    store.setAgents(data.agents || []);
    paintUser();
    startStream();

    booted = true;
    $('boot').hidden = true;
    $('app').hidden = false;

    const startRoute = parseHash() || 'overview';
    const route = startRoute === 'login' ? 'overview' : startRoute;
    // 登录态下落在 #/login 要把地址栏纠正过来，否则刷新一次又回登录页
    if (location.hash !== `#/${route}`) history.replaceState(null, '', `#/${route}`);
    render(route);

    if (store.user.must_change && store.user.via !== 'bearer') {
      setTimeout(forcePasswordChange, 400);
    }
  } else {
    booted = true;
    $('boot').hidden = true;
    $('app').hidden = false;
    if (location.hash !== '#/login') history.replaceState(null, '', '#/login');
    render('login');
  }

  window.addEventListener('hashchange', onHashChange);
}

boot();
