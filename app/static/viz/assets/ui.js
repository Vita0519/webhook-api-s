/* 通用工具：图标、格式化、弹层、头像 */

export const ICONS = {
  overview: '<path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"/>',
  chat: '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9.9 9.9 0 0 1-4-.8L3 21l1.9-4.6A8.2 8.2 0 0 1 4 11.5 8.4 8.4 0 0 1 12.5 3 8.4 8.4 0 0 1 21 11.5z"/>',
  office: '<path d="M3 21h18M5 21V7l7-4 7 4v14M9 21v-5h6v5"/>',
  agents: '<path d="M16 20v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 10a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM22 20v-2a4 4 0 0 0-3-3.9M16 2.1a4 4 0 0 1 0 7.8"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 9 19.4a1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.6 9a1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
  menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
  send: '<path d="M22 2 11 13M22 2l-7 20-4-9-9-4z"/>',
  paperclip: '<path d="M21.4 11.05l-9.19 9.19a5 5 0 0 1-7.07-7.07l9.19-9.19a3.5 3.5 0 1 1 4.95 4.95l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
  refresh: '<path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  camera: '<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>',
  caret: '<path d="m6 9 6 6 6-6"/>',
  warn: '<path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
};

export function icon(name, size = 16) {
  const path = ICONS[name] || '';
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor"
    stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;
}

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export function esc(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ESC[c]);
}

export function el(html) {
  const tpl = document.createElement('template');
  tpl.innerHTML = html.trim();
  return tpl.content.firstElementChild;
}

export function num(n) {
  if (n == null || Number.isNaN(n)) return '0';
  if (n >= 10000) return `${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k`;
  return String(Math.round(n));
}

export function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export function fmtDay(ts) {
  const d = new Date(ts * 1000);
  const now = new Date();
  const same = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (same(d, now)) return '今天';
  const y = new Date(now.getTime() - 86400000);
  if (same(d, y)) return '昨天';
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

export function fmtDateTime(ts) {
  if (!ts) return '—';
  return `${fmtDay(ts)} ${fmtTime(ts)}`;
}

export function rel(ts) {
  if (!ts) return '从未';
  const gap = Date.now() / 1000 - ts;
  if (gap < 60) return '刚刚';
  if (gap < 3600) return `${Math.floor(gap / 60)} 分钟前`;
  if (gap < 86400) return `${Math.floor(gap / 3600)} 小时前`;
  if (gap < 86400 * 30) return `${Math.floor(gap / 86400)} 天前`;
  return fmtDateTime(ts).split(' ')[0];
}

export function statusText(status) {
  return ({ online: '在线', active: '活跃', idle: '空闲', silent: '静默', empty: '无消息' })[status] || status;
}

const DEFAULT_AVATARS = [
  [/^(boss|老板)/i, 'avatar-boss.webp'],
  [/^console|控制台/i, 'avatar-console.webp'],
  [/^ford/i, 'avatar-ford.webp'],
  [/^muse/i, 'avatar-muse.png'],
  [/^qwen/i, 'avatar-qwenpaw.webp'],
  [/^vita/i, 'avatar-vita.webp'],
];

export function defaultAvatar(name) {
  for (const [re, file] of DEFAULT_AVATARS) {
    if (re.test(name || '')) return `agents/${file}`;
  }
  return '';
}

export function avatarHtml(agent, cls = 'av-lg') {
  const name = agent?.display_name || agent?.name || '?';
  const src = agent?.avatar || defaultAvatar(agent?.name);
  if (src) {
    return `<img class="${cls}" src="${esc(src)}" alt="${esc(name)}" loading="lazy"
      onerror="this.style.display='none';this.nextElementSibling&&(this.nextElementSibling.style.display='grid')">
      <div class="av-fallback" style="display:none">${esc(initial(name))}</div>`;
  }
  return `<div class="av-fallback">${esc(initial(name))}</div>`;
}

export function initial(name) {
  const s = String(name || '?').trim();
  if (!s) return '?';
  if (/[\u4e00-\u9fa5]/.test(s[0])) return s.slice(0, 1);
  return s.slice(0, 2).toUpperCase();
}

let toastTimer = 0;
export function toast(message, type = 'ok') {
  const wrap = document.getElementById('toast');
  const node = el(`<div class="toast ${type === 'err' ? 'err' : ''}">${esc(message)}</div>`);
  wrap.appendChild(node);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.remove(), 2600);
}

export function modal({ title, body = '', okText = '确定', cancelText = '取消', danger = false, onOk }) {
  const wrap = document.getElementById('modal');
  wrap.hidden = false;
  wrap.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true">
      <h3>${esc(title)}</h3>
      <div class="body">${body}</div>
      <div class="foot">
        <button class="btn ghost" data-cancel>${esc(cancelText)}</button>
        <button class="btn ${danger ? 'danger' : ''}" data-ok>${esc(okText)}</button>
      </div>
    </div>`;
  const close = () => { wrap.hidden = true; wrap.innerHTML = ''; };
  wrap.querySelector('[data-cancel]').onclick = close;
  wrap.onclick = (e) => { if (e.target === wrap) close(); };
  wrap.querySelector('[data-ok]').onclick = async () => {
    const btn = wrap.querySelector('[data-ok]');
    btn.disabled = true;
    try {
      const keep = await onOk?.(wrap);
      if (keep !== false) close();
    } finally {
      btn.disabled = false;
    }
  };
  return close;
}

export function promptModal({ title, label, value = '', placeholder = '', okText = '保存', onOk }) {
  return modal({
    title,
    okText,
    body: `<label class="field"><span>${esc(label)}</span>
      <input type="text" id="pm-input" value="${esc(value)}" placeholder="${esc(placeholder)}">
    </label>`,
    onOk: (wrap) => onOk(wrap.querySelector('#pm-input').value.trim()),
  });
}

export function debounce(fn, ms = 250) {
  let t = 0;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

export function elVal(root, sel) {
  const node = root.querySelector(sel);
  return node ? node.value : '';
}
