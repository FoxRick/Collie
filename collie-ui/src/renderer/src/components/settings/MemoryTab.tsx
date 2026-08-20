import { useCallback, useEffect, useState } from 'react'
import { Check, History, Pencil, Plus, Sparkles, Trash2, Undo2, X } from 'lucide-react'
import { collieClient, type ArtifactVersion } from '../../lib/ipc'
import DiffView from '../cards/DiffView'

interface Props {
  onNotice: (msg: string) => void
}

interface ProfileEntry {
  [key: string]: string
}

interface Person {
  id: string
  name: string
  relationship?: string
  birthday?: string
  allergies?: string
  preferences?: string
  gift_ideas?: string
  notes?: string
}

interface DateEntry {
  id: string
  date: string
  label: string
  recurring: number
}

type EditTarget =
  | { kind: 'profile'; id: string; draft: string }
  | { kind: 'person'; id: string; draft: Person }
  | { kind: 'date'; id: string; draft: DateEntry }
  | null

type AddKind = 'profile' | 'person' | 'date'

const PROFILE_LABELS: Record<string, string> = {
  dietary: 'Food & allergies',
  wake_time: 'Wakes up',
  sleep_time: 'Goes to sleep',
  location: 'Lives in',
  timezone: 'Timezone',
  medications: 'Medications',
  goals: 'Current goals',
  work: 'Work',
  family: 'Family',
  notes: 'Other notes'
}

/** Keep only real person fields, dropping UI-only keys and empty values. */
export function personFieldsFrom(entry: object): Record<string, string> {
  return Object.fromEntries(
    Object.entries(entry).filter(
      ([key, value]) =>
        key !== 'id' && typeof value === 'string' && value.trim() !== ''
    )
  )
}

