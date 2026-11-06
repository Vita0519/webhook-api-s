import { api, store } from '../api.js';
import { esc, num, fmtTime, statusText, defaultAvatar, initial, debounce } from '../ui.js';

const NW = 126;
const NH = 44;
const NARROW = 640;

const state = { win: '24h', inferred: true, data: null };
let ctxRef = null;

export async function mount(root, ctx) {
  ctxRef = ctx;
  state.win = store.settings?.default_window || '24h';

  root.innerHTML = `
    <div class="pg-overview">
      <div class="kpis" id="kpis">
        ${['信箱', '在线', '窗口内消息', '链路', '配对率'].map((k) => `
          <div class="kpi"><div class="k">${k}</div><div class="v">—</div><div class="s">加载中</div></div>`).join('')}
      </div>

      <section class="graph-wrap">
        <div class="panel-head">
          <h2>链路拓扑</h2>
          <span class="chip" id="graphStat">—</span>
          <span class="spacer"></span>
          <div class="seg" id="winSeg">
            ${Object.entries({ '1h': '1 小时', '24h': '24 小时', '7d': '7 天' }).map(([v, label]) =>
              `<button data-win="${v}" class="${v === state.win ? 'on' : ''}">${label}</button>`).join('')}
          </div>
          <button class="btn ghost sm" id="inferBtn">推断边</button>
        </div>
        <div class="graph-body" id="graphBody">
          <div class="empty-state"><span>正在计算链路…</span></div>
        </div>
        <div class="legend" id="legend">
          <span><i></i>精确配对</span>
          <span><i class="dash"></i>推断</span>
          <span><i class="faint"></i>中继自身</span>
        </div>
      </section>

      <div class="side-col">
        <div class="feed">
          <div class="panel-head">
            <h2>实时事件</h2>
            <span class="spacer"></span>
            <span class="chip" id="feedState">连接中</span>
          </div>
          <div class="feed-list" id="feedList">
            <div class="empty-state"><span>暂无消息</span></div>
          </div>
        </div>
        <div class="rank">
          <div class="panel-head"><h2>信箱往来量</h2></div>
          <div id="rankList" style="padding:4px 0 8px"></div>
        </div>
      </div>
    </div>`;

  const host = root.querySelector('#graphBody');
  const feedList = root.querySelector('#feedList');
  const inferBtn = root.querySelector('#inferBtn');

  const syncInfer = () => { inferBtn.textContent = state.inferred ? '推断边 · 显示' : '推断边 · 隐藏'; };
  syncInfer();

  root.querySelector('#winSeg').onclick = (event) => {
    const btn = event.target.closest('button[data-win]');
    if (!btn || btn.dataset.win === state.win) return;
    state.win = btn.dataset.win;
    root.querySelectorAll('#winSeg button').forEach((b) => b.classList.toggle('on', b === btn));
    load(root, host, feedList);
  };

  inferBtn.onclick = () => {
    state.inferred = !state.inferred;
    syncInfer();
    load(root, host, feedList);
  };

  const reload = debounce(() => load(root, host, feedList), 1200);
  const offMessages = store.on('messages', (items) => {
    appendFeed(feedList, items);
    reload();
  });
  const offStream = store.on('stream-state', (s) => {
    const chip = root.querySelector('#feedState');
    if (!chip) return;
    chip.textContent = s === 'open' ? '实时' : '重连中';
    chip.className = `chip ${s === 'open' ? 'in' : 'warn'}`;
  });

  const observer = new ResizeObserver(debounce(() => {
    if (state.data) drawGraph(host, state.data, ctxRef);
  }, 180));
  observer.observe(host);

  await load(root, host, feedList);

  return () => {
    offMessages();
    offStream();
    observer.disconnect();
  };
}

async function load(root, host, feedList) {
  try {
    const [overview, lineage, events] = await Promise.all([
      api.overview(state.win),
      api.lineage(state.win, state.inferred),
      api.events(28),
    ]);
    store.setAgents(overview.agents || []);
    state.data = lineage;
    renderKpis(root, overview, lineage);
    drawGraph(host, lineage, ctxRef);
    renderFeed(feedList, events.events || []);
    renderRank(root, overview);
  } catch (err) {
    host.innerHTML = `<div class="empty-state"><b>链路加载失败</b><span>${esc(err.message)}</span></div>`;
  }
}

