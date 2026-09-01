import { useEffect, useState } from 'react'
import { API_BASE } from '../api'

// 首次启动向导（§三）：6 步隐私引导，每步可跳过；「已完成」标记存 localStorage（xiao_onboarded）。
const MARKER_KEY = 'xiao_onboarded'

type KeyProviderDef = {
  id: string
  name: string
  cloudProvider: string
  baseUrl: string
  url: string
  exampleModel: string
  steps: string[]
}

// 领 Key 链接与图文步骤（点链接直达官方领取页；模型名不内置，使用者手填官方最新名）
const KEY_PROVIDERS: KeyProviderDef[] = [
  {
    id: 'deepseek',
    name: 'DeepSeek',
    cloudProvider: 'deepseek',
    baseUrl: 'https://api.deepseek.com/v1',
    url: 'https://platform.deepseek.com',
    exampleModel: 'deepseek-v4-pro',
    steps: [
      '打开 platform.deepseek.com，注册并登录',
      '左侧菜单进入「API Keys」',
      '点「创建 API Key」，复制 sk- 开头的密钥（只显示一次，先粘贴保存）',
      '新账号通常赠送免费额度，足够体验',
    ],
  },
  {
    id: 'dashscope',
    name: '通义千问（阿里云百炼）',
    cloudProvider: 'dashscope',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    url: 'https://bailian.console.aliyun.com',
    exampleModel: 'qwen3.8-max',
    steps: [
      '打开阿里云百炼控制台，登录阿里云账号',
      '首次使用按提示「开通模型服务」（新用户有免费额度）',
      '右上角头像 →「API-KEY 管理」→「创建 API Key」并复制',
      '模型名以官方文档为准，下一步手填',
    ],
  },
]

const WIZ_STEPS = ['欢迎', '联网方式', '看屏幕/读剪贴板', '紧急白名单', '试麦克风', '完成']

type GpuTier = 'low' | '8gb' | '16gb' | '24gb' | 'igpu'
const GPU_TIERS: { value: GpuTier; label: string }[] = [
  { value: 'low', label: '8GB 以下' },
  { value: '8gb', label: '8GB' },
  { value: '16gb', label: '16GB' },
  { value: '24gb', label: '24GB 及以上' },
  { value: 'igpu', label: '核显（无独显）' },
]

type Mode = 'cloud' | 'local' | 'custom'
type PrivacyKey = 'asr' | 'llm' | 'vision' | 'tts'

// 三个方案卡片（列名用「须知」，推荐项由显存档位决定）
const MODES: { value: Mode; icon: string; title: string; tag: string; desc: string; note: string }[] = [
  {
    value: 'cloud',
    icon: '🌐',
    title: '联网模式',
    tag: '开箱即用',
    desc: '听懂/思考/说话/看图全云端，快、聪明',
    note: '须知：语音+对话+图片+文字发往阿里云/DeepSeek/微软。',
  },
  {
    value: 'local',
    icon: '🏠',
    title: '本地模式',
    tag: '隐私最强',
    desc: '全在你电脑本地，不出网，反应比云端稍慢',
    note: '须知：需下载约 11~12GB 模型（约 18~20 分钟），对显存/磁盘有需求。',
  },
  {
    value: 'custom',
    icon: '⚙️',
    title: '逐项自定义',
    tag: '灵活',
    desc: '四项各自选（听懂/脑子/看图/说话）',
    note: '',
  },
]

// 逐项自定义四项（生活化命名）
const CUSTOM_ITEMS: { key: PrivacyKey; label: string; cloud: string; local: string }[] = [
  { key: 'asr', label: '怎么听懂', cloud: '阿里云', local: 'FunASR（约1GB）/ MiniCPM-o' },
  { key: 'llm', label: '用什么脑子', cloud: 'DeepSeek/通义', local: 'Ollama Qwen3-8B（约5GB，默认推荐）' },
  { key: 'vision', label: '怎么看图', cloud: 'DeepSeek/Qwen 视觉', local: 'MiniCPM-o 8B（约5~6GB）' },
  { key: 'tts', label: '怎么说话', cloud: '微软/阿里云', local: 'Piper（63MB 已随包）/ MiniCPM-o' },
]

