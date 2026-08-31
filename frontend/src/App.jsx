// frontend/src/App.jsx
import { Suspense, lazy } from 'react'
import { BrowserRouter, Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { GoogleOAuthProvider } from '@react-oauth/google'
import { Toaster } from 'react-hot-toast'
import { AuthProvider, useAuth } from './context/AuthContext'
import Sidebar from './components/Sidebar'

// Lazy-load pages for code splitting
const LandingPage      = lazy(() => import('./pages/LandingPage'))
const LoginPage        = lazy(() => import('./pages/LoginPage'))
const DashboardPage    = lazy(() => import('./pages/DashboardPage'))
const TraceabilityPage = lazy(() => import('./pages/TraceabilityPage'))
const DriftPage        = lazy(() => import('./pages/DriftPage'))
const PRBotPage        = lazy(() => import('./pages/PRBotPage'))
const BenchmarksPage   = lazy(() => import('./pages/BenchmarksPage'))

// Read Google Client ID from Vite env
const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || ''

// ── Route guards ─────────────────────────────────────────────────────────────
function RequireAuth() {
  const { isAuthenticated, loading } = useAuth()
  if (loading) return (
    <div className="min-h-screen flex items-center justify-center bg-surface-900">
      <span className="spinner" style={{width:48,height:48}} />
    </div>
  )
  return isAuthenticated ? <Outlet /> : <Navigate to="/login" replace />
}

// ── Authenticated layout with Sidebar ────────────────────────────────────────
function ProjectLayout() {
  return (
    <div className="flex h-screen overflow-hidden bg-surface-900">
      <Sidebar />
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}

// ── Page loading fallback ─────────────────────────────────────────────────────
function PageLoader() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-surface-900">
      <span className="spinner" style={{width:48,height:48}} />
    </div>
  )
}

export default function App() {
  return (
    <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>
      <AuthProvider>
        <BrowserRouter>
          <Suspense fallback={<PageLoader />}>
            <Routes>
              {/* Public */}
              <Route path="/"      element={<LandingPage />} />
              <Route path="/login" element={<LoginPage />} />

              {/* Protected */}
              <Route element={<RequireAuth />}>
                {/* Dashboard (no sidebar) */}
                <Route path="/dashboard" element={
                  <div className="min-h-screen bg-surface-900">
                    <DashboardPage />
                  </div>
                } />

                {/* Project routes (with sidebar) */}
                <Route path="/projects/:id" element={<ProjectLayout />}>
                  <Route index              element={<TraceabilityPage />} />
                  <Route path="drift"       element={<DriftPage />} />
                  <Route path="pr-bot"      element={<PRBotPage />} />
                  <Route path="benchmarks"  element={<BenchmarksPage />} />
                </Route>
              </Route>

              {/* Catch-all */}
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>

          <Toaster
            position="bottom-right"
            toastOptions={{
              style: {
                background: '#1e293b',
                color: '#e2e8f0',
                border: '1px solid rgba(255,255,255,0.08)',
                borderRadius: '12px',
                fontSize: '13px',
              },
              success: { iconTheme: { primary:'#22c55e', secondary:'#1e293b' } },
              error:   { iconTheme: { primary:'#ef4444', secondary:'#1e293b' } },
            }}
          />
        </BrowserRouter>
      </AuthProvider>
    </GoogleOAuthProvider>
  )
}
