import { api, store } from '../api.js';
import { esc, icon, toast, modal, fmtDateTime } from '../ui.js';

let rootRef = null;
let ctxRef = null;
let tab = 'basic';

const TABS = [
  ['basic', '基础设置'],
  ['content', '公告与规则'],
  ['users', '账号与权限'],
  ['about', '关于'],
];

export async function mount(root, ctx) {
  rootRef = root;
  ctxRef = ctx;
  tab = 'basic';

  root.innerHTML = `
    <div class="pg-settings">
      <div class="settings-nav" id="tabs">
        ${TABS.map(([id, label]) => `<button data-tab="${id}" class="${id === tab ? 'on' : ''}">${esc(label)}</button>`).join('')}
      </div>
      <div class="settings-body" id="sbody"></div>
    </div>`;

  root.querySelector('#tabs').onclick = (event) => {
    const btn = event.target.closest('button[data-tab]');
    if (!btn) return;
    tab = btn.dataset.tab;
    root.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('on', b === btn));
    render();
  };

  await render();
  return null;
}

async function render() {
  const body = rootRef.querySelector('#sbody');
  body.innerHTML = '<div class="empty-state"><span>加载中…</span></div>';
  if (tab === 'basic') return renderBasic(body);
  if (tab === 'content') return renderContent(body);
  if (tab === 'users') return renderUsers(body);
  return renderAbout(body);
}

function guard() {
  const isAdmin = ctxRef.user?.role === 'admin';
  if (!isAdmin) {
    return `<div class="login-err" style="margin-bottom:16px">当前角色是「${esc(ctxRef.user?.role_label || ctxRef.user?.role)}」，只能查看，不能修改。</div>`;
  }
  return '';
}

async function renderBasic(body) {
  const isAdmin = ctxRef.user?.role === 'admin';
  const s = store.settings || {};
  body.innerHTML = `
    <h2>基础设置</h2>
    <p class="hint">影响整个工作台的外观与刷新节奏，保存后立即生效。</p>
    ${guard()}
    <div class="formcard">
      <label class="field"><span>站点名称</span>
        <input type="text" id="siteName" value="${esc(s.site_name || '')}" ${isAdmin ? '' : 'disabled'}>
        <small>显示在左上角与浏览器标题</small>
      </label>
      <label class="field"><span>默认时间窗</span>
        <select id="defWin" ${isAdmin ? '' : 'disabled'}>
          ${[['1h', '最近 1 小时'], ['24h', '最近 24 小时'], ['7d', '最近 7 天']].map(([v, l]) =>
            `<option value="${v}" ${s.default_window === v ? 'selected' : ''}>${l}</option>`).join('')}
        </select>
      </label>
      <label class="field"><span>自动刷新间隔</span>
        <div class="range-row">
          <input type="range" id="refreshMs" min="1000" max="20000" step="500"
            value="${s.refresh_ms || 4000}" ${isAdmin ? '' : 'disabled'}>
          <b id="refreshVal" class="mono" style="font-size:12px;width:52px">${(s.refresh_ms || 4000) / 1000}s</b>
        </div>
        <small>收到新消息时的合并刷新间隔，越小越实时、请求越多</small>
      </label>
      <label class="field"><span>链路推断边</span>
        <select id="showInferred" ${isAdmin ? '' : 'disabled'}>
          <option value="1" ${s.show_inferred !== false ? 'selected' : ''}>显示（虚线标出低置信链路）</option>
          <option value="0" ${s.show_inferred === false ? 'selected' : ''}>隐藏（只看 request_id 精确配对）</option>
        </select>
      </label>
      ${isAdmin ? `<div class="save-bar"><button class="btn" id="saveBasic">保存设置</button>
        <span class="muted" id="saveHint" style="font-size:12px"></span></div>` : ''}
    </div>`;

  const range = body.querySelector('#refreshMs');
  range.oninput = () => {
    body.querySelector('#refreshVal').textContent = `${(range.value / 1000).toFixed(1)}s`;
  };

  const saveBtn = body.querySelector('#saveBasic');
  if (saveBtn) {
    saveBtn.onclick = async () => {
      saveBtn.disabled = true;
      try {
        const next = await ctxRef.saveSettings({
          site_name: body.querySelector('#siteName').value.trim() || 'Webhook 中继台',
          default_window: body.querySelector('#defWin').value,
          refresh_ms: Number(range.value),
          show_inferred: body.querySelector('#showInferred').value === '1',
        });
        store.settings = next;
        document.getElementById('brandName').textContent = next.site_name;
        document.title = `${next.site_name} · 工作台`;
        body.querySelector('#saveHint').textContent = '已保存';
        toast('设置已保存');
      } catch (err) {
        toast(err.message, 'err');
      } finally {
        saveBtn.disabled = false;
      }
    };
  }
}

