import { useEffect, useMemo, useState } from 'react'
import { ArrowLeft, Bot, Eye, Plus, Shapes, Sparkles, Trash2, Zap, X } from 'lucide-react'
import { collieClient, type CollieSkill, type Subagent, type SubagentStarter } from '../lib/ipc'
import AgentAvatar from '../components/AgentAvatar'

function summarizeDescription(description: string): string {
  const clean = description.replace(/\s+/g, ' ').trim()
  if (!clean) return 'A specialized Collie helper.'
  const firstSentence = clean.split(/(?<=[.!?])\s/)[0]
  return firstSentence.length > 108
    ? `${firstSentence.slice(0, 105).trimEnd()}…`
    : firstSentence
}

const AGENT_CATEGORIES = ['All', 'Work', 'Life', 'Research', 'Creative', 'General'] as const
type AgentCategory = (typeof AGENT_CATEGORIES)[number]

function agentCategory(agent: Pick<Subagent, 'name' | 'description'> | SubagentStarter): AgentCategory {
  const copy = `${agent.name} ${agent.description}`.toLowerCase()
  if (/(research|web|source|fact|investigat|market|competitor)/.test(copy)) return 'Research'
  if (/(write|creative|design|content|story|brand|image)/.test(copy)) return 'Creative'
  if (/(trip|travel|meal|home|family|health|fitness|shopping|personal)/.test(copy)) return 'Life'
  if (/(work|project|meeting|email|code|analyst|data|report|business|finance)/.test(copy)) return 'Work'
  return 'General'
}

