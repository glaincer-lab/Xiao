import { useEffect, useState } from 'react'
import { API_BASE } from '../api'

// 首次启动向导（E1）：选语言 → 领 Key → 连通测试 → 选大脑 → 测麦克风 → 完成；任何一步都可跳过进 L0。
// 「已完成」标记存本机 localStorage（xiao_onboarded），下次启动不再弹。
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

// E2a：领 Key 链接与图文步骤（点链接直达官方领取页；模型名不内置，使用者手填官方最新名）
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

const WIZ_STEPS = ['欢迎', '领 Key', '连通测试', '大脑', '麦克风', '完成']

const LANG_OPTIONS = [
  { value: 'zh', label: '中文（简体）', planned: false },
  { value: 'en', label: 'English', planned: true },
]

const ROUTER_OPTIONS = [
  { value: 'auto', label: '智能路由（推荐）', desc: '日常聊天走大模型，复杂任务自动交给 DSH 执行' },
  { value: 'chat', label: '只聊天', desc: '所有话都走大模型对话，不触发 DSH' },
  { value: 'dsh', label: '都交给 DSH', desc: '每句话都走 DSH 执行（适合把它当操作助手）' },
]

export function OnboardingWizard({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState(0)
  const [lang, setLang] = useState('zh')
  const [providerId, setProviderId] = useState('deepseek')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [testing, setTesting] = useState(false)
  const [testOk, setTestOk] = useState<boolean | null>(null)
  const [testMsg, setTestMsg] = useState('')
  const [routerMode, setRouterMode] = useState('auto')
  const [dshOk, setDshOk] = useState<boolean | null>(null)
  const [echoBusy, setEchoBusy] = useState(false)
  const [echoMsg, setEchoMsg] = useState('')
  const [cfg, setCfg] = useState<Record<string, any> | null>(null)
  const [cfgErr, setCfgErr] = useState('')
  const [finishing, setFinishing] = useState(false)
  const [finishMsg, setFinishMsg] = useState('')

  useEffect(() => {
    fetch(`${API_BASE}/api/config`)
      .then((r) => r.json())
      .then((j) => (j.ok ? setCfg(j.config) : setCfgErr('读取配置失败')))
      .catch(() => setCfgErr('后端未启动？请先启动后端，或先跳过向导'))
    fetch(`${API_BASE}/api/status`)
      .then((r) => r.json())
      .then((j) => setDshOk(!!j.dsh_available))
      .catch(() => setDshOk(false))
  }, [])

  const def = KEY_PROVIDERS.find((x) => x.id === providerId)!

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
      const next = JSON.parse(JSON.stringify(cfg))
      if (model.trim() && apiKey.trim()) {
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
      }
      next.router = { ...(next.router || {}), mode: routerMode }
      const r = await fetch(`${API_BASE}/api/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ updates: next }),
      })
      const j = await r.json()
      if (j.ok) {
        localStorage.setItem(MARKER_KEY, '1')
        onClose()
      } else {
        setFinishMsg('保存失败：' + (j.msg || '') + '（可稍后在「设置」里配置）')
      }
    } catch {
      setFinishMsg('保存失败（网络错误），可稍后在「设置」里配置')
    } finally {
      setFinishing(false)
    }
  }

  const canNext = step !== 2 || testOk === true
  const routerLabel = ROUTER_OPTIONS.find((o) => o.value === routerMode)?.label || routerMode
  const hasKey = !!model.trim() && !!apiKey.trim()

  return (
    <div className="modal-overlay">
      <div className="modal modal--wiz" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-role">欢迎使用小二 · 首次配置</span>
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
                三分钟完成初始配置：领一个大模型 Key（新用户有免费额度）就能畅快聊天；
                不配置也没关系——免费基础功能（本地唤醒 + 免费播报 + 规则指令）开箱即用。
              </div>
              <label className="settings-field">
                <span className="settings-field-label">界面语言</span>
                <select value={lang} onChange={(e) => setLang(e.target.value)}>
                  {LANG_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.planned ? '⬜ ' : '✅ '}{o.label}{o.planned ? '（即将支持）' : ''}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          )}

          {step === 1 && (
            <div className="settings-fields">
              <div className="wiz-cards">
                {KEY_PROVIDERS.map((p) => (
                  <button key={p.id} type="button" className={`wiz-card ${providerId === p.id ? 'wiz-card--on' : ''}`} onClick={() => changeProvider(p.id)}>
                    <b>{p.name}</b>
                    <span>新用户有免费额度</span>
                  </button>
                ))}
              </div>
              <div className="settings-guide">
                <div>领取步骤（点下方链接直达官方页面）：</div>
                <ol className="wiz-ollist">
                  {def.steps.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ol>
                <a className="btn" href={def.url} target="_blank" rel="noreferrer">🔗 打开 {def.url.replace('https://', '')}</a>
              </div>
              <p className="settings-hint">已有 Key？直接点「下一步」去填。</p>
            </div>
          )}

          {step === 2 && (
            <div className="settings-fields">
              <label className="settings-field">
                <span className="settings-field-label">服务商</span>
                <select value={providerId} onChange={(e) => changeProvider(e.target.value)}>
                  {KEY_PROVIDERS.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </label>
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
                  placeholder="粘贴刚才领取的 Key"
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
              {testOk === false && <p className="settings-hint">可修改后重试；也可以点下方「跳过」先体验基础功能。</p>}
            </div>
          )}

          {step === 3 && (
            <div className="settings-fields">
              <div className="wiz-cards">
                {ROUTER_OPTIONS.map((o) => (
                  <button key={o.value} type="button" className={`wiz-card ${routerMode === o.value ? 'wiz-card--on' : ''}`} onClick={() => setRouterMode(o.value)}>
                    <b>{o.label}</b>
                    <span>{o.desc}</span>
                  </button>
                ))}
              </div>
              <p className="settings-msg">
                {dshOk === null
                  ? '正在检测本机 DSH…'
                  : dshOk
                    ? '✅ 已检测到本机的 DSH（deepseek-harness），复杂任务可以放心交给它。'
                    : '⬜ 未检测到 DSH：不影响聊天；想要「操作电脑」这类执行能力时，按 README 引导安装 DSH 后重启小二即可。'}
              </p>
            </div>
          )}

          {step === 4 && (
            <div className="settings-fields">
              <div className="settings-guide">
                录一句话并自动回放，验证麦克风和扬声器通路。听不到回放？请检查系统默认音频设备，
                或稍后在「设置 → 音频」里换输入设备。
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
                {hasKey
                  ? `即将保存：模型 ${def.name} · ${model.trim()}；大脑模式「${routerLabel}」。`
                  : '未配置模型 Key（之后可随时在「设置」里配置），仅保存大脑模式「' + routerLabel + '」。'}
              </div>
              {cfgErr && <p className="wiz-verdict wiz-verdict--err">❌ {cfgErr}</p>}
              <p className="settings-msg">完成后随时可在「设置」里改方案、换音色、换模型。</p>
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
              <button className="btn pri" disabled={!canNext} onClick={() => setStep(step + 1)}>下一步</button>
            ) : (
              <button className="btn pri" disabled={finishing} onClick={finish}>{finishing ? '保存中…' : '保存并完成'}</button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