function renderKpis(root, overview, lineage) {
  const k = overview.kpis;
  const cards = [
    { k: '信箱', v: k.inboxes, s: `${k.online} 个活跃` },
    { k: '在线', v: k.online, s: '5 分钟内有消息' },
    { k: '窗口内消息', v: num(k.messages), s: `收 ${num(k.inbound)} · 发 ${num(k.outbound)}` },
    { k: '链路', v: k.edges, s: `${k.inferred} 条为推断` },
    { k: '配对率', v: `${Math.round((k.paired_ratio || 0) * 100)}%`, s: 'request_id 精确配对占比', wide: true },
  ];
  root.querySelector('#kpis').innerHTML = cards.map((c) => `
    <div class="kpi${c.wide ? ' wide' : ''}"><div class="k">${esc(c.k)}</div>
      <div class="v">${esc(String(c.v))}</div><div class="s">${esc(c.s)}</div></div>`).join('');
  root.querySelector('#graphStat').textContent =
    `${lineage.nodes.filter((n) => !n.virtual).length} 节点 · ${lineage.edges.length} 边`;
}

function renderFeed(host, items) {
  if (!items.length) {
    host.innerHTML = '<div class="empty-state"><span>这段时间还没有消息</span></div>';
    return;
  }
  host.innerHTML = items.map(feedHtml).join('');
  host.scrollTop = host.scrollHeight;
}

function feedHtml(m) {
  const inbound = m.direction === 'inbound';
  const body = m.content
    ? esc(m.content.slice(0, 90))
    : (m.media_url ? `[${esc(m.type)}]` : '（空消息）');
  return `<div class="feed-item">
    <span class="t">${fmtTime(m.timestamp)}</span>
    <div class="body">
      <span class="who">${esc(m.name)}</span>
      <span class="chip ${inbound ? 'in' : 'out'}" style="margin-left:4px">${inbound ? '收' : '发'}</span>
      <div class="txt">${body}</div>
    </div>
  </div>`;
}

function appendFeed(host, items) {
  if (!items.length) return;
  const empty = host.querySelector('.empty-state');
  if (empty) host.innerHTML = '';
  const atBottom = host.scrollHeight - host.scrollTop - host.clientHeight < 60;
  host.insertAdjacentHTML('beforeend', items.map(feedHtml).join(''));
  while (host.children.length > 60) host.firstElementChild.remove();
  if (atBottom) host.scrollTop = host.scrollHeight;
}

function renderRank(root, overview) {
  const list = (overview.agents || []).slice(0, 6);
  const max = Math.max(1, ...list.map((a) => a.total));
  const host = root.querySelector('#rankList');
  if (!list.length) {
    host.innerHTML = '<div class="empty-state" style="height:auto;padding:12px"><span>还没有信箱</span></div>';
    return;
  }
  host.innerHTML = list.map((a) => `
    <div class="rank-row" title="${esc(a.name)}">
      <span class="nm">${esc(a.display_name || a.name)}</span>
      <span class="bar" style="width:${Math.max(4, (a.total / max) * 96)}px"></span>
      <span class="vl">${a.total}</span>
    </div>`).join('');
}

/* ── 分层布局：宽屏横向、窄屏纵向 ── */
function layerize(nodes, edges) {
  const have = new Set();
  const dir = [];
  for (const e of edges) {
    const rev = `${e.to}|${e.from}`;
    if (have.has(rev)) continue;
    have.add(`${e.from}|${e.to}`);
    dir.push(e);
  }

  const layer = new Map(nodes.map((n) => [n.name, 0]));
  const indeg = new Map(nodes.map((n) => [n.name, 0]));
  const adj = new Map();
  for (const e of dir) {
    if (indeg.has(e.to)) indeg.set(e.to, indeg.get(e.to) + 1);
    if (!adj.has(e.from)) adj.set(e.from, []);
    adj.get(e.from).push(e.to);
  }

  let roots = nodes.filter((n) => indeg.get(n.name) === 0).map((n) => n.name);
  if (!roots.length && nodes.length) roots = [nodes[0].name];

  const queue = [...roots];
  let guard = 0;
  while (queue.length && guard < 800) {
    guard += 1;
    const cur = queue.shift();
    for (const next of adj.get(cur) || []) {
      const want = Math.min((layer.get(cur) || 0) + 1, 5);
      if (want > (layer.get(next) || 0)) {
        layer.set(next, want);
        queue.push(next);
      }
    }
  }

  const cols = new Map();
  for (const n of nodes) {
    const lv = layer.get(n.name) || 0;
    if (!cols.has(lv)) cols.set(lv, []);
    cols.get(lv).push(n);
  }
  const levels = [...cols.keys()].sort((a, b) => a - b);
  for (const lv of levels) cols.get(lv).sort((a, b) => (b.volume || 0) - (a.volume || 0));
  return { cols, levels };
}

