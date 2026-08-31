// frontend/src/pages/DashboardPage.jsx
// Project management dashboard with file upload modal
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'motion/react'
import { Plus, Folder, ChevronRight, Upload, X, Trash2, CheckCircle } from 'lucide-react'
import { projectsApi } from '../services/api'
import { useDropzone } from 'react-dropzone'
import toast from 'react-hot-toast'

// ── Upload modal ────────────────────────────────────────────────────────────
function UploadModal({ project, onClose, onDone }) {
  const [files, setFiles] = useState({ SRS: null, API_V1: null, API_V2: null })
  const [uploading, setUploading] = useState(false)

  function Dropzone({ docType, label }) {
    const { getRootProps, getInputProps, isDragActive } = useDropzone({
      accept: { 'text/*': ['.md', '.txt', '.json', '.yaml', '.yml'],
                'application/json': ['.json'],
                'application/x-yaml': ['.yaml', '.yml'] },
      maxFiles: 1,
      onDrop: (accepted) => {
        if (accepted[0]) setFiles(f => ({ ...f, [docType]: accepted[0] }))
      },
    })

    const file = files[docType]
    return (
      <div {...getRootProps()} className={`rounded-xl border-2 border-dashed p-4 cursor-pointer transition-colors
          ${isDragActive ? 'border-brand-500 bg-brand-600/10' : 'border-white/10 hover:border-white/20'}`}>
        <input {...getInputProps()} />
        <div className="flex items-center gap-3">
          <div className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0
              ${file ? 'bg-green-500/20 text-green-400' : 'bg-white/5 text-slate-500'}`}>
            {file ? <CheckCircle size={18} /> : <Upload size={18} />}
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-white">{label}</p>
            <p className="text-xs text-slate-500 truncate">
              {file ? file.name : 'Drop file or click to browse'}
            </p>
          </div>
        </div>
      </div>
    )
  }

  async function handleUpload() {
    setUploading(true)
    let uploaded = 0
    for (const [docType, file] of Object.entries(files)) {
      if (!file) continue
      try {
        const fd = new FormData()
        fd.append('doc_type', docType)
        fd.append('file', file)
        await projectsApi.upload(project.id, fd)
        uploaded++
      } catch {
        toast.error(`Failed to upload ${docType}`)
      }
    }
    setUploading(false)
    if (uploaded > 0) { toast.success(`${uploaded} file(s) uploaded!`); onDone() }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm px-4">
      <motion.div
        initial={{ scale: 0.9, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.9, opacity: 0 }}
        className="glass-strong rounded-2xl p-6 w-full max-w-md shadow-2xl"
      >
        <div className="flex items-center justify-between mb-5">
          <div>
            <h2 className="font-bold text-lg text-white">Upload Artifacts</h2>
            <p className="text-xs text-slate-400">{project.name}</p>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-white"><X size={18} /></button>
        </div>

        <div className="flex flex-col gap-3 mb-5">
          <Dropzone docType="SRS"    label="Requirements SRS (.md / .txt)" />
          <Dropzone docType="API_V1" label="OpenAPI Spec v1 (.json / .yaml)" />
          <Dropzone docType="API_V2" label="OpenAPI Spec v2 (.json / .yaml)  ← drift analysis" />
        </div>

        <div className="flex gap-3">
          <button className="btn-secondary flex-1" onClick={onClose}>Cancel</button>
          <button
            className="btn-primary flex-1"
            onClick={handleUpload}
            disabled={uploading || Object.values(files).every(f => !f)}
          >
            {uploading ? <span className="spinner" /> : <Upload size={16} />}
            {uploading ? 'Uploading…' : 'Upload Files'}
          </button>
        </div>
        <p className="text-[10px] text-slate-600 text-center mt-3">
          If no files are uploaded, the built-in E-Commerce demo dataset will be used.
        </p>
      </motion.div>
    </div>
  )
}

// ── Dashboard ────────────────────────────────────────────────────────────────
export default function DashboardPage() {
  const [projects, setProjects] = useState([])
  const [loading,  setLoading]  = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [newName,    setNewName]    = useState('')
  const [newDesc,    setNewDesc]    = useState('')
  const [creating,   setCreating]   = useState(false)
  const [uploadProject, setUploadProject] = useState(null)
  const navigate = useNavigate()

  async function load() {
    try {
      const res = await projectsApi.list()
      setProjects(res.data)
    } catch { toast.error('Failed to load projects.') }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  async function handleCreate() {
    if (!newName.trim()) return
    setCreating(true)
    try {
      const res = await projectsApi.create({ name: newName.trim(), description: newDesc.trim() })
      toast.success('Project created!')
      setShowCreate(false); setNewName(''); setNewDesc('')
      navigate(`/projects/${res.data.id}`)
    } catch { toast.error('Failed to create project.') }
    finally { setCreating(false) }
  }

  async function handleDelete(e, id) {
    e.stopPropagation()
    if (!confirm('Delete this project and all its data?')) return
    try {
      await projectsApi.delete(id)
      setProjects(p => p.filter(x => x.id !== id))
      toast.success('Project deleted.')
    } catch { toast.error('Failed to delete project.') }
  }

  return (
    <div className="min-h-screen bg-surface-900 p-8">
      {/* Header */}
      <motion.div initial={{ opacity:0, y:-20 }} animate={{ opacity:1, y:0 }} className="mb-10">
        <h1 className="text-3xl font-bold"><span className="gradient-text">My Projects</span></h1>
        <p className="text-slate-400 text-sm mt-1">Traceability audits scoped to your account</p>
      </motion.div>

      {/* Create bar */}
      <div className="mb-6">
        <AnimatePresence>
          {showCreate ? (
            <motion.div
              key="form" initial={{ height:0,opacity:0 }} animate={{ height:'auto',opacity:1 }}
              exit={{ height:0,opacity:0 }}
              className="glass rounded-2xl p-5 mb-4 overflow-hidden"
            >
              <div className="flex flex-col gap-3">
                <input className="input" placeholder="Project name *" value={newName} onChange={e=>setNewName(e.target.value)} />
                <input className="input" placeholder="Description (optional)" value={newDesc} onChange={e=>setNewDesc(e.target.value)} />
                <div className="flex gap-3">
                  <button className="btn-secondary flex-1" onClick={()=>setShowCreate(false)}>Cancel</button>
                  <button className="btn-primary flex-1" onClick={handleCreate} disabled={creating||!newName.trim()}>
                    {creating ? <span className="spinner" /> : <Plus size={16} />}
                    {creating ? 'Creating…' : 'Create Project'}
                  </button>
                </div>
              </div>
            </motion.div>
          ) : (
            <button key="btn" className="btn-primary" onClick={()=>setShowCreate(true)}>
              <Plus size={16} /> New Project
            </button>
          )}
        </AnimatePresence>
      </div>

      {/* Grid */}
      {loading ? (
        <div className="flex items-center justify-center h-48">
          <span className="spinner" style={{width:40,height:40}} />
        </div>
      ) : projects.length === 0 ? (
        <div className="text-center py-20 text-slate-500">
          <div className="text-5xl mb-4">📂</div>
          <p className="text-lg font-medium">No projects yet</p>
          <p className="text-sm">Create your first traceability project above.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {projects.map((proj, i) => (
            <motion.div
              key={proj.id}
              initial={{ opacity:0, y:20 }}
              animate={{ opacity:1, y:0 }}
              transition={{ delay: i * 0.05 }}
              onClick={() => navigate(`/projects/${proj.id}`)}
              className="glass rounded-2xl p-5 cursor-pointer transition-all duration-300
                         hover:border-brand-500/30 hover:shadow-[0_0_20px_rgba(59,130,246,0.1)]
                         border border-white/5 group"
            >
              <div className="flex items-start justify-between mb-3">
                <div className="w-10 h-10 rounded-xl bg-brand-600/20 flex items-center justify-center text-lg">
                  <Folder size={20} className="text-brand-400" />
                </div>
                <button
                  onClick={e => handleDelete(e, proj.id)}
                  className="opacity-0 group-hover:opacity-100 text-slate-600 hover:text-red-400 transition-all"
                >
                  <Trash2 size={15} />
                </button>
              </div>
              <h3 className="font-bold text-white text-base mb-1 truncate">{proj.name}</h3>
              {proj.description && <p className="text-xs text-slate-500 mb-3 line-clamp-2">{proj.description}</p>}
              <div className="flex items-center justify-between text-xs text-slate-500 mb-3">
                <span>{proj.doc_count} file{proj.doc_count !== 1 ? 's' : ''} uploaded</span>
                <span>{proj.has_analysis ? '✅ Analysis ready' : '⏳ Not analysed'}</span>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={e => { e.stopPropagation(); setUploadProject(proj) }}
                  className="btn-secondary text-xs py-1.5 px-3 flex-1"
                >
                  <Upload size={12} /> Upload
                </button>
                <button className="btn-primary text-xs py-1.5 px-3 flex-1">
                  Open <ChevronRight size={12} />
                </button>
              </div>
            </motion.div>
          ))}
        </div>
      )}

      <AnimatePresence>
        {uploadProject && (
          <UploadModal
            project={uploadProject}
            onClose={() => setUploadProject(null)}
            onDone={() => { setUploadProject(null); load() }}
          />
        )}
      </AnimatePresence>
    </div>
  )
}
