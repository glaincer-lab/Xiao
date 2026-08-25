import { useCallback, useEffect, useRef, useState } from 'react'
import { connectWS, type ServerEvent, type WSHandle } from './ws'
import { Waveform } from './components/Waveform'
import { Nebula } from './components/Nebula'
import { Typewriter } from './components/Typewriter'
import { SettingsPanel } from './components/SettingsPanel'
import { PermsPanel } from './components/PermsPanel'
import { TaskPanel, type Task } from './components/TaskPanel'
import { WorkPanel, type WorkStep } from './components/WorkPanel'

type Message = {
  id: number
  role: 'user' | 'assistant' | 'system'
  text: string
  kind?: 'plan' | 'result' | 'notice'
}

type UISettings = {
  font: string
  tabularNums: boolean
  scale: number
  leftWidth: number
}
const UI_DEFAULTS: UISettings = {
  font: "'Microsoft YaHei', 'PingFang SC', sans-serif",
  tabularNums: false,
  scale: 1,
  leftWidth: 260,
}
const FONT_OPTIONS = [
  { value: "'Microsoft YaHei', 'PingFang SC', sans-serif", label: '微软雅黑' },
  { value: "'SimHei', sans-serif", label: '黑体' },
  { value: "'KaiTi', 'STKaiti', serif", label: '楷体' },
  { value: "'SimSun', serif", label: '宋体' },
  { value: "'Segoe UI', sans-serif", label: 'Segoe UI' },
  { value: "'Arial', sans-serif", label: 'Arial' },
  { value: "'Georgia', serif", label: 'Georgia' },
  { value: "'Consolas', 'Courier New', monospace", label: 'Consolas 等宽' },
]

const STATE_LABELS: Record<string, string> = {
  idle: '待机',
  listening: '聆听中',
  processing: '思考中',
  speaking: '播报中',
  executing: '执行中',
  sleeping: '休眠',
  confirm_shutdown: '等待确认关闭',
  working: '工作中',
  await_approval: '等待语音确认',
}

let idCounter = 1
let stepIdCounter = 1
const AUTO_POPUP_LEN = 100 // 回答超过这个字数，自动在中间弹大窗

// 从工具入参里提取一句话摘要（本地聊天 agent 与 DSH 步骤共用）
function summarizeArgs(name: string, args: unknown): string {
  const a = (args && typeof args === 'object' ? args : {}) as Record<string, unknown>
  let s = ''
  if (name === 'bash' || name === 'pwsh') s = String(a.command ?? a.script ?? a.path ?? '')
  else if (name === 'write' || name === 'edit') s = String(a.file_path ?? a.path ?? a.file ?? '')
  else if (name === 'web' || name === 'web_search' || name === 'search') s = String(a.query ?? a.prompt ?? '')
  else if (name === 'skill') s = String(a.name ?? '')
  else if (name === 'subagent' || name === 'subagent_fork') s = String(a.description ?? a.prompt ?? '')
  else if (name === 'workflow') s = String(((a.args as Record<string, unknown>) ?? {}).name ?? '')
  else if (name === 'todo' || name === 'todo_write') {
    const arr = Array.isArray(a.todos) ? a.todos : []
    s = arr.map((t) => String((t as { content?: string }).content ?? '')).filter(Boolean).join('、')
  } else if (name === 'read' || name === 'grep') s = String(a.file_path ?? a.path ?? a.pattern ?? '')
  else {
    try {
      s = JSON.stringify(a)
    } catch {
      s = ''
    }
  }
  const t = s.replace(/\s+/g, ' ').trim()
  return t.length > 90 ? t.slice(0, 90) + '…' : t
}

function playChime() {
  try {
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
    const ctx = new Ctx()
    const now = ctx.currentTime
    const gain = ctx.createGain()
    gain.gain.setValueAtTime(0.0001, now)
    gain.gain.exponentialRampToValueAtTime(0.35, now + 0.03)
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.7)
    gain.connect(ctx.destination)
    ;[880, 1174.66].forEach((f, i) => {
      const o = ctx.createOscillator()
      o.type = 'sine'
      o.frequency.value = f
      o.connect(gain)
      o.start(now + i * 0.09)
      o.stop(now + 0.7)
    })
  } catch {
    /* 忽略音频播放限制 */
  }
}

type LogEntry = {
  id: number
  time: string
  label: string
  kind: string
}

function truncate(s: string, n = 60): string {
  const t = s.replace(/\s+/g, ' ').trim()
  return t.length > n ? t.slice(0, n) + '…' : t
}

