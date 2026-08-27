import { useState } from 'react'
import { API_BASE } from '../api'

export type Task = {
  id: string
  text: string
  status: string
  created_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  result?: string | null
  error?: string | null
}

const STATUS_LABEL: Record<string, string> = {
  pending: '排队中',
  running: '进行中',
  done: '完成',
  failed: '失败',
  cancelled: '已取消',
}

function truncate(s: string, n = 80): string {
  const t = s.replace(/\s+/g, ' ').trim()
  return t.length > n ? t.slice(0, n) + '…' : t
}

export function TaskPanel({ tasks, onClose }: { tasks: Task[]; onClose: () => void }) {
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState<string | null>(null)

  const cancel = async (id: string) => {
    setMsg('')
    setBusy(id)
    try {
      const r = await fetch(`${API_BASE}/api/tasks/${id}/cancel`, { method: 'POST' })
      const j = await r.json()
      if (!j.ok) setMsg('取消失败')
    } catch {
      setMsg('取消失败（网络错误）')
    } finally {
      setBusy(null)
    }
  }

  const list = [...tasks].reverse()

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal settings" onClick={(ev) => ev.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-role">任务面板</span>
          <button className="modal-close" onClick={onClose}>✕ 关闭</button>
        </div>

        <div className="settings-body">
          {list.length === 0 ? (
            <p className="settings-msg">暂无任务</p>
          ) : (
            <div className="perms-list">
              {list.map((t) => (
                <div key={t.id} className="perms-item">
                  <div className="perms-item-main">
                    <span className="perms-item-text">{t.text}</span>
                    <span className="perms-item-tags">
                      <span className={`perms-tag ${t.status === 'failed' ? 'perms-tag--err' : ''}`}>
                        {STATUS_LABEL[t.status] ?? t.status}
                      </span>
                    </span>
                    {t.error && <span className="perms-desc">{truncate(t.error)}</span>}
                    {t.result && <span className="perms-desc">{truncate(t.result)}</span>}
                  </div>
                  {(t.status === 'pending' || t.status === 'running') && (
                    <div className="perms-item-actions">
                      <button className="btn btn--reject" disabled={busy === t.id} onClick={() => cancel(t.id)}>
                        {busy === t.id ? '取消中…' : '取消'}
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="settings-foot">
          <span className="settings-msg">{msg}</span>
        </div>
      </div>
    </div>
  )
}
