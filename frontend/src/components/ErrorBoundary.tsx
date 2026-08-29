import { Component, type ErrorInfo, type ReactNode } from 'react'

type Props = { children: ReactNode }
type State = { hasError: boolean; message: string }

/** 顶层错误边界：渲染/生命周期抛出未捕获异常时兜底一张可恢复的卡片，避免整棵组件树白屏。 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, message: '' }

  static getDerivedStateFromError(err: unknown): State {
    return { hasError: true, message: err instanceof Error ? err.message : String(err) }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  render() {
    if (!this.state.hasError) return this.props.children

    return (
      <div className="modal-overlay">
        <div className="modal">
          <div className="modal-head">
            <span className="modal-role">界面出错了</span>
            <button className="modal-close" onClick={() => location.reload()}>刷新</button>
          </div>
          <div className="modal-body">
            <p className="settings-msg">
              小二遇到一个未知问题，已经停在安全状态。
            </p>
            {this.state.message && (
              <pre className="error-detail">{this.state.message}</pre>
            )}
            <div className="composer-actions">
              <button className="btn" onClick={() => location.reload()}>重新加载</button>
              <button className="btn btn--wake" onClick={() => this.setState({ hasError: false, message: '' })}>
                尝试恢复
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }
}
