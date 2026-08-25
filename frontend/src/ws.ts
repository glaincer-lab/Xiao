export type ServerEvent = {
  type: string
  [key: string]: unknown
}

export type WSHandle = {
  send: (msg: object) => void
  close: () => void
}

export function connectWS(
  url: string,
  onEvent: (e: ServerEvent) => void,
  onStatus: (connected: boolean) => void,
): WSHandle {
  let ws: WebSocket | null = null
  let closed = false
  let retry = 0

  const open = () => {
    ws = new WebSocket(url)
    ws.onopen = () => {
      retry = 0
      onStatus(true)
    }
    ws.onclose = () => {
      onStatus(false)
      if (!closed) {
        retry = Math.min(retry + 1, 30)
        setTimeout(open, Math.min(500 * retry, 5000))
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
      if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg))
    },
    close: () => {
      closed = true
      ws?.close()
    },
  }
}