function buildLayout(nodes, edges, W, H, vertical) {
  const pos = new Map();
  if (!nodes.length) return { pos, contentH: H };

  const { cols, levels } = layerize(nodes, edges);

  if (!vertical) {
    const padL = 54;
    const padR = 54;
    const padT = 36;
    const padB = 30;
    const usableW = Math.max(W - padL - padR - NW, 0);
    const colGap = levels.length > 1 ? usableW / (levels.length - 1) : 0;
    const usableH = Math.max(H - padT - padB - NH, 40);

    levels.forEach((lv, i) => {
      const list = cols.get(lv);
      const x = levels.length > 1 ? padL + i * colGap : (W - NW) / 2;
      const gap = list.length > 1 ? Math.min(usableH / (list.length - 1), 132) : 0;
      const total = gap * (list.length - 1);
      const startY = padT + (usableH - total) / 2;
      list.forEach((n, j) => pos.set(n.name, { x, y: startY + j * gap }));
    });
    return { pos, contentH: H };
  }

  const padX = 12;
  const padT = 14;
  const usableW = Math.max(W - padX * 2, NW);
  const perRow = Math.max(1, Math.floor(usableW / (NW + 10)));
  let y = padT;

  for (const lv of levels) {
    const list = cols.get(lv);
    for (let i = 0; i < list.length; i += perRow) {
      const chunk = list.slice(i, i + perRow);
      const free = Math.max(usableW - chunk.length * NW, 0);
      const gap = chunk.length > 1 ? free / (chunk.length + 1) : 0;
      chunk.forEach((n, j) => {
        pos.set(n.name, {
          x: padX + (chunk.length > 1 ? gap * (j + 1) + NW * j : (usableW - NW) / 2),
          y,
        });
      });
      y += NH + 26;
    }
    y += 12;
  }
  return { pos, contentH: y + 10 };
}

function edgeColor(edge) {
  if (edge.confidence >= 1) return '#2f7ff0';
  if (edge.confidence >= 0.5) return '#7aa7e8';
  return '#b9b9b3';
}

function spreadOffset(j, n) {
  if (n <= 1) return 0;
  const step = Math.min(13, 46 / (n - 1));
  return (j - (n - 1) / 2) * step;
}

