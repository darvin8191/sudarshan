import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
// After index.css, so the SUDARSHAN layer wins by order. Keep it on this line.
import '@/sudarshan'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { installGlobalErrorReporter } from '@/utils/errorReporter'
import App from './App.tsx'

installGlobalErrorReporter()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>
)
