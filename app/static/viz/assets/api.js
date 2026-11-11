/* 接口封装 + 全局状态 + SSE */

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

let unauthorizedHandler = null;
export function onUnauthorized(fn) { unauthorizedHandler = fn; }

async function request(path, { method = 'GET', body, form } = {}) {
  const opts = { method, credentials: 'same-origin', headers: {} };
  if (form) {
    opts.body = form;
  } else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (err) {
    throw new ApiError('网络不可达，请检查服务是否在线', 0);
  }
  if (res.status === 401) {
    unauthorizedHandler?.();
    throw new ApiError('未登录或会话已过期', 401);
  }
  const type = res.headers.get('content-type') || '';
  const data = type.includes('application/json') ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = data && data.detail;
    throw new ApiError(typeof detail === 'string' ? detail : `请求失败（${res.status}）`, res.status);
  }
  return data;
}

const qs = (params) => {
  const usable = Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== null && v !== '');
  return usable.length ? `?${new URLSearchParams(usable)}` : '';
};

export const api = {
  me: () => request('/api/console/me'),
  login: (username, password) => request('/api/console/login', { method: 'POST', body: { username, password } }),
  logout: () => request('/api/console/logout', { method: 'POST' }),
  changePassword: (oldPw, newPw) => request('/api/console/password', { method: 'POST', body: { old: oldPw, new: newPw } }),

  overview: (window) => request(`/api/console/overview${qs({ window })}`),
  agents: () => request('/api/console/agents'),
  patchAgent: (name, body) => request(`/api/console/agents/${encodeURIComponent(name)}`, { method: 'PATCH', body }),
  deleteAgent: (name) => request(`/api/console/agents/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  restoreAgent: (name) => request(`/api/console/agents/${encodeURIComponent(name)}/restore`, { method: 'POST' }),
  uploadAvatar: (name, file) => {
    const form = new FormData();
    form.append('file', file);
    return request(`/api/console/agents/${encodeURIComponent(name)}/avatar`, { method: 'POST', form });
  },
  deleteAvatar: (name) => request(`/api/console/agents/${encodeURIComponent(name)}/avatar`, { method: 'DELETE' }),

  lineage: (window, includeInferred) => request(`/api/console/lineage${qs({ window, include_inferred: includeInferred })}`),
  events: (limit) => request(`/api/console/events${qs({ limit })}`),
  messages: (name, params) => request(`/api/console/messages${qs({ name, ...params })}`),
  send: (payload) => request('/api/console/send', { method: 'POST', body: payload }),
  upload: (file) => {
    const form = new FormData();
    form.append('file', file);
    return request('/api/console/upload', { method: 'POST', form });
  },

  settings: () => request('/api/console/settings'),
  saveSettings: (patch) => request('/api/console/settings', { method: 'PUT', body: patch }),

  users: () => request('/api/console/users'),
  createUser: (body) => request('/api/console/users', { method: 'POST', body }),
  deleteUser: (id) => request(`/api/console/users/${encodeURIComponent(id)}`, { method: 'DELETE' }),
};

/* ── 全局状态 ── */
export const store = {
  user: null,
  settings: {},
  agents: [],
  routes: [],
  _listeners: new Map(),

  on(event, fn) {
    if (!this._listeners.has(event)) this._listeners.set(event, new Set());
    this._listeners.get(event).add(fn);
    return () => this._listeners.get(event)?.delete(fn);
  },
  emit(event, payload) {
    this._listeners.get(event)?.forEach((fn) => {
      try { fn(payload); } catch (err) { console.error(`[${event}]`, err); }
    });
  },
  agent(name) {
    return this.agents.find((a) => a.name === name);
  },
  setAgents(list) {
    this.agents = list || [];
    this.emit('agents', this.agents);
  },
};

/* ── SSE ── */
let source = null;
let errorCount = 0;

export function startStream() {
  stopStream();
  errorCount = 0;
  const since = Math.floor(Date.now() / 1000);
  source = new EventSource(`/api/console/stream?since=${since}`);

  source.addEventListener('messages', (ev) => {
    errorCount = 0;
    try {
      const items = JSON.parse(ev.data);
      if (Array.isArray(items) && items.length) store.emit('messages', items);
    } catch { /* 忽略坏包 */ }
  });

  source.addEventListener('tick', () => { errorCount = 0; });

  source.onerror = async () => {
    errorCount += 1;
    store.emit('stream-state', 'reconnecting');
    if (errorCount === 4) {
      try {
        const data = await api.me();
        store.user = data.user;
        store.settings = data.settings;
      } catch {
        stopStream();
        unauthorizedHandler?.();
      }
    }
  };

  source.onopen = () => {
    errorCount = 0;
    store.emit('stream-state', 'open');
  };
}

export function stopStream() {
  if (source) {
    source.close();
    source = null;
  }
}
