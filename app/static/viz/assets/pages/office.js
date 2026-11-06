import { api, store } from '../api.js';
import { esc, num, rel, statusText, defaultAvatar, initial, fmtTime, debounce } from '../ui.js';

let ctxRef = null;
let dataRef = null;

export async function mount(root, ctx) {
  ctxRef = ctx;

  root.innerHTML = `
    <div class="pg-office">
      <div class="panel-head" style="background:var(--surface)">
        <span class="chip" id="officeStat">加载中</span>
        <span class="spacer"></span>
        <span class="muted" style="font-size:11.5px">内环=活跃 · 外环=静默 · 点头像进通信台</span>
      </div>
      <div class="office-body" id="officeBody">
        <div class="empty-state"><span>正在布置工位…</span></div>
      </div>
      <div class="office-foot">
        <div class="office-tag">事件流</div>
        <div class="ticker-wrap"><div class="ticker-track" id="ticker"></div></div>
      </div>
    </div>`;

  const host = root.querySelector('#officeBody');

  const off = store.on('messages', debounce(() => load(root, host), 1500));
  const observer = new ResizeObserver(debounce(() => {
    if (dataRef) draw(host, dataRef);
  }, 160));
  observer.observe(host);

  await load(root, host);

  return () => {
    off();
    observer.disconnect();
  };
}

async function load(root, host) {
  try {
    const [agentsData, lineage, events, overview] = await Promise.all([
      api.agents(),
      api.lineage('24h', false),
      api.events(18),
      api.overview('24h'),
    ]);
    store.setAgents(agentsData.agents || []);
    dataRef = {
      agents: agentsData.agents || [],
      edges: lineage.edges || [],
      kpis: overview.kpis,
    };
    root.querySelector('#officeStat').textContent =
      `${dataRef.agents.length} 个信箱 · ${dataRef.kpis.online} 个在线 · ${dataRef.kpis.messages} 条 / 24h`;
    draw(host, dataRef);
    renderTicker(root.querySelector('#ticker'), events.events || []);
  } catch (err) {
    host.innerHTML = `<div class="empty-state"><b>办公室加载失败</b><span>${esc(err.message)}</span></div>`;
  }
}

function renderTicker(host, items) {
  if (!items.length) {
    host.textContent = '暂无事件';
    return;
  }
  const line = items.slice(-14).map((m) => {
    const arrow = m.direction === 'inbound' ? '→ 中继' : '中继 →';
    return `<span><b>${esc(m.name)}</b> ${arrow} <span class="muted">${fmtTime(m.timestamp)}</span> ${esc((m.content || `[${m.type}]`).slice(0, 34))}</span>`;
  }).join('');
  host.innerHTML = line + line;
}

function ringPositions(items, cx, cy, rx, ry, startAngle) {
  const out = [];
  const count = items.length;
  items.forEach((item, i) => {
    const angle = startAngle + (360 / count) * i;
    const rad = (angle * Math.PI) / 180;
    out.push({ item, x: cx + rx * Math.cos(rad), y: cy + ry * Math.sin(rad) });
  });
  return out;
}

const STATUS_COLOR = {
  online: '#1d9a57', active: '#2f7ff0', idle: '#e89a0c', silent: '#c9c9c4', empty: '#dcdcd7',
};
const AWAKE = ['online', 'active', 'idle'];

