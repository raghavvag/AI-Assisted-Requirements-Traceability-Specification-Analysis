// frontend/src/components/Markdown.jsx
// GitHub-flavoured Markdown (tables, task lists, strikethrough) for bot comments
// and LLM explanations, styled for the dark UI. Raw HTML is not rendered (the
// bot's <!-- marker --> and <sub> footers are dropped), links open in a new tab.
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const components = {
  h1: (p) => <h1 className="text-[15px] font-bold text-white mt-4 mb-2 first:mt-0" {...p} />,
  h2: (p) => <h2 className="text-[14px] font-bold text-white mt-4 mb-2 first:mt-0" {...p} />,
  h3: (p) => <h3 className="text-[13px] font-semibold text-white mt-4 mb-2" {...p} />,
  h4: (p) => <h4 className="text-[12px] font-semibold text-white/85 mt-3 mb-1.5" {...p} />,
  p: (p) => <p className="text-[12px] text-white/70 leading-relaxed my-2" {...p} />,
  strong: (p) => <strong className="font-semibold text-white/90" {...p} />,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-brand-300 hover:text-brand-200 underline-offset-2 hover:underline">
      {children}
    </a>
  ),
  ul: (p) => <ul className="list-disc pl-5 my-2 space-y-1 text-[12px] text-white/70" {...p} />,
  ol: (p) => <ol className="list-decimal pl-5 my-2 space-y-1 text-[12px] text-white/70" {...p} />,
  blockquote: (p) => (
    <blockquote className="border-l-2 border-white/15 pl-3 my-2 text-white/55 [&_p]:my-1 [&_p]:text-white/55" {...p} />
  ),
  hr: () => <hr className="border-white/8 my-3" />,
  code: ({ inline, className, children, ...rest }) => (
    className
      ? <code className={`${className} block`} {...rest}>{children}</code>
      : <code className="font-mono text-[11px] px-1 py-0.5 rounded bg-white/8 text-brand-200" {...rest}>{children}</code>
  ),
  pre: (p) => <pre className="code-block text-[11px] whitespace-pre-wrap my-2" {...p} />,
  table: (p) => (
    <div className="overflow-x-auto my-2 rounded-lg ring-1 ring-white/8">
      <table className="w-full text-[11px] border-collapse" {...p} />
    </div>
  ),
  thead: (p) => <thead className="bg-white/[0.04]" {...p} />,
  th: (p) => <th className="text-left font-semibold text-white/60 px-3 py-1.5 border-b border-white/8 whitespace-nowrap" {...p} />,
  td: (p) => <td className="px-3 py-1.5 border-b border-white/5 text-white/70 align-top" {...p} />,
}

export default function Markdown({ children, className = '' }) {
  return (
    <div className={`min-w-0 break-words ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children || ''}
      </ReactMarkdown>
    </div>
  )
}
