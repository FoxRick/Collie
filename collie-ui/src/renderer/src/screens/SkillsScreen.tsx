import { ui } from "../lib/i18n"
import { useEffect, useMemo, useState } from 'react'
import { ArrowLeft, CheckCircle2, Search, TriangleAlert } from 'lucide-react'
import { collieClient, type CollieSkill } from '../lib/ipc'
import SkillIcon from '../components/SkillIcon'

const SKILL_CATEGORIES = ['All', 'Image', 'Video', 'Data', 'Productivity'] as const
type SkillCategory = (typeof SKILL_CATEGORIES)[number]

function skillCategory(skill: CollieSkill): SkillCategory {
  const copy = `${skill.name} ${skill.description}`.toLowerCase()
  if (/(image|photo|visual|draw|design|graphic|ocr)/.test(copy)) return 'Image'
  if (/(video|film|clip|animation|motion)/.test(copy)) return 'Video'
  if (/(data|spreadsheet|sheet|database|sql|chart|analytics|csv)/.test(copy)) return 'Data'
  return 'Productivity'
}

export default function SkillsScreen(): React.JSX.Element {
  const [skills, setSkills] = useState<CollieSkill[]>([])
  const [selected, setSelected] = useState<CollieSkill | null>(null)
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [notice, setNotice] = useState('')
  const [category, setCategory] = useState<SkillCategory>('All')
  const [draft, setDraft] = useState('')
  const [deleted, setDeleted] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<string[]>([])

  const refresh = async (): Promise<void> => {
    const data = await collieClient.listSkills()
    setSkills(data.skills)
    setDeleted(data.deleted || [])
    setPending((data.pending || []).map((item) => item.name))
    if (data.problems?.length) setNotice(data.problems.map((p) => `${p.name}: ${p.message}`).join(' '))
  }

  useEffect(() => {
    if (new URLSearchParams(window.location.search).has('preview')) {
      setLoading(false)
      return
    }
    void refresh()
      .catch(() => setNotice('I could not load the skill library just yet.'))
      .finally(() => setLoading(false))
  }, [])

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return skills.filter(
      (skill) =>
        (category === 'All' || skillCategory(skill) === category) &&
        (!needle ||
          skill.name.toLowerCase().includes(needle) ||
          skill.description.toLowerCase().includes(needle))
    )
  }, [category, query, skills])

  const openSkill = async (skill: CollieSkill): Promise<void> => {
    setSelected({ ...skill, revision: undefined })
    setDraft('')
    try {
      const detail = await collieClient.getSkillEditor(skill.name)
      setSelected(detail.skill)
      setDraft(detail.skill.content || '')
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'I could not open these instructions.')
    }
  }

  const changeSkill = async (action: Parameters<typeof collieClient.manageSkill>[0]['action'], name = selected?.name, version?: number): Promise<void> => {
    if (!name) return
    setBusy(true)
    setNotice('')
    try {
      const result = await collieClient.manageSkill({ name, action, revision: selected?.revision, content: draft, version })
      setNotice(result.pending ? 'Saved for after the current tasks finish. Refresh to see the result.' : 'Your skill library is updated.')
      await refresh()
      if (!result.pending && selected) {
        if (action === 'delete') setSelected(null)
        else await openSkill(selected)
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'I could not save that skill change.')
    } finally {
      setBusy(false)
    }
  }

  if (selected) {
    const requirements = selected.requirements
    return (
      <main className="section-workspace flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="section-header section-header--detail">
          <div>
            <button type="button" className="section-back" onClick={() => setSelected(null)}>
              <ArrowLeft size={15} /> {ui("All skills")}
            </button>
            <div className="workspace-eyebrow">SKILL PROFILE</div>
            <h1>{selected.name.replace(/-/g, ' ')}</h1>
            <p>{selected.description}</p>
          </div>
          <span className={`availability-pill ${selected.available ? 'is-ready' : 'is-blocked'}`}>
            {selected.available ? <CheckCircle2 size={14} /> : <TriangleAlert size={14} />}
            {selected.available ? 'Ready' : 'Needs setup'}
          </span>
        </header>
        <div className="detail-scroll">
          {notice && <p role="status">{notice}</p>}
          {pending.includes(selected.name) && <p role="status">A change is waiting for the current tasks to finish.</p>}
          <div className="skill-detail-grid">
            <section className="detail-card">
              <span className="detail-label">ABOUT</span>
              <h2 className="skill-detail-title">What this skill adds</h2>
              <p className="skill-detail-copy">{selected.description}</p>
            </section>
            <section className="detail-card">
              <span className="detail-label">ACCESS</span>
              <h2 className="skill-detail-title">Available to your agents</h2>
              <p className="skill-detail-copy">
                {selected.enabled === false ? 'This skill is disabled for new tasks.' : 'Your agents can discover this skill when its requirements are ready.'}
              </p>
            </section>
            <section className="detail-card detail-card--wide">
              <h2 className="skill-detail-title">Manage instructions</h2>
              <p>{selected.source === 'builtin' ? 'Saving creates a custom copy, including supporting assets. The built-in original stays safe.' : 'Changes keep supporting assets and record an instruction version.'} Changes wait until current tasks finish.</p>
              <label htmlFor="skill-instructions">Skill Markdown (including its name and description header)</label>
              <textarea id="skill-instructions" value={draft} onChange={(event) => setDraft(event.target.value)} rows={18} maxLength={32000} className="w-full" />
              <div className="flex flex-wrap gap-2">
                <button type="button" disabled={busy || !selected.revision || pending.includes(selected.name)} onClick={() => void changeSkill('edit')}>Save instructions</button>
                <button type="button" disabled={busy || !selected.revision || pending.includes(selected.name)} onClick={() => void changeSkill(selected.enabled === false ? 'enable' : 'disable')}>{selected.enabled === false ? 'Enable' : 'Disable'}</button>
                {selected.source === 'workspace' && <button type="button" disabled={busy || pending.includes(selected.name)} onClick={() => void changeSkill(selected.overridden ? 'reset' : 'delete')}>{selected.overridden ? 'Reset built-in' : 'Delete (recoverable)'}</button>}
                <button type="button" disabled={busy} onClick={() => { void refresh().catch(() => setNotice('I could not refresh the library.')); void openSkill(selected) }}>Reload saved instructions</button>
              </div>
              {!!selected.versions?.length && <div><h3>Earlier instructions</h3>{selected.versions.map((item) => <button key={item.version} type="button" disabled={busy || pending.includes(selected.name)} onClick={() => void changeSkill('version', selected.name, item.version)}>Restore before edit {item.version} ({item.source})</button>)}</div>}
            </section>
            <section className="detail-card detail-card--wide">
              <span className="detail-label">SETUP</span>
              <h2 className="skill-detail-title">Requirements</h2>
              {selected.available ? (
                <div className="requirement-ready"><CheckCircle2 size={16} /> Everything this skill needs is ready.</div>
              ) : (
                <div className="requirement-list">
                  <p>{selected.unavailable_reason || 'This skill needs a little more setup.'}</p>
                  {requirements?.missing_bins.map((item) => <span key={`bin-${item}`}>App: {item}</span>)}
                  {requirements?.missing_env.map((item) => <span key={`env-${item}`}>Connection: {item}</span>)}
                </div>
              )}
            </section>
          </div>
        </div>
      </main>
    )
  }

  return (
    <main className="section-workspace flex min-w-0 flex-1 flex-col overflow-hidden">
      <header className="section-header">
        <div>
          <h1>{ui("Skills")}</h1>
          <p>{ui("Explore the things Collie and every agent know how to do.")}</p>
        </div>
        <div className="catalog-search">
          <Search size={14} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search skills"
            aria-label="Search skills"
          />
        </div>
      </header>
      {!!pending.length && <p role="status">Changes waiting for tasks to finish: {pending.join(', ')}. <button type="button" onClick={() => void refresh().catch(() => setNotice('I could not refresh the library.'))}>Refresh library</button></p>}
      {!!deleted.length && <section aria-label="Deleted skills"><h2>Recover deleted skills and reset copies</h2>{deleted.map((name) => <button key={name} type="button" disabled={busy || pending.includes(name)} onClick={() => void changeSkill('restore', name)}>Restore {name}</button>)}</section>}
      <div className="section-scroll">
        {notice && <p className="inline-notice" role="status">{notice}</p>}
        <div className="catalog-filters" aria-label="Filter skills by category">
          {SKILL_CATEGORIES.map((item) => (
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
          <div className="section-loading">Checking Collie's skill library...</div>
        ) : filtered.length > 0 ? (
          <div className="skill-grid">
            {filtered.map((skill) => (
              <button key={skill.name} type="button" className="skill-card" onClick={() => void openSkill(skill)}>
                <SkillIcon name={`${skill.name} ${skill.description}`} />
                <span className="skill-card-copy">
                  <b>{skill.name.replace(/-/g, ' ')}</b>
                  <span>{skill.description}</span>
                </span>
                <span className={`skill-status ${skill.available ? 'is-ready' : 'is-blocked'}`}>
                  {skill.enabled === false ? 'Disabled' : skill.available ? 'Ready' : 'Needs setup'}
                </span>
                <small>{skillCategory(skill)} · {skill.source === 'workspace' ? 'Custom' : 'Built in'}</small>
              </button>
            ))}
          </div>
        ) : (
          <div className="section-empty">
            <span className="section-placeholder-icon"><Search size={23} /></span>
            <h2>No skills found</h2>
            <p>Try a broader search.</p>
          </div>
        )}
      </div>
    </main>
  )
}
