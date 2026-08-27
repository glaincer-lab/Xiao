import { useCallback, useEffect, useState } from 'react'
import { API_BASE } from '../api'

type Category = { id: string; label: string; desc: string }
type Deferred = { id: string; ts: string; text: string; needed: string[]; status: string }

const NEEDED_LABEL: Record<string, string> = {
  network: '网络访问',
  write_outside: '写工作区外',
  delete: '删除文件',
  install: '安装软件包',
  system: '修改系统',
}

export function PermsPanel({ onClose }: { onClose: () => void }) {
  const [categories, setCategories] = useState<Category[]>([])
  const [standing, setStanding] = useState<string[]>([])
  const [deferred, setDeferred] = useState<Deferred[]>([])
  const [msg, setMsg] = useState('')

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
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const toggle = async (id: string, granted: boolean) => {
    setMsg('')
    try {
      const r = await fetch('http://127.0.0.1:8123/api/perms/standing', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category: id, granted }),
      })
      const j = await r.json()
      if (j.ok) setStanding(j.standing || [])
      else setMsg('更新失败：' + (j.msg || ''))
    } catch {
      setMsg('更新失败（网络错误）')
    }
  }

  const decide = async (id: string, approved: boolean) => {
    setMsg('')
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
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal settings" onClick={(ev) => ev.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-role">权限面板</span>
          <button className="modal-close" onClick={onClose}>✕ 关闭</button>
        </div>

        <div className="settings-body">
          <h4 className="perms-h">常驻授权（勾选后不再询问）</h4>
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
                  onChange={(e) => toggle(c.id, e.target.checked)}
                />
              </label>
            ))}
          </div>

          <h4 className="perms-h">待授权任务</h4>
          {deferred.length === 0 ? (
            <p className="settings-msg">暂无待授权任务</p>
          ) : (
            <div className="perms-list">
              {deferred.map((d) => (
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
                    <button className="btn btn--allow" onClick={() => decide(d.id, true)}>允许</button>
                    <button className="btn btn--reject" onClick={() => decide(d.id, false)}>拒绝</button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="settings-foot">
          <span className="settings-msg">{msg}</span>
          <button className="btn" onClick={load}>刷新</button>
        </div>
      </div>
    </div>
  )
}
