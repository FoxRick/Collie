import { useEffect, useState } from 'react'
import { PlusCircle, Trash2 } from 'lucide-react'
import { collieClient } from '../../lib/ipc'

interface Props {
  onNotice: (msg: string) => void
}

interface Automation {
  id: string
  name: string
  description?: string
  schedule?: string
  action_type?: string
  enabled: number
}

const BUILT_IN: Array<Omit<Automation, 'id' | 'enabled'>> = [
  {
    name: 'Morning Briefing',
    description: 'Weather + calendar + reminders',
    schedule: 'Every day at wake time',
    action_type: 'briefing'
  },
  {
    name: 'Evening Wind-Down',
    description: "Tomorrow's calendar, bedtime reminder",
    schedule: 'Every day at 9:00 PM',
    action_type: 'briefing'
  },
  {
    name: 'Weekly Review',
    description: 'Week summary, next week preview',
    schedule: 'Sundays at 6:00 PM',
    action_type: 'briefing'
  },
  {
    name: 'Bill Reminders',
    description: 'Upcoming bills this week',
    schedule: 'Fridays at 10:00 AM',
    action_type: 'reminder'
  },
  {
    name: 'Birthday Reminders',
    description: 'Reminder based on people memory',
    schedule: '7 days and 1 day before',
    action_type: 'reminder'
  }
]

export default function AutomationsTab({ onNotice }: Props): React.JSX.Element {
  const [automations, setAutomations] = useState<Automation[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)

  const refresh = async (): Promise<void> => {
    try {
      const data = await collieClient.command<{ automations?: Automation[] }>(
        'list_automations'
      )
      setAutomations(data.automations || [])
    } catch {
      // Use built-in defaults
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  const toggle = async (id: string, enabled: boolean): Promise<void> => {
    try {
      await collieClient.command('toggle_automation', { automation_id: id, enabled })
      setAutomations((prev) =>
        prev.map((a) => (a.id === id ? { ...a, enabled: enabled ? 1 : 0 } : a))
      )
      onNotice(enabled ? 'Automation turned on!' : 'Automation paused.')
    } catch (e) {
      onNotice(e instanceof Error ? e.message : 'Failed to toggle')
    }
  }

  const create = async (): Promise<void> => {
    setBusy(true)
    try {
      await collieClient.createAutomation(description.trim())
      onNotice("Scheduled! I'll be right on time. *tail wag*")
      setCreating(false)
      setDescription('')
      await refresh()
    } catch (e) {
      onNotice(e instanceof Error ? e.message : 'Could not create that one.')
    } finally {
      setBusy(false)
    }
  }

  const remove = async (auto: Automation): Promise<void> => {
    setBusy(true)
    try {
      await collieClient.deleteAutomation(auto.id)
      onNotice(`${auto.name} deleted.`)
      await refresh()
    } catch (e) {
      onNotice(e instanceof Error ? e.message : 'Could not delete.')
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return (
      <div className="py-8 text-center text-sm" style={{ color: 'var(--collie-paw)' }}>
        Sniffing out your automations...
      </div>
    )
  }

  const items =
    automations.length > 0
      ? automations
      : BUILT_IN.map((a, i) => ({
          ...a,
          id: `builtin-${i}`,
          enabled: i === 0 ? 1 : 0
        }))

  return (
    <div>
      <h3 className="mb-1 font-semibold">Automatic Tasks</h3>
      <p className="mb-3 text-sm" style={{ color: 'var(--collie-paw)' }}>
        Collie can check in on a schedule — like a good dog reminding you to eat.
      </p>
      <div className="space-y-2">
        {items.map((auto) => (
          <div
            key={auto.id}
            className="flex items-center gap-3 rounded-xl border p-3"
            style={{ borderColor: 'var(--collie-fur)' }}
          >
            <button
              onClick={() => void toggle(auto.id, auto.enabled !== 1)}
              className={`flex h-6 w-10 shrink-0 items-center rounded-full px-0.5 transition-colors ${
                auto.enabled === 1 ? 'justify-end' : 'justify-start'
              }`}
              style={{
                background: auto.enabled === 1 ? 'var(--collie-grass)' : 'var(--collie-fur)'
              }}
            >
              <div className="h-5 w-5 rounded-full bg-white shadow" />
            </button>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium" style={{ color: 'var(--collie-nose)' }}>
                {auto.name}
              </div>
              {auto.description && (
                <div className="text-xs" style={{ color: 'var(--collie-paw)' }}>
                  {auto.description} · {auto.schedule}
                </div>
              )}
            </div>
            {!auto.id.startsWith('collie-') && !auto.id.startsWith('builtin-') && (
              <button
                onClick={() => void remove(auto)}
                disabled={busy}
                title="Delete"
                className="shrink-0 rounded p-1 disabled:opacity-50"
                style={{ color: 'var(--collie-paw)' }}
              >
                <Trash2 size={14} />
              </button>
            )}
          </div>
        ))}
      </div>

      <button
        onClick={() => setCreating(!creating)}
        className="mt-4 flex items-center gap-1 text-sm"
        style={{ color: 'var(--collie-text-link)' }}
      >
        <PlusCircle size={15} /> Create custom automation
      </button>

      {creating && (
        <div
          className="mt-3 flex flex-col gap-2 rounded-xl border p-3"
          style={{ borderColor: 'var(--collie-fur)' }}
        >
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            placeholder='Describe what you want, with a time — e.g. "Every Friday at 5pm, ask me how my week went and suggest something fun this weekend."'
            className="rounded-lg border px-3 py-2 text-sm"
            style={{ borderColor: 'var(--collie-border)' }}
          />
          <button
            onClick={() => void create()}
            disabled={busy || !description.trim()}
            className="self-start rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            style={{ background: 'var(--collie-btn-primary-bg)' }}
          >
            Create Automation
          </button>
        </div>
      )}
    </div>
  )
}
