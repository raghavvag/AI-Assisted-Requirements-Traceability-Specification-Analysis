// frontend/src/components/Sidebar.jsx
import { Link, useLocation, useParams } from 'react-router-dom'
import { motion } from 'motion/react'
import { BarChart3, GitPullRequest, Home, LogOut, Network, TrendingDown } from 'lucide-react'
import { useAuth } from '../context/AuthContext'

const NAV = [
  { to: '',            icon: Network,      label: 'Traceability' },
  { to: '/drift',      icon: TrendingDown, label: 'Drift Analysis' },
  { to: '/pr-bot',     icon: GitPullRequest,label: 'PR Bot' },
  { to: '/benchmarks', icon: BarChart3,    label: 'Benchmarks' },
]

export default function Sidebar() {
  const { id }     = useParams()
  const location   = useLocation()
  const { user, logout } = useAuth()

  return (
    <motion.aside
      initial={{ x: -80, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      className="w-64 flex flex-col h-screen glass border-r border-white/5 shrink-0"
    >
      {/* Logo */}
      <div className="px-5 py-6 border-b border-white/5">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-brand-600/30 flex items-center justify-center text-lg glow-blue">
            🔍
          </div>
          <div>
            <p className="font-bold text-sm text-white">Traceability</p>
            <p className="text-[10px] text-slate-500 font-mono">AI Engine v2.0</p>
          </div>
        </div>
      </div>

      {/* Dashboard link */}
      <div className="px-3 pt-4">
        <Link to="/dashboard" className="nav-item">
          <Home size={16} />
          All Projects
        </Link>
      </div>

      {/* Project nav */}
      {id && (
        <div className="px-3 pt-2 flex flex-col gap-1">
          <p className="px-4 py-2 text-[10px] font-semibold uppercase tracking-widest text-slate-600">
            Project #{id}
          </p>
          {NAV.map(({ to, icon: Icon, label }) => {
            const href    = `/projects/${id}${to}`
            const isActive = location.pathname === href
            return (
              <Link key={to} to={href} className={`nav-item ${isActive ? 'active' : ''}`}>
                <Icon size={16} />
                {label}
              </Link>
            )
          })}
        </div>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* User */}
      <div className="px-3 pb-4 border-t border-white/5 pt-4">
        <div className="flex items-center gap-3 px-4 py-3 rounded-xl">
          {user?.avatar_url ? (
            <img src={user.avatar_url} className="w-8 h-8 rounded-full" alt={user.name} />
          ) : (
            <div className="w-8 h-8 rounded-full bg-brand-600 flex items-center justify-center text-xs font-bold">
              {user?.name?.[0] ?? '?'}
            </div>
          )}
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-white truncate">{user?.name}</p>
            <p className="text-[10px] text-slate-500 truncate">{user?.email}</p>
          </div>
          <button onClick={logout} className="text-slate-500 hover:text-red-400 transition-colors">
            <LogOut size={15} />
          </button>
        </div>
      </div>
    </motion.aside>
  )
}
