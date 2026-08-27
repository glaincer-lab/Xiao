import { useEffect, useMemo, useState } from 'react'

type FieldOption = { value: string; label: string; status?: 'ok' | 'planned' }
type Field = {
  path: string
  label: string
  type: 'checkbox' | 'select' | 'slider' | 'number' | 'text' | 'textarea' | 'multiselect' | 'guide'
  group: string
  reload: 'soft' | 'restart'
  options?: FieldOption[]
  min?: number
  max?: number
  step?: number
  list?: boolean
  hint?: string
  show_if?: { path: string; value: string }
  guide?: string
}
type Group = { key: string; label: string }

type UISettings = {
  font: string
  tabularNums: boolean
  scale: number
  leftWidth: number
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

// 大模型供应商目录：添加模型式（供应商 → 手填模型名 → Key → 引导）
// 模型名只给「示例占位」，不维护会过时的权威列表；使用者手填官方最新名
type LLMProviderDef = {
  id: string
  name: string
  kind: 'cloud' | 'local' | 'omni'
  cloudProvider?: string
  baseUrl: string
  exampleModel: string
  needsKey: boolean
  status: 'ok' | 'planned'
  keyHint?: string
  recommend?: string
}
const LLM_PROVIDERS: LLMProviderDef[] = [
  { id: 'deepseek', name: 'DeepSeek', kind: 'cloud', cloudProvider: 'deepseek', baseUrl: 'https://api.deepseek.com/v1',
    exampleModel: 'deepseek-v4-pro', needsKey: true, status: 'ok', keyHint: '到 platform.deepseek.com 创建 API Key', recommend: '模型名以官方文档为准，手填最新名称' },
  { id: 'dashscope', name: '通义千问', kind: 'cloud', cloudProvider: 'dashscope', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    exampleModel: 'qwen3.8-max', needsKey: true, status: 'ok', keyHint: '到阿里云百炼（Model Studio）创建 API Key', recommend: '模型名以官方文档为准，手填最新名称' },
  { id: 'openai', name: 'OpenAI', kind: 'cloud', cloudProvider: 'openai', baseUrl: 'https://api.openai.com/v1',
    exampleModel: 'gpt-5', needsKey: true, status: 'ok', keyHint: '到 platform.openai.com 创建 API Key', recommend: '模型名以官方文档为准，手填最新名称' },
  { id: 'glm', name: '智谱 GLM', kind: 'cloud', cloudProvider: 'glm', baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    exampleModel: 'glm-4.6', needsKey: true, status: 'ok', keyHint: '到 open.bigmodel.cn（Z.ai）创建 API Key', recommend: '模型名以官方文档为准，手填最新名称' },
  { id: 'kimi', name: 'Kimi（月之暗面）', kind: 'cloud', cloudProvider: 'kimi', baseUrl: 'https://api.moonshot.cn/v1',
    exampleModel: 'kimi-k3', needsKey: true, status: 'ok', keyHint: '到 platform.moonshot.cn 创建 API Key', recommend: '模型名以官方文档为准，手填最新名称' },
  { id: 'ollama', name: '本地 Ollama', kind: 'local', baseUrl: 'http://localhost:11434/v1',
    exampleModel: 'qwen3', needsKey: false, status: 'ok', keyHint: '装好 Ollama 后执行 ollama pull <模型>，模型名填下方' },
]

// CosyVoice v3 音色（短名，flash/plus 通用；中文名展示，value 存短名）
const COSYVOICE_VOICES = [
  { value: 'longanyang', label: '龙安洋（男·阳光大男孩）' },
  { value: 'longanlang_v3', label: '龙安朗（男·清爽利落）' },
  { value: 'longanyun_v3', label: '龙安昀（男·居家暖男）' },
  { value: 'longze_v3', label: '龙泽（男·温暖元气）' },
  { value: 'longsanshu_v3', label: '龙三叔（男·沉稳质感）' },
  { value: 'longfei_v3', label: '龙飞（男·热血磁性）' },
  { value: 'longanhuan_v3', label: '龙安欢（女·四川话）' },
  { value: 'longxiaochun_v3', label: '龙小淳（女·知性积极）' },
  { value: 'longxiaoxia_v3', label: '龙小夏（女·沉稳权威）' },
  { value: 'longanwen_v3', label: '龙安温（女·优雅知性）' },
  { value: 'longanli_v3', label: '龙安莉（女·利落从容）' },
  { value: 'longwan_v3', label: '龙婉（女·细腻柔声）' },
  { value: 'longlaotie_v3', label: '龙老铁（东北话）' },
  { value: 'longanyue_v3', label: '龙安粤（粤语）' },
]
// Qwen-Audio-TTS 音色（存短名，运行时由后端拼 qwen-audio-3.0-tts-{flash|plus}- 前缀）
const QWEN_VOICES = [
  { value: 'longyingsongliu', label: '龙莹松柳（男·爽朗利落）' },
  { value: 'longlanyufu', label: '龙岚煜芙（男·温柔亲和）' },
  { value: 'longxinruixuan', label: '龙昕蕊璇（男·自然亲和）' },
  { value: 'longxiamuyan', label: '龙霞暮燕（男·专业解说）' },
  { value: 'longluliuche', label: '龙露柳澈（男·标准播音）' },
  { value: 'longbaixiuyun', label: '龙柏岫云（男·标准播音）' },
  { value: 'longyuzhihe', label: '龙羽芷荷（男·客观冷静）' },
  { value: 'longcanzhuyue', label: '龙璨竹月（女·平实质朴）' },
  { value: 'longrongzhihe', label: '龙蓉芷荷（女·电台质感）' },
  { value: 'longlanghongmo', label: '龙朗虹沫（女·温柔亲和）' },
  { value: 'longfengyueyao', label: '龙风月瑶（女·直爽利落）' },
  { value: 'longtongxuxian', label: '龙彤旭弦（女·活泼灵动）' },
  { value: 'longliuxulan', label: '龙柳旭澜（女·标准播音）' },
  { value: 'longyujunxuan', label: '龙羽珺萱（女·温柔坚韧）' },
]
// 档位下拉
const TIER_OPTIONS = [
  { value: 'flash', label: 'flash（快、省钱）' },
  { value: 'plus', label: 'plus（音质更好）' },
]

// 已保存的模型条目（多模型管理列表）
type SavedModel = {
  id: string
  name: string
  provider: string
  model: string
  baseUrl: string
  apiKey?: string
  temperature?: number
}
// 已保存的识别方案（识别多方案管理）
type SavedASR = {
  id: string
  name: string
  provider: 'cloud' | 'local' | 'omni'
  model: string
  apiKey?: string
  localEngine?: string
  localModelDir?: string
}
// 已保存的播报方案（播报多方案管理）
type SavedTTS = {
  id: string
  name: string
  provider: 'edge' | 'cosyvoice' | 'qwen' | 'piper' | 'omni'
  voice: string
  rate: string
  tier: 'flash' | 'plus'
  apiKey?: string
  piperModel?: string
}
// 已保存的唤醒方案（唤醒多方案管理）
type SavedWake = {
  id: string
  name: string
  engine: 'sherpa' | 'omni'
  keyword: string
  pinyin: string
  threshold: number
  modelDir: string
}

const EXTRA_TABS: Group[] = [
  { key: 'audio', label: '音频' },
  { key: 'ui', label: '界面' },
]

function getPath(obj: Record<string, any>, path: string): any {
  return path.split('.').reduce((o: any, k) => (o == null ? undefined : o[k]), obj)
}

function setPath(obj: Record<string, any>, path: string, value: any): Record<string, any> {
  const next = JSON.parse(JSON.stringify(obj))
  const parts = path.split('.')
  let node = next
  for (let i = 0; i < parts.length - 1; i++) {
    if (typeof node[parts[i]] !== 'object' || node[parts[i]] === null) node[parts[i]] = {}
    node = node[parts[i]]
  }
  node[parts[parts.length - 1]] = value
  return next
}

export function SettingsPanel({ onClose, ui, setUi }: { onClose: () => void; ui: UISettings; setUi: (u: UISettings) => void }) {
  const [groups, setGroups] = useState<Group[]>([])
  const [fields, setFields] = useState<Field[]>([])
  const [config, setConfig] = useState<Record<string, any> | null>(null)
  const [tab, setTab] = useState('wake')
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  const [inputs, setInputs] = useState<{ index: number; name: string; is_default: boolean }[]>([])
  const [previewText, setPreviewText] = useState('你好，欢迎使用语音助手。')
  const [voices, setVoices] = useState<FieldOption[]>([])
  const [voicesLoading, setVoicesLoading] = useState(false)
  const [echoBusy, setEchoBusy] = useState(false)
  const [echoMsg, setEchoMsg] = useState('')
  const [showAddModel, setShowAddModel] = useState(false)
  const [addForm, setAddForm] = useState({ provider: 'deepseek', model: 'deepseek-v4-pro', name: '', apiKey: '', baseUrl: 'https://api.deepseek.com/v1', temperature: 0.3 })
  const [editModelId, setEditModelId] = useState<string | null>(null)
  const [showAddASR, setShowAddASR] = useState(false)
  const [asrForm, setAsrForm] = useState<{ provider: 'cloud' | 'local' | 'omni'; model: string; name: string; apiKey: string; localEngine: string; localModelDir: string }>({ provider: 'cloud', model: 'fun-asr-flash-8k-realtime', name: '', apiKey: '', localEngine: 'funasr', localModelDir: '' })
  const [editASRId, setEditASRId] = useState<string | null>(null)
  const [showAddTTS, setShowAddTTS] = useState(false)
  const [ttsForm, setTtsForm] = useState<{ provider: 'edge' | 'cosyvoice' | 'qwen' | 'piper' | 'omni'; voice: string; rate: string; name: string; apiKey: string; tier: 'flash' | 'plus'; piperModel: string }>({ provider: 'edge', voice: 'zh-CN-YunjianNeural', rate: '+30%', name: '', apiKey: '', tier: 'flash', piperModel: 'models/zh_CN-huayan-medium.onnx' })
  const [editTTSId, setEditTTSId] = useState<string | null>(null)
  const [showAddWake, setShowAddWake] = useState(false)
  const [wakeForm, setWakeForm] = useState<{ engine: 'sherpa' | 'omni'; keyword: string; pinyin: string; threshold: number; modelDir: string; name: string; baseUrl: string; model: string }>({ engine: 'sherpa', keyword: '小二', pinyin: 'x iǎo èr', threshold: 0.25, modelDir: '', name: '', baseUrl: 'http://localhost:8000/v1', model: 'openbmb/MiniCPM-o-4_5' })
  useEffect(() => {
    fetch('http://127.0.0.1:8123/api/config/schema')
      .then((r) => r.json())
      .then((j) => {
        if (j.ok) {
          setGroups(j.groups || [])
          setFields(j.fields || [])
        } else setMsg('读取设置结构失败')
      })
      .catch(() => setMsg('读取设置结构失败（后端未启动？）'))

    fetch('http://127.0.0.1:8123/api/config')
      .then((r) => r.json())
      .then((j) => (j.ok ? setConfig(j.config) : setMsg('读取配置失败')))
      .catch(() => setMsg('读取配置失败（后端未启动？）'))

    fetch('http://127.0.0.1:8123/api/audio/devices')
      .then((r) => r.json())
      .then((j) => (j.ok ? setInputs(j.inputs || []) : null))
      .catch(() => null)
  }, [])

  const fieldsByGroup = useMemo(() => {
    const m: Record<string, Field[]> = {}
    for (const f of fields) (m[f.group] ||= []).push(f)
    return m
  }, [fields])

  const setField = (path: string, value: any) => {
    setConfig((prev) => (prev ? setPath(prev, path, value) : prev))
  }

  const save = async () => {
    if (!config) return
    setSaving(true)
    setMsg('')
    // 统计本次是否动了「需重启」的字段
    const restartChanged = fields.some((f) => f.reload === 'restart')
    try {
      const r = await fetch('http://127.0.0.1:8123/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ updates: config }),
      })
      const j = await r.json()
      if (j.ok) {
        setMsg(restartChanged ? '已保存。含引擎类设置，需重启后端生效。' : '已保存，软配置即时生效。')
      } else {
        setMsg('保存失败：' + (j.msg || ''))
      }
    } catch {
      setMsg('保存失败（网络错误）')
    } finally {
      setSaving(false)
    }
  }

  const preview = async () => {
    const voice = getPath(config || {}, 'tts.voice')
    const rate = getPath(config || {}, 'tts.rate')
    setMsg('正在合成试听…')
    try {
      await fetch('http://127.0.0.1:8123/api/tts/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice, rate, text: previewText }),
      })
      setMsg('试听播放完毕。')
    } catch {
      setMsg('试听失败（网络错误）')
    }
  }

  const clearMemory = async () => {
    try {
      const r = await fetch('http://127.0.0.1:8123/api/memory/clear', { method: 'POST' })
      const j = await r.json()
      setMsg(j.ok ? '对话记忆已清空。' : '清空失败：' + (j.msg || ''))
    } catch {
      setMsg('清空失败（网络错误）')
    }
  }

  const loadVoices = async () => {
    setVoicesLoading(true)
    setMsg('正在拉取音色列表…')
    try {
      const r = await fetch('http://127.0.0.1:8123/api/tts/voices')
      const j = await r.json()
      if (j.ok && Array.isArray(j.voices) && j.voices.length > 0) {
        setVoices(j.voices)
        setMsg(`已加载 ${j.voices.length} 个中文音色。`)
      } else {
        setMsg('拉取音色失败：' + (j.msg || '无结果'))
      }
    } catch {
      setMsg('拉取音色失败（网络错误）')
    } finally {
      setVoicesLoading(false)
    }
  }

  const runEcho = async () => {
    setEchoBusy(true)
    setEchoMsg('正在录音，请对麦克风说话…（录完自动回放）')
    try {
      const r = await fetch('http://127.0.0.1:8123/api/mic/echo', {
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

  const visible = (f: Field) => {
    if (!f.show_if) return true
    return config ? getPath(config, f.show_if.path) === f.show_if.value : false
  }

  const renderField = (f: Field) => {
    const value = config ? getPath(config, f.path) : undefined
    const common = { key: f.path }

    if (f.type === 'checkbox') {
      return (
        <label className="settings-field" {...common}>
          <span className="settings-field-label">
            {f.label}
            {f.hint && <span className="settings-hint">{f.hint}</span>}
          </span>
          <input type="checkbox" checked={!!value} onChange={(e) => setField(f.path, e.target.checked)} />
        </label>
      )
    }
    if (f.type === 'select') {
      // tts.voice 优先用动态拉取的完整音色列表（更多/更新），未拉取时回退 schema 硬编码
      const opts = f.path === 'tts.voice' && voices.length > 0 ? voices : (f.options || [])
      return (
        <label className="settings-field" {...common}>
          <span className="settings-field-label">
            {f.label}
            {f.hint && <span className="settings-hint">{f.hint}</span>}
          </span>
          <select value={String(value ?? '')} onChange={(e) => setField(f.path, e.target.value)}>
            {opts.map((o) => (
              <option key={o.value} value={o.value}>
                {o.status === 'planned' ? '⬜ ' : o.status === 'ok' ? '✅ ' : ''}{o.label}
              </option>
            ))}
          </select>
        </label>
      )
    }
    if (f.type === 'slider') {
      const n = Number(value ?? f.min ?? 0)
      return (
        <label className="settings-field" {...common}>
          <span className="settings-field-label">
            {f.label}
            {f.hint && <span className="settings-hint">{f.hint}</span>}
          </span>
          <span className="settings-range">
            <input type="range" min={f.min} max={f.max} step={f.step} value={n} onChange={(e) => setField(f.path, Number(e.target.value))} />
            <em className="settings-val">{n}</em>
          </span>
        </label>
      )
    }
    if (f.type === 'number') {
      return (
        <label className="settings-field" {...common}>
          <span className="settings-field-label">
            {f.label}
            {f.hint && <span className="settings-hint">{f.hint}</span>}
          </span>
          <input type="number" min={f.min} max={f.max} value={value ?? ''} onChange={(e) => setField(f.path, Number(e.target.value))} />
        </label>
      )
    }
    if (f.type === 'textarea') {
      // list 类型：值可能是数组，编辑时按「每行一个」拆分保存
      const text = Array.isArray(value) ? value.join('\n') : String(value ?? '')
      return (
        <label className="settings-field settings-field--col" {...common}>
          <span className="settings-field-label">
            {f.label}
            {f.hint && <span className="settings-hint">{f.hint}</span>}
          </span>
          <textarea
            className="settings-textarea"
            rows={4}
            value={text}
            onChange={(e) => {
              const v = e.target.value
              setField(f.path, f.list ? v.split('\n').map((s) => s.trim()).filter(Boolean) : v)
            }}
          />
        </label>
      )
    }
    if (f.type === 'multiselect') {
      const arr: string[] = Array.isArray(value) ? value : []
      return (
        <div className="settings-field settings-field--col" {...common}>
          <span className="settings-field-label">
            {f.label}
            {f.hint && <span className="settings-hint">{f.hint}</span>}
          </span>
          <div className="settings-multi">
            {(f.options || []).map((o) => {
              const on = arr.includes(o.value)
              return (
                <button
                  key={o.value}
                  type="button"
                  className={`settings-chip ${on ? 'settings-chip--on' : ''}`}
                  onClick={() => setField(f.path, on ? arr.filter((x) => x !== o.value) : [...arr, o.value])}
                >
                  {o.label}
                </button>
              )
            })}
          </div>
        </div>
      )
    }
    if (f.type === 'guide') {
      return (
        <div className="settings-guide" {...common}>
          {f.guide}
        </div>
      )
    }
    // text
    return (
      <label className="settings-field" {...common}>
        <span className="settings-field-label">
          {f.label}
          {f.hint && <span className="settings-hint">{f.hint}</span>}
        </span>
        <input type="text" value={value ?? ''} onChange={(e) => setField(f.path, e.target.value)} />
      </label>
    )
  }

  const renderLLMConfig = () => {
    const savedModels: SavedModel[] = config ? (getPath(config, 'llm.models') || []) : []
    const activeId = config ? (getPath(config, 'llm.active') || '') : ''
    const activeModel = savedModels.find((m) => m.id === activeId)
    const formDef = LLM_PROVIDERS.find((x) => x.id === addForm.provider)

    // 把某个模型条目写入「当前生效」字段（后端 factory 读这些）
    const applyModel = (m: SavedModel) => {
      const def = LLM_PROVIDERS.find((x) => x.id === m.provider)
      const kind = def?.kind || 'cloud'
      setField('llm.active', m.id)
      setField('llm.provider', kind)
      if (kind === 'cloud') {
        setField('llm.cloud.provider', def?.cloudProvider || m.provider)
        setField('llm.cloud.base_url', m.baseUrl)
        setField('llm.cloud.model', m.model)
        setField('llm.cloud.api_key', m.apiKey || '')
        setField('llm.cloud.temperature', m.temperature ?? 0.3)
      } else if (kind === 'local') {
        setField('llm.local.base_url', m.baseUrl)
        setField('llm.local.model', m.model)
        setField('llm.local.temperature', m.temperature ?? 0.3)
      } else {
        setField('llm.omni.base_url', m.baseUrl)
        setField('llm.omni.model', m.model)
      }
    }

    const updateModel = (id: string, patch: Partial<SavedModel>) => {
      const next = savedModels.map((m) => (m.id === id ? { ...m, ...patch } : m))
      setField('llm.models', next)
      const updated = next.find((m) => m.id === id)
      if (updated && activeId === id) applyModel(updated)
    }

    const startNewModel = () => {
      setEditModelId(null)
      setAddForm({ provider: 'deepseek', model: 'deepseek-v4-pro', name: '', apiKey: '', baseUrl: 'https://api.deepseek.com/v1', temperature: 0.3 })
      setShowAddModel(true)
    }

    const startEditModel = (m: SavedModel) => {
      setEditModelId(m.id)
      setAddForm({ provider: m.provider, model: m.model, name: m.name, apiKey: m.apiKey || '', baseUrl: m.baseUrl, temperature: m.temperature ?? 0.3 })
      setShowAddModel(true)
    }

    const saveModelForm = () => {
      const def = LLM_PROVIDERS.find((x) => x.id === addForm.provider)
      if (editModelId) {
        updateModel(editModelId, {
          name: addForm.name || def?.name || addForm.provider,
          provider: addForm.provider,
          model: addForm.model,
          baseUrl: addForm.baseUrl || def?.baseUrl || '',
          apiKey: addForm.apiKey,
          temperature: addForm.temperature,
        })
      } else {
        const m: SavedModel = {
          id: 'm_' + Date.now(),
          name: addForm.name || def?.name || addForm.provider,
          provider: addForm.provider,
          model: addForm.model,
          baseUrl: addForm.baseUrl || def?.baseUrl || '',
          apiKey: addForm.apiKey,
          temperature: addForm.temperature,
        }
        setField('llm.models', [...savedModels, m])
        applyModel(m)
      }
      setShowAddModel(false)
      setEditModelId(null)
    }

    const removeModel = (id: string) => {
      const next = savedModels.filter((m) => m.id !== id)
      setField('llm.models', next)
      if (activeId === id) {
        setField('llm.active', next.length ? next[0].id : '')
        if (next.length) applyModel(next[0])
      }
    }

    return (
      <>
        <div className="settings-actions">
          <button className="btn" onClick={startNewModel}>＋ 新建模型</button>
        </div>
        {showAddModel && (
          <div className="modal-overlay" onClick={() => { setShowAddModel(false); setEditModelId(null) }}>
            <div className="modal settings-form-modal" onClick={(e) => e.stopPropagation()}>
              <div className="modal-head">
                <span className="modal-role">{editModelId ? '编辑模型' : '新建模型'}</span>
                <button className="modal-close" onClick={() => { setShowAddModel(false); setEditModelId(null) }}>✕ 关闭</button>
              </div>
              <div className="modal-body">
                <div className="settings-fields">
            <label className="settings-field">
              <span className="settings-field-label">供应商</span>
              <select value={addForm.provider} onChange={(e) => {
                const def = LLM_PROVIDERS.find((x) => x.id === e.target.value)
                setAddForm({ ...addForm, provider: e.target.value, model: def?.exampleModel || '', baseUrl: def?.baseUrl || '' })
              }}>
                {LLM_PROVIDERS.map((x) => (
                  <option key={x.id} value={x.id}>{x.status === 'planned' ? '⬜ ' : '✅ '}{x.name}</option>
                ))}
              </select>
            </label>

            <label className="settings-field">
              <span className="settings-field-label">模型名称（手填）</span>
              <input type="text" value={addForm.model} placeholder={`如 ${formDef?.exampleModel || '模型名'}（以官方为准）`} onChange={(e) => setAddForm({ ...addForm, model: e.target.value })} />
            </label>

            {formDef?.needsKey && (
              <label className="settings-field">
                <span className="settings-field-label">API Key</span>
                <input type="password" value={addForm.apiKey} placeholder="sk-..." onChange={(e) => setAddForm({ ...addForm, apiKey: e.target.value })} />
              </label>
            )}

            <label className="settings-field">
              <span className="settings-field-label">接口地址</span>
              <input type="text" value={addForm.baseUrl} onChange={(e) => setAddForm({ ...addForm, baseUrl: e.target.value })} />
            </label>

            <label className="settings-field">
              <span className="settings-field-label">名称（可选）</span>
              <input type="text" value={addForm.name} placeholder="如「我的 DeepSeek」" onChange={(e) => setAddForm({ ...addForm, name: e.target.value })} />
            </label>

            {formDef?.kind !== 'omni' && (
              <label className="settings-field">
                <span className="settings-field-label">随机度</span>
                <span className="settings-range">
                  <input type="range" min={0} max={1} step={0.05} value={addForm.temperature} onChange={(e) => setAddForm({ ...addForm, temperature: Number(e.target.value) })} />
                  <em className="settings-val">{addForm.temperature}</em>
                </span>
              </label>
            )}

            {(formDef?.keyHint || formDef?.recommend) && (
              <div className="settings-guide">
                {formDef?.keyHint && <div>{formDef.keyHint}</div>}
                {formDef?.recommend && <div>{formDef.recommend}</div>}
              </div>
            )}

            <div className="settings-actions">
              <button className="btn" onClick={saveModelForm}>保存</button>
              <button className="btn" onClick={() => { setShowAddModel(false); setEditModelId(null) }}>取消</button>
            </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* 下面：已保存模型 */}
        <label className="settings-field">
          <span className="settings-field-label">已保存模型</span>
          <div className="scheme-list">
            {savedModels.length === 0 && <p className="settings-msg">还没有模型，点上方「新建模型」。</p>}
            {savedModels.map((m) => {
              const isActive = m.id === activeId
              const def = LLM_PROVIDERS.find((x) => x.id === m.provider)
              return (
                <div key={m.id} className={`scheme-item ${isActive ? 'scheme-item--on' : ''}`}>
                  <div className="scheme-item-main" onClick={() => applyModel(m)}>
                    <span className="scheme-item-icon">{def?.status === 'ok' ? '✅' : '⬜'}</span>
                    <div className="scheme-item-info">
                      <div className="scheme-item-name">{m.name}{isActive && <span className="scheme-item-tag">当前</span>}</div>
                      <div className="scheme-item-sub">{def?.name || m.provider} · {m.model}</div>
                    </div>
                  </div>
                  <div className="scheme-item-actions">
                    {!isActive && <button type="button" className="btn btn--sm" onClick={() => applyModel(m)}>设为当前</button>}
                    <button type="button" className="btn btn--sm" onClick={() => startEditModel(m)}>编辑</button>
                    <button type="button" className="btn btn--sm" onClick={() => removeModel(m.id)}>删除</button>
                  </div>
                </div>
              )
            })}
          </div>
        </label>

        {activeModel && (
          <label className="settings-field">
            <span className="settings-field-label">随机度</span>
            <span className="settings-range">
              <input type="range" min={0} max={1} step={0.05} value={activeModel.temperature ?? 0.3} onChange={(e) => updateModel(activeId, { temperature: Number(e.target.value) })} />
              <em className="settings-val">{activeModel.temperature ?? 0.3}</em>
            </span>
          </label>
        )}
      </>
    )
  }

  const renderASRConfig = () => {
    const savedASR: SavedASR[] = config ? (getPath(config, 'asr.models') || []) : []
    const activeASR = config ? (getPath(config, 'asr.active') || '') : ''
    const activeASRScheme = savedASR.find((m) => m.id === activeASR)

    const applyASR = (m: SavedASR) => {
      setField('asr.active', m.id)
      setField('asr.provider', m.provider)
      if (m.provider === 'cloud') {
        setField('asr.cloud.provider', 'aliyun')
        setField('asr.cloud.model', m.model)
        setField('asr.cloud.api_key', m.apiKey || '')
      } else if (m.provider === 'local') {
        setField('asr.local.engine', m.localEngine || 'funasr')
        setField('asr.local.model', m.model)
        setField('asr.local.model_dir', m.localModelDir || '')
      }
    }

    const updateASR = (id: string, patch: Partial<SavedASR>) => {
      const next = savedASR.map((m) => (m.id === id ? { ...m, ...patch } : m))
      setField('asr.models', next)
      const updated = next.find((m) => m.id === id)
      if (updated && activeASR === id) applyASR(updated)
    }

    const startNewASR = () => {
      setEditASRId(null)
      setAsrForm({ provider: 'cloud', model: 'fun-asr-flash-8k-realtime', name: '', apiKey: '', localEngine: 'funasr', localModelDir: '' })
      setShowAddASR(true)
    }

    const startEditASR = (m: SavedASR) => {
      setEditASRId(m.id)
      setAsrForm({ provider: m.provider, model: m.model, name: m.name, apiKey: m.apiKey || '', localEngine: m.localEngine || 'funasr', localModelDir: m.localModelDir || '' })
      setShowAddASR(true)
    }

    const saveASRForm = () => {
      if (editASRId) {
        updateASR(editASRId, {
          name: asrForm.name || '识别方案',
          provider: asrForm.provider,
          model: asrForm.model,
          apiKey: asrForm.apiKey,
          localEngine: asrForm.localEngine,
          localModelDir: asrForm.localModelDir,
        })
      } else {
        const m: SavedASR = {
          id: 'a_' + Date.now(),
          name: asrForm.name || (asrForm.provider === 'cloud' ? '云端识别' : asrForm.provider === 'local' ? '本地识别' : '一体化识别'),
          provider: asrForm.provider,
          model: asrForm.model,
          apiKey: asrForm.apiKey,
          localEngine: asrForm.localEngine,
          localModelDir: asrForm.localModelDir,
        }
        setField('asr.models', [...savedASR, m])
        applyASR(m)
      }
      setShowAddASR(false)
      setEditASRId(null)
    }

    const removeASR = (id: string) => {
      const next = savedASR.filter((m) => m.id !== id)
      setField('asr.models', next)
      if (activeASR === id) {
        setField('asr.active', next.length ? next[0].id : '')
        if (next.length) applyASR(next[0])
      }
    }

    return (
      <>
        <div className="settings-actions">
          <button className="btn" onClick={startNewASR}>＋ 新建识别方案</button>
        </div>
        {showAddASR && (
          <div className="modal-overlay" onClick={() => { setShowAddASR(false); setEditASRId(null) }}>
            <div className="modal settings-form-modal" onClick={(e) => e.stopPropagation()}>
              <div className="modal-head">
                <span className="modal-role">{editASRId ? '编辑方案' : '新建方案'}</span>
                <button className="modal-close" onClick={() => { setShowAddASR(false); setEditASRId(null) }}>✕ 关闭</button>
              </div>
              <div className="modal-body">
                <div className="settings-fields">
            <label className="settings-field">
              <span className="settings-field-label">识别方式</span>
              <select value={asrForm.provider} onChange={(e) => {
                const p = e.target.value as 'cloud' | 'local' | 'omni'
                setAsrForm({ ...asrForm, provider: p, model: p === 'cloud' ? 'fun-asr-flash-8k-realtime' : p === 'local' ? 'paraformer-zh' : '' })
              }}>
                <option value="cloud">✅ 云端（Fun-ASR / Qwen）</option>
                <option value="local">✅ 本地 FunASR</option>
                <option value="omni">⬜ 一体化 MiniCPM-o（本地）</option>
              </select>
            </label>

            {asrForm.provider === 'cloud' && (
              <>
                <label className="settings-field">
                  <span className="settings-field-label">云端模型</span>
                  <select value={asrForm.model} onChange={(e) => setAsrForm({ ...asrForm, model: e.target.value })}>
                    <option value="fun-asr-flash-8k-realtime">✅ Fun-ASR（方言：重庆话/四川话/粤语，8k）</option>
                    <option value="qwen-audio-3.0-asr-flash-streaming">✅ Qwen-Audio（普通话+方言，16k）</option>
                    <option value="qwen3-asr-flash-realtime">✅ Qwen3-ASR（普通话，16k）</option>
                  </select>
                </label>
                <label className="settings-field">
                  <span className="settings-field-label">API Key</span>
                  <input type="password" value={asrForm.apiKey} placeholder="阿里云百炼 Key（留空读环境变量）" onChange={(e) => setAsrForm({ ...asrForm, apiKey: e.target.value })} />
                </label>
              </>
            )}

            {asrForm.provider === 'local' && (
              <>
                <label className="settings-field">
                  <span className="settings-field-label">本地引擎</span>
                  <select value={asrForm.localEngine} onChange={(e) => setAsrForm({ ...asrForm, localEngine: e.target.value })}>
                    <option value="funasr">FunASR</option>
                  </select>
                </label>
                <label className="settings-field">
                  <span className="settings-field-label">本地模型（手填）</span>
                  <input type="text" value={asrForm.model} placeholder="如 paraformer-zh（以官方为准）" onChange={(e) => setAsrForm({ ...asrForm, model: e.target.value })} />
                </label>
                <label className="settings-field">
                  <span className="settings-field-label">本地模型目录</span>
                  <input type="text" value={asrForm.localModelDir} placeholder="自行下载的模型目录，留空自动获取" onChange={(e) => setAsrForm({ ...asrForm, localModelDir: e.target.value })} />
                </label>
              </>
            )}

            {asrForm.provider === 'omni' && (
              <div className="settings-guide">一体化 MiniCPM-o 会接管识别。请配置 omni 服务地址（config.yaml 的 llm.omni）。</div>
            )}

            <label className="settings-field">
              <span className="settings-field-label">名称（可选）</span>
              <input type="text" value={asrForm.name} placeholder="如「普通话识别」「重庆话识别」" onChange={(e) => setAsrForm({ ...asrForm, name: e.target.value })} />
            </label>

            <div className="settings-actions">
              <button className="btn" onClick={saveASRForm}>保存</button>
              <button className="btn" onClick={() => { setShowAddASR(false); setEditASRId(null) }}>取消</button>
            </div>
                </div>
              </div>
            </div>
          </div>
        )}

        <label className="settings-field">
          <span className="settings-field-label">已保存方案</span>
          <div className="scheme-list">
            {savedASR.length === 0 && <p className="settings-msg">还没有方案，点上方「新建方案」。</p>}
            {savedASR.map((m) => {
              const isActive = m.id === activeASR
              return (
                <div key={m.id} className={`scheme-item ${isActive ? 'scheme-item--on' : ''}`}>
                  <div className="scheme-item-main" onClick={() => applyASR(m)}>
                    <span className="scheme-item-icon">{m.provider === 'omni' ? '⬜' : '✅'}</span>
                    <div className="scheme-item-info">
                      <div className="scheme-item-name">{m.name}{isActive && <span className="scheme-item-tag">当前</span>}</div>
                      <div className="scheme-item-sub">{m.provider === 'cloud' ? '云端' : m.provider === 'local' ? '本地' : '一体化'} · {m.model}</div>
                    </div>
                  </div>
                  <div className="scheme-item-actions">
                    {!isActive && <button type="button" className="btn btn--sm" onClick={() => applyASR(m)}>设为当前</button>}
                    <button type="button" className="btn btn--sm" onClick={() => startEditASR(m)}>编辑</button>
                    <button type="button" className="btn btn--sm" onClick={() => removeASR(m.id)}>删除</button>
                  </div>
                </div>
              )
            })}
          </div>
        </label>
      </>
    )
  }

  const renderTTSConfig = () => {
    const savedTTS: SavedTTS[] = config ? (getPath(config, 'tts.models') || []) : []
    const activeTTS = config ? (getPath(config, 'tts.active') || '') : ''
    const voiceField = fields.find((f) => f.path === 'tts.voice')
    const rateField = fields.find((f) => f.path === 'tts.rate')
    const voiceOptions: FieldOption[] = voices.length > 0 ? voices : (voiceField?.options || [])
    const rateOptions: FieldOption[] = rateField?.options || []
    const activeScheme = savedTTS.find((m) => m.id === activeTTS)

    const applyTTS = (m: SavedTTS) => {
      setField('tts.active', m.id)
      setField('tts.provider', m.provider)
      if (m.provider === 'edge') {
        setField('tts.voice', m.voice)
        setField('tts.rate', m.rate)
      }
    }

    const updateTTS = (id: string, patch: Partial<SavedTTS>) => {
      const next = savedTTS.map((m) => (m.id === id ? { ...m, ...patch } : m))
      setField('tts.models', next)
      const updated = next.find((m) => m.id === id)
      if (updated && activeTTS === id) applyTTS(updated)
    }

    const providerName = (p: string) => p === 'edge' ? 'edge-tts' : p === 'cosyvoice' ? 'CosyVoice v3' : p === 'qwen' ? 'Qwen-Audio-TTS' : p === 'piper' ? '本地 Piper' : 'MiniCPM-o'

    const startNewTTS = () => {
      setEditTTSId(null)
      setTtsForm({ provider: 'edge', voice: 'zh-CN-YunjianNeural', rate: '+30%', name: '', apiKey: '', tier: 'flash', piperModel: 'models/zh_CN-huayan-medium.onnx' })
      setShowAddTTS(true)
    }

    const startEditTTS = (m: SavedTTS) => {
      setEditTTSId(m.id)
      setTtsForm({ provider: m.provider, voice: m.voice, rate: m.rate, name: m.name, apiKey: m.apiKey || '', tier: m.tier || 'flash', piperModel: m.piperModel || 'models/zh_CN-huayan-medium.onnx' })
      setShowAddTTS(true)
    }

    const saveTTSForm = () => {
      if (editTTSId) {
        updateTTS(editTTSId, {
          name: ttsForm.name || '方案',
          provider: ttsForm.provider,
          voice: ttsForm.voice,
          rate: ttsForm.rate,
          tier: ttsForm.tier,
          apiKey: ttsForm.apiKey,
          piperModel: ttsForm.piperModel,
        })
      } else {
        const m: SavedTTS = {
          id: 't_' + Date.now(),
          name: ttsForm.name || providerName(ttsForm.provider),
          provider: ttsForm.provider,
          voice: ttsForm.voice,
          rate: ttsForm.rate,
          tier: ttsForm.tier,
          apiKey: ttsForm.apiKey,
          piperModel: ttsForm.piperModel,
        }
        setField('tts.models', [...savedTTS, m])
        applyTTS(m)
      }
      setShowAddTTS(false)
      setEditTTSId(null)
    }

    const removeTTS = (id: string) => {
      const next = savedTTS.filter((m) => m.id !== id)
      setField('tts.models', next)
      if (activeTTS === id) {
        setField('tts.active', next.length ? next[0].id : '')
        if (next.length) applyTTS(next[0])
      }
    }

    return (
      <>
        <div className="settings-actions">
          <button className="btn" onClick={startNewTTS}>＋ 新建方案</button>
        </div>
        {showAddTTS && (
          <div className="modal-overlay" onClick={() => { setShowAddTTS(false); setEditTTSId(null) }}>
            <div className="modal settings-form-modal" onClick={(e) => e.stopPropagation()}>
              <div className="modal-head">
                <span className="modal-role">{editTTSId ? '编辑方案' : '新建方案'}</span>
                <button className="modal-close" onClick={() => { setShowAddTTS(false); setEditTTSId(null) }}>✕ 关闭</button>
              </div>
              <div className="modal-body">
                <div className="settings-fields">
            <label className="settings-field">
              <span className="settings-field-label">播报方式</span>
              <select value={ttsForm.provider} onChange={(e) => {
                const p = e.target.value as 'edge' | 'cosyvoice' | 'qwen' | 'piper' | 'omni'
                // 切换播报方式时同步默认音色/档位，避免把别的引擎参数误带进来
                const patch: any = { provider: p, tier: p === 'cosyvoice' || p === 'qwen' ? 'flash' : 'flash' }
                if (p === 'edge') patch.voice = 'zh-CN-YunjianNeural'
                else if (p === 'cosyvoice') patch.voice = 'longanyang'
                else if (p === 'qwen') patch.voice = 'longyingsongliu'
                setTtsForm({ ...ttsForm, ...patch })
              }}>
                <option value="edge">✅ edge-tts 免费云</option>
                <option value="cosyvoice">✅ CosyVoice v3（付费云）</option>
                <option value="qwen">✅ Qwen-Audio-TTS（付费云）</option>
                <option value="piper">✅ 本地 Piper（离线）</option>
                <option value="omni">✅ MiniCPM-o（本地 vLLM）</option>
              </select>
            </label>

            {ttsForm.provider === 'edge' && (
              <>
                <label className="settings-field">
                  <span className="settings-field-label">音色</span>
                  <select value={ttsForm.voice} onChange={(e) => setTtsForm({ ...ttsForm, voice: e.target.value })}>
                    {voiceOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                </label>
                <label className="settings-field">
                  <span className="settings-field-label">语速</span>
                  <select value={ttsForm.rate} onChange={(e) => setTtsForm({ ...ttsForm, rate: e.target.value })}>
                    {rateOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                </label>
              </>
            )}

            {(ttsForm.provider === 'cosyvoice' || ttsForm.provider === 'qwen') && (
              <>
                <label className="settings-field">
                  <span className="settings-field-label">档位</span>
                  <select value={ttsForm.tier} onChange={(e) => setTtsForm({ ...ttsForm, tier: e.target.value as 'flash' | 'plus' })}>
                    {TIER_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                </label>
                <label className="settings-field">
                  <span className="settings-field-label">音色</span>
                  <select value={ttsForm.voice} onChange={(e) => setTtsForm({ ...ttsForm, voice: e.target.value })}>
                    {(ttsForm.provider === 'cosyvoice' ? COSYVOICE_VOICES : QWEN_VOICES).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                </label>
                <label className="settings-field">
                  <span className="settings-field-label">API Key（可留空，走 .env）</span>
                  <input type="password" value={ttsForm.apiKey} placeholder="阿里云百炼 Key（留空读 DASHSCOPE_API_KEY）" onChange={(e) => setTtsForm({ ...ttsForm, apiKey: e.target.value })} />
                </label>
              </>
            )}

            {ttsForm.provider === 'piper' && (
              <label className="settings-field">
                <span className="settings-field-label">Piper 声库路径</span>
                <input type="text" value={ttsForm.piperModel} placeholder="models/zh_CN-huayan-medium.onnx" onChange={(e) => setTtsForm({ ...ttsForm, piperModel: e.target.value })} />
              </label>
            )}

            {ttsForm.provider === 'omni' && (
              <div className="settings-guide">MiniCPM-o 通过本地 vLLM-omni 服务播报（llm.omni 的 base_url，默认 localhost:8000）。需本机已启动 vLLM-omni，否则无声音。</div>
            )}

            <label className="settings-field">
              <span className="settings-field-label">名称（可选）</span>
              <input type="text" value={ttsForm.name} placeholder="如「免费云音」「本地离线」" onChange={(e) => setTtsForm({ ...ttsForm, name: e.target.value })} />
            </label>

            <div className="settings-actions">
              <button className="btn" onClick={saveTTSForm}>保存</button>
              <button className="btn" onClick={() => { setShowAddTTS(false); setEditTTSId(null) }}>取消</button>
            </div>
                </div>
              </div>
            </div>
          </div>
        )}

        <label className="settings-field">
          <span className="settings-field-label">已保存方案</span>
          <div className="scheme-list">
            {savedTTS.length === 0 && <p className="settings-msg">还没有方案，点上方「新建方案」。</p>}
            {savedTTS.map((m) => {
              const isActive = m.id === activeTTS
              return (
                <div key={m.id} className={`scheme-item ${isActive ? 'scheme-item--on' : ''}`}>
                  <div className="scheme-item-main" onClick={() => applyTTS(m)}>
                    <span className="scheme-item-icon">✅</span>
                    <div className="scheme-item-info">
                      <div className="scheme-item-name">{m.name}{isActive && <span className="scheme-item-tag">当前</span>}</div>
                      <div className="scheme-item-sub">{providerName(m.provider)}{(m.provider === 'cosyvoice' || m.provider === 'qwen') ? ` · ${m.tier || 'flash'}` : ''} · {m.voice || '默认'}</div>
                    </div>
                  </div>
                  <div className="scheme-item-actions">
                    {!isActive && <button type="button" className="btn btn--sm" onClick={() => applyTTS(m)}>设为当前</button>}
                    <button type="button" className="btn btn--sm" onClick={() => startEditTTS(m)}>编辑</button>
                    <button type="button" className="btn btn--sm" onClick={() => removeTTS(m.id)}>删除</button>
                  </div>
                </div>
              )
            })}
          </div>
        </label>

        {activeScheme && activeScheme.provider === 'edge' && (
          <>
            <label className="settings-field">
              <span className="settings-field-label">音色</span>
              <select value={activeScheme.voice} onChange={(e) => updateTTS(activeTTS, { voice: e.target.value })}>
                {voiceOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
            <label className="settings-field">
              <span className="settings-field-label">语速</span>
              <select value={activeScheme.rate} onChange={(e) => updateTTS(activeTTS, { rate: e.target.value })}>
                {rateOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
          </>
        )}
        {(activeScheme?.provider === 'cosyvoice' || activeScheme?.provider === 'qwen') && (
          <>
            <label className="settings-field">
              <span className="settings-field-label">档位</span>
              <select value={activeScheme.tier || 'flash'} onChange={(e) => updateTTS(activeTTS, { tier: e.target.value as 'flash' | 'plus' })}>
                {TIER_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
            <label className="settings-field">
              <span className="settings-field-label">音色</span>
              <select value={activeScheme.voice} onChange={(e) => updateTTS(activeTTS, { voice: e.target.value })}>
                {(activeScheme.provider === 'cosyvoice' ? COSYVOICE_VOICES : QWEN_VOICES).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
          </>
        )}
        {activeScheme && activeScheme.provider === 'piper' && (
          <label className="settings-field">
            <span className="settings-field-label">Piper 声库路径</span>
            <input type="text" value={activeScheme.piperModel || 'models/zh_CN-huayan-medium.onnx'} onChange={(e) => updateTTS(activeTTS, { piperModel: e.target.value })} />
          </label>
        )}
      </>
    )
  }

  const renderWakeConfig = () => {
    const savedWake: SavedWake[] = config ? (getPath(config, 'wake_word.models') || []) : []
    const activeWake = config ? (getPath(config, 'wake_word.active') || '') : ''

    // 唤醒固定两个方案：默认 sherpa + 可选 omni（缺哪个就兜底构造，确保始终显示）
    const sherpaWake: SavedWake = savedWake.find((m) => m.engine === 'sherpa') || {
      id: 'w_sherpa',
      name: '本地关键词 sherpa（普通话）',
      engine: 'sherpa',
      keyword: '小二',
      pinyin: 'x iǎo èr',
      threshold: 0.25,
      modelDir: '',
    }
    const omniWake: SavedWake = savedWake.find((m) => m.engine === 'omni') || {
      id: 'w_omni',
      name: '一体化 MiniCPM-o',
      engine: 'omni',
      keyword: '小二',
      pinyin: '',
      threshold: 0.25,
      modelDir: '',
    }
    const fixedWakes: SavedWake[] = [sherpaWake, omniWake]
    const activeWakeScheme = fixedWakes.find((m) => m.id === activeWake) || sherpaWake

    const upsertWake = (m: SavedWake) => {
      const exists = savedWake.some((x) => x.id === m.id)
      const next = exists ? savedWake.map((x) => (x.id === m.id ? m : x)) : [...savedWake, m]
      setField('wake_word.models', next)
    }

    const applyWake = (m: SavedWake) => {
      upsertWake(m)
      setField('wake_word.active', m.id)
      setField('wake_word.engine', m.engine)
      if (m.engine === 'sherpa') {
        setField('wake_word.keyword', m.keyword)
        setField('wake_word.pinyin', m.pinyin)
        setField('wake_word.threshold', m.threshold)
        setField('wake_word.model_dir', m.modelDir || '')
      }
    }

    const startEditWake = (m: SavedWake) => {
      const omni = config ? (getPath(config, 'llm.omni') || {}) : {}
      setWakeForm({
        engine: m.engine,
        keyword: m.keyword,
        pinyin: m.pinyin,
        threshold: m.threshold,
        modelDir: m.modelDir,
        name: m.name,
        baseUrl: omni.base_url || 'http://localhost:8000/v1',
        model: omni.model || 'openbmb/MiniCPM-o-4_5',
      })
      setShowAddWake(true)
    }

    const saveWakeForm = () => {
      if (wakeForm.engine === 'sherpa') {
        const next = { ...sherpaWake, name: wakeForm.name || sherpaWake.name, keyword: wakeForm.keyword, pinyin: wakeForm.pinyin, threshold: wakeForm.threshold, modelDir: wakeForm.modelDir }
        upsertWake(next)
        if (activeWake === sherpaWake.id) applyWake(next)
      } else {
        // omni 方案：只编辑一体化 MiniCPM-o 的地址/模型（唤醒/识别/播报共用）
        setField('llm.omni.base_url', wakeForm.baseUrl)
        setField('llm.omni.model', wakeForm.model)
      }
      setShowAddWake(false)
    }

    return (
      <>
        {showAddWake && (
          <div className="modal-overlay" onClick={() => { setShowAddWake(false) }}>
            <div className="modal settings-form-modal" onClick={(e) => e.stopPropagation()}>
              <div className="modal-head">
                <span className="modal-role">编辑方案</span>
                <button className="modal-close" onClick={() => { setShowAddWake(false) }}>✕ 关闭</button>
              </div>
              <div className="modal-body">
                <div className="settings-fields">
                  {wakeForm.engine === 'sherpa' && (
                    <>
                      <label className="settings-field">
                        <span className="settings-field-label">唤醒词</span>
                        <input type="text" value={wakeForm.keyword} onChange={(e) => setWakeForm({ ...wakeForm, keyword: e.target.value })} />
                      </label>
                      <label className="settings-field">
                        <span className="settings-field-label">拼音</span>
                        <input type="text" value={wakeForm.pinyin} placeholder="声母韵母空格分隔，如 x iǎo èr" onChange={(e) => setWakeForm({ ...wakeForm, pinyin: e.target.value })} />
                      </label>
                      <label className="settings-field">
                        <span className="settings-field-label">灵敏度</span>
                        <span className="settings-range">
                          <input type="range" min={0.1} max={1} step={0.05} value={wakeForm.threshold} onChange={(e) => setWakeForm({ ...wakeForm, threshold: Number(e.target.value) })} />
                          <em className="settings-val">{wakeForm.threshold}</em>
                        </span>
                        <span className="settings-hint">越小越灵敏，也越容易误唤醒；仅对本地关键词方案生效。</span>
                      </label>
                      <label className="settings-field">
                        <span className="settings-field-label">本地模型目录</span>
                        <input type="text" value={wakeForm.modelDir} placeholder="sherpa KWS 模型目录（含 tokens/encoder/decoder/joiner）" onChange={(e) => setWakeForm({ ...wakeForm, modelDir: e.target.value })} />
                      </label>
                      <label className="settings-field">
                        <span className="settings-field-label">名称（可选）</span>
                        <input type="text" value={wakeForm.name} placeholder="如「本地关键词唤醒」" onChange={(e) => setWakeForm({ ...wakeForm, name: e.target.value })} />
                      </label>
                    </>
                  )}
                  {wakeForm.engine === 'omni' && (
                    <>
                      <div className="settings-guide">一体化 MiniCPM-o 的服务地址与模型名，唤醒/识别/播报共用。</div>
                      <label className="settings-field">
                        <span className="settings-field-label">服务地址（base_url）</span>
                        <input type="text" value={wakeForm.baseUrl} placeholder="如 http://localhost:8000/v1" onChange={(e) => setWakeForm({ ...wakeForm, baseUrl: e.target.value })} />
                      </label>
                      <label className="settings-field">
                        <span className="settings-field-label">模型名</span>
                        <input type="text" value={wakeForm.model} placeholder="如 openbmb/MiniCPM-o-4_5（以官方为准）" onChange={(e) => setWakeForm({ ...wakeForm, model: e.target.value })} />
                      </label>
                    </>
                  )}
                  <div className="settings-actions">
                    <button className="btn" onClick={saveWakeForm}>保存</button>
                    <button className="btn" onClick={() => { setShowAddWake(false) }}>取消</button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        <label className="settings-field">
          <span className="settings-field-label">可选方案</span>
          <div className="scheme-list">
            {fixedWakes.map((m) => {
              const isActive = m.id === activeWake
              const isDefault = m.engine === 'sherpa'
              return (
                <div key={m.id} className={`scheme-item ${isActive ? 'scheme-item--on' : ''}`}>
                  <div className="scheme-item-main" onClick={() => applyWake(m)}>
                    <span className="scheme-item-icon">{isDefault ? '✅' : '⬜'}</span>
                    <div className="scheme-item-info">
                      <div className="scheme-item-name">
                        {m.name}
                        {isDefault && <span className="scheme-item-tag">默认</span>}
                        {isActive && <span className="scheme-item-tag">当前</span>}
                      </div>
                      <div className="scheme-item-sub">{isDefault ? '本地关键词' : '一体化 MiniCPM-o'} · {m.keyword}</div>
                    </div>
                  </div>
                  <div className="scheme-item-actions">
                    {!isActive && <button type="button" className="btn btn--sm" onClick={() => applyWake(m)}>设为当前</button>}
                    <button type="button" className="btn btn--sm" onClick={() => startEditWake(m)}>编辑</button>
                  </div>
                </div>
              )
            })}
          </div>
        </label>

        {activeWakeScheme && activeWakeScheme.engine === 'sherpa' && (
          <label className="settings-field">
            <span className="settings-field-label">灵敏度</span>
            <span className="settings-range">
              <input type="range" min={0.1} max={1} step={0.05} value={activeWakeScheme.threshold} onChange={(e) => {
                const next = { ...sherpaWake, threshold: Number(e.target.value) }
                upsertWake(next)
                if (activeWake === sherpaWake.id) setField('wake_word.threshold', next.threshold)
              }} />
              <em className="settings-val">{activeWakeScheme.threshold}</em>
            </span>
          </label>
        )}
      </>
    )
  }

  const allTabs = [...groups, ...EXTRA_TABS]
  const activeTab = allTabs.find((t) => t.key === tab) ?? allTabs[0]
  const currentInput = config ? getPath(config, 'audio.input_device') : undefined

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal settings" onClick={(ev) => ev.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-role">设置</span>
          <button className="modal-close" onClick={onClose}>✕ 关闭</button>
        </div>

        <div className="settings-layout">
          <div className="settings-nav">
            {allTabs.map((t) => (
              <button key={t.key} className={`settings-nav-item ${tab === t.key ? 'settings-nav-item--on' : ''}`} onClick={() => setTab(t.key)}>
                {t.label}
              </button>
            ))}
          </div>

          <div className="settings-body">
          {!config ? (
            <p className="settings-msg">{msg || '读取中…'}</p>
          ) : tab === 'ui' ? (
            <div className="settings-fields">
              <label className="settings-field">
                <span className="settings-field-label">字体（统一）</span>
                <select value={ui.font} onChange={(e) => setUi({ ...ui, font: e.target.value })}>
                  {FONT_OPTIONS.map((f) => (
                    <option key={f.value} value={f.value}>{f.label}</option>
                  ))}
                </select>
              </label>
              <label className="settings-field">
                <span className="settings-field-label">数字等宽对齐</span>
                <input type="checkbox" checked={ui.tabularNums} onChange={(e) => setUi({ ...ui, tabularNums: e.target.checked })} />
              </label>
              <label className="settings-field">
                <span className="settings-field-label">文字大小：{Math.round(ui.scale * 100)}%</span>
                <input type="range" min={0.85} max={1.3} step={0.05} value={ui.scale} onChange={(e) => setUi({ ...ui, scale: Number(e.target.value) })} />
              </label>
              <label className="settings-field">
                <span className="settings-field-label">聊天框宽度：{ui.leftWidth}px</span>
                <input type="range" min={200} max={400} step={10} value={ui.leftWidth} onChange={(e) => setUi({ ...ui, leftWidth: Number(e.target.value) })} />
              </label>
              <p className="settings-msg">界面外观设置即时生效，并自动保存到本机。</p>
            </div>
          ) : tab === 'audio' ? (
            <div className="settings-fields">
              <label className="settings-field">
                <span className="settings-field-label">
                  输入设备（麦克风）
                  <span className="settings-hint">重启后端后生效</span>
                </span>
                <select
                  value={String(currentInput ?? '')}
                  onChange={(e) => {
                    const v = e.target.value
                    setField('audio.input_device', v === '' ? null : Number(v))
                  }}
                >
                  <option value="">系统默认</option>
                  {inputs.map((d) => (
                    <option key={d.index} value={d.index}>
                      {d.name}{d.is_default ? '（默认）' : ''}
                    </option>
                  ))}
                </select>
              </label>
              {inputs.length === 0 && <p className="settings-msg">未枚举到输入设备（sounddevice 可能不可用）。</p>}
              <div className="settings-actions">
                <button className="btn" onClick={runEcho} disabled={echoBusy}>
                  {echoBusy ? '录音中…' : '🎤 回声测试'}
                </button>
              </div>
              {echoMsg && <p className="settings-msg">{echoMsg}</p>}
            </div>
          ) : tab === 'llm' ? (
            <div className="settings-fields">
              {renderLLMConfig()}
              {(fieldsByGroup['llm'] || []).filter((f) => f.path.startsWith('agent.')).map(renderField)}
              <div className="settings-actions">
                <button className="btn" onClick={clearMemory}>一键清空对话记忆</button>
              </div>
            </div>
          ) : tab === 'asr' ? (
            <div className="settings-fields">
              {renderASRConfig()}
              {(fieldsByGroup['asr'] || []).filter((f) => f.path.startsWith('vad.')).map(renderField)}
            </div>
          ) : tab === 'tts' ? (
            <div className="settings-fields">
              {renderTTSConfig()}
              <div className="settings-actions">
                <button className="btn" onClick={loadVoices} disabled={voicesLoading}>
                  {voicesLoading ? '拉取中…' : '🔄 刷新 / 更多音色'}
                </button>
                {voices.length > 0 && <span className="settings-msg">已加载 {voices.length} 个音色</span>}
              </div>
              <label className="settings-field settings-field--col">
                <span className="settings-field-label">试听文本</span>
                <textarea className="settings-textarea" rows={2} value={previewText} onChange={(e) => setPreviewText(e.target.value)} />
              </label>
              <div className="settings-actions">
                <button className="btn" onClick={preview}>▶ 试听</button>
              </div>
            </div>
          ) : tab === 'wake' ? (
            <div className="settings-fields">
              {renderWakeConfig()}
              {(fieldsByGroup['wake'] || []).filter((f) => f.path.startsWith('bargein.')).map(renderField)}
            </div>
          ) : (
            <div className="settings-fields">
              {(fieldsByGroup[tab] || []).filter(visible).map(renderField)}
            </div>
          )}
        </div>
        </div>

        <div className="settings-foot">
          <span className="settings-msg">{msg}</span>
          <button className="btn" onClick={save} disabled={saving || !config}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}
