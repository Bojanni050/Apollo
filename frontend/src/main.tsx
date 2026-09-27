import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { applyStoredThemeBeforePaint } from './theme'
import './styles/app.css'

// Before React renders anything, so a returning Calm user never sees a frame
// of the default theme flash past.
applyStoredThemeBeforePaint()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
