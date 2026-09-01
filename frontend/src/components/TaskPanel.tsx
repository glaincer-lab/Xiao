import { useCallback, useEffect, useState } from 'react'
import { API_BASE } from '../api'
import { truncate } from '../lib/text'

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

/* 按 id 合并两批任务：后者覆盖前者同名项，保留仅前者存在的旧项 */
function mergeTasks(base: Task[], incoming: Task[]): Task[] {
  const map = new Map<string, Task>()
  for (const t of base) map.set(t.id, t)
  for (const t of incoming) map.set(t.id, t)
  return [...map.values()]
}

export function TaskPanel({ tasks, onClose }: { tasks: Task[]; onClose: () => void }) {
  const [list, setList] = useState<Task[]>(tasks)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    setMsg('')
    fetch(`${API_BASE}/api/tasks`)
      .then((r) => r.json())
      .then((j) => {
        if (j && j.ok && Array.isArray(j.tasks)) setList(j.tasks)
        else setMsg('读取失败')
      })
      .catch(() => setMsg('读取失败（后端未启动？）'))
      .finally(() => setLoading(false))
  }, [])

  // 打开面板即主动拉一次，不依赖 WS 推送（断线或错过事件时面板也不空白）
  useEffect(() => {
    load()
  }, [load])

  // 打开期间仍接收 App 层 WS 推送的 task_event，按 id 合并（新数据覆盖旧数据）
  useEffect(() => {
    setList((prev) => mergeTasks(prev, tasks))
  }, [tasks])

  const cancel = async (id: string) => {
    setMsg('')
    setBusy(id)
    try {
      const r = await fetch(`${API_BASE}/api/tasks/${id}/cancel`, { method: 'POST' })
      const j = await r.json()
      if (j.ok) load()
      else setMsg('取消失败')
    } catch {
      setMsg('取消失败（网络错误）')
    } finally {
      setBusy(null)
    }
  }

  const list2 = [...list].reverse()

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal settings" onClick={(ev) => ev.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-role">任务面板</span>
          <button className="modal-close" onClick={onClose}>✕ 关闭</button>
        </div>

        <div className="settings-body">
          {loading && list.length === 0 ? (
            <p className="settings-msg">加载中…</p>
          ) : list2.length === 0 ? (
            <p className="settings-msg">暂无任务</p>
          ) : (
            <div className="perms-list">
              {list2.map((t) => (
                <div key={t.id} className="perms-item">
                  <div className="perms-item-main">
                    <span className="perms-item-text">{t.text}</span>
                    <span className="perms-item-tags">
                      <span className={`perms-tag ${t.status === 'failed' ? 'perms-tag--err' : ''}`}>
                        {STATUS_LABEL[t.status] ?? t.status}
                      </span>
                    </span>
                    {t.error && <span className="perms-desc">{truncate(t.error, 80)}</span>}
                    {t.result && <span className="perms-desc">{truncate(t.result, 80)}</span>}
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
          <button className="btn" onClick={load}>刷新</button>
        </div>
      </div>
    </div>
  )
}