function draw(host, data) {
  const W = Math.max(host.clientWidth || 720, 280);
  const H = Math.max(host.clientHeight || 420, 260);
  const agents = data.agents;

  if (!agents.length) {
    host.innerHTML = `<div class="empty-state"><b>办公室还空着</b>
      <span>有信箱收到第一条消息后，工位会自动出现</span></div>`;
    return;
  }

  const narrow = W < 640;
  const coreR = narrow ? 34 : 46;
  const sr = narrow ? 21 : 30;

  const active = agents.filter((a) => AWAKE.includes(a.status));
  const dormant = agents.filter((a) => !AWAKE.includes(a.status));
  const total = active.length + dormant.length;

  const cx = W / 2;
  const cy = H / 2 + 2;
  const ringBase = Math.max(Math.min(W, H) / 2 - (narrow ? 58 : 80), 92);
  const rx = narrow
    ? ringBase
    : Math.min(W / 2 - 130, ringBase * 1.15);
  const ry = narrow ? Math.min(Math.max(H / 2 - 88, 120), rx * 1.5) : ringBase;

  const seats = [];
  const guides = [];
  const twoRings = !narrow && total > 10 && dormant.length > 0;

  if (twoRings) {
    seats.push(...ringPositions(active, cx, cy, rx * 0.62, ry * 0.62, -90));
    seats.push(...ringPositions(dormant, cx, cy, rx, ry, -90 + 180 / dormant.length));
    guides.push([rx * 0.62, ry * 0.62], [rx, ry]);
  } else {
    seats.push(...ringPositions([...active, ...dormant], cx, cy, rx, ry, -90));
    guides.push([rx, ry]);
  }

  const seatMap = new Map(seats.map((s) => [s.item.name, s]));
  const parts = [];

  parts.push(`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img">
    <title>中继办公室</title>
    <desc>中心是中继核，${total} 个工位按活跃度环绕排布，弧线表示真实消息链路。</desc>
    <defs>
      <marker id="oarrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5.5" markerHeight="5.5" orient="auto-start-reverse">
        <path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
      </marker>
    </defs>`);

  for (const [gx, gy] of guides) {
    parts.push(`<ellipse cx="${cx}" cy="${cy}" rx="${gx}" ry="${gy}" fill="none"
      stroke="#efefec" stroke-width="1" stroke-dasharray="3 6"/>`);
  }

  for (const seat of seats) {
    const dx = seat.x - cx;
    const dy = seat.y - cy;
    const dist = Math.hypot(dx, dy) || 1;
    const ux = dx / dist;
    const uy = dy / dist;
    const sx = cx + ux * (coreR + 4);
    const sy = cy + uy * (coreR + 4);
    const ex = seat.x - ux * (sr + 4);
    const ey = seat.y - uy * (sr + 4);
    parts.push(`<line x1="${sx.toFixed(1)}" y1="${sy.toFixed(1)}" x2="${ex.toFixed(1)}" y2="${ey.toFixed(1)}"
      stroke="#f0f0ed" stroke-width="1"/>`);
  }

  for (const edge of data.edges) {
    const a = seatMap.get(edge.from);
    const b = seatMap.get(edge.to);
    if (!a || !b) continue;
    const mx = (a.x + b.x) / 2;
    const my = (a.y + b.y) / 2;
    const qx = mx + (mx - cx) * 0.26;
    const qy = my + (my - cy) * 0.26;
    const strong = edge.confidence >= 1;
    const stroke = strong ? '#2f7ff0' : '#b9b9b3';
    const width = (1 + Math.min(edge.count, 8) * 0.35).toFixed(2);
    const dash = edge.inferred ? ' stroke-dasharray="4 4"' : '';
    parts.push(`<path d="M${a.x.toFixed(1)} ${a.y.toFixed(1)} Q${qx.toFixed(1)} ${qy.toFixed(1)} ${b.x.toFixed(1)} ${b.y.toFixed(1)}"
      fill="none" stroke="${stroke}" stroke-width="${width}"${dash} opacity=".8"
      marker-end="url(#oarrow)" data-from="${esc(edge.from)}" data-to="${esc(edge.to)}" class="oedge"/>`);
  }

  parts.push(`<circle cx="${cx}" cy="${cy}" r="${coreR}" fill="#185FA5" stroke="#0C447C" stroke-width="1"/>
    <text x="${cx}" y="${cy - 8}" text-anchor="middle" font-size="${narrow ? 11 : 12}"
      font-weight="600" fill="#fff">中继核</text>
    <text x="${cx}" y="${cy + 9}" text-anchor="middle" font-size="${narrow ? 12 : 13}"
      font-weight="600" fill="#fff" font-family="ui-monospace,Menlo,monospace">${num(data.kpis.messages)}</text>
    <text x="${cx}" y="${cy + 22}" text-anchor="middle" font-size="9.5" fill="#B5D4F4">24h 消息</text>`);

  seats.forEach((seat) => {
    const a = seat.item;
    const av = a.avatar || defaultAvatar(a.name);
    const label = a.display_name || a.name;
    const maxChars = narrow ? 9 : 8;
    const shown = label.length > maxChars ? `${label.slice(0, maxChars).trimEnd()}…` : label;
    const clipId = `seat-${Math.random().toString(36).slice(2, 8)}`;
    const asleep = !AWAKE.includes(a.status);

    parts.push(`<g class="seat" data-name="${esc(a.name)}">
      <circle cx="${seat.x}" cy="${seat.y}" r="${sr + 4}" fill="${asleep ? '#fafaf8' : '#fff'}"
        stroke="${asleep ? '#e6e6e1' : '#dbe7fb'}" stroke-width="${asleep ? 1 : 2}"/>`);

    if (av) {
      parts.push(`<clipPath id="${clipId}"><circle cx="${seat.x}" cy="${seat.y}" r="${sr}"/></clipPath>
        <image href="${esc(av)}" x="${seat.x - sr}" y="${seat.y - sr}"
          width="${sr * 2}" height="${sr * 2}" clip-path="url(#${clipId})"
          preserveAspectRatio="xMidYMid slice" opacity="${asleep ? 0.55 : 1}"/>`);
    } else {
      parts.push(`<circle cx="${seat.x}" cy="${seat.y}" r="${sr}" fill="${asleep ? '#f1f1ee' : '#e8f1fe'}"/>
        <text x="${seat.x}" y="${seat.y + 5}" text-anchor="middle" font-size="${narrow ? 12 : 14}"
          font-weight="600" fill="${asleep ? '#8b8b87' : '#1b5cc0'}">${esc(initial(label))}</text>`);
    }

    const color = STATUS_COLOR[a.status] || '#dcdcd7';
    parts.push(`<circle cx="${seat.x + sr * 0.72}" cy="${seat.y - sr * 0.72}" r="${narrow ? 3.6 : 4.5}"
      fill="${color}" stroke="#fff" stroke-width="1.6"/>`);

    parts.push(`<text x="${seat.x}" y="${seat.y + sr + 17}" text-anchor="middle" font-size="${narrow ? 11 : 11.5}"
      font-weight="600" fill="${asleep ? '#8b8b87' : '#171717'}">${esc(shown)}</text>
      <text x="${seat.x}" y="${seat.y + sr + 30}" text-anchor="middle" font-size="10"
        fill="#9a9a97" font-family="ui-monospace,Menlo,monospace">${a.inbound}/${a.outbound}</text>
      <title>${esc(label)} · 收 ${a.inbound} 发 ${a.outbound} · ${statusText(a.status)} · ${rel(a.last_ts)}</title>
    </g>`);
  });

  parts.push('</svg>');
  host.innerHTML = parts.join('');

  host.querySelectorAll('.seat').forEach((g) => {
    const name = g.dataset.name;
    g.addEventListener('mouseenter', () => {
      host.querySelectorAll('.seat').forEach((o) => o.classList.toggle('dim', o.dataset.name !== name));
      host.querySelectorAll('.oedge').forEach((e) => {
        e.style.opacity = (e.dataset.from === name || e.dataset.to === name) ? '1' : '.06';
      });
    });
    g.addEventListener('mouseleave', () => {
      host.querySelectorAll('.seat').forEach((o) => o.classList.remove('dim'));
      host.querySelectorAll('.oedge').forEach((e) => { e.style.opacity = ''; });
    });
    g.addEventListener('click', () => ctxRef?.go('chat', { agent: name }));
  });
}
