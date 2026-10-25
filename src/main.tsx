import React from 'react'
import ReactDOM from 'react-dom/client'
import './i18n'
import App from './App'
import BootGate from './components/BootGate'
import { ConfirmProvider } from './components/ConfirmDialog'
import { PromptProvider } from './components/PromptDialog'
import { ToastProvider } from './features/knowledge/Toast'
import './styles/global.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfirmProvider>
      <PromptProvider>
        <ToastProvider>
          {/* 启动门（窗口先行 2026-10-24）：boot 期挡住 App——业务 hook 不挂载、
              不向未就绪后端发请求；就绪确认后放行。 */}
          <BootGate>
            <App />
          </BootGate>
        </ToastProvider>
      </PromptProvider>
    </ConfirmProvider>
  </React.StrictMode>,
)
