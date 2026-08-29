export type ServerEvent = {
  type: string
  [key: string]: unknown
}

export type WSHandle = {
  send: (msg: object) => void
  close: () => void
}

const PENDING_CAP = 32
const HEARTBEAT_INTERVAL = 15000
const HEARTBEAT_TIMEOUT = 40000

export function connectWS(
  url: string,
  onEvent: (e: ServerEvent) => void,
  onStatus: (connected: boolean) => void,
): WSHandle {
  let ws: WebSocket | null = null
  let closed = false
  let retry = 0
  let timer: number | null = null
  let heartbeat: number | null = null
  let heartbeatDeadline: number | null = null
  const pending: object[] = []

  const clearHeartbeat = () => {
    if (heartbeat !== null) {
      window.clearInterval(heartbeat)
      heartbeat = null
    }
    heartbeatDeadline = null
  }

  const startHeartbeat = () => {
    clearHeartbeat()
    // 每 15s 发一次 ping；若距上次收到 pong 超过 40s 判定链路假死，主动断开触发重连
    heartbeat = window.setInterval(() => {
      const now = Date.now()
      if (heartbeatDeadline !== null && now - heartbeatDeadline > HEARTBEAT_TIMEOUT) {
        ws?.close()
        return
      }
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }))
      }
    }, HEARTBEAT_INTERVAL)
  }

  const open = () => {
    ws = new WebSocket(url)
    ws.onopen = () => {
      retry = 0
      onStatus(true)
      heartbeatDeadline = Date.now()
      startHeartbeat()
      // 重连成功：补发断线期间入队的消息
      while (pending.length > 0 && ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(pending.shift()))
      }
    }
    ws.onclose = () => {
      onStatus(false)
      clearHeartbeat()
      if (!closed) {
        retry = Math.min(retry + 1, 30)
        timer = window.setTimeout(open, Math.min(500 * retry, 5000))
      }
    }
    ws.onerror = () => {
      ws?.close()
    }
    ws.onmessage = (ev) => {
      // 收到任何服务端消息都视为链路存活，刷新心跳截止
      heartbeatDeadline = Date.now()
      try {
        const e = JSON.parse(ev.data) as ServerEvent
        if (e.type === 'pong') return
        onEvent(e)
      } catch {
        /* 忽略无法解析的消息 */
      }
    }
  }
  open()

  return {
    send: (msg) => {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(msg))
      } else if (!closed) {
        // 未连接时入队等重连补发；超过上限丢弃最旧的，防内存膨胀
        pending.push(msg)
        if (pending.length > PENDING_CAP) pending.shift()
      }
    },
    close: () => {
      closed = true
      if (timer !== null) {
        window.clearTimeout(timer)
        timer = null
      }
      clearHeartbeat()
      pending.length = 0
      ws?.close()
    },
  }
}
