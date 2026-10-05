import React from 'react'
import ReactDOM from 'react-dom/client'
import './i18n'
import App from './App'
import { ConfirmProvider } from './components/ConfirmDialog'
import './styles/global.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfirmProvider>
      <App />
    </ConfirmProvider>
  </React.StrictMode>,
)
