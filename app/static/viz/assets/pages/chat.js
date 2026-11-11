import { api, store } from '../api.js';
import { esc, icon, fmtTime, fmtDay, rel, statusText, defaultAvatar, initial, toast, debounce } from '../ui.js';

const S = {
  agents: [],
  last: new Map(),
  selected: null,
  msgs: [],
  oldest: null,
  unread: new Map(),
  pending: null,
  ctx: null,
  els: {},
  filter: '',
  loading: false,
  exhausted: false,
  gen: 0,
};

export async function mount(root, ctx) {
  S.ctx = ctx;
  S.selected = null;
  S.msgs = [];
  S.unread.clear();
  S.filter = '';
  const preselect = ctx.params?.agent || null;

  root.innerHTML = `
    <div class="pg-chat" id="chatPage">
      <div class="thread-list">
        <div class="thread-search">
          <input type="search" id="threadSearch" placeholder="搜索信箱…" autocomplete="off">
        </div>
        <div class="threads" id="threads">
          <div class="empty-state"><span>加载中…</span></div>
        </div>
      </div>

      <div class="chat-main">
        <div class="chat-head">
          <button class="iconbtn only-mobile" id="backBtn" title="返回列表">${icon('caret', 16)}</button>
          <div id="headAv" style="width:32px;height:32px;flex:none;display:grid;place-items:center"></div>
          <div style="min-width:0">
            <h2 id="headName">选择信箱</h2>
            <div class="sub" id="headSub">从左边挑一个信箱开始对话</div>
          </div>
          <span class="spacer" style="flex:1"></span>
          <span class="chip" id="headChip" hidden></span>
        </div>

        <div class="chat-scroll" id="chatScroll">
          <div class="empty-state">
            <b>还没有选信箱</b><span>左侧列出了所有被自动检测到的信箱</span>
          </div>
        </div>

        <div class="composer">
          <div class="tools">
            <button class="iconbtn" id="attachBtn" title="发送图片 / 文件">${icon('paperclip', 14)}</button>
            <span id="attachChip" class="muted" style="font-size:12px"></span>
            <input type="file" id="fileInput" hidden>
          </div>
          <div class="row">
            <textarea id="input" placeholder="写消息…" rows="1" disabled></textarea>
            <button class="btn" id="sendBtn" disabled>${icon('send', 14)}发送</button>
          </div>
        </div>
      </div>

      <aside class="chat-side" id="chatSide"></aside>
    </div>`;

  S.els = {
    page: root.querySelector('#chatPage'),
    threads: root.querySelector('#threads'),
    scroll: root.querySelector('#chatScroll'),
    input: root.querySelector('#input'),
    sendBtn: root.querySelector('#sendBtn'),
    attachBtn: root.querySelector('#attachBtn'),
    attachChip: root.querySelector('#attachChip'),
    fileInput: root.querySelector('#fileInput'),
    headName: root.querySelector('#headName'),
    headSub: root.querySelector('#headSub'),
    headAv: root.querySelector('#headAv'),
    headChip: root.querySelector('#headChip'),
    side: root.querySelector('#chatSide'),
  };

  root.querySelector('#threadSearch').oninput = (e) => {
    S.filter = e.target.value.trim().toLowerCase();
    renderThreads();
  };
  root.querySelector('#backBtn').onclick = () => {
    S.els.page.classList.remove('thread-open');
  };

  S.els.sendBtn.onclick = sendMessage;
  S.els.input.onkeydown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      sendMessage();
    }
  };
  S.els.input.oninput = () => {
    S.els.input.style.height = 'auto';
    S.els.input.style.height = `${Math.min(S.els.input.scrollHeight, 120)}px`;
  };
  S.els.attachBtn.onclick = () => S.els.fileInput.click();
  S.els.fileInput.onchange = async () => {
    const file = S.els.fileInput.files?.[0];
    if (!file) return;
    S.els.attachBtn.disabled = true;
    S.els.attachChip.textContent = '上传中…';
    try {
      const data = await api.upload(file);
      S.pending = { url: data.url, type: data.type, name: data.name };
      S.els.attachChip.innerHTML = `${icon('paperclip', 12)} ${esc(data.name)} <a href="#" id="clearAttach">移除</a>`;
      root.querySelector('#clearAttach').onclick = (ev) => {
        ev.preventDefault();
        S.pending = null;
        S.els.attachChip.textContent = '';
      };
    } catch (err) {
      toast(err.message, 'err');
      S.els.attachChip.textContent = '';
    } finally {
      S.els.attachBtn.disabled = false;
      S.els.fileInput.value = '';
    }
  };

  S.els.scroll.onscroll = async () => {
    if (S.exhausted || S.loading || !S.oldest || !S.selected) return;
    if (S.els.scroll.scrollTop > 30) return;
    const gen = S.gen;
    const before = S.oldest;
    S.loading = true;
    try {
      const data = await api.messages(S.selected, { limit: 40, before });
      // 请求期间切了会话 / 重新打开过：整包丢弃，否则会把上一页并进新列表造成重复气泡
      if (gen !== S.gen) return;
      const seen = new Set(S.msgs.map((m) => m.id));
      const older = (data.messages || []).filter((m) => !seen.has(m.id));
      if (older.length) {
        const height = S.els.scroll.scrollHeight;
        S.msgs = [...older, ...S.msgs];
        S.oldest = S.msgs[0].timestamp;
        renderMessages(false);
        S.els.scroll.scrollTop = S.els.scroll.scrollHeight - height + 30;
      } else {
        // 到头了：只提示一次，改成贴在列表顶部，不再反复弹窗
        S.exhausted = true;
        if (!S.els.scroll.querySelector('.no-more-top')) {
          S.els.scroll.insertAdjacentHTML('afterbegin',
            '<div class="daysep no-more-top">没有更早的消息了</div>');
        }
      }
    } catch (err) {
      if (gen === S.gen) toast(err.message, 'err');
    } finally {
      S.loading = false;
    }
  };

  const refreshThreads = debounce(renderThreads, 400);
  const offMessages = store.on('messages', (items) => {
    for (const item of items) {
      S.last.set(item.name, item);
      if (item.name === S.selected) {
        appendMessages([item]);
      } else if (item.direction === 'inbound') {
        S.unread.set(item.name, (S.unread.get(item.name) || 0) + 1);
      }
    }
    refreshThreads();
  });

  await loadThreads();
  if (preselect && S.agents.some((a) => a.name === preselect)) open(preselect);
  else if (S.agents.length) open(S.agents[0].name);

  return () => offMessages();
}