function describeEvent(e: ServerEvent): { label: string; kind: string } | null {
  switch (e.type) {
    case 'asr_partial':
      return null
    case 'state':
      return { label: `状态 → ${STATE_LABELS[String(e.state)] ?? String(e.state)}`, kind: 'info' }
    case 'wake':
      return { label: '唤醒', kind: 'ok' }
    case 'interrupted':
      return { label: '打断', kind: 'warn' }
    case 'asr_final':
      return { label: `你说：${truncate(String(e.text ?? ''))}`, kind: 'user' }
    case 'assistant_plan':
      return { label: `计划：${truncate(String(e.text ?? ''))}`, kind: 'assistant' }
    case 'assistant_result':
      return { label: `回答：${truncate(String(e.text ?? ''))}`, kind: 'assistant' }
    case 'tool_call':
      return { label: `执行：${String(e.name ?? '')}`, kind: 'tool' }
    case 'tool_result':
      return { label: `完成：${String(e.name ?? '')} ${truncate(String(e.summary ?? ''))}`, kind: 'tool' }
    case 'router_mode':
      return { label: `路由 → ${String(e.mode ?? 'auto')}`, kind: 'info' }
    case 'reminder_fired':
      return { label: `提醒：${truncate(String(e.text ?? ''))}`, kind: 'warn' }
    case 'app_shutdown':
      return { label: '程序即将关闭', kind: 'warn' }
    default:
      return { label: e.type, kind: 'info' }
  }
}

