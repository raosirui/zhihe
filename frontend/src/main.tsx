import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { Toaster } from 'sonner'
import App from './App'
import { AuthProvider } from './AuthContext'
import './index.css'

createRoot(document.getElementById('root')!).render(<StrictMode><BrowserRouter><AuthProvider><App /><Toaster position="top-center" richColors closeButton /></AuthProvider></BrowserRouter></StrictMode>)
