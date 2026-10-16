import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
import { initTelemetry } from './telemetry'

// Initialise OpenTelemetry before rendering so the DocumentLoad span
// captures the full page load and FetchInstrumentation is ready for
// the first API call.  The await is intentionally non-blocking (we
// don't delay render) — OTel SDK is best-effort.
initTelemetry().catch(() => {
  // Already handled inside initTelemetry — just suppress unhandled rejection
})

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