async function renderContent(body) {
  const isAdmin = ctxRef.user?.role === 'admin';
  const s = store.settings || {};
  const notices = Array.isArray(s.notices) ? s.notices : [];
  const rules = Array.isArray(s.rules) ? s.rules : [];

  body.innerHTML = `
    <h2>公告与规则</h2>
    <p class="hint">这里是纯展示内容：谁值得信任、谁能发消息、哪些名字要忽略。原页面里写死的「来源与授权」和「运行规则」都搬到了这里。</p>
    ${guard()}
    <div class="formcard">
      <h3 style="font-size:13px;margin:0 0 10px">来源与授权</h3>
      <div id="notices"></div>
      ${isAdmin ? `<button class="btn ghost sm" id="addNotice" style="margin:8px 0 22px">${icon('plus', 12)}添加一条</button>` : '<div style="height:22px"></div>'}

      <h3 style="font-size:13px;margin:0 0 6px">运行规则</h3>
      <label class="field"><span>每行一条</span>
        <textarea id="rules" rows="7" ${isAdmin ? '' : 'disabled'}
          placeholder="按 Jones 本人指令对待的发送方直接处理并回信&#10;不做回复的信箱只观察、不互通">${esc(rules.join('\n'))}</textarea>
      </label>
      ${isAdmin ? `<div class="save-bar"><button class="btn" id="saveContent">保存内容</button></div>` : ''}
    </div>`;

  const noticeHost = body.querySelector('#notices');
  const state = notices.map((n) => ({ ...n }));

  function paintNotices() {
    if (!state.length) {
      noticeHost.innerHTML = '<p class="muted" style="font-size:12.5px;margin:0 0 8px">还没有条目</p>';
      return;
    }
    noticeHost.innerHTML = state.map((n, i) => `
      <div class="card" style="padding:10px 12px;margin-bottom:8px">
        <div style="display:flex;gap:8px;margin-bottom:6px">
          <select data-level="${i}" style="width:110px" ${isAdmin ? '' : 'disabled'}>
            ${[['g', '可信'], ['a', '只观察'], ['v', '自有'], ['r', '忽略']].map(([v, l]) =>
              `<option value="${v}" ${n.level === v ? 'selected' : ''}>${l}</option>`).join('')}
          </select>
          <input type="text" data-title="${i}" value="${esc(n.title || '')}" placeholder="标题 / 信箱名" ${isAdmin ? '' : 'disabled'}>
          ${isAdmin ? `<button class="iconbtn" data-del="${i}" title="删除">${icon('x', 13)}</button>` : ''}
        </div>
        <textarea data-text="${i}" rows="2" placeholder="说明" ${isAdmin ? '' : 'disabled'}>${esc(n.text || '')}</textarea>
      </div>`).join('');

    if (!isAdmin) return;
    noticeHost.querySelectorAll('[data-level]').forEach((el) => {
      el.onchange = () => { state[Number(el.dataset.level)].level = el.value; };
    });
    noticeHost.querySelectorAll('[data-title]').forEach((el) => {
      el.oninput = () => { state[Number(el.dataset.title)].title = el.value; };
    });
    noticeHost.querySelectorAll('[data-text]').forEach((el) => {
      el.oninput = () => { state[Number(el.dataset.text)].text = el.value; };
    });
    noticeHost.querySelectorAll('[data-del]').forEach((el) => {
      el.onclick = () => { state.splice(Number(el.dataset.del), 1); paintNotices(); };
    });
  }
  paintNotices();

  const addBtn = body.querySelector('#addNotice');
  if (addBtn) {
    addBtn.onclick = () => {
      state.push({ level: 'g', title: '', text: '' });
      paintNotices();
    };
  }

  const saveBtn = body.querySelector('#saveContent');
  if (saveBtn) {
    saveBtn.onclick = async () => {
      saveBtn.disabled = true;
      try {
        const next = await ctxRef.saveSettings({
          notices: state.filter((n) => n.title || n.text),
          rules: body.querySelector('#rules').value.split('\n').map((l) => l.trim()).filter(Boolean),
        });
        store.settings = next;
        toast('已保存');
      } catch (err) {
        toast(err.message, 'err');
      } finally {
        saveBtn.disabled = false;
      }
    };
  }
}