export default function MemoryTab({ onNotice }: Props): React.JSX.Element {
  const [profile, setProfile] = useState<ProfileEntry>({})
  const [people, setPeople] = useState<Person[]>([])
  const [dates, setDates] = useState<DateEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<EditTarget>(null)
  const [saving, setSaving] = useState(false)
  const [adding, setAdding] = useState(false)
  const [addKind, setAddKind] = useState<AddKind>('profile')
  const [newProfile, setNewProfile] = useState({ key: '', value: '' })
  const [newPerson, setNewPerson] = useState<Person>({ id: '', name: '' })
  const [newDate, setNewDate] = useState<DateEntry>({
    id: '',
    date: '',
    label: '',
    recurring: 0
  })
  const [versions, setVersions] = useState<ArtifactVersion[]>([])
  const [historyOpen, setHistoryOpen] = useState(false)
  const [undoingId, setUndoingId] = useState<string | null>(null)
  const [reviewBusy, setReviewBusy] = useState<'dream' | 'gardener' | null>(null)
  const [reviewMsg, setReviewMsg] = useState('')
  const [pendingReview, setPendingReview] = useState<{
    diff_text?: string | null
    created_at?: string | null
  } | null>(null)
  const [reviewActionBusy, setReviewActionBusy] = useState(false)

  const refreshHistory = useCallback(async (): Promise<void> => {
    try {
      const [profile, dream] = await Promise.all([
        collieClient.listVersions({ artifact_type: 'memory_profile', limit: 50 }),
        collieClient.listVersions({ artifact_type: 'memory_dream', limit: 50 })
      ])
      const merged = [...profile.versions, ...dream.versions].sort((a, b) =>
        b.created_at.localeCompare(a.created_at)
      )
      setVersions(merged)
    } catch {
      setVersions([])
    }
  }, [])

  const refresh = useCallback(async () => {
    const [pData, ppl, dateData, pending] = await Promise.all([
      collieClient.command<{ profile: ProfileEntry }>('get_profile'),
      collieClient.command<{ people: Person[] }>('get_people'),
      collieClient.command<{ dates: DateEntry[] }>('get_dates'),
      collieClient.getDreamPending()
    ])
    setProfile(pData.profile || {})
    setPeople(ppl.people || [])
    setDates(dateData.dates || [])
    setPendingReview(
      pending.pending
        ? { diff_text: pending.diff_text ?? null, created_at: pending.created_at ?? null }
        : null
    )
    await refreshHistory()
  }, [refreshHistory])

  const undoVersion = async (versionId: string): Promise<void> => {
    setUndoingId(versionId)
    try {
      await collieClient.rollbackArtifact(versionId)
      await refreshHistory()
      onNotice('Undone — the earlier version is back.')
    } catch (error) {
      onNotice(error instanceof Error ? error.message : 'I could not undo that change.')
    } finally {
      setUndoingId(null)
    }
  }

  const runSelfReview = async (kind: 'dream' | 'gardener'): Promise<void> => {
    setReviewBusy(kind)
    setReviewMsg('')
    try {
      const outcome =
        kind === 'dream'
          ? await collieClient.runDream()
          : await collieClient.runGardener()
      const message =
        outcome.message ??
        (kind === 'dream'
          ? 'Memory review done.'
          : 'Improvement suggestions are ready — check the chat for the review cards.')
      setReviewMsg(message)
      if (kind === 'dream') {
        const dreamOutcome = outcome as { pending?: boolean; diff?: string }
        if (dreamOutcome.pending) {
          setPendingReview({ diff_text: dreamOutcome.diff ?? null, created_at: null })
        }
        await refreshHistory()
      }
      onNotice(message)
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'That review could not run right now.'
      setReviewMsg(message)
      onNotice(message)
    } finally {
      setReviewBusy(null)
    }
  }

  const applyDream = async (): Promise<void> => {
    setReviewActionBusy(true)
    try {
      const result = await collieClient.applyDreamProposal()
      if (result.applied) {
        setPendingReview(null)
        await refreshHistory()
        onNotice('Memory updated — you can undo it in History below.')
      } else {
        onNotice(
          result.reason === 'no_pending'
            ? 'Nothing is waiting for review.'
            : 'I could not apply that review.'
        )
      }
    } catch (error) {
      onNotice(error instanceof Error ? error.message : 'I could not apply that review.')
    } finally {
      setReviewActionBusy(false)
    }
  }

  const dismissDream = async (): Promise<void> => {
    setReviewActionBusy(true)
    try {
      await collieClient.dismissDreamProposal()
      setPendingReview(null)
      onNotice('Review dismissed — your memory stays as it was.')
    } catch (error) {
      onNotice(error instanceof Error ? error.message : 'I could not dismiss that review.')
    } finally {
      setReviewActionBusy(false)
    }
  }

  const versionLabel = (version: ArtifactVersion): string => {
    if (version.artifact_type === 'memory_dream') return "Collie's weekly memory review"
    if (version.source === 'gardener') return "Collie's improvement suggestions"
    return 'Your memory edit'
  }

  useEffect(() => {
    void refresh().catch(() => undefined).finally(() => setLoading(false))
  }, [refresh])

  const runChange = async (change: () => Promise<unknown>, message: string): Promise<boolean> => {
    setSaving(true)
    try {
      await change()
      await refresh()
      setEditing(null)
      onNotice(message)
      return true
    } catch (error) {
      onNotice(error instanceof Error ? error.message : 'Could not update that memory.')
      return false
    } finally {
      setSaving(false)
    }
  }

  const confirmDelete = (label: string, change: () => Promise<unknown>): void => {
    if (!window.confirm(`Forget ${label}? This removes it from Collie's memory.`)) return
    void runChange(change, `Forgot ${label}.`)
  }

  if (loading) {
    return <div className="settings-loading">Looking through your memories...</div>
  }

  const profileEntries = Object.entries(profile).filter(([, value]) => value && value !== '')
  const empty = profileEntries.length === 0 && people.length === 0 && dates.length === 0
  const availableProfileKeys = Object.keys(PROFILE_LABELS).filter((key) => !profile[key])

  const startAdding = (): void => {
    setEditing(null)
    setAddKind(availableProfileKeys.length > 0 ? 'profile' : 'person')
    setNewProfile({ key: availableProfileKeys[0] || '', value: '' })
    setNewPerson({ id: '', name: '' })
    setNewDate({ id: '', date: '', label: '', recurring: 0 })
    setAdding(true)
  }

  const saveNewMemory = (): void => {
    if (addKind === 'profile') {
      void runChange(
        () => collieClient.command('set_profile_memory', newProfile),
        'Memory added.'
      ).then((saved) => setAdding(!saved))
      return
    }
      if (addKind === 'person') {
        // Strip UI-only fields (id) and empty values — the core's add_person
        // only accepts real person fields.
        void runChange(
          () =>
            collieClient.command('add_person_memory', {
              fields: personFieldsFrom(newPerson)
            }),
          `${newPerson.name}'s memory added.`
        ).then((saved) => setAdding(!saved))
        return
      }
    void runChange(
      () => collieClient.command('add_date_memory', {
        date: newDate.date,
        label: newDate.label,
        recurring: Boolean(newDate.recurring)
      }),
      'Important date added.'
    ).then((saved) => setAdding(!saved))
  }

  const addIsValid =
    (addKind === 'profile' && Boolean(newProfile.key && newProfile.value.trim())) ||
    (addKind === 'person' && Boolean(newPerson.name.trim())) ||
    (addKind === 'date' && Boolean(newDate.date.trim() && newDate.label.trim()))

  return (
    <div className="memory-settings">
      <div className="memory-note">
        Memory is stored in a structured local database. Collie also keeps an automatic,
        readable <code>MEMORY.md</code> mirror for agent context.
      </div>

      <div className="memory-toolbar">
        <button
          type="button"
          className="settings-button is-primary"
          onClick={adding ? () => setAdding(false) : startAdding}
        >
          {adding ? <X size={14} /> : <Plus size={14} />}
          {adding ? 'Cancel' : 'Add memory'}
        </button>
      </div>

      {adding && (
        <section className="memory-card memory-add-card">
          <div className="memory-kind-picker" role="radiogroup" aria-label="Memory type">
            {([
              ['profile', 'About you'],
              ['person', 'Person'],
              ['date', 'Important date']
            ] as const).map(([kind, label]) => (
              <button
                key={kind}
                type="button"
                role="radio"
                aria-checked={addKind === kind}
                className={`settings-button ${addKind === kind ? 'is-selected' : ''}`}
                onClick={() => setAddKind(kind)}
              >
                {label}
              </button>
            ))}
          </div>

          {addKind === 'profile' && (
            availableProfileKeys.length > 0 ? (
              <div className="memory-form-grid memory-add-form">
                <label>
                  <span>What kind of memory?</span>
                  <select
                    value={newProfile.key}
                    onChange={(event) =>
                      setNewProfile((current) => ({ ...current, key: event.target.value }))
                    }
                  >
                    {availableProfileKeys.map((key) => (
                      <option value={key} key={key}>{PROFILE_LABELS[key]}</option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>What should Collie remember?</span>
                  <input
                    autoFocus
                    value={newProfile.value}
                    onChange={(event) =>
                      setNewProfile((current) => ({ ...current, value: event.target.value }))
                    }
                  />
                </label>
              </div>
            ) : (
              <p className="memory-add-complete">
                All “About you” categories already have a memory. Edit one below, or add a
                person or important date.
              </p>
            )
          )}

          {addKind === 'person' && (
            <div className="memory-form-grid memory-add-form">
              {([
                ['name', 'Name'],
                ['relationship', 'Relationship'],
                ['birthday', 'Birthday'],
                ['allergies', 'Allergies'],
                ['preferences', 'Likes and dislikes'],
                ['gift_ideas', 'Gift ideas'],
                ['notes', 'Notes']
              ] as const).map(([field, label]) => (
                <label key={field}>
                  <span>{label}</span>
                  <input
                    autoFocus={field === 'name'}
                    value={newPerson[field] || ''}
                    onChange={(event) =>
                      setNewPerson((current) => ({ ...current, [field]: event.target.value }))
                    }
                  />
                </label>
              ))}
            </div>
          )}

          {addKind === 'date' && (
            <div className="memory-form-grid memory-add-form">
              <label>
                <span>Date</span>
                <input
                  autoFocus
                  placeholder="MM-DD or YYYY-MM-DD"
                  value={newDate.date}
                  onChange={(event) =>
                    setNewDate((current) => ({ ...current, date: event.target.value }))
                  }
                />
              </label>
              <label>
                <span>What is it?</span>
                <input
                  value={newDate.label}
                  onChange={(event) =>
                    setNewDate((current) => ({ ...current, label: event.target.value }))
                  }
                />
              </label>
              <label className="memory-checkbox">
                <input
                  type="checkbox"
                  checked={Boolean(newDate.recurring)}
                  onChange={(event) =>
                    setNewDate((current) => ({
                      ...current,
                      recurring: event.target.checked ? 1 : 0
                    }))
                  }
                />
                Repeat every year
              </label>
            </div>
          )}

          <div className="memory-add-actions">
            <button
              type="button"
              className="settings-button is-primary"
              disabled={saving || !addIsValid}
              onClick={saveNewMemory}
            >
              <Check size={14} /> Save memory
            </button>
          </div>
        </section>
      )}

      {empty && !adding ? (
        <div className="settings-empty">
          I&apos;m still learning about you. Add a memory or keep chatting and I&apos;ll
          remember what matters.
        </div>
      ) : (
        <>
          {profileEntries.length > 0 && (
            <section className="memory-section">
              <h3>About you</h3>
              <div className="memory-list">
                {profileEntries.map(([key, value]) => {
                  const isEditing = editing?.kind === 'profile' && editing.id === key
                  return (
                    <div className="memory-row" key={key}>
                      <span className="memory-label">{PROFILE_LABELS[key] || key}</span>
                      {isEditing ? (
                        <input
                          autoFocus
                          value={editing.draft}
                          onChange={(event) =>
                            setEditing({ kind: 'profile', id: key, draft: event.target.value })
                          }
                        />
                      ) : (
                        <span className="memory-value">{value}</span>
                      )}
                      <div className="memory-actions">
                        {isEditing ? (
                          <>
                            <button
                              aria-label="Save memory"
                              disabled={saving}
                              onClick={() =>
                                void runChange(
                                  () => collieClient.command('set_profile_memory', {
                                    key,
                                    value: editing.draft
                                  }),
                                  'Memory updated.'
                                )
                              }
                            ><Check size={15} /></button>
                            <button aria-label="Cancel editing" onClick={() => setEditing(null)}>
                              <X size={15} />
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              aria-label="Edit memory"
                              onClick={() => setEditing({ kind: 'profile', id: key, draft: value })}
                            ><Pencil size={14} /></button>
                            <button
                              aria-label="Delete memory"
                              onClick={() =>
                                confirmDelete(
                                  PROFILE_LABELS[key] || key,
                                  () => collieClient.command('delete_profile_memory', { key })
                                )
                              }
                            ><Trash2 size={14} /></button>
                          </>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </section>
          )}

          {people.length > 0 && (
            <section className="memory-section">
              <h3>People</h3>
              <div className="memory-card-list">
                {people.map((person) => {
                  const isEditing = editing?.kind === 'person' && editing.id === person.id
                  const draft = isEditing ? editing.draft : person
                  return (
                    <article className="memory-card" key={person.id}>
                      {isEditing ? (
                        <div className="memory-form-grid">
                          {([
                            ['name', 'Name'],
                            ['relationship', 'Relationship'],
                            ['birthday', 'Birthday'],
                            ['allergies', 'Allergies'],
                            ['preferences', 'Likes and dislikes'],
                            ['gift_ideas', 'Gift ideas'],
                            ['notes', 'Notes']
                          ] as const).map(([field, label]) => (
                            <label key={field}>
                              <span>{label}</span>
                              <input
                                value={draft[field] || ''}
                                onChange={(event) =>
                                  setEditing({
                                    kind: 'person',
                                    id: person.id,
                                    draft: { ...draft, [field]: event.target.value }
                                  })
                                }
                              />
                            </label>
                          ))}
                        </div>
                      ) : (
                        <>
                          <strong>{person.name}</strong>
                          {person.relationship && <span>{person.relationship}</span>}
                          <p>
                            {[
                              person.birthday && `Birthday: ${person.birthday}`,
                              person.allergies && `Allergies: ${person.allergies}`,
                              person.preferences && `Likes: ${person.preferences}`,
                              person.gift_ideas && `Gift ideas: ${person.gift_ideas}`,
                              person.notes
                            ].filter(Boolean).join(' · ') || 'No extra details yet.'}
                          </p>
                        </>
                      )}
                      <div className="memory-card-actions">
                        {isEditing ? (
                          <>
                            <button
                              className="settings-button is-primary"
                              disabled={saving || !draft.name.trim()}
                              onClick={() =>
                                void runChange(
                                  () => collieClient.command('update_person_memory', {
                                    person_id: person.id,
                                    fields: draft
                                  }),
                                  `${draft.name}'s memory updated.`
                                )
                              }
                            ><Check size={14} /> Save</button>
                            <button className="settings-button" onClick={() => setEditing(null)}>
                              Cancel
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              className="settings-icon-button"
                              aria-label={`Edit ${person.name}`}
                              onClick={() => setEditing({ kind: 'person', id: person.id, draft: { ...person } })}
                            ><Pencil size={14} /></button>
                            <button
                              className="settings-icon-button is-danger"
                              aria-label={`Delete ${person.name}`}
                              onClick={() =>
                                confirmDelete(
                                  person.name,
                                  () => collieClient.command('delete_person_memory', { person_id: person.id })
                                )
                              }
                            ><Trash2 size={14} /></button>
                          </>
                        )}
                      </div>
                    </article>
                  )
                })}
              </div>
            </section>
          )}

          {dates.length > 0 && (
            <section className="memory-section">
              <h3>Important dates</h3>
              <div className="memory-list">
                {dates.map((dateEntry) => {
                  const isEditing = editing?.kind === 'date' && editing.id === dateEntry.id
                  const draft = isEditing ? editing.draft : dateEntry
                  return (
                    <div className="memory-row memory-date-row" key={dateEntry.id}>
                      {isEditing ? (
                        <>
                          <input
                            aria-label="Date"
                            value={draft.date}
                            onChange={(event) =>
                              setEditing({ kind: 'date', id: dateEntry.id, draft: { ...draft, date: event.target.value } })
                            }
                          />
                          <input
                            aria-label="Label"
                            value={draft.label}
                            onChange={(event) =>
                              setEditing({ kind: 'date', id: dateEntry.id, draft: { ...draft, label: event.target.value } })
                            }
                          />
                          <label className="memory-checkbox">
                            <input
                              type="checkbox"
                              checked={Boolean(draft.recurring)}
                              onChange={(event) =>
                                setEditing({
                                  kind: 'date',
                                  id: dateEntry.id,
                                  draft: { ...draft, recurring: event.target.checked ? 1 : 0 }
                                })
                              }
                            />
                            Yearly
                          </label>
                        </>
                      ) : (
                        <>
                          <span className="memory-date">{dateEntry.date}</span>
                          <span className="memory-value">{dateEntry.label}</span>
                          {dateEntry.recurring === 1 && <small>Yearly</small>}
                        </>
                      )}
                      <div className="memory-actions">
                        {isEditing ? (
                          <>
                            <button
                              aria-label="Save date"
                              disabled={saving || !draft.date.trim() || !draft.label.trim()}
                              onClick={() =>
                                void runChange(
                                  () => collieClient.command('update_date_memory', {
                                    date_id: dateEntry.id,
                                    fields: draft
                                  }),
                                  'Important date updated.'
                                )
                              }
                            ><Check size={15} /></button>
                            <button aria-label="Cancel editing" onClick={() => setEditing(null)}>
                              <X size={15} />
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              aria-label="Edit date"
                              onClick={() => setEditing({ kind: 'date', id: dateEntry.id, draft: { ...dateEntry } })}
                            ><Pencil size={14} /></button>
                            <button
                              aria-label="Delete date"
                              onClick={() =>
                                confirmDelete(
                                  dateEntry.label,
                                  () => collieClient.command('delete_date_memory', { date_id: dateEntry.id })
                                )
                              }
                            ><Trash2 size={14} /></button>
                          </>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </section>
          )}
        </>
      )}

      <section className="memory-section">
        <h3><Sparkles size={14} /> Collie's self-review</h3>
        <p className="memory-note">
          Run a memory review — Collie proposes tidy-ups to long-term memory,
          and you review and approve them before anything changes (everything
          stays undoable). Or ask for improvement suggestions from recent run
          records; those appear as review cards in the 🔔 conversation.
        </p>
        <div className="memory-toolbar">
          <button
            type="button"
            className="settings-button"
            disabled={reviewBusy !== null}
            onClick={() => void runSelfReview('dream')}
          >
            <History size={13} />{' '}
            {reviewBusy === 'dream' ? 'Reviewing…' : 'Review memory now'}
          </button>
          <button
            type="button"
            className="settings-button"
            disabled={reviewBusy !== null}
            onClick={() => void runSelfReview('gardener')}
          >
            <Sparkles size={13} />{' '}
            {reviewBusy === 'gardener' ? 'Thinking…' : 'Suggest improvements'}
          </button>
        </div>
        {reviewMsg && <p className="memory-note">{reviewMsg}</p>}
        {pendingReview && (
          <div className="memory-note review-pending">
            <b>Memory review waiting for you</b>
            <span>
              Collie reviewed your conversations and is proposing tidied-up
              long-term memory. Nothing is applied until you approve.
            </span>
            {pendingReview.diff_text && (
              <DiffView
                diff={pendingReview.diff_text}
                label="Proposed memory changes"
              />
            )}
            <div className="memory-toolbar">
              <button
                type="button"
                className="settings-button"
                disabled={reviewActionBusy}
                onClick={() => void applyDream()}
              >
                <Check size={13} />{' '}
                {reviewActionBusy ? 'Working…' : 'Apply changes'}
              </button>
              <button
                type="button"
                className="settings-button"
                disabled={reviewActionBusy}
                onClick={() => void dismissDream()}
              >
                <X size={13} /> Not now
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="memory-section">
        <h3><History size={14} /> History</h3>
        <p className="memory-note">
          Every memory change is snapshotted — edits, Collie's weekly memory reviews,
          and improvement suggestions — so anything can be undone.
        </p>
        <div className="memory-toolbar">
          <button
            type="button"
            className="settings-button"
            onClick={() => setHistoryOpen((current) => !current)}
            aria-expanded={historyOpen}
          >
            {historyOpen ? 'Hide history' : `Show history (${versions.length})`}
          </button>
        </div>
        {historyOpen && (
          versions.length === 0 ? (
            <p className="memory-note">No changes recorded yet.</p>
          ) : (
            <div className="version-list">
              {versions.map((version) => (
                <div className="version-row" key={version.id}>
                  <div className="version-row-main">
                    <b>{versionLabel(version)}</b>
                    <span>
                      {version.status === 'rolled_back' ? 'Already undone · ' : ''}
                      {new Date(version.created_at).toLocaleString()}
                    </span>
                    {version.diff_text && (
                      <DiffView
                        diff={version.diff_text}
                        label={`Version ${version.version} — see what changed`}
                      />
                    )}
                  </div>
                  {version.status === 'applied' && (
                    <button
                      type="button"
                      className="settings-button"
                      disabled={undoingId === version.id}
                      onClick={() => void undoVersion(version.id)}
                    >
                      <Undo2 size={13} /> {undoingId === version.id ? 'Undoing…' : 'Undo'}
                    </button>
                  )}
                </div>
              ))}
            </div>
          )
        )}
      </section>
    </div>
  )
}