function drawGraph(host, data, ctx) {
  const W = Math.max(host.clientWidth || 560, 280);
  const H = Math.max(host.clientHeight || 300, 200);
  const vertical = W < NARROW;

  if (!data.nodes.length) {
    host.innerHTML = `<div class="empty-state"><b>还没有可画的链路</b>
      <span>信箱有消息往来后，这里会自动出现拓扑</span></div>`;
    return;
  }

  const { pos, contentH } = buildLayout(data.nodes, data.edges, W, H, vertical);
  const viewH = Math.max(H, contentH);

  const neighbors = new Map();
  for (const e of data.edges) {
    if (!neighbors.has(e.from)) neighbors.set(e.from, new Set());
    if (!neighbors.has(e.to)) neighbors.set(e.to, new Set());
    neighbors.get(e.from).add(e.to);
    neighbors.get(e.to).add(e.from);
  }

  const seenPair = new Map();
  const renderEdges = [];
  for (const e of data.edges) {
    const key = [e.from, e.to].sort().join('\u0000');
    const prev = seenPair.get(key);
    if (prev) {
      prev.bidir = true;
      prev.count += e.count;
      prev.inferred = prev.inferred && e.inferred;
      continue;
    }
    const item = { ...e, bidir: false };
    seenPair.set(key, item);
    renderEdges.push(item);
  }

  const arriveGroups = new Map();
  const leaveGroups = new Map();
  renderEdges.forEach((e, i) => {
    if (!arriveGroups.has(e.to)) arriveGroups.set(e.to, []);
    arriveGroups.get(e.to).push(i);
    if (!leaveGroups.has(e.from)) leaveGroups.set(e.from, []);
    leaveGroups.get(e.from).push(i);
  });
  const arriveOff = new Map();
  const leaveOff = new Map();
  const sortKey = vertical ? (n) => pos.get(n)?.x || 0 : (n) => pos.get(n)?.y || 0;
  for (const list of arriveGroups.values()) {
    list.sort((i, j) => sortKey(renderEdges[i].from) - sortKey(renderEdges[j].from));
    list.forEach((idx, j) => arriveOff.set(idx, spreadOffset(j, list.length)));
  }
  for (const list of leaveGroups.values()) {
    list.sort((i, j) => sortKey(renderEdges[i].to) - sortKey(renderEdges[j].to));
    list.forEach((idx, j) => leaveOff.set(idx, spreadOffset(j, list.length)));
  }

  const parts = [];
  parts.push(`<svg viewBox="0 0 ${W} ${viewH}" preserveAspectRatio="xMidYMid meet" role="img"
    ${viewH > H ? `style="height:${viewH}px"` : ''}>
    <defs>
      <marker id="garrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
      </marker>
    </defs>`);

  renderEdges.forEach((e, idx) => {
    const a = pos.get(e.from);
    const b = pos.get(e.to);
    if (!a || !b) return;

    let d = '';
    let mid;
    if (vertical) {
      if (Math.abs(a.y - b.y) < 2) {
        const leftSide = b.x >= a.x;
        const x1 = a.x + (leftSide ? NW : 0);
        const x2 = b.x + (leftSide ? NW : 0);
        const y1 = a.y + NH / 2;
        const y2 = b.y + NH / 2;
        const c1 = [x1 + (leftSide ? 46 : -46), y1];
        const c2 = [x2 + (leftSide ? 46 : -46), y2];
        d = `M${x1} ${y1} C${c1[0]} ${c1[1]} ${c2[0]} ${c2[1]} ${x2} ${y2}`;
        mid = [(x1 + 3 * c1[0] + 3 * c2[0] + x2) / 8, (y1 + 3 * c1[1] + 3 * c2[1] + y2) / 8];
      } else {
        const down = b.y > a.y;
        const x1 = a.x + NW / 2 + (leaveOff.get(idx) || 0);
        const x2 = b.x + NW / 2 + (arriveOff.get(idx) || 0);
        const y1 = a.y + (down ? NH : 0);
        const y2 = b.y + (down ? 0 : NH);
        const dy = Math.max(22, Math.abs(y2 - y1) / 2) * (down ? 1 : -1);
        const c1 = [x1, y1 + dy];
        const c2 = [x2, y2 - dy];
        d = `M${x1} ${y1} C${c1[0]} ${c1[1]} ${c2[0]} ${c2[1]} ${x2} ${y2}`;
        mid = [(x1 + 3 * c1[0] + 3 * c2[0] + x2) / 8, (y1 + 3 * c1[1] + 3 * c2[1] + y2) / 8];
      }
    } else if (a.x === b.x) {
      const x = a.x + NW;
      const ay = a.y + NH / 2 + (leaveOff.get(idx) || 0);
      const by = b.y + NH / 2 + (arriveOff.get(idx) || 0);
      const c1 = [x + 62, ay];
      const c2 = [x + 62, by];
      d = `M${x} ${ay} C${c1[0]} ${c1[1]} ${c2[0]} ${c2[1]} ${x} ${by}`;
      mid = [(x + 3 * c1[0] + 3 * c2[0] + x) / 8, (ay + 3 * c1[1] + 3 * c2[1] + by) / 8];
    } else if (b.x > a.x) {
      const x1 = a.x + NW;
      const x2 = b.x - 7;
      const ay = a.y + NH / 2 + (leaveOff.get(idx) || 0);
      const by = b.y + NH / 2 + (arriveOff.get(idx) || 0);
      const dx = Math.max(34, (x2 - x1) / 2);
      const c1 = [x1 + dx, ay];
      const c2 = [x2 - dx, by];
      d = `M${x1} ${ay} C${c1[0]} ${c1[1]} ${c2[0]} ${c2[1]} ${x2} ${by}`;
      mid = [(x1 + 3 * c1[0] + 3 * c2[0] + x2) / 8, (ay + 3 * c1[1] + 3 * c2[1] + by) / 8];
    } else {
      const x1 = a.x + NW / 2 + (leaveOff.get(idx) || 0);
      const x2 = b.x + NW / 2 + (arriveOff.get(idx) || 0);
      const lift = Math.min(a.y, b.y) > 74 ? 54 : -54;
      const y1 = a.y + (lift > 0 ? 0 : NH);
      const y2 = b.y + (lift > 0 ? 0 : NH);
      const c1 = [x1, a.y + lift];
      const c2 = [x2, b.y + lift];
      d = `M${x1} ${y1} C${c1[0]} ${c1[1]} ${c2[0]} ${c2[1]} ${x2} ${y2}`;
      mid = [(x1 + 3 * c1[0] + 3 * c2[0] + x2) / 8, (y1 + 3 * c1[1] + 3 * c2[1] + y2) / 8];
    }

    const width = 1 + Math.min(e.count, 9) * 0.42;
    const dash = e.inferred ? ' stroke-dasharray="5 4"' : '';
    const markers = e.bidir
      ? ' marker-start="url(#garrow)" marker-end="url(#garrow)"'
      : ' marker-end="url(#garrow)"';
    parts.push(`<path class="gedge" data-from="${esc(e.from)}" data-to="${esc(e.to)}"
      d="${d}" fill="none" stroke="${edgeColor(e)}" stroke-width="${width.toFixed(2)}"${dash}${markers}
      opacity="${e.inferred ? 0.85 : 1}"/>`);

    if (e.count > 1) {
      parts.push(`<text x="${mid[0].toFixed(1)}" y="${mid[1].toFixed(1)}" text-anchor="middle"
        font-size="10" fill="#8f8f8b" stroke="#fff" stroke-width="3" paint-order="stroke"
        font-family="ui-monospace,Menlo,monospace">${e.count}${e.bidir ? ' ⇄' : ''}</text>`);
    }
  });

  for (const n of data.nodes) {
    const p = pos.get(n.name);
    if (!p) continue;
    const virtual = n.virtual;
    const label = (n.display_name || n.name);
    let shown = label;
    if (label.length > (vertical ? 7 : 6)) shown = `${label.slice(0, vertical ? 7 : 6).trimEnd()}…`;
    const fill = virtual ? '#f6f5f4' : '#ffffff';
    const stroke = virtual ? '#d8d8d3' : '#e2e2df';
    const clipId = `clip-${Math.random().toString(36).slice(2, 8)}`;
    const av = n.avatar || defaultAvatar(n.name);
    const cx = p.x + 24;
    const cy = p.y + NH / 2;

    parts.push(`<g class="gnode${virtual ? ' virtual' : ''}" data-name="${esc(n.name)}">
      <rect class="box" x="${p.x}" y="${p.y}" width="${NW}" height="${NH}" rx="10"
        fill="${fill}" stroke="${stroke}" stroke-width="1"/>`);

    if (av) {
      parts.push(`<clipPath id="${clipId}"><circle cx="${cx}" cy="${cy}" r="13"/></clipPath>
        <image href="${esc(av)}" x="${cx - 13}" y="${cy - 13}" width="26" height="26"
          clip-path="url(#${clipId})" preserveAspectRatio="xMidYMid slice"/>`);
    } else {
      const bg = virtual ? '#ececea' : '#e8f1fe';
      const fg = virtual ? '#5c5c5a' : '#1b5cc0';
      parts.push(`<circle cx="${cx}" cy="${cy}" r="13" fill="${bg}"/>
        <text x="${cx}" y="${cy + 4}" text-anchor="middle" font-size="10.5"
          font-weight="600" fill="${fg}">${esc(initial(label))}</text>`);
    }

    const dotColor = { online: '#1d9a57', active: '#2f7ff0', idle: '#e89a0c', silent: '#c9c9c4', empty: '#dcdcd7' }[n.status] || '#dcdcd7';
    parts.push(`<circle cx="${cx + 10}" cy="${cy - 10}" r="3.6" fill="${dotColor}" stroke="#fff" stroke-width="1.4"/>`);

    parts.push(`<text x="${p.x + 44}" y="${p.y + 19}" font-size="12" font-weight="600" fill="#171717">${esc(shown)}</text>
      <text x="${p.x + 44}" y="${p.y + 33}" font-size="10.5" fill="#9a9a97"
        font-family="ui-monospace,Menlo,monospace">${n.inbound}/${n.outbound}</text>
      <title>${esc(label)} · 收 ${n.inbound} 发 ${n.outbound} · ${statusText(n.status)}</title>
    </g>`);
  }

  parts.push('</svg>');
  host.innerHTML = parts.join('');

  host.querySelectorAll('.gnode').forEach((g) => {
    const name = g.dataset.name;
    const related = neighbors.get(name) || new Set();

    g.addEventListener('mouseenter', () => {
      host.querySelectorAll('.gedge').forEach((edge) => {
        const on = edge.dataset.from === name || edge.dataset.to === name;
        edge.style.opacity = on ? '1' : '0.07';
      });
      host.querySelectorAll('.gnode').forEach((other) => {
        const otherName = other.dataset.name;
        other.classList.toggle('dim', otherName !== name && !related.has(otherName));
      });
    });

    g.addEventListener('mouseleave', () => {
      host.querySelectorAll('.gedge').forEach((edge) => { edge.style.opacity = ''; });
      host.querySelectorAll('.gnode').forEach((other) => other.classList.remove('dim'));
    });

    if (!g.classList.contains('virtual') && ctx) {
      g.addEventListener('click', () => ctx.go('chat', { agent: name }));
      g.style.cursor = 'pointer';
    }
  });
}
