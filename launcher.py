"""小二启动器：本地网页，3 个按钮拉起 PowerShell 窗口执行命令。

用法：双击「启动器.bat」→ 本服务运行并自动打开浏览器 → 点按钮。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 8124
PS = "pwsh" if shutil.which("pwsh") else "powershell"

HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>小二 · 启动器</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font-family:"Microsoft YaHei",system-ui,sans-serif; background:radial-gradient(circle at 50% 0%, #1a2440 0%, #0b0f1c 60%); color:#e6edf3; min-height:100vh; display:flex; align-items:center; justify-content:center; }
  .wrap { width:min(560px, 92vw); }
  h1 { text-align:center; font-size:28px; margin:0 0 6px; }
  .tip { text-align:center; color:#8b98a9; font-size:13px; margin:0 0 28px; }
  .card { background:#121a2b; border:1px solid #26324a; border-radius:14px; padding:18px 20px; margin-bottom:16px; }
  .name { font-weight:600; margin-bottom:10px; }
  .name span { color:#5b8cff; margin-right:8px; }
  button { width:100%; padding:12px; font-size:15px; font-weight:600; border:0; border-radius:9px; cursor:pointer; background:#2563eb; color:#fff; transition:.15s; }
  button:hover { background:#1d4ed8; }
  button:active { transform:scale(.98); }
  button:disabled { opacity:.6; cursor:default; }
  .hint { font-size:12px; color:#7d8b9e; margin-top:10px; font-family:Consolas,monospace; }
  .order { text-align:center; color:#fbbf24; font-size:13px; margin-top:8px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>小二 · 启动器</h1>
  <p class="tip">点按钮会弹出 PowerShell 窗口执行命令；执行情况和报错都在那个窗口里，报错可直接复制给我。</p>

  <div class="card">
    <div class="name"><span>①</span>启动前端服务</div>
    <button onclick="launch('frontend', this)">启动前端</button>
    <div class="hint">命令：npm run dev（端口 5173）</div>
  </div>

  <div class="card">
    <div class="name"><span>②</span>启动后端服务</div>
    <button onclick="launch('backend', this)">启动后端</button>
    <div class="hint">命令：python run.py（端口 8123）</div>
  </div>

  <div class="card">
    <div class="name"><span>③</span>启动网页</div>
    <button onclick="openApp()">打开网页</button>
    <div class="hint">打开 http://localhost:5173</div>
  </div>

  <div class="card">
    <div class="name"><span>④</span>安装/更新前端依赖</div>
    <button onclick="launch('install-deps', this)">安装依赖</button>
    <div class="hint">命令：npm install（首次使用或新增库后需跑一次）</div>
  </div>

  <p class="order">首次使用：先 ④ 装依赖 → ① 前端 → ② 后端 → ③ 打开网页</p>
</div>
<script>
async function launch(s, btn) {
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '已拉起，看弹出的窗口…';
  try {
    const r = await fetch('/api/launch/' + s, {method:'POST'});
    const j = await r.json();
    btn.textContent = j.ok ? '已启动 ✓' : ('失败：' + j.msg);
  } catch(e) {
    btn.textContent = '请求失败：' + e;
  }
  setTimeout(() => { btn.disabled = false; btn.textContent = old; }, 3000);
}
function openApp() { window.open('http://localhost:5173', '_blank'); }
</script>
</body>
</html>"""


def _spawn_ps(command: str) -> None:
    subprocess.Popen(
        [PS, "-NoExit", "-Command", command],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/launch/frontend":
            _spawn_ps(f'Set-Location "{ROOT / "frontend"}"; npm run dev')
            self._ok("前端服务已在新的 PowerShell 窗口启动")
        elif self.path == "/api/launch/install-deps":
            _spawn_ps(f'Set-Location "{ROOT / "frontend"}"; npm install')
            self._ok("前端依赖安装已在新的 PowerShell 窗口启动")
        elif self.path == "/api/launch/backend":
            venv_py = str(ROOT / ".venv" / "Scripts" / "python.exe")
            _spawn_ps(f'Set-Location "{ROOT}"; & "{venv_py}" run.py')
            self._ok("后端服务已在新的 PowerShell 窗口启动")
        else:
            self.send_response(404)
            self.end_headers()

    def _ok(self, msg: str):
        body = json.dumps({"ok": True, "msg": msg}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静音访问日志
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"[小二启动器] 运行中：{url}（浏览器会自动打开；关闭本窗口=退出启动器）")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