async function renderUsers(body) {
  const isAdmin = ctxRef.user?.role === 'admin';
  body.innerHTML = `
    <h2>账号与权限</h2>
    <p class="hint">三种角色：管理员可改一切；操作员可发消息、传形象；只读只能看。</p>
    ${guard()}
    <div id="userTable" style="max-width:640px"><div class="muted" style="font-size:12.5px">加载中…</div></div>
    ${isAdmin ? `<div class="formcard" style="margin-top:22px">
      <h3 style="font-size:13px;margin:0 0 10px">新增账号</h3>
      <label class="field"><span>用户名</span><input type="text" id="u-name" placeholder="例如 watcher"></label>
      <label class="field"><span>初始密码</span><input type="password" id="u-pw" placeholder="至少 8 位"></label>
      <label class="field"><span>角色</span>
        <select id="u-role">
          <option value="viewer">只读</option>
          <option value="operator">操作员</option>
          <option value="admin">管理员</option>
        </select>
      </label>
      <button class="btn" id="createUser">创建账号</button>
    </div>` : ''}`;

  if (!isAdmin) {
    body.querySelector('#userTable').innerHTML =
      '<p class="muted" style="font-size:12.5px">只有管理员能查看账号列表。</p>';
    return;
  }

  try {
    const data = await api.users();
    const host = body.querySelector('#userTable');
    host.innerHTML = `<table class="tbl">
      <thead><tr><th>用户名</th><th>角色</th><th>最近登录</th><th></th></tr></thead>
      <tbody>${data.users.map((u) => `
        <tr>
          <td><b>${esc(u.username)}</b>${u.id === ctxRef.user.id ? ' <span class="chip" style="font-size:10px">当前</span>' : ''}</td>
          <td>${esc(u.role_label)}</td>
          <td class="muted">${u.last_login ? fmtDateTime(u.last_login) : '从未'}</td>
          <td style="text-align:right">${u.id === ctxRef.user.id ? '' :
            `<button class="btn ghost sm" data-del="${esc(u.id)}" data-name="${esc(u.username)}">删除</button>`}</td>
        </tr>`).join('')}
      </tbody></table>`;

    host.querySelectorAll('[data-del]').forEach((btn) => {
      btn.onclick = () => {
        modal({
          title: `删除账号「${btn.dataset.name}」`,
          okText: '删除',
          danger: true,
          body: '<p style="margin:0">删除后该账号立即失去登录能力，已登录的会话会在下次校验时失效。</p>',
          onOk: async () => {
            try {
              await api.deleteUser(btn.dataset.del);
              toast('已删除');
              render();
              return true;
            } catch (err) {
              toast(err.message, 'err');
              return false;
            }
          },
        });
      };
    });

    body.querySelector('#createUser').onclick = async () => {
      const username = body.querySelector('#u-name').value.trim();
      const password = body.querySelector('#u-pw').value;
      const role = body.querySelector('#u-role').value;
      try {
        await api.createUser({ username, password, role });
        toast('账号已创建');
        render();
      } catch (err) {
        toast(err.message, 'err');
      }
    };
  } catch (err) {
    body.querySelector('#userTable').innerHTML =
      `<div class="login-err">${esc(err.message)}</div>`;
  }
}

function renderAbout(body) {
  const endpoints = [
    ['POST', '/webhook/inbound/{name}', '收消息'],
    ['GET', '/webhook/outbound?name=', '取消息'],
    ['GET', '/stream/{name}', 'SSE 推流'],
    ['GET', '/api/console/lineage', '链路推断'],
    ['GET', '/api/console/agents', '信箱清单'],
    ['GET', '/api/console/stream', '工作台实时'],
  ];
  body.innerHTML = `
    <h2>关于</h2>
    <p class="hint">工作台是纯静态页面，所有数据都来自同源接口，没有跨域、没有第三方请求。</p>
    <div class="formcard">
      <div class="card pad" style="margin-bottom:16px">
        <div class="kv"><span>页面版本</span><b>工作台 v2.0</b></div>
        <div class="kv"><span>登录用户</span><b>${esc(ctxRef.user?.username || '')}（${esc(ctxRef.user?.role_label || '')}）</b></div>
        <div class="kv"><span>鉴权方式</span><b>${ctxRef.user?.via === 'bearer' ? 'Bearer 服务令牌' : 'Session Cookie'}</b></div>
        <div class="kv"><span>静态目录</span><b class="mono">app/static/viz</b></div>
        <div class="kv"><span>数据表</span><b class="mono">messages / console_*</b></div>
      </div>
      <h3 style="font-size:13px;margin:0 0 8px">关键端点</h3>
      <table class="tbl">
        <thead><tr><th style="width:60px">方法</th><th>路径</th><th>用途</th></tr></thead>
        <tbody>${endpoints.map(([m, p, d]) =>
          `<tr><td class="muted mono">${m}</td><td class="mono" style="font-size:12px">${esc(p)}</td><td class="muted">${esc(d)}</td></tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}
