import { useCallback, useEffect, useRef, useState } from 'react'
import { API_BASE, WS_URL } from './api'
import { connectWS, type ServerEvent, type WSHandle } from './ws'
import { Nebula, SPRITE_NAMES, type SpriteMode } from './components/Nebula'
import { VoiceLine } from './components/VoiceLine'
import { Typewriter } from './components/Typewriter'
import { SettingsPanel } from './components/SettingsPanel'
import { PermsPanel } from './components/PermsPanel'
import { TaskPanel, type Task } from './components/TaskPanel'
import { WorkPanel, type WorkStep } from './components/WorkPanel'
import { OnboardingWizard } from './components/OnboardingWizard'

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
  sprite: SpriteMode // 粒子精灵：'auto' 按状态自动挑，0~4 固定锁定
}
const UI_DEFAULTS: UISettings = {
  font: "'Microsoft YaHei', 'PingFang SC', sans-serif",
  tabularNums: false,
  scale: 1,
  leftWidth: 260,
  sprite: 'auto',
}
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

// 精灵轮换顺序
const CYCLE: SpriteMode[] = ['auto', 0, 1, 2, 3, 4]

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

let _chimeCtx: AudioContext | null = null

function playChime() {
  try {
    // 复用单个 AudioContext（浏览器对同时存在的 context 数量严格受限），
    // 避免每次唤醒都新建一个再也不释放的 context → 导致资源泄漏/无声
    const Ctx =
      window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
    const ctx = _chimeCtx ?? (_chimeCtx = new Ctx())
    if (ctx.state === 'suspended') void ctx.resume()
    const now = ctx.currentTime
    const gain = ctx.createGain()
    gain.gain.setValueAtTime(0.0001, now)
    gain.gain.exponentialRampToValueAtTime(0.35, now + 0.03)
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.7)
    gain.connect(ctx.destination)
    const oscs: OscillatorNode[] = []
    ;[880, 1174.66].forEach((f, i) => {
      const o = ctx.createOscillator()
      o.type = 'sine'
      o.frequency.value = f
      o.connect(gain)
      o.start(now + i * 0.09)
      o.stop(now + 0.7)
      oscs.push(o)
    })
    // 播完自动断开节点图，让本次音频可被回收；context 本体保留复用
    oscs[oscs.length - 1].addEventListener('ended', () => {
      for (const o of oscs) {
        try {
          o.disconnect()
        } catch {
          /* noop */
        }
      }
      try {
        gain.disconnect()
      } catch {
        /* noop */
      }
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
    case 'mic_level': // 高频画声线事件，不进日志面板（否则约 10 条/秒刷屏）
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
    case 'work_step': {
      const name = String(e.name ?? 'tool')
      const label = e.status === 'start' ? `执行 ${name}` : e.status === 'error' ? `${name} 失败` : `${name} 完成`
      return { label, kind: e.status === 'error' ? 'warn' : 'tool' }
    }
    case 'dsh_chunk':
      return null
    default:
      return { label: e.type, kind: 'info' }
  }
}

export default function App() {
  const [sessionState, setSessionState] = useState('idle')
  const [connected, setConnected] = useState(false)
  const [liveText, setLiveText] = useState('')
  const [micLevel, setMicLevel] = useState(0)
  const [messages, setMessages] = useState<Message[]>([])
  const [toolActivity, setToolActivity] = useState('')
  const [input, setInput] = useState('')
  const [routerMode, setRouterMode] = useState('auto')
  const [expanded, setExpanded] = useState<Message | null>(null)
  const [interruptFlash, setInterruptFlash] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [showPerms, setShowPerms] = useState(false)
  const [showTasks, setShowTasks] = useState(false)
  // 首次启动向导（E1）：本机没有完成标记时弹出，完成后写 localStorage 不再弹
  const [showWizard, setShowWizard] = useState(() => !localStorage.getItem('xiao_onboarded'))
  const [tasks, setTasks] = useState<Task[]>([])
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [logOpen, setLogOpen] = useState(false) // 日志栏默认收起：点击「日志」才展开
  const [dshAvailable, setDshAvailable] = useState<boolean | null>(null)
  const [workSteps, setWorkSteps] = useState<WorkStep[]>([])
  const [dshLive, setDshLive] = useState('')
  const [approvalText, setApprovalText] = useState('')
  const [clock, setClock] = useState('')
  const [shuttingDown, setShuttingDown] = useState(false) // 收到 app_shutdown 后置真：显示关机谢幕遮罩
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

  // 顶栏时钟：等宽字体秒级跳动
  useEffect(() => {
    const tick = () => {
      const d = new Date()
      setClock(
        [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':'),
      )
    }
    tick()
    const id = window.setInterval(tick, 1000)
    return () => window.clearInterval(id)
  }, [])

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
      case 'mic_level': {
        setMicLevel(Number(e.level ?? 0))
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
        addMessage({ role: 'assistant', text, kind: 'plan' })
        break
      }
      case 'assistant_result': {
        const text = String(e.text ?? '')
        addMessage({ role: 'assistant', text, kind: 'result' })
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
        const summary = truncate(String(e.summary ?? ''), 120)
        setWorkSteps((prev) => {
          if (status !== 'start') {
            for (let i = prev.length - 1; i >= 0; i--) {
              if (prev[i].name === name && prev[i].status === 'start') {
                const next = [...prev]
                next[i] = { ...next[i], status, summary: summary || next[i].summary }
                return next
              }
            }
          }
          return [...prev.slice(-199), { id: stepIdCounter++, name, status: status as WorkStep['status'], summary, source: 'dsh', time: nowTime() }]
        })
        break
      }
      case 'dsh_chunk':
        setDshLive((prev) => (prev + String(e.text ?? '')).slice(-2000))
        break
      case 'router_mode':
        setRouterMode(String(e.mode ?? 'auto'))
        break
      case 'reminder_fired':
        addMessage({ role: 'system', text: String(e.text ?? ''), kind: 'notice' })
        break
      case 'task_event': {
        const t = e as unknown as Task
        if (t.status === 'running') setDshLive('')
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
      case 'app_shutdown':
        // 后端播报完结束语后退出：前端盖谢幕遮罩，等 Electron 主进程收尾
        setShuttingDown(true)
        break
      default:
        break
    }
  }

  // 快照补偿：状态 + 任务列表一起拉，挂载时与 WS 重连成功后各调一次，
  // 避免断线期间丢事件导致前端状态与后端不同步
  const refreshSnapshot = useCallback(() => {
    fetch(`${API_BASE}/api/status`)
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
    fetch(`${API_BASE}/api/tasks`)
      .then((r) => r.json())
      .then((d) => {
        if (d && d.ok && Array.isArray(d.tasks)) setTasks(d.tasks)
      })
      .catch(() => {
        /* 后端未就绪时忽略 */
      })
  }, [])

  useEffect(() => {
    const onStatus = (ok: boolean) => {
      setConnected(ok)
      if (ok) refreshSnapshot()
    }
    const ws = connectWS(WS_URL, handleEvent, onStatus)
    wsRef.current = ws
    refreshSnapshot()
    return () => ws.close()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (logBodyRef.current) {
      logBodyRef.current.scrollTop = logBodyRef.current.scrollHeight
    }
  }, [logs])

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

  // 底栏精灵轮换：自动 → 圆形 → 六边晶体 → 蜂巢描边 → 菱晶切片 → 铁环 → 自动
  const cycleSprite = () => {
    setUi((prev) => ({ ...prev, sprite: CYCLE[(CYCLE.indexOf(prev.sprite) + 1) % CYCLE.length] }))
  }

  // 关机确认对话框按钮：把「确认关闭 / 取消」当一句话发给对话管线（与语音说法一致）
  const sendShutdownText = (text: string) => {
    wsRef.current?.send({ type: 'text', text })
    addMessage({ role: 'user', text })
  }

  const setMode = (m: string) => {
    setRouterMode(m)
    wsRef.current?.send({ type: 'router_mode', mode: m })
  }

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
          <div className="clock" aria-hidden>{clock || '--:--:--'}</div>
        </div>
        <div className="brand">
          <span className="hex" aria-hidden>⬡</span>
          <span className="brand-name">小二</span>
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
          <Nebula state={sessionState} sprite={ui.sprite} level={micLevel} />
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
              <VoiceLine level={micLevel} state={sessionState} />
            </div>
          )}
        </section>

        <aside className="col col--right">
          {(workSteps.length > 0 || activeTaskCount > 0 || dshLive) && (
            <WorkPanel
              steps={workSteps}
              activeTaskCount={activeTaskCount}
              live={dshLive}
              onClear={() => {
                setWorkSteps([])
                setDshLive('')
              }}
            />
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

      <footer className={`logbar ${logOpen ? 'logbar--open' : ''}`}>
        <div className="logbar-head">
          <button className="logbar-toggle" title={logOpen ? '收起日志' : '展开日志'} onClick={() => setLogOpen((v) => !v)}>
            <span className="logbar-title">日志</span>
            <span className="logbar-caret">{logOpen ? '▾' : '▸'}</span>
          </button>
          <span className="logbar-count">{logs.length}</span>
          <span className="logbar-spacer" />
          {logOpen && (
            <button className="logbar-clear" onClick={() => setLogs([])}>
              清空
            </button>
          )}
          <button
            className="btn sprite-cycle"
            title="轮换粒子精灵形态（也可在 设置 → 界面 中选择）"
            onClick={cycleSprite}
          >
            ⬡ {ui.sprite === 'auto' ? '自动' : SPRITE_NAMES[ui.sprite]}
          </button>
        </div>
        {logOpen && (
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

      {/* 关机谢幕：后端播完结束语发出 app_shutdown 后盖全屏遮罩，声音放完 Electron 再退出 */}
      {shuttingDown && (
        <div className="shutdown-veil" aria-hidden>
          <div className="shutdown-veil-inner">
            <span className="shutdown-glyph">⬡</span>
            <p className="shutdown-title">小二已休眠</p>
            <p className="shutdown-sub">播报结束后程序自动退出 · 期待下次唤醒</p>
          </div>
        </div>
      )}

      {/* 关机确认：进入 confirm_shutdown 状态时弹出玻璃拟态对话框 */}
      {sessionState === 'confirm_shutdown' && (
        <div className="mask show" onClick={() => sendShutdownText('取消')}>
          <div className="dialog dialog--shutdown" onClick={(ev) => ev.stopPropagation()}>
            <div className="dlg-head">
              <h2>关机确认</h2>
            </div>
            <div className="dlg-body">
              <p className="dlg-text">
                确认要让小二进入睡眠吗？
                <br />
                播报与监听将停止，本地引擎保持待命。
              </p>
            </div>
            <div className="dlg-foot">
              <button className="pri" onClick={() => sendShutdownText('取消')}>取消</button>
              <button className="pri danger" onClick={() => sendShutdownText('确认关闭')}>确认关机</button>
            </div>
          </div>
        </div>
      )}

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

      {showWizard && <OnboardingWizard onClose={() => setShowWizard(false)} />}
      {showTasks && <TaskPanel tasks={tasks} onClose={() => setShowTasks(false)} />}
      {showPerms && <PermsPanel onClose={() => setShowPerms(false)} />}
      {showSettings && <SettingsPanel onClose={() => setShowSettings(false)} ui={ui} setUi={setUi} />}
    </div>
  )
}
