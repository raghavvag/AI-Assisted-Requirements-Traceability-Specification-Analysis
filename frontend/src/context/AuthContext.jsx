// frontend/src/context/AuthContext.jsx
import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { authApi } from '../services/api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user,    setUser]    = useState(() => {
    try { return JSON.parse(localStorage.getItem('user')) } catch { return null }
  })
  const [loading, setLoading] = useState(true)

  // Verify stored token on mount
  useEffect(() => {
    const token = localStorage.getItem('access_token')
    if (!token) { setLoading(false); return }

    authApi.me()
      .then(res => setUser(res.data))
      .catch(err => {
        // Only drop the session on a real auth rejection (401/403).
        // A network error / backend-down must NOT log the user out.
        if (err?.response?.status === 401 || err?.response?.status === 403) {
          localStorage.removeItem('access_token')
          localStorage.removeItem('refresh_token')
          localStorage.removeItem('user')
          setUser(null)
        }
      })
      .finally(() => setLoading(false))
  }, [])

  const loginWithGoogle = useCallback(async (idToken) => {
    const res = await authApi.googleLogin(idToken)
    const { access_token, refresh_token, user: userData } = res.data
    localStorage.setItem('access_token',  access_token)
    localStorage.setItem('refresh_token', refresh_token)
    localStorage.setItem('user',          JSON.stringify(userData))
    setUser(userData)
    return userData
  }, [])

  const logout = useCallback(() => {
    localStorage.clear()
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{ user, loading, loginWithGoogle, logout, isAuthenticated: !!user }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
