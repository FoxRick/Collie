import { useEffect, useState } from 'react'
import { collieClient } from '../../lib/ipc'

interface Props {
  onNotice: (msg: string) => void
}

export default function ProfileTab({ onNotice }: Props): React.JSX.Element {
  const [vision, setVision] = useState('')
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    void (async () => {
      try {
        const resp = await collieClient.command<{ content: string }>('read_file', {
          path: 'VISION.md'
        })
        // read_file returns "" for a missing file — fall back to the default
        // in both cases (missing file OR read error).
        setVision(resp.content || DEFAULT_VISION)
      } catch {
        setVision(DEFAULT_VISION)
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  const save = async (): Promise<void> => {
    try {
      await collieClient.command('write_file', { path: 'VISION.md', content: vision })
      setSaved(true)
      onNotice('Personality saved!')
      setTimeout(() => setSaved(false), 2000)
    } catch (e) {
      onNotice(e instanceof Error ? e.message : 'Failed to save')
    }
  }

  return (
    <div>
      <h3 className="mb-1 font-semibold">Collie's Personality</h3>
      <p className="mb-3 text-sm" style={{ color: 'var(--collie-paw)' }}>
        This is your VISION.md. Collie reads this at the start of every conversation.
      </p>
      {loading ? (
        <div className="py-8 text-center text-sm" style={{ color: 'var(--collie-paw)' }}>
          Looking through your settings...
        </div>
      ) : (
        <>
          <textarea
            value={vision}
            onChange={(e) => setVision(e.target.value)}
            className="h-48 w-full rounded-xl border p-3 text-sm leading-relaxed"
            style={{ borderColor: 'var(--collie-fur)', color: 'var(--collie-nose)' }}
            placeholder="Enter Collie's personality..."
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
            <button
              onClick={() => setVision(DEFAULT_VISION)}
              className="rounded-lg px-4 py-2 text-sm"
              style={{ color: 'var(--collie-paw)', background: 'var(--collie-fur)' }}
            >
              Reset to default
            </button>
          </div>
        </>
      )}
    </div>
  )
}

const DEFAULT_VISION = `You are Collie, a helpful personal assistant. You are direct, warm, and a bit playful. You remember what matters to your user and proactively help.

You never use jargon or technical terms unless the user does first. You explain things simply.

When the user asks you to do something, ask for confirmation before taking action.`
