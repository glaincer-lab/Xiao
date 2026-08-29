// 小二 · 语音工作助手 桌面壳（Electron）
// 常驻托盘 + 自动拉起 Python 后端 + 关窗隐藏 + 可选开机自启
const { app, BrowserWindow, Tray, Menu, nativeImage, dialog } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const fs = require('fs')
const http = require('http')
const net = require('net')

// 打包后：backend/ run.py config.yaml models/ frontend/dist 都在 resources 下；开发态：仓库根
const ROOT = app.isPackaged ? process.resourcesPath : path.resolve(__dirname, '..')
const DEV = process.env.XIAO_DEV === '1'
const BACKEND_HOST = '127.0.0.1'
// 端口跟随 config.yaml 的 server.port（打包态读 resources 下配置），读取失败回退 8123
function readBackendPort() {
  try {
    const cfg = fs.readFileSync(path.join(ROOT, 'config.yaml'), 'utf8')
    const m = cfg.match(/^server:\s*$[\s\S]*?^\s+port:\s*(\d+)/m)
    if (m) return Number(m[1])
  } catch {
    /* 忽略 */
  }
  return 8123
}
const BACKEND_PORT = readBackendPort()
const FRONTEND_URL = DEV ? 'http://localhost:5173' : `http://${BACKEND_HOST}:${BACKEND_PORT}`

let mainWindow = null
let tray = null
let backendProc = null
let quitting = false

// 单实例
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      mainWindow.show()
      mainWindow.focus()
    }
  })
  app.whenReady().then(bootstrap)
}

async function bootstrap() {
  await ensureBackend()
  createWindow()
  createTray()
}

function findPython() {
  // 打包态：优先安装包内置的 Python 运行时（使用者无需装 Python）
  if (app.isPackaged) {
    const bundled = path.join(process.resourcesPath, 'runtime', 'python', 'python.exe')
    if (fs.existsSync(bundled)) return bundled
    dialog.showErrorBox('小二', '内置 Python 运行时缺失，安装包可能不完整。\n请重新下载安装包；问题仍存在请到项目主页反馈。')
    app.quit()
    return ''
  }
  // 开发态：仓库 .venv → 环境变量 → 系统 python
  const venv =
    process.platform === 'win32'
      ? path.join(ROOT, '.venv', 'Scripts', 'python.exe')
      : path.join(ROOT, '.venv', 'bin', 'python')
  if (fs.existsSync(venv)) return venv
  return process.env.XIAO_PYTHON || 'python'
}

function portInUse() {
  return new Promise((resolve) => {
    const s = net.connect(BACKEND_PORT, BACKEND_HOST)
    s.once('connect', () => {
      s.destroy()
      resolve(true)
    })
    s.once('error', () => resolve(false))
  })
}

function waitForBackend() {
  return new Promise((resolve) => {
    let tries = 0
    const poll = () => {
      const req = http.get(`http://${BACKEND_HOST}:${BACKEND_PORT}/health`, (res) => {
        res.resume()
        resolve(true)
      })
      req.on('error', () => {
        if (++tries >= 60) return resolve(false)
        setTimeout(poll, 500)
      })
      req.setTimeout(1000, () => req.destroy())
    }
    poll()
  })
}

function startBackend() {
  const py = findPython()
  if (!py) return
  backendProc = spawn(py, ['run.py'], { cwd: ROOT, stdio: 'ignore' })
  backendProc.on('exit', () => {
    backendProc = null
    // 后端播完结束语后主动退出：稍等声卡收尾，再让整个桌面壳退出
    if (!quitting) {
      quitting = true
      setTimeout(() => app.quit(), 900)
    }
  })
}

async function ensureBackend() {
  if (await portInUse()) return
  startBackend()
  await waitForBackend()
}

function getTrayIcon() {
  const p = path.join(__dirname, 'assets', 'tray.png')
  if (fs.existsSync(p)) return nativeImage.createFromPath(p)
  // 兜底：用原始位图生成 16x16 蓝色圆点
  const size = 16
  const buf = Buffer.alloc(size * size * 4)
  for (let i = 0; i < size * size; i++) {
    buf[i * 4] = 56
    buf[i * 4 + 1] = 189
    buf[i * 4 + 2] = 248
    buf[i * 4 + 3] = 255
  }
  return nativeImage.createFromBitmap(buf, { width: size, height: size })
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 760,
    frame: false,
    transparent: true,
    resizable: false,
    autoHideMenuBar: true,
    backgroundColor: '#00000000',
    show: false,
    icon: path.join(__dirname, 'assets', 'icon.png'),
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  })
  mainWindow.loadURL(FRONTEND_URL)
  mainWindow.once('ready-to-show', () => mainWindow.show())
  mainWindow.on('close', (e) => {
    if (!quitting) {
      e.preventDefault()
      mainWindow.hide() // 关闭 → 隐藏到托盘（常驻后台）
    }
  })
  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

function createTray() {
  tray = new Tray(getTrayIcon())
  const menu = Menu.buildFromTemplate([
    {
      label: '显示 / 隐藏',
      click: () => (mainWindow.isVisible() ? mainWindow.hide() : mainWindow.show()),
    },
    {
      label: '开机自启',
      type: 'checkbox',
      checked: app.getLoginItemSettings().openAtLogin,
      click: (item) => app.setLoginItemSettings({ openAtLogin: item.checked }),
    },
    { type: 'separator' },
    {
      label: '退出',
      click: () => {
        quitting = true
        app.quit()
      },
    },
  ])
  tray.setToolTip('小二 · 语音工作助手')
  tray.setContextMenu(menu)
  tray.on('click', () => (mainWindow.isVisible() ? mainWindow.hide() : mainWindow.show()))
}

// 关窗不退出，保持托盘常驻
app.on('window-all-closed', () => {})

app.on('before-quit', () => {
  quitting = true
})

app.on('quit', () => {
  if (backendProc) {
    try {
      backendProc.kill()
    } catch {
      /* 忽略 */
    }
  }
})