// 隐私说明弹窗内容：收集什么 / 发给谁 / 存多久 / 如何撤回
const PRIVACY_INFO: Record<PrivacyKey, { title: string; lines: string[] }> = {
  asr: {
    title: '隐私说明 · 听懂（语音识别）',
    lines: [
      '收集什么：你说话时的语音音频。',
      '发给谁：云端 → 阿里云百炼；本地 → 不出网，仅本机 FunASR / MiniCPM-o 处理。',
      '存多久：云端按供应商政策保存；本地留存由你控制，可随时删除。',
      '如何撤回：设置 → 权限 → 关闭「语音上云识别」，即回退本地。',
    ],
  },
  llm: {
    title: '隐私说明 · 脑子（对话）',
    lines: [
      '收集什么：你说的话与对话上下文。',
      '发给谁：云端 → DeepSeek / 通义千问；本地 → Ollama Qwen3-8B 本机处理，不出网。',
      '存多久：云端按供应商政策；本地留存由你控制。',
      '如何撤回：设置 → 权限 → 关闭「对话上云」，即回退本地。',
    ],
  },
  vision: {
    title: '隐私说明 · 看图（图片识别）',
    lines: [
      '收集什么：你发来的图片、截屏。',
      '发给谁：云端 → DeepSeek / Qwen 视觉；本地 → MiniCPM-o 8B 本机处理，不出网。',
      '存多久：云端按供应商政策；本地留存由你控制。',
      '如何撤回：设置 → 权限 → 关闭「图片上云」。',
    ],
  },
  tts: {
    title: '隐私说明 · 说话（语音合成）',
    lines: [
      '收集什么：要播报出来的文字。',
      '发给谁：云端 → 微软 / 阿里云；本地 → Piper 本机合成，不出网。',
      '存多久：云端按供应商政策；本地不出网。',
      '如何撤回：设置 → 权限 → 关闭「语音上云合成」。',
    ],
  },
}

const PRESET_WHITELIST = ['火警', '烟雾报警', '燃气泄漏']

