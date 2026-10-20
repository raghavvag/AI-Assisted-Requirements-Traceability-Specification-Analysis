// frontend/src/pages/DashboardPage.jsx
// Project management dashboard with file upload modal
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'motion/react'
import { Plus, Folder, ChevronRight, Upload, X, Trash2, CheckCircle, AlertTriangle } from 'lucide-react'
import { apiError, projectsApi } from '../services/api'
import SrsPreview from '../components/SrsPreview'
import PrActivity from '../components/PrActivity'
import { useDropzone } from 'react-dropzone'
import toast from 'react-hot-toast'

// ── Upload modal ────────────────────────────────────────────────────────────
const MAX_UPLOAD_MB = 10

// react-dropzone accepts a file when its MIME type OR its extension matches.
// Windows often reports no MIME type for .md / .yaml, so extensions are listed
// under every type.
const ACCEPT = {
  SRS: {
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
    'application/pdf': ['.pdf'],
    'text/markdown': ['.md'],
    'text/plain': ['.txt', '.md'],
  },
  API: {
    'application/json': ['.json'],
    'application/x-yaml': ['.yaml', '.yml'],
    'text/yaml': ['.yaml', '.yml'],
    'text/plain': ['.json', '.yaml', '.yml'],
  },
}

const SLOTS = [
  { docType: 'SRS',    label: 'Requirements SRS', hint: '.docx · .pdf · .md · .txt', accept: ACCEPT.SRS },
  { docType: 'API_V1', label: 'OpenAPI spec v1',  hint: '.json · .yaml', accept: ACCEPT.API },
  { docType: 'API_V2', label: 'OpenAPI spec v2',  hint: '.json · .yaml · enables drift analysis', accept: ACCEPT.API },
]

