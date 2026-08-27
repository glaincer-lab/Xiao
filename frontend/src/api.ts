// 后端地址推导：
// - dev（vite 5173）→ 后端固定 127.0.0.1:8123
// - 生产（FastAPI 托管 dist / Electron 同源加载）→ 与页面同源
const isDev = location.port === '5173'

export const API_BASE = isDev
  ? 'http://127.0.0.1:8123'
  : `${location.protocol}//${location.host}`

export const WS_URL = `${isDev ? 'ws://127.0.0.1:8123' : `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`}/ws`
