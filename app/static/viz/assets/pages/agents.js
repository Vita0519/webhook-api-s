import { api, store } from '../api.js';
import { esc, icon, initial, defaultAvatar, statusText, rel, toast, modal, debounce } from '../ui.js';

let filter = '';
let agentsRef = [];

const T = {
  fileInput: null,
  uploading: '',
  reload: () => {},
  setUploading(name) { this.uploading = name; },
};

export async function mount(root, ctx) {
  filter = '';
  const canEdit = ctx.user?.role === 'admin';
  const canUpload = ['admin', 'operator'].includes(ctx.user?.role);

  root.innerHTML = `
    <div class="pg-agents">
      <div class="panel-head" style="padding:0 0 12px;border:0;background:transparent">
        <h2 style="font-size:14px">自动检测到的信箱</h2>
        <span class="chip" id="cnt">—</span>
        <span class="spacer"></span>
        <input type="search" id="search" placeholder="搜索信箱…" style="width:170px" autocomplete="off">
        ${canEdit ? `<button class="btn ghost sm" id="addBtn">${icon('plus', 13)}新增标注</button>` : ''}
        <button class="btn ghost sm" id="refreshBtn">${icon('refresh', 13)}刷新</button>
      </div>
      <p class="muted" style="font-size:12px;margin:0 0 12px;line-height:1.7">
        信箱不靠手工登记：只要 <code>messages</code> 表里出现过名字，就会在这里自动出现。
        形象、显示名与角色属于「标注」，存在独立的 <code>console_agents</code> 表里，不影响消息数据。
        ${canUpload ? '' : '当前角色只读，无法上传形象。'}
      </p>
      <div class="grid-agents" id="grid"></div>
    </div>`;

  const grid = root.querySelector('#grid');
  const fileInput = document.createElement('input');
  fileInput.type = 'file';
  fileInput.accept = 'image/png,image/jpeg,image/webp,image/gif';
  fileInput.hidden = true;
  root.appendChild(fileInput);
  T.fileInput = fileInput;
  T.reload = () => load(grid, root, ctx);

  fileInput.onchange = async () => {
    const file = fileInput.files?.[0];
    const name = T.uploading;
    if (!file || !name) return;
    try {
      await api.uploadAvatar(name, file);
      toast('形象已更新');
      T.reload();
    } catch (err) {
      toast(err.message, 'err');
    } finally {
      fileInput.value = '';
      T.uploading = '';
    }
  };

  root.querySelector('#search').oninput = (e) => {
    filter = e.target.value.trim().toLowerCase();
    renderGrid(grid, root, ctx);
  };
  root.querySelector('#refreshBtn').onclick = () => T.reload();
  if (canEdit) root.querySelector('#addBtn').onclick = () => addAgent(ctx);

  await T.reload();
  return null;
}

async function load(grid, root, ctx) {
  try {
    const data = await api.agents();
    agentsRef = data.agents || [];
    store.setAgents(agentsRef);
    root.querySelector('#cnt').textContent = `${agentsRef.length} 个信箱`;
    renderGrid(grid, root, ctx);
  } catch (err) {
    grid.innerHTML = `<div class="empty-state"><b>加载失败</b><span>${esc(err.message)}</span></div>`;
  }
}