function Dropzone({ slot, file, status, onFile }) {
  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: slot.accept,
    maxFiles: 1,
    maxSize: MAX_UPLOAD_MB * 1024 * 1024,
    onDrop: (accepted) => { if (accepted[0]) onFile(accepted[0]) },
    onDropRejected: (rejections) => {
      const err = rejections[0]?.errors?.[0]
      const exts = slot.hint.split(' · ').filter((h) => h.startsWith('.')).join(', ')
      toast.error(err?.code === 'file-too-large'
        ? `${slot.label}: file is larger than ${MAX_UPLOAD_MB} MB`
        : `${slot.label}: use ${exts}`)
    },
  })

  const state = status?.state
  const tone = state === 'error' ? 'bg-noncompliant/15 text-noncompliant'
    : (file || state === 'done') ? 'bg-green-500/20 text-green-400' : 'bg-white/5 text-slate-500'
  return (
    <div>
      <div {...getRootProps()} className={`rounded-xl border-2 border-dashed p-4 cursor-pointer transition-colors
          ${isDragActive ? 'border-brand-500 bg-brand-600/10'
            : state === 'error' ? 'border-noncompliant/40' : 'border-white/10 hover:border-white/20'}`}>
        <input {...getInputProps()} />
        <div className="flex items-center gap-3">
          <div className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${tone}`}>
            {state === 'uploading' ? <span className="spinner" style={{ width: 16, height: 16 }} />
              : state === 'error' ? <AlertTriangle size={18} />
              : (file || state === 'done') ? <CheckCircle size={18} /> : <Upload size={18} />}
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-white">
              {slot.label} <span className="text-xs font-normal text-slate-500">({slot.hint})</span>
            </p>
            <p className="text-xs text-slate-500 truncate">
              {file ? file.name : 'Drop file or click to browse'}
            </p>
          </div>
        </div>
      </div>
      {status?.message && (
        <p className={`text-[11px] mt-1.5 px-1 ${state === 'error' ? 'text-noncompliant' : 'text-compliant'}`}>
          {status.message}
        </p>
      )}
    </div>
  )
}

function UploadModal({ project, onClose, onDone }) {
  const [files, setFiles] = useState({ SRS: null, API_V1: null, API_V2: null })
  const [status, setStatus] = useState({})
  const [uploading, setUploading] = useState(false)
  const [srsPreview, setSrsPreview] = useState(null)
  const [uploadedAny, setUploadedAny] = useState(false)

  function setFile(docType, file) {
    setFiles((f) => ({ ...f, [docType]: file }))
    setStatus((s) => ({ ...s, [docType]: undefined }))
  }

  async function handleUpload() {
    setUploading(true)
    let ok = 0
    let failed = 0
    let srsDone = false
    for (const [docType, file] of Object.entries(files)) {
      if (!file) continue
      setStatus((s) => ({ ...s, [docType]: { state: 'uploading' } }))
      try {
        const fd = new FormData()
        fd.append('doc_type', docType)
        fd.append('file', file)
        const res = await projectsApi.upload(project.id, fd)
        const ex = res.data.extraction
        const message = ex
          ? `${ex.n_requirements} requirements extracted${ex.n_warnings ? ` · ${ex.n_warnings} warning(s)` : ''}`
          : 'Uploaded'
        setStatus((s) => ({ ...s, [docType]: { state: 'done', message } }))
        setFiles((f) => ({ ...f, [docType]: null }))
        if (docType === 'SRS') srsDone = true
        ok++
      } catch (err) {
        failed++
        setStatus((s) => ({ ...s, [docType]: { state: 'error', message: apiError(err, `Failed to upload ${docType}`) } }))
      }
    }
    setUploading(false)
    if (ok) setUploadedAny(true)

    if (srsDone && !failed) {
      try {
        const res = await projectsApi.srsRequirements(project.id)
        setSrsPreview(res.data)
      } catch { /* the preview is optional; the upload already succeeded */ }
    }
    if (failed) toast.error(`${failed} file(s) were rejected. See the messages below.`)
    else if (ok && !srsDone) { toast.success(`${ok} file(s) uploaded`); onDone() }
  }

  function close() { uploadedAny ? onDone() : onClose() }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm px-4">
      <motion.div
        initial={{ scale: 0.9, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.9, opacity: 0 }}
        className={`glass-strong rounded-2xl p-6 w-full shadow-2xl max-h-[92vh] overflow-y-auto
          ${srsPreview ? 'max-w-2xl' : 'max-w-md'}`}
      >
        <div className="flex items-center justify-between mb-5">
          <div>
            <h2 className="font-bold text-lg text-white">
              {srsPreview ? 'Review extracted requirements' : 'Upload Artifacts'}
            </h2>
            <p className="text-xs text-slate-400">{project.name}</p>
          </div>
          <button onClick={close} className="text-slate-500 hover:text-white"><X size={18} /></button>
        </div>

        {srsPreview ? (
          <>
            <SrsPreview data={srsPreview} />
            <p className="text-[11px] text-slate-500 mt-3">
              These requirements become the baseline for the analysis. If one is missing, fix the document
              (tables need an ID column and a Description column; headings or paragraphs should start with an
              ID such as FR-1 or REQ-04) and upload it again.
            </p>
            <div className="flex gap-3 mt-4">
              <button className="btn-secondary flex-1" onClick={() => setSrsPreview(null)}>Upload more files</button>
              <button className="btn-primary flex-1" onClick={onDone}>Looks good</button>
            </div>
          </>
        ) : (
          <>
            <div className="flex flex-col gap-3 mb-5">
              {SLOTS.map((slot) => (
                <Dropzone
                  key={slot.docType}
                  slot={slot}
                  file={files[slot.docType]}
                  status={status[slot.docType]}
                  onFile={(f) => setFile(slot.docType, f)}
                />
              ))}
            </div>

            <div className="flex gap-3">
              <button className="btn-secondary flex-1" onClick={close}>{uploadedAny ? 'Close' : 'Cancel'}</button>
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
              Formal SRS documents (IEEE 830 / ISO 29148, Word or PDF) are supported: requirement tables and
              tagged headings are extracted and boilerplate is removed. Max {MAX_UPLOAD_MB} MB per file.
              Without uploads, the built-in E-Commerce demo dataset is used.
            </p>
          </>
        )}
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

      {/* Live bot reviews; rows open in the first project's PR Bot page */}
      <PrActivity projectId={projects[0]?.id} />

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
