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

  useEffect(() => {
    if (new URLSearchParams(window.location.search).has('preview')) {
      setLoading(false)
      return
    }
    void collieClient
      .listSkills()
      .then((data) => setSkills(data.skills))
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
    setSelected(skill)
    try {
      const detail = await collieClient.getSkill(skill.name)
      setSelected(detail.skill)
    } catch {
      // The summary still contains everything needed for the detail page.
    }
  }

  if (selected) {
    const requirements = selected.requirements
    return (
      <main className="section-workspace flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="section-header section-header--detail">
          <div>
            <button type="button" className="section-back" onClick={() => setSelected(null)}>
              <ArrowLeft size={15} /> All skills
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
                All agents currently inherit this shared skill{selected.available ? '.' : ' once setup is complete.'}
              </p>
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
          <h1>Skills</h1>
          <p>Explore the things Collie and every agent know how to do.</p>
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
                  {skill.available ? 'Ready' : 'Needs setup'}
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