function renderGrid(grid, root, ctx) {
  const canEdit = ctx.user?.role === 'admin';
  const canUpload = ['admin', 'operator'].includes(ctx.user?.role);
  let list = agentsRef;
  if (filter) list = list.filter((a) => `${a.name} ${a.display_name}`.toLowerCase().includes(filter));

  if (!list.length) {
    grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1"><b>还没有信箱</b>
      <span>任意 agent 往 /webhook/inbound/{name} 推一条消息，这里就会自动出现</span></div>`;
    return;
  }

  grid.innerHTML = list.map((a) => {
    const av = a.avatar || defaultAvatar(a.name);
    const label = a.display_name || a.name;
    return `<div class="agent-card" data-name="${esc(a.name)}">
      <div class="av-wrap">
        ${av
          ? `<img class="av-lg" src="${esc(av)}" alt="${esc(label)}"
               onerror="this.style.display='none';this.nextElementSibling.style.display='grid'">
             <div class="av-fallback" style="display:none">${esc(initial(label))}</div>`
          : `<div class="av-fallback">${esc(initial(label))}</div>`}
        ${canUpload ? `<button class="av-edit" data-act="upload" title="上传形象">${icon('camera', 11)}</button>` : ''}
      </div>
      <div class="info">
        <div class="nm">${esc(label)}
          ${a.role ? `<span class="chip" style="font-size:10px;padding:0 6px">${esc(a.role)}</span>` : ''}
        </div>
        <div class="raw">${esc(a.name)}</div>
        <div class="stats">
          <span>收 <b>${a.inbound}</b></span>
          <span>发 <b>${a.outbound}</b></span>
          <span>共 <b>${a.total}</b></span>
        </div>
        <div class="stats" style="margin-top:4px;color:var(--ink3)">
          <span class="dot ${esc(a.status)}"></span>
          <span>${statusText(a.status)} · ${rel(a.last_ts)}</span>
        </div>
      </div>
      <div class="agent-actions">
        <button class="iconbtn" data-act="chat" title="打开通信台">${icon('chat', 13)}</button>
        ${canEdit ? `<button class="iconbtn" data-act="edit" title="编辑资料">${icon('settings', 13)}</button>` : ''}
      </div>
    </div>`;
  }).join('');

  grid.querySelectorAll('.agent-card').forEach((card) => {
    const name = card.dataset.name;
    const agent = agentsRef.find((a) => a.name === name);
    card.querySelectorAll('[data-act]').forEach((btn) => {
      btn.onclick = (event) => {
        event.stopPropagation();
        const act = btn.dataset.act;
        if (act === 'upload') {
          T.setUploading(name);
          T.fileInput.click();
        } else if (act === 'chat') {
          ctx.go('chat', { agent: name });
        } else if (act === 'edit') {
          editAgent(agent);
        }
      };
    });
  });
}

function editAgent(agent) {
  modal({
    title: `编辑「${agent.display_name || agent.name}」`,
    okText: '保存',
    body: `
      <label class="field"><span>显示名</span>
        <input type="text" id="f-name" value="${esc(agent.display_name === agent.name ? '' : agent.display_name)}" placeholder="${esc(agent.name)}">
        <small>留空则直接用原始名字 <code>${esc(agent.name)}</code></small>
      </label>
      <label class="field"><span>角色标签</span>
        <input type="text" id="f-role" value="${esc(agent.role || '')}" placeholder="例如：发送方 / 应答方 / 只观察">
      </label>
      <label class="field"><span>备注</span>
        <textarea id="f-note" rows="3" placeholder="谁能给它发消息、要不要回复…">${esc(agent.note || '')}</textarea>
      </label>
      ${agent.has_custom_avatar ? '<button class="btn ghost sm" id="f-clear" type="button">恢复默认形象</button>' : ''}`,
    onOk: async (wrap) => {
      try {
        await api.patchAgent(agent.name, {
          display_name: wrap.querySelector('#f-name').value.trim(),
          role: wrap.querySelector('#f-role').value.trim(),
          note: wrap.querySelector('#f-note').value.trim(),
        });
        toast('已保存');
        T.reload();
        return true;
      } catch (err) {
        toast(err.message, 'err');
        return false;
      }
    },
  });

  const clear = document.getElementById('f-clear');
  if (clear) {
    clear.onclick = async () => {
      try {
        await api.deleteAvatar(agent.name);
        toast('已恢复默认形象');
        document.getElementById('modal').hidden = true;
        document.getElementById('modal').innerHTML = '';
        T.reload();
      } catch (err) {
        toast(err.message, 'err');
      }
    };
  }
}

function addAgent(ctx) {
  modal({
    title: '新增信箱标注',
    okText: '创建',
    body: `<p style="margin:0 0 12px;font-size:12.5px;color:var(--ink2)">
        用于给还没来过消息的信箱提前配好形象和角色。名字要和对方推送时用的 <code>name</code> 完全一致。</p>
      <label class="field"><span>信箱名</span><input type="text" id="a-name" placeholder="例如 muse3"></label>`,
    onOk: async (wrap) => {
      const name = wrap.querySelector('#a-name').value.trim();
      if (!name) { toast('请填写信箱名', 'err'); return false; }
      try {
        await api.patchAgent(name, { display_name: '' });
        toast('已创建，可以上传形象了');
        T.reload();
        return true;
      } catch (err) {
        toast(err.message, 'err');
        return false;
      }
    },
  });
}
