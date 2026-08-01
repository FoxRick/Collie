import { Bot, LockKeyhole, Play, Shapes, Workflow } from 'lucide-react'

interface Props {
  data: Record<string, unknown>
}

const agentEmoji: Record<string, string> = {
  researcher: '🔎',
  analyst: '📊',
  reviewer: '🛡️',
  operator: '⚙️'
}

function compose(command: string): void {
  window.dispatchEvent(new CustomEvent<string>('collie:compose-command', { detail: command }))
}

export default function CapabilityListCard({ data }: Props): React.JSX.Element {
  const kind = data.kind === 'skill' ? 'skill' : 'agent'
  const items = Array.isArray(data.items)
    ? data.items.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
    : []

  return (
    <section className="capability-list-card" aria-label={kind === 'agent' ? 'Agents' : 'Skills'}>
      <div className="capability-list-heading">
        {kind === 'agent' ? <Bot size={17} /> : <Shapes size={17} />}
        <span>{kind === 'agent' ? 'Specialist agents' : 'Reusable skills'}</span>
      </div>
      <div className="capability-grid">
        {items.map((item) => {
          const name = String(item.name || '')
          const available = item.available !== false
          const emoji = kind === 'agent'
            ? (agentEmoji[name.toLowerCase()] || '🐕')
            : '✨'
          const command = kind === 'agent' ? `/agent ${name} ` : `/skill ${name} `
          return (
            <button
              type="button"
              className="capability-card"
              key={`${kind}-${name}`}
              disabled={!available}
              onClick={() => compose(command)}
            >
              <span className={`capability-avatar capability-avatar--${kind}`}>{emoji}</span>
              <span className="capability-card-copy">
                <b>{name}</b>
                <small>{String(item.description || 'No description yet.')}</small>
                <em>
                  {!available
                    ? <><LockKeyhole size={11} /> Unavailable</>
                    : kind === 'agent'
                      ? <><Play size={11} /> Ask this agent</>
                      : <><Workflow size={11} /> Use this skill</>}
                </em>
              </span>
            </button>
          )
        })}
      </div>
    </section>
  )
}