async function loadThreads() {
  const [agentsData, events] = await Promise.all([api.agents(), api.events(80)]);
  S.agents = agentsData.agents || [];
  store.setAgents(S.agents);
  S.last.clear();
  for (const e of events.events || []) S.last.set(e.name, e);
  renderThreads();
}

function renderThreads() {
  const host = S.els.threads;
  if (!host) return;
  let list = [...S.agents];
  if (S.filter) {
    list = list.filter((a) => `${a.name} ${a.display_name}`.toLowerCase().includes(S.filter));
  }
  list.sort((a, b) => (S.last.get(b.name)?.timestamp || 0) - (S.last.get(a.name)?.timestamp || 0));

  if (!list.length) {
    host.innerHTML = `<div class="empty-state"><b>没有信箱</b>
      <span>往 /webhook/inbound/{name} 发一条消息就能自动出现</span></div>`;
    return;
  }

  host.innerHTML = list.map((a) => {
    const last = S.last.get(a.name);
    const preview = last
      ? `${last.via === 'console' ? '我：' : ''}${last.content || `[${last.type}]`}`
      : '暂无消息';
    const unread = S.unread.get(a.name) || 0;
    const av = a.avatar || defaultAvatar(a.name);
    const when = last ? shortWhen(last.timestamp) : '';
    return `<div class="thread ${a.name === S.selected ? 'on' : ''}" data-name="${esc(a.name)}">
      <div style="width:36px;height:36px;flex:none">
        ${av
          ? `<img src="${esc(av)}" alt="" style="width:36px;height:36px;border-radius:50%;object-fit:cover"
               onerror="this.style.display='none';this.nextElementSibling.style.display='grid'">
             <div class="av-fallback" style="display:none;width:36px;height:36px;font-size:13px">${esc(initial(a.display_name || a.name))}</div>`
          : `<div class="av-fallback" style="width:36px;height:36px;font-size:13px">${esc(initial(a.display_name || a.name))}</div>`}
      </div>
      <div class="body">
        <div class="row1">
          <span class="nm">${esc(a.display_name || a.name)}</span>
          <span class="dot ${esc(a.status)}" style="transform:scale(.85)"></span>
          <span class="when">${when}</span>
        </div>
        <div class="preview">${esc(preview.slice(0, 42))}</div>
      </div>
      ${unread ? `<span class="badge">${unread > 99 ? '99+' : unread}</span>` : ''}
    </div>`;
  }).join('');

  host.querySelectorAll('.thread').forEach((node) => {
    node.onclick = () => open(node.dataset.name);
  });
}

