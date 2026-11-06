import { api } from '../api.js';
import { esc } from '../ui.js';

export async function mount(root) {
  root.innerHTML = `
    <div class="pg-login">
      <form class="login-card" id="loginForm">
        <div class="logo">Webhook 中继台</div>
        <div class="sub">登录以管理工作台 · 账号由管理员分配</div>
        <div id="loginErr" class="login-err" hidden></div>
        <label class="field"><span>用户名</span>
          <input type="text" id="username" autocomplete="username" placeholder="admin" required>
        </label>
        <label class="field"><span>密码</span>
          <input type="password" id="password" autocomplete="current-password" placeholder="首次登录请填 WEBHOOK_API_KEY" required>
        </label>
        <button class="btn" type="submit" id="loginBtn">登录</button>
        <p class="muted" style="font-size:11.5px;margin:16px 0 0;line-height:1.7">
          首次启动会自动创建 <b>admin</b> 账号，密码与你配置的 <code>WEBHOOK_API_KEY</code> 相同，
          登录后会要求立即设置新密码。角色分为管理员 / 操作员 / 只读。
        </p>
      </form>
    </div>`;

  const form = root.querySelector('#loginForm');
  const errBox = root.querySelector('#loginErr');
  const btn = root.querySelector('#loginBtn');

  form.onsubmit = async (event) => {
    event.preventDefault();
    errBox.hidden = true;
    btn.disabled = true;
    btn.textContent = '登录中…';
    try {
      await api.login(root.querySelector('#username').value.trim(), root.querySelector('#password').value);
      location.hash = '#/overview';
      location.reload();
    } catch (err) {
      errBox.hidden = false;
      errBox.textContent = err.message || '登录失败';
      btn.disabled = false;
      btn.textContent = '登录';
    }
  };

  root.querySelector('#username').focus();
  return null;
}
