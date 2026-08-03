import { useEffect, useState } from 'react'
import { collieClient } from '../../lib/ipc'

interface Props {
  onNotice: (msg: string) => void
}

export default function ContextTab({ onNotice }: Props): React.JSX.Element {
  const [agents, setAgents] = useState('')
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    void (async () => {
      try {
        const resp = await collieClient.command<{ content: string }>('read_file', {
          path: 'AGENTS.md'
        })
        setAgents(resp.content || '')
      } catch {
        setAgents('')
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  const save = async (): Promise<void> => {
    try {
      await collieClient.command('write_file', { path: 'AGENTS.md', content: agents })
      setSaved(true)
      onNotice('Context saved!')
      setTimeout(() => setSaved(false), 2000)
    } catch (e) {
      onNotice(e instanceof Error ? e.message : 'Failed to save')
    }
  }

  return (
    <div>
      <h3 className="mb-1 font-semibold">Instructions for Collie</h3>
      <p className="mb-3 text-sm" style={{ color: 'var(--collie-paw)' }}>
        This is your AGENTS.md. Add anything Collie should know about your life, work, or preferences.
      </p>
      {loading ? (
        <div className="py-8 text-center text-sm" style={{ color: 'var(--collie-paw)' }}>
          Looking through your settings...
        </div>
      ) : (
        <>
          <textarea
            value={agents}
            onChange={(e) => setAgents(e.target.value)}
            className="h-48 w-full rounded-xl border p-3 text-sm leading-relaxed"
            style={{ borderColor: 'var(--collie-fur)', color: 'var(--collie-nose)' }}
            placeholder="I'm a freelance designer. I use Figma, Notion, and Gmail..."
          />
          <div className="mt-2 flex gap-2">
            <button
              onClick={() => void save()}
              disabled={saved}
              className="rounded-lg px-4 py-2 text-sm font-medium text-white"
              style={{ background: saved ? 'var(--collie-grass)' : 'var(--collie-amber)' }}
            >
              {saved ? 'Saved!' : 'Save'}
            </button>
          </div>
        </>
      )}
    </div>
  )
}
