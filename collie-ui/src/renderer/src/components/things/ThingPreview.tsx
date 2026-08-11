import { useEffect, useState } from 'react'
import { ExternalLink, Loader2 } from 'lucide-react'
import type { Thing } from '../../lib/ipc'
import { useT } from '../../lib/i18n'
import MarkdownContent from '../MarkdownContent'

interface Props {
  thing: Thing
  conversationId: string
  onOpen: (thing: Thing) => void
}

type PreviewState =
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'text'; text: string }
  | { phase: 'image'; dataUrl: string }

/** In-panel preview for a thing: markdown/text or image, else Open fallback. */
export default function ThingPreview({ thing, conversationId, onOpen }: Props): React.JSX.Element {
  const t = useT()
  const [state, setState] = useState<PreviewState>({ phase: 'loading' })

  useEffect(() => {
    let cancelled = false
    setState({ phase: 'loading' })
    window.collie
      .thingRead(conversationId, thing.id)
      .then((result) => {
        if (cancelled) return
        if (result.kind === 'image' && result.dataUrl) {
          setState({ phase: 'image', dataUrl: result.dataUrl })
        } else if (result.kind === 'text' && result.text !== undefined) {
          setState({ phase: 'text', text: result.text })
        } else {
          setState({ phase: 'error', message: t('things.previewFallback') })
        }
      })
      .catch((error: unknown) => {
        if (cancelled) return
        setState({
          phase: 'error',
          message: error instanceof Error ? error.message : t('things.previewFallback')
        })
      })
    return () => {
      cancelled = true
    }
  }, [thing, t])

  return (
    <div className="thing-preview">
      <header className="thing-preview-head">
        <b>{thing.title}</b>
        <button type="button" onClick={() => onOpen(thing)}>
          <ExternalLink size={14} />
          {t('things.open')}
        </button>
      </header>
      <div className="thing-preview-body">
        {state.phase === 'loading' && (
          <div className="thing-preview-loading">
            <Loader2 size={18} className="thing-spin" />
          </div>
        )}
        {state.phase === 'error' && (
          <div className="thing-preview-error">{state.message}</div>
        )}
        {state.phase === 'image' && (
          <img src={state.dataUrl} alt={thing.title} className="thing-preview-image" />
        )}
        {state.phase === 'text' && (
          <div className="thing-preview-text">
            <MarkdownContent content={state.text} />
          </div>
        )}
      </div>
    </div>
  )
}
