export type ServerEvent = {
  type: string
  [key: string]: unknown
}

export type WSHandle = {
  send: (msg: object) => void
  close: () => void
}

const PENDING_CAP = 32

export function connectWS(
  url: string,
  onEvent: (e: ServerEvent) => void,
  onStatus: (connected: boolean) => void,
): WSHandle {
  let ws: WebSocket | null = null
  let closed = false
  let retry = 0
  let timer: number | null = null
  const pending: object[] = []

  const open = () => {
    ws = new WebSocket(url)
    ws.onopen = () => {
      retry = 0
      onStatus(true)
      // 重连成功：补发断线期间入队的消息
      while (pending.length > 0 && ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(pending.shift()))
      }
    }
    ws.onclose = () => {
      onStatus(false)
      if (!closed) {
        retry = Math.min(retry + 1, 30)
        timer = window.setTimeout(open, Math.min(500 * retry, 5000))
      }
    }
    ws.onerror = () => {
      ws?.close()
    }
    ws.onmessage = (ev) => {
      try {
        onEvent(JSON.parse(ev.data))
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
      pending.length = 0
      ws?.close()
    },
  }
}
