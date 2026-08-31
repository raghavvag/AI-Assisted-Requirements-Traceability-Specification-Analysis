// frontend/src/pages/LoginPage.jsx
// Full-screen animated login with Google OAuth button
import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { GoogleLogin } from '@react-oauth/google'
import { motion } from 'motion/react'
import { useAuth } from '../context/AuthContext'
import toast from 'react-hot-toast'

// ── Three.js particle background ────────────────────────────────────────────
function ParticleBackground() {
  const canvasRef = useRef(null)
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    let animId, width, height

    const particles = Array.from({ length: 80 }, () => ({
      x: Math.random(), y: Math.random(),
      vx: (Math.random() - 0.5) * 0.0003,
      vy: (Math.random() - 0.5) * 0.0003,
      r: Math.random() * 2 + 0.5,
      alpha: Math.random() * 0.5 + 0.1,
    }))

    function resize() {
      width = canvas.width  = canvas.offsetWidth
      height = canvas.height = canvas.offsetHeight
    }
    resize()
    window.addEventListener('resize', resize)

    function draw() {
      ctx.clearRect(0, 0, width, height)
      particles.forEach(p => {
        p.x = (p.x + p.vx + 1) % 1
        p.y = (p.y + p.vy + 1) % 1
        ctx.beginPath()
        ctx.arc(p.x * width, p.y * height, p.r, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(99,179,237,${p.alpha})`
        ctx.fill()
      })
      // Draw edges between nearby particles
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const dx = (particles[i].x - particles[j].x) * width
          const dy = (particles[i].y - particles[j].y) * height
          const d  = Math.sqrt(dx * dx + dy * dy)
          if (d < 120) {
            ctx.beginPath()
            ctx.moveTo(particles[i].x * width, particles[i].y * height)
            ctx.lineTo(particles[j].x * width, particles[j].y * height)
            ctx.strokeStyle = `rgba(59,130,246,${0.12 * (1 - d / 120)})`
            ctx.lineWidth = 0.6
            ctx.stroke()
          }
        }
      }
      animId = requestAnimationFrame(draw)
    }
    draw()
    return () => { cancelAnimationFrame(animId); window.removeEventListener('resize', resize) }
  }, [])
  return <canvas ref={canvasRef} className="absolute inset-0 w-full h-full" />
}

export default function LoginPage() {
  const { loginWithGoogle, isAuthenticated } = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    if (isAuthenticated) navigate('/dashboard', { replace: true })
  }, [isAuthenticated, navigate])

  async function handleGoogleSuccess(credentialResponse) {
    try {
      await loginWithGoogle(credentialResponse.credential)
      toast.success('Welcome! Redirecting to dashboard...')
      navigate('/dashboard', { replace: true })
    } catch (err) {
      // Surface the real backend reason instead of a generic message — this is
      // almost always a Google Client ID mismatch or a missing OAuth "test user".
      const detail =
        err?.response?.data?.detail ||
        err?.message ||
        'Login failed. Check that GOOGLE_CLIENT_ID matches and your email is an OAuth test user.'
      toast.error(String(detail), { duration: 8000 })
      console.error('Google login failed:', err?.response?.status, detail)
    }
  }

  return (
    <div className="relative min-h-screen bg-surface-900 flex items-center justify-center overflow-hidden">
      <ParticleBackground />

      {/* Radial glow */}
      <div className="absolute inset-0 bg-hero-glow pointer-events-none" />

      {/* Card */}
      <motion.div
        initial={{ opacity: 0, y: 40, scale: 0.95 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.6, ease: 'easeOut' }}
        className="relative z-10 glass-strong rounded-3xl p-10 w-full max-w-md mx-4
                   shadow-[0_0_80px_rgba(59,130,246,0.12)]"
      >
        {/* Icon */}
        <motion.div
          animate={{ y: [0, -8, 0] }}
          transition={{ repeat: Infinity, duration: 4, ease: 'easeInOut' }}
          className="w-20 h-20 mx-auto mb-6 rounded-2xl bg-brand-600/20
                     border border-brand-500/30 flex items-center justify-center text-4xl glow-blue"
        >
          🔍
        </motion.div>

        <h1 className="text-center text-2xl font-bold mb-1">
          <span className="gradient-text">AI Traceability Engine</span>
        </h1>
        <p className="text-center text-slate-400 text-sm mb-8">
          Semantic compliance verification between<br />
          requirements and OpenAPI specifications.
        </p>

        {/* Features */}
        <div className="grid grid-cols-2 gap-3 mb-8">
          {[
            { emoji: '🕸️', text: '3D Force Graph' },
            { emoji: '📡', text: 'Qdrant Vector DB' },
            { emoji: '🤖', text: 'LLM Diagnostics' },
            { emoji: '🔐', text: 'Multi-Tenant Auth' },
          ].map(({ emoji, text }) => (
            <div key={text} className="glass rounded-xl p-3 flex items-center gap-2 text-xs text-slate-400">
              <span className="text-base">{emoji}</span>{text}
            </div>
          ))}
        </div>

        {/* Google Login button */}
        <div className="flex justify-center">
          <GoogleLogin
            onSuccess={handleGoogleSuccess}
            onError={() => toast.error('Google login failed.')}
            theme="filled_blue"
            shape="pill"
            size="large"
            text="signin_with"
            useOneTap
          />
        </div>

        <p className="text-center text-[10px] text-slate-600 mt-6">
          B.Tech Capstone Project · AI-Assisted Requirements Traceability
        </p>
      </motion.div>
    </div>
  )
}