export default function App() {
  const [sessionState, setSessionState] = useState('idle')
  const [connected, setConnected] = useState(false)
  const [liveText, setLiveText] = useState('')
  const [messages, setMessages] = useState<Message[]>([])
  const [toolActivity, setToolActivity] = useState('')
  const [input, setInput] = useState('')
  const [routerMode, setRouterMode] = useState('auto')
  const [expanded, setExpanded] = useState<Message | null>(null)
  const [interruptFlash, setInterruptFlash] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [showPerms, setShowPerms] = useState(false)
  const [showTasks, setShowTasks] = useState(false)
  const [tasks, setTasks] = useState<Task[]>([])
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [showLog, setShowLog] = useState(false)
  const [dshAvailable, setDshAvailable] = useState<boolean | null>(null)
  const [workSteps, setWorkSteps] = useState<WorkStep[]>([])
  const [approvalText, setApprovalText] = useState('')
  const [ui, setUi] = useState<UISettings>(() => {
    try {
      const raw = localStorage.getItem('xiao_ui')
      if (raw) return { ...UI_DEFAULTS, ...JSON.parse(raw) }
    } catch {
      /* ignore */
    }
    return UI_DEFAULTS
  })

  // 应用 UI 设置到 CSS 变量并持久化
  useEffect(() => {
    const root = document.documentElement
    root.style.setProperty('--font-ui', ui.font)
    root.style.setProperty('--tnum', ui.tabularNums ? 'tabular-nums' : 'normal')
    root.style.setProperty('--fs', String(ui.scale))
    root.style.setProperty('--left-width', ui.leftWidth + 'px')
    try {
      localStorage.setItem('xiao_ui', JSON.stringify(ui))
    } catch {
      /* ignore */
    }
  }, [ui])
  const wsRef = useRef<WSHandle | null>(null)
  const interruptTimer = useRef<number | null>(null)
  const logBodyRef = useRef<HTMLDivElement | null>(null)
  const messagesRef = useRef<HTMLDivElement | null>(null)
  const followBottom = useRef(true)

  const addMessage = useCallback((m: Omit<Message, 'id'>): Message => {
    const msg: Message = { ...m, id: idCounter++ }
    setMessages((prev) => [...prev, msg])
    return msg
  }, [])

  const pushLog = useCallback((label: string, kind = 'info') => {
    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    setLogs((prev) => [...prev.slice(-199), { id: idCounter++, time, label, kind }])
  }, [])

  const nowTime = () => new Date().toLocaleTimeString('zh-CN', { hour12: false })

  const pushWorkStep = useCallback((s: Omit<WorkStep, 'id' | 'time'>) => {
    setWorkSteps((prev) => [...prev.slice(-199), { ...s, id: stepIdCounter++, time: nowTime() }])
  }, [])

  const answerApproval = useCallback((decision: 'allow' | 'reject') => {
    wsRef.current?.send({ type: 'approval_answer', decision })
  }, [])

  const handleEvent = (e: ServerEvent) => {
    const desc = describeEvent(e)
    if (desc) pushLog(desc.label, desc.kind)
    switch (e.type) {
      case 'state': {
        const s = String(e.state ?? 'idle')
        setSessionState(s)
        if (s !== 'await_approval') setApprovalText('')
        break
      }
      case 'wake':
        playChime()
        break
      case 'interrupted':
        setInterruptFlash(true)
        if (interruptTimer.current !== null) window.clearTimeout(interruptTimer.current)
        interruptTimer.current = window.setTimeout(() => setInterruptFlash(false), 1500)
        break
      case 'asr_partial': {
        // 实时识别中间结果是「累积全文」，直接覆盖显示，避免旧文本重复拼接
        setLiveText(String(e.text ?? ''))
        break
      }
      case 'asr_final': {
        const t = String(e.text ?? '').trim()
        setLiveText('')
        setExpanded(null)
        if (t) addMessage({ role: 'user', text: t })
        break
      }
      case 'assistant_plan': {
        const text = String(e.text ?? '')
        setApprovalText(text)
        const msg = addMessage({ role: 'assistant', text, kind: 'plan' })
        if (text.length > AUTO_POPUP_LEN) setExpanded(msg)
        break
      }
      case 'assistant_result': {
        const text = String(e.text ?? '')
        const msg = addMessage({ role: 'assistant', text, kind: 'result' })
        if (text.length > AUTO_POPUP_LEN) setExpanded(msg)
        break
      }
      case 'tool_call': {
        setToolActivity(`正在执行：${String(e.name ?? '')}`)
        pushWorkStep({ name: String(e.name ?? 'tool'), status: 'start', summary: summarizeArgs(String(e.name ?? ''), e.args), source: 'local' })
        break
      }
      case 'tool_result': {
        setToolActivity('')
        const name = String(e.name ?? '')
        const summary = truncate(String(e.summary ?? ''), 120)
        setWorkSteps((prev) => {
          for (let i = prev.length - 1; i >= 0; i--) {
            if (prev[i].name === name && prev[i].status === 'start') {
              const next = [...prev]
              next[i] = { ...next[i], status: 'done', summary: summary || next[i].summary }
              return next
            }
          }
          return prev
        })
        break
      }
      case 'work_step': {
        const name = String(e.name ?? 'tool')
        const status = e.status === 'error' ? 'error' : e.status === 'start' ? 'start' : 'done'
        pushWorkStep({ name, status: status as WorkStep['status'], summary: truncate(String(e.summary ?? ''), 120), source: 'dsh' })
        break
      }
      case 'router_mode':
        setRouterMode(String(e.mode ?? 'auto'))
        break
      case 'reminder_fired':
        addMessage({ role: 'system', text: String(e.text ?? ''), kind: 'notice' })
        break
      case 'task_event': {
        const t = e as unknown as Task
        setTasks((prev) => {
          const i = prev.findIndex((x) => x.id === t.id)
          if (i >= 0) {
            const next = [...prev]
            next[i] = { ...next[i], ...t }
            return next
          }
          return [...prev, t]
        })
        break
      }
      default:
        break
    }
  }

  useEffect(() => {
    // 直接连后端 WS（绕过 vite 代理，dev/prod 后端都在 127.0.0.1:8123）
    const ws = connectWS('ws://127.0.0.1:8123/ws', handleEvent, setConnected)
    wsRef.current = ws
    return () => ws.close()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    fetch('http://127.0.0.1:8123/api/status')
      .then((r) => r.json())
      .then((d) => {
        if (d && d.ok) {
          setDshAvailable(Boolean(d.dsh_available))
          if (d.state) setSessionState(String(d.state))
          if (d.router_mode) setRouterMode(String(d.router_mode))
        }
      })
      .catch(() => {
        /* 后端未就绪时保持「检测中」，连接状态由 WS 的 conn 灯单独体现 */
      })
  }, [])

  useEffect(() => {
    fetch('http://127.0.0.1:8123/api/tasks')
      .then((r) => r.json())
      .then((d) => {
        if (d && d.ok && Array.isArray(d.tasks)) setTasks(d.tasks)
      })
      .catch(() => {
        /* 后端未就绪时忽略 */
      })
  }, [])

  useEffect(() => {
    if (showLog && logBodyRef.current) {
      logBodyRef.current.scrollTop = logBodyRef.current.scrollHeight
    }
  }, [logs, showLog])

  // 对话自动滚动：新消息滚到底；打字机逐字展开时若贴近底部则跟随
  useEffect(() => {
    const el = messagesRef.current
    if (!el) return
    followBottom.current = true
    el.scrollTop = el.scrollHeight
  }, [messages])

  // 用户手动上翻时停止跟随，回到底部附近才恢复
  useEffect(() => {
    const el = messagesRef.current
    if (!el) return
    const onScroll = () => {
      followBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    }
    el.addEventListener('scroll', onScroll)
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  // 打字机展开 / 气泡增高时，若仍在底部则跟随（MutationObserver 监听文本与子节点变化）
  useEffect(() => {
    const el = messagesRef.current
    if (!el) return
    const mo = new MutationObserver(() => {
      if (followBottom.current) el.scrollTop = el.scrollHeight
    })
    mo.observe(el, { childList: true, subtree: true, characterData: true })
    return () => mo.disconnect()
  }, [])

  const sendText = () => {
    const t = input.trim()
    if (!t) return
    wsRef.current?.send({ type: 'text', text: t })
    addMessage({ role: 'user', text: t })
    setInput('')
    setExpanded(null)
  }

  const wake = () => wsRef.current?.send({ type: 'wake' })

  const interrupt = () => wsRef.current?.send({ type: 'interrupt' })

  const setMode = (m: string) => {
    setRouterMode(m)
    wsRef.current?.send({ type: 'router_mode', mode: m })
  }

  const active = sessionState === 'listening' || sessionState === 'speaking' || sessionState === 'confirm_shutdown' || sessionState === 'working'
  const activeTaskCount = tasks.filter((t) => t.status === 'pending' || t.status === 'running').length

  const dshBusy = sessionState === 'executing' || sessionState === 'working'
  const dshState = dshBusy ? 'busy' : dshAvailable === true ? 'ok' : dshAvailable === false ? 'off' : 'unknown'
  const dshLabel = dshBusy ? 'DSH 干活中' : dshAvailable === true ? 'DSH 就绪' : dshAvailable === false ? 'DSH 未找到' : 'DSH 检测中…'

  return (
    <div className="app">
      <div className="bg-glow" aria-hidden />

      <header className="topbar">
        <div className="topbar-left">
          <div className={`status status--${sessionState}`}>
            <span className="status-dot" />
            {STATE_LABELS[sessionState] ?? sessionState}
          </div>
          <div className={`dsh dsh--${dshState}`} title="DSH 桥状态">
            <span className="dsh-dot" />
            {dshLabel}
          </div>
          <div className={`conn ${connected ? 'conn--on' : ''}`}>
            <span className="conn-dot" />
            {connected ? '已连接' : '连接中…'}
          </div>
        </div>
        <div className="brand">
          <span className="brand-dot" />
          <span className="brand-name">小二</span>
          <span className="brand-sub">语音工作助手</span>
        </div>
        <div className="topbar-right">
          <div className="router-toggle">
            <button className={`router-btn ${routerMode === 'auto' ? 'router-btn--on' : ''}`} onClick={() => setMode('auto')}>自动</button>
            <button className={`router-btn ${routerMode === 'chat' ? 'router-btn--on' : ''}`} onClick={() => setMode('chat')}>聊天</button>
            <button className={`router-btn ${routerMode === 'dsh' ? 'router-btn--on' : ''}`} onClick={() => setMode('dsh')}>DSH</button>
          </div>
          <button className="settings-btn" onClick={() => setShowTasks(true)}>
            任务{activeTaskCount > 0 && <span className="chip-badge">{activeTaskCount}</span>}
          </button>
          <button className="settings-btn" onClick={() => setShowPerms(true)}>权限</button>
          <button className="settings-btn" onClick={() => setShowSettings(true)}>设置</button>
        </div>
      </header>

      <main className="stage">
        <aside className="col col--left">
          <section className="panel panel--messages">
            <div className="messages" ref={messagesRef}>
              {messages.length === 0 && (
                <div className="empty">
                  <p>唤醒后，你说的话会实时显示在这里</p>
                  <p>我会先说明准备做什么，再汇报结果</p>
                </div>
              )}
              {messages.map((m, i) => {
                const prev = messages[i - 1]
                const grouped = !!(prev && prev.role === m.role && prev.kind === m.kind)
                return (
                  <div
                    key={m.id}
                    className={`msg msg--${m.role}${m.kind ? ` msg--${m.kind}` : ''}${grouped ? ' msg--grouped' : ' msg--group-start'}`}
                  >
                    <div className="bubble">
                      <span className="bubble-text">
                        {m.role === 'assistant' ? <Typewriter text={m.text} /> : m.text}
                      </span>
                      {m.kind !== 'notice' && (m.text.length > AUTO_POPUP_LEN || m.text.includes('\n')) && (
                        <button className="expand-btn" onClick={() => setExpanded(m)}>展开全文</button>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </section>
        </aside>

        <section className="col col--center">
          <Nebula state={sessionState} />
          {sessionState === 'await_approval' ? (
            <div className="approval-card">
              <div className="approval-ring" aria-hidden />
              <p className="approval-q">{approvalText || '是否允许执行？'}</p>
              <div className="approval-btns">
                <button className="btn btn--allow btn--big" onClick={() => answerApproval('allow')}>允许</button>
                <button className="btn btn--reject btn--big" onClick={() => answerApproval('reject')}>拒绝</button>
              </div>
              <span className="approval-tip">点一下即可，比说话更准（受干扰也能确认）</span>
            </div>
          ) : (
            <div className="livebox">
              {liveText ? (
                <p className="live-text">{liveText}</p>
              ) : (
                <p className="live-hint">
                  {sessionState === 'idle' || sessionState === 'sleeping'
                    ? '说「小二」唤醒我'
                    : sessionState === 'confirm_shutdown'
                      ? '请说「确认关闭」或「取消」'
                      : sessionState === 'working'
                        ? '任务进行中，可说「进展」或「取消」'
                        : toolActivity || '我在听…'}
                </p>
              )}
              <Waveform active={active} />
            </div>
          )}
        </section>

        <aside className="col col--right">
          {(workSteps.length > 0 || activeTaskCount > 0) && (
            <WorkPanel steps={workSteps} activeTaskCount={activeTaskCount} onClear={() => setWorkSteps([])} />
          )}
          <section className="panel panel--composer">
            <div className="composer">
              <textarea
                className="composer-input"
                placeholder="也可以直接打字…（回车换行，Ctrl+回车发送）"
                value={input}
                rows={3}
                onChange={(ev) => setInput(ev.target.value)}
                onKeyDown={(ev) => {
                  if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) {
                    ev.preventDefault()
                    sendText()
                  }
                }}
              />
              <div className="composer-actions">
                <button className="btn" onClick={sendText}>
                  发送
                </button>
                <button className="btn" onClick={interrupt}>
                  打断
                </button>
                <button className="btn btn--wake" onClick={wake}>
                  唤醒
                </button>
              </div>
            </div>
          </section>
        </aside>
      </main>

      <footer className="logbar">
        <div className="logbar-head">
          <button className="logbar-toggle" onClick={() => setShowLog((v) => !v)}>
            {showLog ? '▾' : '▸'} 日志
            <span className="logbar-count">{logs.length}</span>
          </button>
          {!showLog && logs.length > 0 && (
            <span className={`logbar-last logline--${logs[logs.length - 1].kind}`}>
              {logs[logs.length - 1].time} {logs[logs.length - 1].label}
            </span>
          )}
          {showLog && (
            <button className="logbar-clear" onClick={() => setLogs([])}>
              清空
            </button>
          )}
        </div>
        {showLog && (
          <div className="logbar-body" ref={logBodyRef}>
            {logs.length === 0 ? (
              <span className="logbar-empty">暂无日志</span>
            ) : (
              logs.map((l) => (
                <div key={l.id} className={`logline logline--${l.kind}`}>
                  <span className="logline-time">{l.time}</span>
                  <span className="logline-text">{l.label}</span>
                </div>
              ))
            )}
          </div>
        )}
      </footer>

      {interruptFlash && <div className="interrupt-flash" aria-hidden />}

      {expanded && (
        <div className="modal-overlay" onClick={() => setExpanded(null)}>
          <div className="modal" onClick={(ev) => ev.stopPropagation()}>
            <div className="modal-head">
              <span className="modal-role">{expanded.role === 'user' ? '你' : '小二'}</span>
              <button className="modal-close" onClick={() => setExpanded(null)}>关闭</button>
            </div>
            <div className="modal-body">
              {expanded.role === 'assistant' ? <Typewriter text={expanded.text} /> : expanded.text}
            </div>
          </div>
        </div>
      )}

      {showTasks && <TaskPanel tasks={tasks} onClose={() => setShowTasks(false)} />}
      {showPerms && <PermsPanel onClose={() => setShowPerms(false)} />}
      {showSettings && <SettingsPanel onClose={() => setShowSettings(false)} ui={ui} setUi={setUi} />}
    </div>
  )
}
