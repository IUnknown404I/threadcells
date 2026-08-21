import React from 'react'
import ReactDOM from 'react-dom/client'
import { TerminalView } from './components/TerminalView'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <TerminalView terminalId="e2e-terminal" onClose={() => {}} />,
)