export default function AgentsScreen(): React.JSX.Element {
  const [agents, setAgents] = useState<Subagent[]>([])
  const [starters, setStarters] = useState<SubagentStarter[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [prompt, setPrompt] = useState('')
  const [executionPosture, setExecutionPosture] =
    useState<'read_only' | 'inherit'>('read_only')
  const [notice, setNotice] = useState('')
  const [skills, setSkills] = useState<CollieSkill[]>([])
  const [category, setCategory] = useState<AgentCategory>('All')

  const selected = useMemo(
    () => agents.find((agent) => agent.id === selectedId) ?? null,
    [agents, selectedId]
  )

  const refresh = async (): Promise<void> => {
    if (new URLSearchParams(window.location.search).has('preview')) {
      setLoading(false)
      return
    }
    try {
      const [data, skillData] = await Promise.all([
        collieClient.listSubagents(),
        collieClient.listSkills().catch(() => ({ skills: [] }))
      ])
      setAgents(data.subagents)
      setStarters(data.starters)
      setSkills(skillData.skills)
      setSelectedId((current) =>
        current && data.subagents.some((agent) => agent.id === current) ? current : null
      )
    } catch {
      setNotice('I could not round up your agents just yet.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  useEffect(() => {
    setPrompt(selected?.system_prompt ?? '')
    setExecutionPosture(selected?.execution_posture ?? 'read_only')
  }, [selected])

  const createAgent = async (
    nextName = name.trim(),
    nextDescription = description.trim(),
    systemPrompt?: string,
    executionPosture: 'read_only' | 'inherit' = 'read_only'
  ): Promise<void> => {
    if (!nextName) return
    setBusy(true)
    setNotice('')
    try {
      const result = await collieClient.createSubagent(
        nextName,
        nextDescription,
        systemPrompt,
        executionPosture
      )
      await refresh()
      setSelectedId(result.subagent.id)
      setCreating(false)
      setName('')
      setDescription('')
      setNotice(`${nextName} joined the team.`)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'I could not create that agent.')
    } finally {
      setBusy(false)
    }
  }

  const saveAgent = async (): Promise<void> => {
    if (!selected || !prompt.trim()) return
    setBusy(true)
    try {
      await collieClient.updateSubagent(selected.id, {
        system_prompt: prompt.trim(),
        execution_posture: executionPosture
      })
      await refresh()
      setNotice(`${selected.name} is up to date.`)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'I could not save those changes.')
    } finally {
      setBusy(false)
    }
  }

  const deleteAgent = async (): Promise<void> => {
    if (!selected || !window.confirm(`Remove ${selected.name} from your team?`)) return
    setBusy(true)
    try {
      await collieClient.deleteSubagent(selected.id)
      setSelectedId(null)
      await refresh()
      setNotice(`${selected.name} is off the team.`)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'I could not remove that agent.')
    } finally {
      setBusy(false)
    }
  }

  if (selected) {
    return (
      <main className="section-workspace flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="section-header section-header--detail">
          <div>
            <button className="section-back" type="button" onClick={() => setSelectedId(null)}>
              <ArrowLeft size={15} /> All agents
            </button>
            <div className="agent-profile-title">
              <AgentAvatar identity={selected.id} name={selected.name} size={64} />
              <div>
                <div className="workspace-eyebrow">AGENT PROFILE</div>
                <h1>{selected.name}</h1>
              </div>
            </div>
            <p>{selected.description || 'A focused helper on your Collie team.'}</p>
          </div>
          <button
            type="button"
            className="danger-button"
            onClick={() => void deleteAgent()}
            disabled={busy}
          >
            <Trash2 size={15} /> Remove agent
          </button>
        </header>
        <div className="detail-scroll">
          <div className="detail-grid">
            <section className="detail-card">
              <div className="detail-card-heading">
                <div>
                  <span className="detail-label">Capabilities</span>
                  <h2>Skills this agent can use</h2>
                </div>
                <Shapes size={18} />
              </div>
              <div className="skill-inheritance">
                <b>{skills.filter((skill) => skill.available).length} available skills</b>
                <span>This agent inherits Collie's shared skill set.</span>
              </div>
              <div className="agent-skill-list" aria-label={`Skills available to ${selected.name}`}>
                {skills.map((skill) => (
                  <span key={skill.name} className={skill.available ? '' : 'is-unavailable'}>
                    {skill.name.replace(/-/g, ' ')}
                  </span>
                ))}
              </div>
            </section>
            <section className="detail-card detail-card--wide">
              <div className="detail-card-heading">
                <div>
                  <span className="detail-label">Advanced</span>
                  <h2>Agent instructions</h2>
                </div>
              </div>
              <p className="detail-help">
                These instructions shape how {selected.name} approaches work and communicates.
              </p>
              <fieldset className="agent-access-control">
                <legend>Action access</legend>
                <p>
                  Choose whether this agent only researches and advises, or can use Collie's
                  approved action tools.
                </p>
                <div>
                  <label className={executionPosture === 'read_only' ? 'is-selected' : ''}>
                    <input
                      type="radio"
                      name="agent-access"
                      value="read_only"
                      checked={executionPosture === 'read_only'}
                      onChange={() => setExecutionPosture('read_only')}
                    />
                    <Eye size={14} />
                    <span><b>Read-only</b><small>Research and recommendations</small></span>
                  </label>
                  <label className={executionPosture === 'inherit' ? 'is-selected' : ''}>
                    <input
                      type="radio"
                      name="agent-access"
                      value="inherit"
                      checked={executionPosture === 'inherit'}
                      onChange={() => setExecutionPosture('inherit')}
                    />
                    <Zap size={14} />
                    <span><b>Can take action</b><small>Uses the chat's approvals and access</small></span>
                  </label>
                </div>
              </fieldset>
              <textarea
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                rows={14}
                className="agent-prompt"
                aria-label={`Instructions for ${selected.name}`}
              />
              <div className="detail-actions">
                <button
                  type="button"
                  className="primary-button"
                  onClick={() => void saveAgent()}
                  disabled={
                    busy ||
                    !prompt.trim() ||
                    (prompt === selected.system_prompt &&
                      executionPosture === selected.execution_posture)
                  }
                >
                  Save changes
                </button>
              </div>
            </section>
          </div>
          {notice && <p className="inline-notice" role="status">{notice}</p>}
        </div>
      </main>
    )
  }

  const existing = new Set(agents.map((agent) => agent.name.toLowerCase()))
  const availableStarters = starters.filter((starter) => !existing.has(starter.name.toLowerCase()))
  const visibleAgents = category === 'All'
    ? agents
    : agents.filter((agent) => agentCategory(agent) === category)

  return (
    <main className="section-workspace flex min-w-0 flex-1 flex-col overflow-hidden">
      <header className="section-header">
        <div>
          <h1>Agents</h1>
          <p>Focused helpers with their own instructions and access to Collie's skills.</p>
        </div>
        <button
          type="button"
          className="primary-button"
          onClick={() => {
            setExecutionPosture('read_only')
            setCreating(true)
          }}
        >
          <Plus size={16} /> New agent
        </button>
      </header>

      <div className="section-scroll">
        {notice && <p className="inline-notice" role="status">{notice}</p>}
        <div className="catalog-filters" aria-label="Filter agents by category">
          {AGENT_CATEGORIES.map((item) => (
            <button
              key={item}
              type="button"
              className={category === item ? 'is-active' : ''}
              aria-pressed={category === item}
              onClick={() => setCategory(item)}
            >
              {item}
            </button>
          ))}
        </div>
        {loading ? (
          <div className="section-loading">Rounding up your team...</div>
        ) : visibleAgents.length > 0 ? (
          <div className="agent-grid">
            {visibleAgents.map((agent) => (
              <button
                key={agent.id}
                type="button"
                className="agent-card"
                onClick={() => setSelectedId(agent.id)}
              >
                <AgentAvatar identity={agent.id} name={agent.name} size={54} />
                <span className="agent-card-copy" title={agent.description || undefined}>
                  <b>{agent.name}</b>
                  <span>{summarizeDescription(agent.description)}</span>
                </span>
                <span className="agent-card-meta">
                  <span><Shapes size={12} /> {agentCategory(agent)}</span>
                  <span>
                    {agent.execution_posture === 'read_only'
                      ? <><Eye size={12} /> Read-only</>
                      : <><Zap size={12} /> Can take action</>}
                  </span>
                </span>
              </button>
            ))}
          </div>
        ) : (
          <div className="section-empty">
            <span className="section-placeholder-icon"><Bot size={24} /></span>
            <h2>Build your first specialist</h2>
            <p>Create an agent for work you do often, or start with one of Collie's favorites.</p>
          </div>
        )}

        {availableStarters.length > 0 && (
          <section className="starter-section">
            <div className="starter-heading">
              <div>
                <span className="detail-label">QUICK START</span>
                <h2>Ready-made teammates</h2>
              </div>
              <Sparkles size={18} />
            </div>
            <div className="starter-grid">
              {availableStarters.map((starter) => (
                <button
                  key={starter.name}
                  type="button"
                  className="starter-card"
                  disabled={busy}
                  onClick={() =>
                    void createAgent(
                      starter.name,
                      starter.description,
                      starter.system_prompt,
                      starter.execution_posture || 'read_only'
                    )
                  }
                >
                  <AgentAvatar identity={starter.name} name={starter.name} size={42} />
                  <b>{starter.name}</b>
                  <span>{starter.description}</span>
                  <small><Plus size={12} /> Add to team</small>
                </button>
              ))}
            </div>
          </section>
        )}
      </div>

      {creating && (
        <div className="dialog-backdrop" role="presentation">
          <section
            className="dialog-card"
            role="dialog"
            aria-modal="true"
            aria-labelledby="new-agent-title"
          >
            <div className="dialog-heading">
              <div>
                <span className="detail-label">NEW TEAMMATE</span>
                <h2 id="new-agent-title">Create an agent</h2>
              </div>
              <button type="button" className="icon-button" onClick={() => setCreating(false)} aria-label="Close">
                <X size={17} />
              </button>
            </div>
            <label className="form-field">
              <span>Name</span>
              <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Trip Planner" autoFocus />
            </label>
            <label className="form-field">
              <span>What should this agent be good at?</span>
              <textarea
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                rows={4}
                placeholder="Plans trips, compares options, and builds practical itineraries."
              />
            </label>
            <fieldset className="agent-access-control agent-access-control--dialog">
              <legend>Action access</legend>
              <p>You can change this later from the agent profile.</p>
              <div>
                <label className={executionPosture === 'read_only' ? 'is-selected' : ''}>
                  <input
                    type="radio"
                    name="new-agent-access"
                    value="read_only"
                    checked={executionPosture === 'read_only'}
                    onChange={() => setExecutionPosture('read_only')}
                  />
                  <Eye size={14} />
                  <span><b>Read-only</b><small>Research and advise</small></span>
                </label>
                <label className={executionPosture === 'inherit' ? 'is-selected' : ''}>
                  <input
                    type="radio"
                    name="new-agent-access"
                    value="inherit"
                    checked={executionPosture === 'inherit'}
                    onChange={() => setExecutionPosture('inherit')}
                  />
                  <Zap size={14} />
                  <span><b>Can take action</b><small>Subject to approvals</small></span>
                </label>
              </div>
            </fieldset>
            <p className="dialog-hint">Collie will write the detailed instructions. You can refine them afterward.</p>
            <div className="dialog-actions">
              <button type="button" className="secondary-button" onClick={() => setCreating(false)}>Cancel</button>
              <button
                type="button"
                className="primary-button"
                disabled={busy || !name.trim()}
                onClick={() =>
                  void createAgent(name.trim(), description.trim(), undefined, executionPosture)
                }
              >
                {busy ? 'Writing instructions...' : 'Create agent'}
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  )
}