async function open(name) {
  const gen = ++S.gen;
  S.selected = name;
  S.unread.delete(name);
  S.pending = null;
  // 立刻把上一条会话的列表和游标清掉：切换瞬间仍可能有一次滚动事件打进来
  S.msgs = [];
  S.oldest = null;
  S.exhausted = false;
  S.loading = false;
  S.els.attachChip.textContent = '';
  S.els.page.classList.add('thread-open');
  renderThreads();

  const agent = store.agent(name) || S.agents.find((a) => a.name === name) || { name, display_name: name };
  S.els.headName.textContent = agent.display_name || agent.name;
  S.els.headSub.textContent = `收 ${agent.inbound || 0} · 发 ${agent.outbound || 0} · ${rel(agent.last_ts)}`;
  const av = agent.avatar || defaultAvatar(agent.name);
  S.els.headAv.innerHTML = av
    ? `<img src="${esc(av)}" alt="" style="width:32px;height:32px;border-radius:50%;object-fit:cover">`
    : `<div class="av-fallback" style="width:32px;height:32px;font-size:12px">${esc(initial(agent.display_name || agent.name))}</div>`;
  S.els.headChip.hidden = false;
  S.els.headChip.className = `chip ${agent.status === 'online' ? 'in' : ''}`;
  S.els.headChip.textContent = statusText(agent.status || 'empty');

  S.els.input.disabled = false;
  S.els.sendBtn.disabled = false;
  S.els.scroll.innerHTML = '<div class="empty-state"><span>加载消息…</span></div>';

  try {
    const data = await api.messages(name, { limit: 60 });
    if (gen !== S.gen) return; // 期间切到别的会话了，这份结果作废
    S.msgs = data.messages || [];
    S.oldest = S.msgs[0]?.timestamp || null;
    renderMessages(true);
    renderSide(agent);
  } catch (err) {
    if (gen === S.gen) {
      S.els.scroll.innerHTML = `<div class="empty-state"><b>加载失败</b><span>${esc(err.message)}</span></div>`;
    }
  }
}

function shortWhen(ts) {
  const d = new Date(ts * 1000);
  const now = new Date();
  const sameDay = d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
  return sameDay ? fmtTime(ts) : fmtDay(ts);
}

function safeUrl(raw) {
  const u = String(raw || '').trim();
  if (!u) return '';
  const low = u.toLowerCase();
  if (low.startsWith('javascript:') || low.startsWith('data:')
      || low.startsWith('vbscript:') || low.startsWith('file:')) return '';
  if (u.startsWith('/uploads/')) return u.includes('..') || u.includes('\\') ? '' : u;
  try {
    const p = new URL(u, location.origin);
    return (p.protocol === 'http:' || p.protocol === 'https:') ? u : '';
  } catch { return ''; }
}

function mediaHtml(m) {
  if (!m.media_url) return '';
  const url = esc(safeUrl(m.media_url));
  if (!url) return '<span class="file">[附件链接已拦截]</span>';
  if (m.type === 'image') return `<img src="${url}" alt="图片" loading="lazy" onerror="this.remove()">`;
  if (m.type === 'video') return `<video src="${url}" controls preload="metadata"></video>`;
  if (m.type === 'audio') return `<audio src="${url}" controls preload="metadata"></audio>`;
  return `<a class="file" href="${url}" target="_blank" rel="noopener">${icon('paperclip', 13)}${esc(m.content || '下载附件')}</a>`;
}

function bubbleHtml(m) {
  // 「我发的」按 via 判断，不看 direction：控制台发出的消息在库里是 inbound
  // （人发给信箱），历史上有一批写成了 outbound，两种都要能显示在右侧。
  const mine = m.via === 'console';
  const kind = mine ? '发' : (m.direction === 'inbound' ? '收' : '回');
  const meta = `${fmtTime(m.timestamp)} · ${kind}${mine ? ' · 控制台' : ''}`;
  const text = m.content && m.type !== 'file' ? esc(m.content) : '';
  const inner = (text ? `<div class="text">${text}</div>` : '')
    + mediaHtml(m)
    + `<span class="meta">${meta}</span>`;
  return `<div class="bubble ${mine ? 'right' : 'left'}" data-id="${esc(m.id)}">${inner}</div>`;
}

function renderMessages(toBottom) {
  if (!S.msgs.length) {
    S.els.scroll.innerHTML = `<div class="empty-state"><b>还没有消息</b>
      <span>在下方输入框发一条，就会通过中继投递到这个信箱</span></div>`;
    return;
  }
  const parts = [];
  let lastDay = '';
  for (const m of S.msgs) {
    const day = fmtDay(m.timestamp);
    if (day !== lastDay) {
      parts.push(`<div class="daysep">${day}</div>`);
      lastDay = day;
    }
    parts.push(bubbleHtml(m));
  }
  S.els.scroll.innerHTML = parts.join('');
  if (toBottom) S.els.scroll.scrollTop = S.els.scroll.scrollHeight;
}

