import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { isNative, syncStatusBar } from './native'
import { loadTheme } from './storage'

// Theme lands on <html> before first paint - a saved choice wins, otherwise
// the system preference - so dark-mode users never get a white flash.
document.documentElement.dataset.theme =
  loadTheme() ?? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')

// Inside the iOS shell: mark the root so CSS can pad for the notch/home
// indicator, and color the status bar to match the theme.
if (isNative) {
  document.documentElement.classList.add('native')
  syncStatusBar(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light')
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// The service worker exists for PWA installability on the web; inside the
// native shell it would only add a caching layer nobody asked for.
if ('serviceWorker' in navigator && !isNative) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register(`${import.meta.env.BASE_URL}sw.js`).catch(() => {})
  })
}
