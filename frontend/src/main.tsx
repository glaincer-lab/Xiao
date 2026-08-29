import ReactDOM from 'react-dom/client'
import App from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import './styles.css'

// Electron 桌面壳标记：透明窗/圆角等只在桌面端生效（浏览器端保持原有不透明背景）
if (typeof navigator !== 'undefined' && navigator.userAgent.toLowerCase().includes('electron')) {
  document.documentElement.classList.add('is-electron')
}

// 注意：不用 React.StrictMode，否则开发模式会双重挂载、导致 WebSocket 被反复创建/销毁
ReactDOM.createRoot(document.getElementById('root')!).render(
  <ErrorBoundary>
    <App />
  </ErrorBoundary>,
)