export function OnboardingWizard({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState(0)

  // 第 2 步 · 联网方式
  const [gpuMethod, setGpuMethod] = useState<'auto' | 'manual'>('auto')
  const [gpuDetecting, setGpuDetecting] = useState(false)
  const [gpuName, setGpuName] = useState('')
  const [gpuMsg, setGpuMsg] = useState('')
  const [gpuTier, setGpuTier] = useState<GpuTier>('8gb')
  const [mode, setMode] = useState<Mode>('cloud')
  const [providerId, setProviderId] = useState('deepseek')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [testing, setTesting] = useState(false)
  const [testOk, setTestOk] = useState<boolean | null>(null)
  const [testMsg, setTestMsg] = useState('')
  const [custom, setCustom] = useState<Record<PrivacyKey, 'cloud' | 'local'>>({ asr: 'cloud', llm: 'cloud', vision: 'cloud', tts: 'cloud' })
  const [privacyFor, setPrivacyFor] = useState<PrivacyKey | null>(null)

  // 第 3 步 · 看屏幕/读剪贴板
  const [clipScreen, setClipScreen] = useState(false)

  // 第 4 步 · 紧急白名单
  const [whitelist, setWhitelist] = useState<string[]>([])
  const [whiteInput, setWhiteInput] = useState('')

  // 第 5 步 · 试麦克风
  const [echoBusy, setEchoBusy] = useState(false)
  const [echoMsg, setEchoMsg] = useState('')

  // 完成 / 保存
  const [cfg, setCfg] = useState<Record<string, any> | null>(null)
  const [cfgErr, setCfgErr] = useState('')
  const [finishing, setFinishing] = useState(false)
  const [finishMsg, setFinishMsg] = useState('')

  useEffect(() => {
    fetch(`${API_BASE}/api/config`)
      .then((r) => r.json())
      .then((j) => (j.ok ? setCfg(j.config) : setCfgErr('读取配置失败')))
      .catch(() => setCfgErr('后端未启动？请先启动后端，或先跳过向导'))
  }, [])

  const def = KEY_PROVIDERS.find((x) => x.id === providerId)!

  const recommendedMode: Mode = gpuTier === '16gb' || gpuTier === '24gb' ? 'local' : 'cloud'

  const changeTier = (tier: GpuTier) => {
    setGpuTier(tier)
    // 换档位 = 重新推荐；用户之后仍可手动改选方案
    setMode(tier === '16gb' || tier === '24gb' ? 'local' : 'cloud')
  }

  // 自动检测：用 WebGL 拿真实 GPU 渲染器名（不虚构硬件）；显存大小无法前端精确获取，引导手选档位
  const detectGpu = () => {
    if (gpuDetecting) return
    setGpuDetecting(true)
    setGpuName('')
    setGpuMsg('检测中…约需 5~10 秒，请稍候')
    window.setTimeout(() => {
      let name = ''
      try {
        const canvas = document.createElement('canvas')
        const gl = canvas.getContext('webgl')
        if (gl) {
          const ext = gl.getExtension('WEBGL_debug_renderer_info')
          if (ext) {
            const raw: unknown = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)
            if (typeof raw === 'string') name = raw
          }
        }
      } catch {
        name = ''
      }
      if (name) {
        setGpuName(name)
        setGpuMsg('已识别到显卡，请对照下方选择「显存档位」（显存大小需你确认）')
      } else {
        setGpuMsg('未能自动识别显卡，请在下方手动选择档位')
      }
      setGpuDetecting(false)
    }, 1500)
  }

  const changeProvider = (id: string) => {
    setProviderId(id)
    setTestOk(null)
    setTestMsg('')
  }

  const runTest = async () => {
    if (testing || !model.trim() || !apiKey.trim()) return
    setTesting(true)
    setTestMsg('正在连通测试…')
    try {
      const r = await fetch(`${API_BASE}/api/provider/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target: 'llm',
          model: { provider: def.cloudProvider, model: model.trim(), baseUrl: def.baseUrl, apiKey: apiKey.trim() },
        }),
      })
      const j = await r.json()
      setTestOk(!!j.ok)
      setTestMsg(j.ok ? `连通正常（${j.latency_ms ?? '?'} ms）` : j.msg || '测试失败')
    } catch {
      setTestOk(false)
      setTestMsg('连不上后端服务：请确认后端已启动')
    } finally {
      setTesting(false)
    }
  }

  const runEcho = async () => {
    if (echoBusy) return
    setEchoBusy(true)
    setEchoMsg('正在录音，请对着麦克风说一句话…（录完自动回放）')
    try {
      const r = await fetch(`${API_BASE}/api/mic/echo`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ duration: 3 }),
      })
      const j = await r.json()
      if (j.ok) {
        const peak = Number(j.peak || 0)
        setEchoMsg(peak < 500 ? `回放完成，但录到音量很低（峰值 ${peak}），麦克风可能没接好或被占用。` : `回放完成，录到音量正常（峰值 ${peak}），通路 OK。`)
      } else {
        setEchoMsg('回声测试失败：' + (j.msg || ''))
      }
    } catch {
      setEchoMsg('回声测试失败（网络错误）')
    } finally {
      setEchoBusy(false)
    }
  }

  const toggleWhite = (item: string) => {
    setWhitelist((prev) => (prev.includes(item) ? prev.filter((x) => x !== item) : [...prev, item]))
  }

  const addWhite = () => {
    const v = whiteInput.trim()
    if (v && !whitelist.includes(v)) setWhitelist((prev) => [...prev, v])
    setWhiteInput('')
  }

  const skip = () => {
    localStorage.setItem(MARKER_KEY, '1')
    onClose()
  }

  const finish = async () => {
    if (!cfg) {
      setFinishMsg('后端未连接，无法保存。请确认后端已启动，或先「跳过」。')
      return
    }
    setFinishing(true)
    setFinishMsg('')
    try {
      const asrCloud = mode === 'cloud' ? true : mode === 'local' ? false : custom.asr === 'cloud'
      const llmCloud = mode === 'cloud' ? true : mode === 'local' ? false : custom.llm === 'cloud'
      const visionCloud = mode === 'cloud' ? true : mode === 'local' ? false : custom.vision === 'cloud'
      const ttsCloud = mode === 'cloud' ? true : mode === 'local' ? false : custom.tts === 'cloud'

      // 写授权中心（12 项中的相关项；guard_outbound 保持默认开）
      const authSets: Array<[string, unknown]> = [
        ['cloud_asr', asrCloud],
        ['cloud_llm', llmCloud],
        ['cloud_vision', visionCloud],
        ['cloud_tts', ttsCloud],
        ['clipboard_read', clipScreen],
        ['screen_capture', clipScreen],
        ['emergency_passthrough', whitelist],
        ['guard_outbound', true],
      ]
      const authErrors = await Promise.all(
        authSets.map(async ([key, value]) => {
          try {
            const r = await fetch(`${API_BASE}/api/authorizations/set`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ key, value }),
            })
            const j = await r.json()
            return j.ok ? '' : j.msg || `${key} 保存失败`
          } catch {
            return `${key} 保存失败（网络错误）`
          }
        }),
      )
      const authErr = authErrors.find(Boolean) || ''

      // 联网（或自定义脑子选云）且已填 Key：把云端模型写入 config（复用领 Key → 保存逻辑）
      let cfgErr = ''
      if (llmCloud && model.trim() && apiKey.trim()) {
        const next = JSON.parse(JSON.stringify(cfg))
        const entry = {
          id: 'm_' + Date.now(),
          name: def.name,
          provider: def.id,
          model: model.trim(),
          baseUrl: def.baseUrl,
          apiKey: apiKey.trim(),
          temperature: 0.3,
        }
        next.llm = {
          ...(next.llm || {}),
          models: [...(next.llm?.models || []), entry],
          active: entry.id,
          provider: 'cloud',
          cloud: {
            ...(next.llm?.cloud || {}),
            provider: def.cloudProvider,
            base_url: entry.baseUrl,
            model: entry.model,
            api_key: entry.apiKey,
            temperature: 0.3,
          },
        }
        const r = await fetch(`${API_BASE}/api/config`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ updates: next }),
        })
        const j = await r.json()
        if (!j.ok) cfgErr = j.msg || '模型配置保存失败'
      }

      if (authErr || cfgErr) {
        setFinishMsg('保存失败：' + (authErr || cfgErr) + '（可稍后在「设置」里配置）')
        return
      }
      localStorage.setItem(MARKER_KEY, '1')
      onClose()
    } catch {
      setFinishMsg('保存失败（网络错误），可稍后在「设置」里配置')
    } finally {
      setFinishing(false)
    }
  }

  const showKeyStep = mode === 'cloud' || (mode === 'custom' && custom.llm === 'cloud')

  // 总结卡文案（第 6 步）
  const asrCloud = mode === 'cloud' ? true : mode === 'local' ? false : custom.asr === 'cloud'
  const llmCloud = mode === 'cloud' ? true : mode === 'local' ? false : custom.llm === 'cloud'
  const visionCloud = mode === 'cloud' ? true : mode === 'local' ? false : custom.vision === 'cloud'
  const ttsCloud = mode === 'cloud' ? true : mode === 'local' ? false : custom.tts === 'cloud'
  const summary = [
    `语音（听懂）→ ${asrCloud ? '发阿里云' : '走本地'}`,
    `对话（脑子）→ ${llmCloud ? '发 DeepSeek/通义' : '走本地'}`,
    `看图 → ${visionCloud ? '发云端视觉' : '走本地'}`,
    `说话 → ${ttsCloud ? '发微软/阿里云' : '走本地'}`,
    `剪贴板+截屏 → ${clipScreen ? '已开启' : '未开启'}`,
    `紧急白名单 → ${whitelist.length ? whitelist.join('、') : '空'}`,
  ]

  const renderKeyStep = () => (
    <div className="settings-fields">
      <label className="settings-field">
        <span className="settings-field-label">服务商</span>
        <select value={providerId} onChange={(e) => changeProvider(e.target.value)}>
          {KEY_PROVIDERS.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
      </label>
      <div className="settings-guide">
        <div>领取步骤（点下方链接直达官方页面）：</div>
        <ol className="wiz-ollist">
          {def.steps.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ol>
        <a className="btn" href={def.url} target="_blank" rel="noreferrer">🔗 打开 {def.url.replace('https://', '')}</a>
      </div>
      <label className="settings-field">
        <span className="settings-field-label">
          模型名（手填）
          <span className="settings-hint">以官方文档最新名称为准，如 {def.exampleModel}</span>
        </span>
        <input
          type="text"
          value={model}
          placeholder={`如 ${def.exampleModel}（以官方为准）`}
          onChange={(e) => { setModel(e.target.value); setTestOk(null) }}
        />
      </label>
      <label className="settings-field">
        <span className="settings-field-label">API Key</span>
        <input
          type="password"
          value={apiKey}
          placeholder="粘贴刚才领取的 Key（可留空稍后配置）"
          onChange={(e) => { setApiKey(e.target.value); setTestOk(null) }}
        />
      </label>
      <div className="settings-actions">
        <button className="btn" onClick={runTest} disabled={testing || !model.trim() || !apiKey.trim()}>
          {testing ? '测试中…' : '🔌 测试连通'}
        </button>
      </div>
      {testOk !== null && (
        <p className={`wiz-verdict ${testOk ? 'wiz-verdict--ok' : 'wiz-verdict--err'}`}>
          {testOk ? '✅ ' : '❌ '}{testMsg}
        </p>
      )}
      <p className="settings-hint">不填 Key 也可继续——基础功能开箱即用，Key 之后随时在「设置」里补。</p>
    </div>
  )

  const renderCustom = () => (
    <div className="settings-fields">
      {CUSTOM_ITEMS.map((it) => (
        <div key={it.key} className="settings-field settings-field--col">
          <span className="settings-field-label">
            {it.label}
            <a
              className="settings-hint"
              role="button"
              tabIndex={0}
              style={{ cursor: 'pointer', color: 'var(--cyan)' }}
              onClick={() => setPrivacyFor(it.key)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setPrivacyFor(it.key) } }}
            >
              🔗 隐私说明
            </a>
          </span>
          <div className="settings-multi">
            <button type="button" className={`settings-chip ${custom[it.key] === 'cloud' ? 'settings-chip--on' : ''}`} onClick={() => setCustom((c) => ({ ...c, [it.key]: 'cloud' }))}>
              ☁️ 云端：{it.cloud}
            </button>
            <button type="button" className={`settings-chip ${custom[it.key] === 'local' ? 'settings-chip--on' : ''}`} onClick={() => setCustom((c) => ({ ...c, [it.key]: 'local' }))}>
              🏠 本地：{it.local}
            </button>
          </div>
        </div>
      ))}
      {custom.llm === 'cloud' && (
        <p className="settings-hint">脑子选了云端 → 下方补一个领 Key（可留空）。</p>
      )}
    </div>
  )

  return (
    <div className="modal-overlay">
      <div className="modal modal--wiz" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-role">欢迎使用小二 · 隐私引导</span>
          <button className="modal-close" onClick={skip}>✕ 关闭</button>
        </div>

        <div className="wiz-steps">
          {WIZ_STEPS.map((s, i) => (
            <span key={s} className={`wiz-step ${i === step ? 'wiz-step--on' : ''} ${i < step ? 'wiz-step--done' : ''}`}>
              <i>{i < step ? '✓' : i + 1}</i>
              {s}
            </span>
          ))}
        </div>

        <div className="modal-body">
          {step === 0 && (
            <div className="settings-fields">
              <div className="settings-guide">
                你好，我是小二，你的桌面语音伙伴。接下来 2 分钟，我们定一件最重要的事：你的数据去哪儿。每步都能跳过，以后随时在设置里改。
              </div>
            </div>
          )}

          {step === 1 && (
            <div className="settings-fields">
              <div className="settings-guide">
                先看看你的显卡，我按显存给你推荐。也可以跳过这步，直接选方案。
              </div>

              <div className="settings-field">
                <span className="settings-field-label">检测方式</span>
                <div className="settings-multi">
                  <button type="button" className={`settings-chip ${gpuMethod === 'auto' ? 'settings-chip--on' : ''}`} onClick={() => setGpuMethod('auto')}>
                    🔍 自动检测
                  </button>
                  <button type="button" className={`settings-chip ${gpuMethod === 'manual' ? 'settings-chip--on' : ''}`} onClick={() => setGpuMethod('manual')}>
                    ✍️ 手动选择档位
                  </button>
                </div>
              </div>

              {gpuMethod === 'auto' ? (
                <div className="settings-field settings-field--col">
                  <div className="settings-actions">
                    <button className="btn" onClick={detectGpu} disabled={gpuDetecting}>
                      {gpuDetecting ? '检测中…' : '开始检测'}
                    </button>
                  </div>
                  {gpuName && <p className="settings-msg">显卡：{gpuName}</p>}
                  {gpuMsg && <p className="settings-hint">{gpuMsg}</p>}
                </div>
              ) : null}

              <div className="settings-field settings-field--col">
                <span className="settings-field-label">显存档位</span>
                <div className="settings-multi">
                  {GPU_TIERS.map((t) => (
                    <button key={t.value} type="button" className={`settings-chip ${gpuTier === t.value ? 'settings-chip--on' : ''}`} onClick={() => changeTier(t.value)}>
                      {t.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="wiz-cards">
                {MODES.map((m) => {
                  const rec = m.value === recommendedMode
                  return (
                    <button key={m.value} type="button" className={`wiz-card ${mode === m.value ? 'wiz-card--on' : ''}`} onClick={() => setMode(m.value)}>
                      <b>{rec ? '⭐ ' : ''}{m.icon} {m.title}</b>
                      <span>{m.desc}</span>
                      {m.note && <span>{m.note}</span>}
                    </button>
                  )
                })}
              </div>

              {(gpuTier === 'low' || gpuTier === '8gb' || gpuTier === 'igpu') && (
                <div className="settings-guide">
                  如实提示：本地模式跑「对话 Qwen3-8B + 说话 Piper」最舒服（约 6GB 显存）；要「本地看图」再加 MiniCPM-o 会超 8GB，只能轮流加载、切换慢。全本地全功能需 16GB 显存或 32GB 内存。
                </div>
              )}

              {mode === 'cloud' && (
                <>
                  <h4 className="perms-h">联网模式 · 领 Key（可留空，稍后在设置里补）</h4>
                  {renderKeyStep()}
                </>
              )}
              {mode === 'local' && (
                <div className="settings-guide">
                  已选本地模式：四项全部不出网。建议先配好本地引擎（Ollama Qwen3-8B / FunASR / Piper），再回「设置」里启用；开箱即用的免费云端兜底也可在设置里再开。
                </div>
              )}
              {mode === 'custom' && (
                <>
                  <h4 className="perms-h">逐项自定义 · 四项各自选</h4>
                  {renderCustom()}
                  {showKeyStep && renderKeyStep()}
                </>
              )}
            </div>
          )}

          {step === 2 && (
            <div className="settings-fields">
              <div className="settings-guide">
                这一步比较敏感，默认是关的。识别会跟着第 2 步选的模式走：联网 = 上云看，本地 = 本地看。
              </div>
              <label className="settings-field">
                <span className="settings-field-label">
                  允许我读剪贴板 + 截屏看屏幕
                  <span className="settings-hint">不勾 = 保持关闭（推荐）</span>
                </span>
                <input type="checkbox" checked={clipScreen} onChange={(e) => setClipScreen(e.target.checked)} />
              </label>
            </div>
          )}

          {step === 3 && (
            <div className="settings-fields">
              <div className="settings-guide">
                紧急白名单：命中这些词（如火警）时，允许小二即使被暂停也能提醒你。默认是空的，可留空。
              </div>
              <div className="settings-field settings-field--col">
                <span className="settings-field-label">预制项</span>
                <div className="settings-multi">
                  {PRESET_WHITELIST.map((w) => (
                    <button key={w} type="button" className={`settings-chip ${whitelist.includes(w) ? 'settings-chip--on' : ''}`} onClick={() => toggleWhite(w)}>
                      {w}
                    </button>
                  ))}
                </div>
              </div>
              <div className="settings-field">
                <span className="settings-field-label">手填条目</span>
                <input type="text" value={whiteInput} placeholder="如「老人跌倒」，回车添加" onChange={(e) => setWhiteInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addWhite() } }} />
              </div>
              <div className="settings-actions">
                <button className="btn" onClick={addWhite} disabled={!whiteInput.trim()}>＋ 添加</button>
              </div>
              {whitelist.length > 0 && <p className="settings-msg">已选：{whitelist.join('、')}</p>}
            </div>
          )}

          {step === 4 && (
            <div className="settings-fields">
              <div className="settings-guide">
                录一句话并自动回放，验证麦克风和扬声器通路。听不到回放？请检查系统默认音频设备，或稍后在「设置 → 音频」里换输入设备。
              </div>
              <div className="settings-actions">
                <button className="btn" onClick={runEcho} disabled={echoBusy}>
                  {echoBusy ? '录音中…' : '🎤 录一句试试'}
                </button>
              </div>
              {echoMsg && <p className="settings-msg">{echoMsg}</p>}
            </div>
          )}

          {step === 5 && (
            <div className="settings-fields">
              <div className="settings-guide">
                即将保存你的隐私设置。以后随时可在「设置 → 权限」里逐项改。
              </div>
              <div className="settings-guide">
                {summary.map((s) => (
                  <div key={s}>· {s}</div>
                ))}
              </div>
              {cfgErr && <p className="wiz-verdict wiz-verdict--err">❌ {cfgErr}</p>}
            </div>
          )}
        </div>

        <div className="wiz-foot">
          {step < WIZ_STEPS.length - 1 && (
            <button className="btn" onClick={skip}>跳过，先体验基础功能</button>
          )}
          <span className="settings-msg">{finishMsg}</span>
          <div className="wiz-foot-actions">
            {step > 0 && <button className="btn" onClick={() => setStep(step - 1)}>上一步</button>}
            {step < WIZ_STEPS.length - 1 ? (
              <button className="btn pri" onClick={() => setStep(step + 1)}>下一步</button>
            ) : (
              <button className="btn pri" disabled={finishing} onClick={finish}>{finishing ? '保存中…' : '保存并完成'}</button>
            )}
          </div>
        </div>
      </div>

      {privacyFor && PRIVACY_INFO[privacyFor] && (
        <div className="modal-overlay" onClick={() => setPrivacyFor(null)}>
          <div className="modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <span className="modal-role">{PRIVACY_INFO[privacyFor].title}</span>
              <button className="modal-close" onClick={() => setPrivacyFor(null)}>✕ 关闭</button>
            </div>
            <div className="modal-body">
              {PRIVACY_INFO[privacyFor].lines.map((l) => (
                <p key={l} className="settings-msg" style={{ margin: '6px 0' }}>{l}</p>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