function appendMessages(items) {
  const fresh = items.filter((m) => m.name === S.selected && !S.msgs.some((x) => x.id === m.id));
  if (!fresh.length) return;
  const atBottom = S.els.scroll.scrollHeight - S.els.scroll.scrollTop - S.els.scroll.clientHeight < 80;
  const hadNone = !S.msgs.length;
  S.msgs.push(...fresh);
  if (hadNone) {
    renderMessages(true);
  } else {
    const day = fmtDay(fresh[0].timestamp);
    const lastDay = S.msgs.length > fresh.length ? fmtDay(S.msgs[S.msgs.length - fresh.length - 1].timestamp) : '';
    if (day !== lastDay) S.els.scroll.insertAdjacentHTML('beforeend', `<div class="daysep">${day}</div>`);
    S.els.scroll.insertAdjacentHTML('beforeend', fresh.map(bubbleHtml).join(''));
    if (atBottom) S.els.scroll.scrollTop = S.els.scroll.scrollHeight;
  }
  S.els.headSub.textContent = `收/发已更新 · ${rel(fresh[fresh.length - 1].timestamp)}`;
}

async function sendMessage() {
  const name = S.selected;
  if (!name) return;
  const input = S.els.input;
  const text = input.value.trim();
  if (!text && !S.pending) return;

  const payload = {
    name,
    content: text || `[${S.pending.type}]`,
    type: S.pending?.type || 'text',
    media_url: S.pending?.url || null,
  };

  S.els.sendBtn.disabled = true;
  try {
    const res = await api.send(payload);
    input.value = '';
    input.style.height = 'auto';
    S.pending = null;
    S.els.attachChip.textContent = '';
    appendMessages([{
      id: res.id,
      name,
      direction: 'inbound',   // 人发给信箱，与后端保持一致
      type: payload.type,
      content: payload.content,
      media_url: payload.media_url,
      timestamp: res.timestamp,
      via: 'console',
    }]);
    S.els.scroll.scrollTop = S.els.scroll.scrollHeight;
  } catch (err) {
    toast(err.message, 'err');
  } finally {
    S.els.sendBtn.disabled = false;
  }
}

function renderSide(agent) {
  const a = store.agent(agent.name) || agent;
  S.els.side.innerHTML = `
    <div class="side-block" style="text-align:center;padding:18px 16px 14px">
      <div style="width:56px;height:56px;margin:0 auto 8px">
        ${a.avatar || defaultAvatar(a.name)
          ? `<img src="${esc(a.avatar || defaultAvatar(a.name))}" alt="" style="width:56px;height:56px;border-radius:50%;object-fit:cover">`
          : `<div class="av-fallback" style="width:56px;height:56px;font-size:18px">${esc(initial(a.display_name || a.name))}</div>`}
      </div>
      <div style="font-weight:600;font-size:14px">${esc(a.display_name || a.name)}</div>
      <div class="mono muted" style="font-size:11.5px">${esc(a.name)}</div>
      ${a.role ? `<span class="chip" style="margin-top:8px">${esc(a.role)}</span>` : ''}
    </div>
    <div class="side-block">
      <h3>往来统计</h3>
      <div class="kv"><span>收进</span><b>${a.inbound || 0}</b></div>
      <div class="kv"><span>发出</span><b>${a.outbound || 0}</b></div>
      <div class="kv"><span>总计</span><b>${a.total || 0}</b></div>
      <div class="kv"><span>最近</span><b>${rel(a.last_ts)}</b></div>
      <div class="kv"><span>状态</span><b>${statusText(a.status)}</b></div>
    </div>
    ${a.note ? `<div class="side-block"><h3>备注</h3>
      <div style="font-size:12.5px;color:var(--ink2);line-height:1.7">${esc(a.note)}</div></div>` : ''}
    <div class="side-block" style="border-bottom:0">
      <h3>快捷操作</h3>
      <div style="display:flex;flex-direction:column;gap:6px">
        <button class="btn ghost sm" id="sideAgents">编辑信箱资料</button>
        <button class="btn ghost sm" id="sideOffice">在办公室中查看</button>
      </div>
    </div>`;

  S.els.side.querySelector('#sideAgents').onclick = () => S.ctx.go('agents');
  S.els.side.querySelector('#sideOffice').onclick = () => S.ctx.go('office');
}
