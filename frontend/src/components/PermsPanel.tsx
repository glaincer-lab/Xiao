import { useCallback, useEffect, useState } from 'react'
import { API_BASE } from '../api'

type Category = { id: string; label: string; desc: string }
type Deferred = { id: string; ts: string; text: string; needed: string[]; status: string }
type AuthItem = { key: string; type: 'bool' | 'int' | 'list' | 'dict'; label: string; default: unknown; desc: string }

const NEEDED_LABEL: Record<string, string> = {
  network: '网络访问',
  write_outside: '写工作区外',
  delete: '删除文件',
  install: '安装软件包',
  system: '修改系统',
}

// 删除数据端点（由后端补，未实现时优雅显示失败）
const DELETE_ENDPOINTS: { key: string; label: string; url: string; body: () => Record<string, unknown> }[] = [
  { key: 'audit', label: '对话流水/审计日志', url: '/api/audit/clear', body: () => ({}) },
  { key: 'persona', label: '画像（我观察的）', url: '/api/persona/clear', body: () => ({}) },
  { key: 'memv4', label: '会话原文（memv4）', url: '/api/memv4/clear', body: () => ({}) },
]

export function PermsPanel({ onClose }: { onClose: () => void }) {
  const [categories, setCategories] = useState<Category[]>([])
  const [standing, setStanding] = useState<string[]>([])
  const [deferred, setDeferred] = useState<Deferred[]>([])
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(true)
  const [standingBusy, setStandingBusy] = useState<Record<string, boolean>>({})
  const [deferredBusy, setDeferredBusy] = useState<Record<string, boolean>>({})

  // 隐私授权区（§十一.7）
  const [authItems, setAuthItems] = useState<AuthItem[]>([])
  const [authorizations, setAuthorizations] = useState<Record<string, unknown>>({})
  const [authLoading, setAuthLoading] = useState(true)
  const [authBusy, setAuthBusy] = useState<Record<string, boolean>>({})
  const [whiteInput, setWhiteInput] = useState('')

  // 删除数据子区
  const [delBusy, setDelBusy] = useState(false)
  const [delMsg, setDelMsg] = useState('')
  const [memId, setMemId] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')

  const load = useCallback(() => {
    fetch(`${API_BASE}/api/perms`)
      .then((r) => r.json())
      .then((j) => {
        if (j.ok) {
          setCategories(j.categories || [])
          setStanding(j.standing || [])
          setDeferred(j.deferred || [])
        } else {
          setMsg('读取失败')
        }
      })
      .catch(() => setMsg('读取失败（后端未启动？）'))
      .finally(() => setLoading(false))
  }, [])

  const loadAuth = useCallback(() => {
    fetch(`${API_BASE}/api/authorizations`)
      .then((r) => r.json())
      .then((j) => {
        if (j.ok) {
          setAuthItems(j.items || [])
          setAuthorizations(j.authorizations || {})
        } else {
          setMsg('读取隐私授权失败')
        }
      })
      .catch(() => setMsg('读取隐私授权失败（后端未启动？）'))
      .finally(() => setAuthLoading(false))
  }, [])

  useEffect(() => {
    load()
    loadAuth()
  }, [load, loadAuth])

  const toggle = async (id: string, granted: boolean) => {
    setMsg('')
    const prev = standing.includes(id)
    if (prev === granted) return
    // 乐观更新：立即反映勾选，失败再回滚，避免等待期间勾选框滞后
    setStanding((s) => (granted ? [...s, id] : s.filter((x) => x !== id)))
    setStandingBusy((b) => ({ ...b, [id]: true }))
    try {
      const r = await fetch(`${API_BASE}/api/perms/standing`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category: id, granted }),
      })
      const j = await r.json()
      if (j.ok) setStanding(j.standing || [])
      else {
        setStanding((s) => (prev ? [...s, id] : s.filter((x) => x !== id)))
        setMsg('更新失败：' + (j.msg || ''))
      }
    } catch {
      setStanding((s) => (prev ? [...s, id] : s.filter((x) => x !== id)))
      setMsg('更新失败（网络错误）')
    } finally {
      setStandingBusy((b) => {
        const n = { ...b }
        delete n[id]
        return n
      })
    }
  }

  const decide = async (id: string, approved: boolean) => {
    setMsg('')
    setDeferredBusy((b) => ({ ...b, [id]: true }))
    try {
      const r = await fetch(`${API_BASE}/api/perms/deferred/${id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved }),
      })
      const j = await r.json()
      if (j.ok) setDeferred(j.deferred || [])
      else setMsg('处理失败：' + (j.msg || ''))
    } catch {
      setMsg('处理失败（网络错误）')
    } finally {
      setDeferredBusy((b) => {
        const n = { ...b }
        delete n[id]
        return n
      })
    }
  }

  // 授权项整值写入（bool / int / list / dict 通用）
  const setAuth = async (key: string, value: unknown) => {
    setMsg('')
    setAuthBusy((b) => ({ ...b, [key]: true }))
    try {
      const r = await fetch(`${API_BASE}/api/authorizations/set`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, value }),
      })
      const j = await r.json()
      if (j.ok) setAuthorizations(j.authorizations || {})
      else setMsg('更新失败：' + (j.msg || ''))
    } catch {
      setMsg('更新失败（网络错误）')
    } finally {
      setAuthBusy((b) => {
        const n = { ...b }
        delete n[key]
        return n
      })
    }
  }

  // 细项授权（per_feature）单开关
  const setFeature = async (feature: string, granted: boolean) => {
    setMsg('')
    setAuthBusy((b) => ({ ...b, ['per_feature']: true }))
    try {
      const r = await fetch(`${API_BASE}/api/authorizations/set_feature`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ feature, granted }),
      })
      const j = await r.json()
      if (j.ok) setAuthorizations(j.authorizations || {})
      else setMsg('更新失败：' + (j.msg || ''))
    } catch {
      setMsg('更新失败（网络错误）')
    } finally {
      setAuthBusy((b) => {
        const n = { ...b }
        delete n['per_feature']
        return n
      })
    }
  }

  const whiteList = Array.isArray(authorizations['emergency_passthrough'])
    ? (authorizations['emergency_passthrough'] as string[])
    : []

  const addWhiteTag = () => {
    const v = whiteInput.trim()
    if (!v) return
    setWhiteInput('')
    if (whiteList.includes(v)) return
    setAuth('emergency_passthrough', [...whiteList, v])
  }

  const removeWhiteTag = (tag: string) => {
    setAuth('emergency_passthrough', whiteList.filter((x) => x !== tag))
  }

  const perFeature = authorizations['per_feature']
  const perFeatureEntries =
    perFeature && typeof perFeature === 'object' && !Array.isArray(perFeature)
      ? Object.entries(perFeature as Record<string, unknown>)
      : []

  const callDelete = async (label: string, url: string, body?: Record<string, unknown>) => {
    setDelMsg('')
    setDelBusy(true)
    try {
      const r = await fetch(`${API_BASE}${url}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      })
      if (r.status === 404 || r.status === 405) {
        setDelMsg(`${label}：该功能暂未开放（后端未实现）`)
        return
      }
      const j = await r.json().catch(() => null)
      if (j && j.ok) setDelMsg(`${label}：已完成`)
      else setDelMsg(`${label}：${(j && j.msg) || '操作失败'}`)
    } catch {
      setDelMsg(`${label}：操作失败（网络错误）`)
    } finally {
      setDelBusy(false)
    }
  }

  const renderAuthRow = (item: AuthItem) => {
    if (item.type === 'bool') {
      return (
        <label key={item.key} className="settings-field">
          <span className="settings-field-label">
            {item.label}
            <span className="perms-desc">{item.desc}</span>
          </span>
          <input
            type="checkbox"
            checked={!!authorizations[item.key]}
            disabled={!!authBusy[item.key]}
            onChange={(e) => setAuth(item.key, e.target.checked)}
          />
        </label>
      )
    }
    if (item.type === 'int') {
      // proactivity_level：0~100 滑块（后端固定范围）
      const val = Number(authorizations[item.key] ?? 0)
      return (
        <label key={item.key} className="settings-field">
          <span className="settings-field-label">
            {item.label}
            <span className="perms-desc">{item.desc}</span>
          </span>
          <span className="settings-range">
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              value={val}
              disabled={!!authBusy[item.key]}
              onChange={(e) => setAuthorizations((prev) => ({ ...prev, [item.key]: Number(e.target.value) }))}
              onMouseUp={(e) => setAuth(item.key, Number(e.currentTarget.value))}
              onTouchEnd={(e) => setAuth(item.key, Number(e.currentTarget.value))}
              onKeyUp={(e) => setAuth(item.key, Number(e.currentTarget.value))}
            />
            <em className="settings-val">{val}</em>
          </span>
        </label>
      )
    }
    if (item.type === 'list') {
      return (
        <div key={item.key} className="settings-field settings-field--col">
          <span className="settings-field-label">
            {item.label}
            <span className="perms-desc">{item.desc}</span>
          </span>
          {whiteList.length > 0 ? (
            <div className="settings-multi">
              {whiteList.map((tag) => (
                <span key={tag} className="settings-chip settings-chip--on">
                  {tag}
                  <button type="button" className="btn btn--sm" style={{ marginLeft: 6, padding: '0 6px' }} onClick={() => removeWhiteTag(tag)}>
                    ×
                  </button>
                </span>
              ))}
            </div>
          ) : (
            <p className="settings-msg">暂未配置（默认不穿透）</p>
          )}
          <div className="settings-field">
            <span className="settings-field-label">添加条目</span>
            <input
              type="text"
              value={whiteInput}
              placeholder="如「老人跌倒」，回车添加"
              onChange={(e) => setWhiteInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addWhiteTag() } }}
            />
          </div>
          <div className="settings-actions">
            <button className="btn" onClick={addWhiteTag} disabled={!whiteInput.trim() || !!authBusy[item.key]}>
              ＋ 添加
            </button>
          </div>
        </div>
      )
    }
    // dict（per_feature）：逐条只读/开关
    return (
      <div key={item.key} className="settings-field settings-field--col">
        <span className="settings-field-label">
          {item.label}
          <span className="perms-desc">{item.desc}</span>
        </span>
        {perFeatureEntries.length === 0 ? (
          <p className="settings-msg">暂无细项授权</p>
        ) : (
          <div className="settings-fields">
            {perFeatureEntries.map(([feat, on]) => (
              <label key={feat} className="settings-field">
                <span className="settings-field-label">{feat}</span>
                <input
                  type="checkbox"
                  checked={!!on}
                  disabled={!!authBusy['per_feature']}
                  onChange={(e) => setFeature(feat, e.target.checked)}
                />
              </label>
            ))}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal settings" onClick={(ev) => ev.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-role">权限面板</span>
          <button className="modal-close" onClick={onClose}>✕ 关闭</button>
        </div>

        <div className="settings-body">
          <h4 className="perms-h">隐私授权（默认最小化，本人授权才开）</h4>
          {authLoading ? (
            <p className="settings-msg">加载中…</p>
          ) : authItems.length === 0 ? (
            <p className="settings-msg">暂无授权项（后端未实现 /api/authorizations？）</p>
          ) : (
            <div className="settings-fields">{authItems.map((item) => renderAuthRow(item))}</div>
          )}

          <h4 className="perms-h">常驻授权（勾选后不再询问）</h4>
          {loading ? (
            <p className="settings-msg">加载中…</p>
          ) : categories.length === 0 ? (
            <p className="settings-msg">暂无权限项</p>
          ) : (
            <div className="settings-fields">
              {categories.map((c) => (
                <label key={c.id} className="settings-field">
                  <span className="settings-field-label">
                    {c.label}
                    <span className="perms-desc">{c.desc}</span>
                  </span>
                  <input
                    type="checkbox"
                    checked={standing.includes(c.id)}
                    disabled={!!standingBusy[c.id]}
                    onChange={(e) => toggle(c.id, e.target.checked)}
                  />
                </label>
              ))}
            </div>
          )}

          <h4 className="perms-h">待授权任务</h4>
          {loading ? (
            <p className="settings-msg">加载中…</p>
          ) : deferred.length === 0 ? (
            <p className="settings-msg">暂无待授权任务</p>
          ) : (
            <div className="perms-list">
              {deferred.map((d) => {
                const busy = !!deferredBusy[d.id]
                return (
                  <div key={d.id} className="perms-item">
                    <div className="perms-item-main">
                      <span className="perms-item-text">{d.text}</span>
                      <span className="perms-item-tags">
                        {(d.needed || []).map((n) => (
                          <span key={n} className="perms-tag">{NEEDED_LABEL[n] || n}</span>
                        ))}
                      </span>
                    </div>
                    <div className="perms-item-actions">
                      <button className="btn btn--allow" disabled={busy} onClick={() => decide(d.id, true)}>
                        {busy ? '处理中…' : '允许'}
                      </button>
                      <button className="btn btn--reject" disabled={busy} onClick={() => decide(d.id, false)}>
                        拒绝
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          <h4 className="perms-h">删除数据（部分选择，非一键全清）</h4>
          <div className="settings-fields">
            <div className="settings-field">
              <span className="settings-field-label">删除单条记忆（填 id）</span>
              <input type="text" value={memId} placeholder="记忆条目 id" onChange={(e) => setMemId(e.target.value)} />
            </div>
            <div className="settings-actions">
              <button
                className="btn"
                disabled={delBusy || !memId.trim()}
                onClick={() => callDelete('删除单条记忆', '/api/memory/delete', { id: memId.trim() })}
              >
                删除该条
              </button>
            </div>
            <div className="settings-field">
              <span className="settings-field-label">删除记忆区间</span>
              <span className="settings-select-row">
                <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
                <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} />
              </span>
            </div>
            <div className="settings-actions">
              <button
                className="btn"
                disabled={delBusy || !fromDate || !toDate}
                onClick={() => callDelete('删除记忆区间', '/api/memory/delete_range', { from: fromDate, to: toDate })}
              >
                删除区间
              </button>
            </div>
            {DELETE_ENDPOINTS.map((d) => (
              <div key={d.key} className="settings-actions">
                <button
                  className="btn"
                  disabled={delBusy}
                  onClick={() => {
                    if (window.confirm(`确认清空「${d.label}」？此操作不可恢复。`)) {
                      callDelete(`清空${d.label}`, d.url, d.body())
                    }
                  }}
                >
                  清空{d.label}
                </button>
              </div>
            ))}
            {delMsg && <p className="settings-msg">{delMsg}</p>}
          </div>
        </div>

        <div className="settings-foot">
          <span className="settings-msg">{msg}</span>
          <button className="btn" onClick={() => { load(); loadAuth() }}>刷新</button>
        </div>
      </div>
    </div>
  )
}
