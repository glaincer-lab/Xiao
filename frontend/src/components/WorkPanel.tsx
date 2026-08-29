import { useEffect, useRef } from 'react'

export type WorkStep = {
  id: number
  name: string
  status: 'start' | 'done' | 'error'
  summary?: string
  source: 'local' | 'dsh'
  time: string
}

const STATUS_ICON: Record<WorkStep['status'], string> = {
  start: '●',
  done: '✓',
  error: '✗',
}

export function WorkPanel({
  steps,
  activeTaskCount,
  live,
  onClear,
}: {
  steps: WorkStep[]
  activeTaskCount: number
  live?: string
  onClear: () => void
}) {
  const list = [...steps].reverse()
  const liveRef = useRef<HTMLPreElement>(null)

  useEffect(() => {
    if (liveRef.current) {
      liveRef.current.scrollTop = liveRef.current.scrollHeight
    }
  }, [live])

  return (
    <section className="panel panel--work">
      <div className="work-head">
        <span className="work-title">
          ⚡ 工作台
          {activeTaskCount > 0 && <span className="chip-badge">{activeTaskCount}</span>}
        </span>
        <button className="work-clear" onClick={onClear} disabled={list.length === 0}>
          清空
        </button>
      </div>
      {live ? (
        <div className="work-live">
          <span className="work-live-title">实时输出</span>
          <pre ref={liveRef} className="work-live-text">{live}</pre>
        </div>
      ) : null}
      <div className="work-body">
        {list.length === 0 ? (
          <span className="work-empty">暂无进行中的步骤</span>
        ) : (
          list.map((s) => (
            <div key={s.id} className={`work-step work-step--${s.status}`}>
              <span className="work-step-ic">{STATUS_ICON[s.status]}</span>
              <div className="work-step-main">
                <span className="work-step-name">{s.name}</span>
                {s.summary && <span className="work-step-summary">{s.summary}</span>}
              </div>
              {s.source === 'dsh' && <span className="work-step-src">DSH</span>}
            </div>
          ))
        )}
      </div>
    </section>
  )
}
