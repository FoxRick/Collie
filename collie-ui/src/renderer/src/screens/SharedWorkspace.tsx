import { useCallback, useEffect, useRef, useState } from 'react'
import { Archive, Download, RefreshCw, Send, Users, X } from 'lucide-react'
import {
  canComposeSharedSession,
  formatBytes,
  sharedCollaboration,
  sharedMessages,
  type ArchiveStatus,
  type CollaborationBootstrap,
  type CollaborationEvent,
  type CollaborationSession,
  type SharedFile,
  type SharedNotification
} from '../lib/collaboration'
import {
  collieClient,
  type LocalCollaborationEvent,
  type CollieAutomation
} from '../lib/ipc'
import SharedMessageList from '../components/SharedMessageList'

interface Props {
  onBack: () => void
  personalMessages?: Array<{ id: string; role: string; content: string }>
}
interface SavedArchive {
  path: string
  digest: string
  manifest: {
    session_id: string
    events: LocalCollaborationEvent[]
    recipients?: string[]
  }
}
interface DraftResult {
  draftId: string
  sessionId: string
  content: string
  state: string
}
const lifecycle: Record<string, string> = {
  active: 'Shared conversation',
  closing: 'Closing · waiting for accepted work',
  archive_pending: 'Waiting for verified local copies',
  purging: 'Removing cloud content',
  archived_local: 'Cloud copy removed · recover from a local archive'
}
const audiencePolicy = {
  summary:
    'Owners may invite additional participants who can read all published history.',
  includes_existing_history: true
}

export default function SharedWorkspace({
  onBack,
  personalMessages = []
}: Props): React.JSX.Element {
  const [api] = useState(sharedCollaboration)
  const [data, setData] = useState<CollaborationBootstrap>({})
  const [orgId, setOrgId] = useState('')
  const [orgName, setOrgName] = useState('')
  const [active, setActive] = useState<CollaborationSession | null>(null)
  const activeId = useRef<string | null>(null)
  const [events, setEvents] = useState<CollaborationEvent[]>([])
  const [files, setFiles] = useState<SharedFile[]>([])
  const [syncError, setSyncError] = useState('')
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [mentions, setMentions] = useState<Record<string, string[]>>({})
  const [consents, setConsents] = useState<Record<string, boolean>>({})
  const [notifications, setNotifications] = useState<SharedNotification[]>([])
  const [notificationCursor, setNotificationCursor] = useState(0)
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [sending, setSending] = useState(false)
  const [archive, setArchive] = useState<ArchiveStatus | null>(null)
  const [archives, setArchives] = useState<SavedArchive[]>([])
  const [offlineArchive, setOfflineArchive] = useState<SavedArchive | null>(
    null
  )
  const [privateResult, setPrivateResult] = useState<DraftResult | null>(null)
  const [savedDrafts, setSavedDrafts] = useState<DraftResult[]>([])
  const [copyOpen, setCopyOpen] = useState(false)
  const [copyIds, setCopyIds] = useState<string[]>([])
  const [copyText, setCopyText] = useState('')
  const [routines, setRoutines] = useState<CollieAutomation[] | null>(null)
  const [routineId, setRoutineId] = useState('')
  const [privateBusy, setPrivateBusy] = useState(false)
  const [query, setQuery] = useState('')
  const [slackOpen, setSlackOpen] = useState(false)
  const [channelId, setChannelId] = useState('')
  const [installations, setInstallations] = useState<
    Array<{ installation_id: string; team_id: string; status: string }>
  >([])
  const [installationId, setInstallationId] = useState('')
  const [editing, setEditing] = useState<CollaborationEvent | null>(null)
  const [inviteAccount, setInviteAccount] = useState('')
  const [inviteName, setInviteName] = useState('')
  const [runningSession, setRunningSession] = useState<string | null>(null)
  const mounted = useRef(true)
  const refreshing = useRef(false)
  const readableError = (error: unknown): string =>
    error instanceof Error
      ? error.message
      : 'That did not work. Please try again.'

  const refreshArchives = useCallback(async () => {
    const local = await api.listLocalArchives()
    if (mounted.current) setArchives(local as unknown as SavedArchive[])
  }, [api])
  const refresh = useCallback(async () => {
    if (refreshing.current) return
    refreshing.current = true
    try {
      const next = await api.bootstrap()
      if (!mounted.current) return
      setData(next)
      setSyncError(next.sync_error || '')
      setOrgId((current) => current || next.organizations?.[0]?.id || '')
      setActive((current) =>
        current
          ? next.sessions?.find((session) => session.id === current.id) ||
            current
          : null
      )
      const recovered = await api.listPrivateDrafts()
      if (mounted.current) setSavedDrafts(recovered)
      const inbox = await api.notifications()
      if (mounted.current) {
        setNotifications(inbox.notifications || [])
        setNotificationCursor(inbox.next_cursor)
      }
    } catch (error) {
      if (mounted.current) setNotice(readableError(error))
    } finally {
      refreshing.current = false
    }
  }, [api])
  useEffect(() => {
    mounted.current = true
    void refresh()
    void refreshArchives().catch(() => undefined)
    const visible = (): void => {
      if (document.visibilityState === 'visible') void refresh()
    }
    const timer = window.setInterval(visible, 20000)
    document.addEventListener('visibilitychange', visible)
    return () => {
      mounted.current = false
      clearInterval(timer)
      document.removeEventListener('visibilitychange', visible)
    }
  }, [refresh, refreshArchives])

  const reconcile = useCallback(
    async (session: CollaborationSession) => {
      // Main reconciles paginated cloud events into its durable local materializer.
      const result = await api.openSession(session.id)
      if (!mounted.current || activeId.current !== session.id) return
      setEvents(sharedMessages(result.events || [], session.members))
      setFiles(result.files || [])
      setSyncError(result.sync_error || '')
      setActive((current) =>
        current?.id === session.id &&
        JSON.stringify(current.run_state) !== JSON.stringify(result.run_state)
          ? { ...current, run_state: result.run_state }
          : current
      )
      if (result.archive) setArchive(result.archive)
    },
    [api]
  )
  useEffect(() => {
    if (!active || offlineArchive) return
    let inFlight = false
    const update = async (): Promise<void> => {
      if (inFlight || document.visibilityState !== 'visible') return
      inFlight = true
      try {
        await reconcile(active)
      } catch (error) {
        if (activeId.current === active.id) setNotice(readableError(error))
      } finally {
        inFlight = false
      }
    }
    void update()
    const timer = window.setInterval(() => void update(), 8000)
    return () => clearInterval(timer)
  }, [active, offlineArchive, reconcile])

  const perform = async (
    operation: () => Promise<unknown>,
    success = ''
  ): Promise<void> => {
    setBusy(true)
    setNotice('')
    try {
      await operation()
      if (success) setNotice(success)
    } catch (error) {
      setNotice(readableError(error))
    } finally {
      setBusy(false)
    }
  }
  const choose = (session: CollaborationSession): void => {
    activeId.current = session.id
    setActive(session)
    setEvents([])
    setFiles([])
    setArchive(null)
    setOfflineArchive(null)
    setEditing(null)
    setNotice('')
  }
  const openArchive = (saved: SavedArchive): void => {
    activeId.current = null
    setActive(null)
    setOfflineArchive(saved)
    setArchive(null)
    setFiles([])
    setSyncError('')
    setEvents(sharedMessages(saved.manifest.events || []))
    setNotice(
      'This verified copy is available on this computer. Supabase cannot restore it after cloud deletion.'
    )
  }
  const draft = active ? drafts[active.id] || '' : ''
  const setDraft = (value: string): void => {
    if (active) setDrafts((current) => ({ ...current, [active.id]: value }))
  }
  const writable = Boolean(
    active && !offlineArchive && canComposeSharedSession(active.status)
  )
  const canPublishAccepted = Boolean(
    active && !offlineArchive && ['active', 'closing'].includes(active.status)
  )
  const send = async (): Promise<void> => {
    if (!active || !writable || !draft.trim() || sending) return
    const session = active
    const content = draft.trim()
    setSending(true)
    setNotice('')
    try {
      // Main persists first; only a successful local save permits clearing the composer.
      const mentioned = (mentions[session.id] || []).filter((id) =>
        session.members?.some(
          (member) =>
            member.id === id && content.includes(`@${member.display_name}`)
        )
      )
      await api.sendMessage(session.id, content, session.revision, mentioned)
      setMentions((current) => ({ ...current, [session.id]: [] }))
      setDrafts((current) => ({
        ...current,
        [session.id]:
          current[session.id]?.trim() === content ? '' : current[session.id]
      }))
      await reconcile(session)
    } catch (error) {
      setNotice(readableError(error))
    } finally {
      setSending(false)
    }
  }
  const runPrivate = async (): Promise<void> => {
    if (!active || !writable || !draft.trim() || privateBusy) return
    const session = active
    const content = draft.trim()
    setPrivateBusy(true)
    setRunningSession(session.id)
    setNotice('')
    try {
      const result = await api.runPrivate(session.id, content)
      setPrivateResult({
        draftId: result.draftId,
        sessionId: session.id,
        content: result.content || '',
        state: result.state || 'Review this private result before publishing.'
      })
      setDrafts((current) => ({ ...current, [session.id]: '' }))
    } catch (error) {
      setNotice(readableError(error))
    } finally {
      setPrivateBusy(false)
      setRunningSession(null)
    }
  }
  const publish = async (): Promise<void> => {
    if (
      !privateResult?.content ||
      active?.id !== privateResult.sessionId ||
      !canPublishAccepted
    )
      return
    await perform(async () => {
      await api.publishDraft(privateResult.draftId, privateResult.content)
      setSavedDrafts((current) =>
        current.filter((item) => item.draftId !== privateResult.draftId)
      )
      setPrivateResult(null)
      await reconcile(active)
    })
  }
  const saveArchive = async (): Promise<void> => {
    if (!active) return
    await perform(async () => {
      setArchive(await api.saveArchive(active.id))
      await refreshArchives()
    }, 'Verified and saved on this computer. Other participants must verify their copies before cloud deletion.')
  }
  const selectedOrg = data.organizations?.find((org) => org.id === orgId)
  const members = selectedOrg?.members || []
  const matches = members.filter((member) =>
    member.display_name.toLowerCase().includes(query.toLowerCase())
  )
  const currentPrivate =
    privateResult?.sessionId === active?.id ? privateResult : null
  const title = offlineArchive
    ? 'Local archive'
    : active?.title || 'Choose a shared conversation'
  useEffect(() => {
    if (!slackOpen || !active?.organization_id) return
    let cancelled = false
    void api
      .slackSettings(active.organization_id)
      .then((result) => {
        if (cancelled) return
        setInstallations(result.installations || [])
        setInstallationId(
          (current) =>
            current || result.installations?.[0]?.installation_id || ''
        )
      })
      .catch((error) => {
        if (!cancelled) setNotice(readableError(error))
      })
    return () => {
      cancelled = true
    }
  }, [api, slackOpen, active?.organization_id])

  return (
    <main className="shared-workspace">
      <header className="shared-header">
        <div>
          <button className="settings-back" onClick={onBack}>
            ← Back to local chat
          </button>
          <h1>
            <Users size={22} /> Shared conversations
          </h1>
          <p>
            Published content is visible to participants. Personal tools,
            memory, and approvals stay private.
          </p>
        </div>
        <button
          className="settings-icon-button"
          onClick={() => void refresh()}
          aria-label="Refresh shared conversations"
        >
          <RefreshCw size={17} />
        </button>
      </header>
      {notice && (
        <div className="shared-notice" role="status">
          {notice}
        </div>
      )}
      {syncError && (
        <div className="shared-notice" role="status">
          Waiting to sync. Your saved local messages remain available.{' '}
          {syncError}
        </div>
      )}
      <div className="shared-grid">
        <aside className="shared-list">
          {!!data.invites?.length && (
            <section className="shared-card">
              <h2>Invitations</h2>
              {data.invites.map((invite) => (
                <div className="shared-invite" key={invite.id}>
                  <span>{invite.inviter_name || 'Invitation'}</span>
                  {invite.kind !== 'organization' && (
                    <label>
                      <input
                        type="checkbox"
                        checked={consents[invite.id] || false}
                        onChange={(event) =>
                          setConsents((current) => ({
                            ...current,
                            [invite.id]: event.target.checked
                          }))
                        }
                      />
                      {invite.audience_policy?.summary ||
                        'Owners may invite additional participants who can read all published history.'}
                    </label>
                  )}
                  <button
                    disabled={
                      busy ||
                      (invite.kind !== 'organization' && !consents[invite.id])
                    }
                    className="settings-button"
                    onClick={() =>
                      void perform(async () => {
                        if (invite.kind === 'organization')
                          await api.acceptOrganizationInvite(invite.id)
                        else
                          await api.acceptInvite(
                            invite.id,
                            consents[invite.id] === true,
                            invite.audience_policy_version || 1
                          )
                        await refresh()
                      })
                    }
                  >
                    Accept
                  </button>
                </div>
              ))}
            </section>
          )}
          {notifications.some((item) => !item.read_at) && (
            <section className="shared-card">
              <h2>Notifications</h2>
              {notifications
                .filter((item) => !item.read_at)
                .map((item) => (
                  <button
                    className="shared-session"
                    key={item.notification_id}
                    onClick={() => {
                      const session = data.sessions?.find(
                        (row) => row.id === item.session_id
                      )
                      if (session) choose(session)
                    }}
                  >
                    {item.kind === 'mention'
                      ? 'You were mentioned'
                      : 'Conversation invitation'}{' '}
                    ·{' '}
                    {data.sessions?.find((row) => row.id === item.session_id)
                      ?.title || 'Shared conversation'}
                  </button>
                ))}
              <button
                className="settings-button"
                onClick={() =>
                  void perform(async () => {
                    await api.ackNotifications(notificationCursor)
                    await refresh()
                  })
                }
              >
                Mark as read
              </button>
            </section>
          )}
          <section className="shared-card">
            <h2>Your organization</h2>
            <select
              value={orgId}
              onChange={(event) => setOrgId(event.target.value)}
              aria-label="Organization"
            >
              <option value="">Choose an organization</option>
              {data.organizations?.map((org) => (
                <option value={org.id} key={org.id}>
                  {org.name}
                </option>
              ))}
            </select>
            <div className="shared-create-row">
              <input
                value={orgName}
                onChange={(event) => setOrgName(event.target.value)}
                placeholder="New organization name"
                aria-label="New organization name"
              />
              <button
                className="settings-button"
                disabled={!orgName.trim() || busy}
                onClick={() =>
                  void perform(async () => {
                    await api.createOrganization(orgName.trim())
                    setOrgName('')
                    await refresh()
                  })
                }
              >
                Create
              </button>
            </div>
          </section>
          <section className="shared-card">
            <div className="shared-card-heading">
              <h2>Conversations</h2>
              <button
                className="settings-button"
                disabled={!orgId || busy}
                onClick={() =>
                  void perform(async () => {
                    const result = await api.createSession(
                      orgId,
                      'New shared conversation'
                    )
                    await refresh()
                    if (result.session) choose(result.session)
                  })
                }
              >
                New
              </button>
            </div>
            {data.sessions
              ?.filter((session) => session.organization_id === orgId)
              .map((session) => (
                <button
                  className={`shared-session ${session.id === active?.id ? 'is-active' : ''}`}
                  key={session.id}
                  onClick={() => choose(session)}
                >
                  <span>{session.title || 'Archived conversation'}</span>
                  <small>
                    {lifecycle[session.status] || session.status}
                    {session.unread ? ` · ${session.unread} unread` : ''}
                  </small>
                </button>
              ))}
          </section>
          <section className="shared-card">
            <h2>Invite to organization</h2>
            <p>
              Ask the person for the account ID shown in their shared workspace.
            </p>
            {data.account?.id && (
              <small>Your account ID: {data.account.id}</small>
            )}
            <input
              aria-label="Invitee account ID"
              placeholder="Account ID"
              value={inviteAccount}
              onChange={(event) => setInviteAccount(event.target.value)}
            />
            <input
              aria-label="Invitee display name"
              placeholder="Their name"
              value={inviteName}
              onChange={(event) => setInviteName(event.target.value)}
            />
            <button
              className="settings-button"
              disabled={
                !orgId || !inviteAccount.trim() || !inviteName.trim() || busy
              }
              onClick={() =>
                void perform(async () => {
                  await api.inviteOrganization(
                    orgId,
                    inviteAccount.trim(),
                    inviteName.trim()
                  )
                  setInviteAccount('')
                  setInviteName('')
                }, 'Organization invitation sent. Conversation access requires a separate invitation.')
              }
            >
              Invite person
            </button>
          </section>
          <section className="shared-card">
            <h2>Local archives</h2>
            <button
              className="settings-button"
              disabled={busy}
              onClick={() =>
                void perform(async () => {
                  await api.importArchive()
                  await refreshArchives()
                })
              }
            >
              Import archive bundle
            </button>
            {archives.length ? (
              archives.map((saved) => (
                <button
                  className="shared-session"
                  key={saved.path}
                  onClick={() => openArchive(saved)}
                >
                  <span>{saved.manifest.session_id}</span>
                  <small>Verified local copy</small>
                </button>
              ))
            ) : (
              <p>No local archives on this computer.</p>
            )}
          </section>
          <section className="shared-card">
            <h2>Find people</h2>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search this organization"
              aria-label="Search this organization"
            />
            {query &&
              matches.map((member) => (
                <div className="directory-row" key={member.id}>
                  <span>{member.display_name}</span>
                  <button
                    className="settings-button"
                    disabled={
                      !writable ||
                      busy ||
                      active?.members?.some((item) => item.id === member.id)
                    }
                    onClick={() =>
                      void perform(async () => {
                        if (active)
                          await api.invite(active.id, member.id, audiencePolicy)
                      }, 'Invitation sent. History is available only after acceptance.')
                    }
                  >
                    Invite
                  </button>
                </div>
              ))}
          </section>
        </aside>
        <section className="shared-card shared-chat">
          <div className="shared-chat-heading">
            <div>
              <h2>{title}</h2>
              {active && (
                <>
                  <p>{lifecycle[active.status] || active.status}</p>
                    <div className="shared-participants" aria-label="Participants">
                      {active.members?.map((member) => (
                        <span className="shared-participant" key={member.id}>
                          <span className="shared-avatar" aria-hidden="true">
                            {member.display_name.slice(0, 1).toUpperCase()}
                          </span>
                          {member.display_name}
                        </span>
                      ))}
                    </div>
                    {active.members?.some(member => member.id === data.account?.id && member.role === 'owner') && active.status !== 'purging' && active.status !== 'archived_local' && (
                      <details>
                        <summary>Manage participants</summary>
                        <p>Removing someone revokes cloud access and removes their archive-copy requirement. Existing local copies remain with them.</p>
                        {active.members.filter(member => member.id !== data.account?.id).map(member => (
                          <button key={member.id} className="settings-button" disabled={busy} onClick={() => void perform(async () => { await api.revokeMember(active.id, member.id); await refresh(); await reconcile(active) })}>
                            Remove {member.display_name}'s access
                          </button>
                        ))}
                      </details>
                    )}
                    {active.quota && (
                    <small>
                      Storage {formatBytes(active.quota.used_bytes)} /{' '}
                      {formatBytes(active.quota.limit_bytes)} ·{' '}
                      {active.quota.used_messages || 0} /{' '}
                      {active.quota.limit_messages || 200} messages
                    </small>
                  )}
                </>
              )}
            </div>
            <div className="shared-actions">
              {active && (
                <>
                  <button
                    className="settings-button"
                    onClick={() => setSlackOpen((value) => !value)}
                  >
                    Slack
                  </button>
                  <button
                    className="settings-button"
                    disabled={busy || active.status !== 'active'}
                    onClick={() =>
                      void perform(async () => {
                        const result = await api.requestArchive(active.id)
                        setArchive(result.archive || null)
                        await refresh()
                      })
                    }
                  >
                    <Archive size={14} /> Archive
                  </button>
                  <button
                    className="settings-button"
                    disabled={
                      busy ||
                      !['archive_pending', 'purging'].includes(active.status)
                    }
                    onClick={() => void saveArchive()}
                  >
                    Save verified copy
                  </button>
                  {active.status !== 'active' && (
                    <button
                      className="settings-button"
                      disabled={busy}
                      onClick={() =>
                        void perform(async () => {
                          const result = await api.continueSession(active.id)
                          await refresh()
                          if (result.session) choose(result.session)
                        })
                      }
                    >
                      Start next conversation
                    </button>
                  )}
                </>
              )}
              {(offlineArchive || archive?.local_path) && (
                <button
                  className="settings-button"
                  disabled={busy}
                  onClick={() =>
                    void perform(() =>
                      api.exportArchive(
                        offlineArchive?.path || archive?.local_path || ''
                      )
                    )
                  }
                >
                  <Download size={14} /> Export bundle
                </button>
              )}
            </div>
          </div>
          {slackOpen && active && (
            <section className="slack-settings">
              <h3>Slack channel audience</h3>
              <select
                aria-label="Slack workspace"
                value={installationId}
                onChange={(event) => setInstallationId(event.target.value)}
              >
                <option value="">Choose a connected workspace</option>
                {installations.map((item) => (
                  <option
                    key={item.installation_id}
                    value={item.installation_id}
                  >
                    {item.team_id} · {item.status}
                  </option>
                ))}
              </select>
              <p>
                Messages published here can be seen by the selected Slack
                channel. Slack keeps its own history after Collie archives.
                Every pilot participant must link their own desktop.
              </p>
              <button
                className="settings-button"
                disabled={busy}
                onClick={() =>
                  void perform(() => api.installSlack(active.organization_id))
                }
              >
                Add to Slack
              </button>
              <button
                className="settings-button"
                disabled={busy}
                onClick={() =>
                  void perform(async () => {
                    const result = await api.linkSlackSender(installationId)
                    setNotice(
                      result.message ||
                        `Send @Collie link ${result.code} in Slack to verify your account.`
                    )
                  })
                }
              >
                Link my Slack account
              </button>
              <label>
                Selected channel ID
                <input
                  value={channelId}
                  onChange={(event) => setChannelId(event.target.value)}
                  placeholder="C…"
                />
              </label>
              <button
                className="settings-button"
                disabled={!channelId || busy}
                onClick={() =>
                  void perform(
                    () =>
                      api.selectSlackChannel(
                        active.organization_id,
                        installationId,
                        channelId
                      ),
                    'Selected channel updated.'
                  )
                }
              >
                Select channel
              </button>
            </section>
          )}
          {archive && (
            <div className="archive-status" role="status">
              <span>
                {lifecycle[archive.state] || archive.state}
                {archive.pending_participants?.length
                  ? ` · Waiting for ${archive.pending_participants.map((member) => member.display_name).join(', ')}`
                  : ''}
              </span>
              {archive.local_path && (
                <small>Saved at {archive.local_path}</small>
              )}
            </div>
          )}
          {active?.run_state && (
            <div className="shared-notice" role="status">
              {active.run_state.status === 'queued'
                ? 'Request queued · waiting for the requester’s computer'
                : active.run_state.status === 'needs_reconciliation'
                  ? 'Request paused · external actions need review before retry'
                  : 'Request in progress · private work stays with the requester'}
              {(active.run_state.requested_by === data.account?.id ||
                active.members?.some(
                  (member) =>
                    member.id === data.account?.id && member.role === 'owner'
                )) && (
                <button
                  className="settings-button"
                  disabled={busy}
                  onClick={() =>
                    void perform(
                      () => api.stopSessionRun(active.id),
                      'Request stopped.'
                    )
                  }
                >
                  Stop request
                </button>
              )}
            </div>
          )}
          {active?.run_state?.status === 'needs_reconciliation' &&
            active.run_state.requested_by === data.account?.id && (
              <section className="shared-card">
                <p>
                  Check your local tool activity and any external changes before
                  trying again. This does not undo actions already performed.
                </p>
                <button
                  className="settings-button"
                  disabled={busy}
                  onClick={() =>
                    void perform(async () => {
                      await api.resolveReconciliation(active.run_state!.run_id)
                      await reconcile(active)
                    }, 'Previous request closed. You can submit a new request.')
                  }
                >
                  I reviewed the actions; close this request
                </button>
              </section>
            )}
          {active && writable && (
            <button
              className="settings-button"
              disabled={busy}
              onClick={() =>
                void perform(async () => {
                  await api.uploadFile(
                    active.id,
                    active.revision || 1,
                    active.membership_revision || 1
                  )
                  await reconcile(active)
                })
              }
            >
              Share a file from this computer
            </button>
          )}
          {active && files.length > 0 && (
            <section className="shared-files" aria-label="Shared files">
              {files.map((file) => (
                <div className="directory-row" key={file.file_id}>
                  <span>
                    {file.name} · {formatBytes(file.byte_length)}
                  </span>
                  <button
                    className="settings-button"
                    disabled={busy}
                    onClick={() =>
                      void perform(() =>
                        api.downloadFile(active.id, file.file_id)
                      )
                    }
                  >
                    <Download size={14} /> Save copy
                  </button>
                </div>
              ))}
            </section>
          )}
          {runningSession && (
            <button
              className="settings-button is-danger"
              onClick={() =>
                void perform(
                  () => api.stopSessionRun(runningSession),
                  'Stopped. Review any external actions before retrying.'
                )
              }
            >
              Stop my running request
            </button>
          )}
          <SharedMessageList
            events={events}
            readOnly={!writable}
            viewerId={data.account?.id}
            onEdit={setEditing}
          />
          {editing && active && writable && (
            <section className="shared-card">
              <label>
                Edit your published message
                <textarea
                  value={editing.content}
                  onChange={(event) =>
                    setEditing({ ...editing, content: event.target.value })
                  }
                />
              </label>
              <button
                className="settings-button"
                disabled={busy || !editing.content.trim()}
                onClick={() =>
                  void perform(async () => {
                    await api.editMessage(
                      active.id,
                      editing.id,
                      editing.content,
                      editing.revision || 1
                    )
                    setEditing(null)
                    await reconcile(active)
                  })
                }
              >
                Save edit
              </button>
              <button
                className="settings-button is-danger"
                disabled={busy}
                onClick={() =>
                  void perform(async () => {
                    await api.deleteMessage(
                      active.id,
                      editing.id,
                      editing.revision || 1
                    )
                    setEditing(null)
                    await reconcile(active)
                  })
                }
              >
                Remove this message for participants
              </button>
              <button
                className="settings-button"
                onClick={() => setEditing(null)}
              >
                Cancel
              </button>
            </section>
          )}
          {active &&
            savedDrafts
              .filter(
                (item) =>
                  item.sessionId === active.id &&
                  item.draftId !== currentPrivate?.draftId
              )
              .map((item) => (
                <div className="shared-notice" key={item.draftId}>
                  <span>
                    Private result saved on this computer ·{' '}
                    {item.state === 'lease_lost'
                      ? 'Previous request expired; review before starting again.'
                      : 'Ready to review'}
                  </span>
                  <button
                    className="settings-button"
                    onClick={() => setPrivateResult(item)}
                  >
                    Review saved result
                  </button>
                </div>
              ))}
          {writable && (
            <button
              className="settings-button"
              onClick={() =>
                void perform(async () => {
                  setRoutines((await collieClient.listRoutines()).routines)
                })
              }
            >
              Routine delivery…
            </button>
          )}
          {routines && active && (
            <section className="shared-card">
              <h3>Publish future routine results</h3>
              <p>
                The routine uses your model and tools. Its future result text
                will be published automatically to{' '}
                {active.members
                  ?.map((member) => member.display_name)
                  .join(', ')}
                . Tool logs remain private. Audience changes pause delivery
                until you authorize it again.
              </p>
              <select
                aria-label="Your routine"
                value={routineId}
                onChange={(event) => setRoutineId(event.target.value)}
              >
                <option value="">Choose your routine</option>
                {routines.map((routine) => (
                  <option key={routine.id} value={routine.id}>
                    {routine.name}
                  </option>
                ))}
              </select>
              <button
                className="settings-button"
                disabled={!routineId || busy || !writable}
                onClick={() =>
                  void perform(
                    () =>
                      api.setRoutineDelivery(
                        routineId,
                        active.id,
                        active.membership_revision
                      ),
                    'Future routine results will be published to this audience.'
                  )
                }
              >
                Authorize future result publication
              </button>
              <button
                className="settings-button"
                disabled={!routineId || busy}
                onClick={() =>
                  void perform(
                    () => api.setRoutineDelivery(routineId, null),
                    'Shared routine delivery disabled.'
                  )
                }
              >
                Disable shared delivery
              </button>
              <button
                className="settings-button"
                onClick={() => setRoutines(null)}
              >
                Close
              </button>
            </section>
          )}
          {writable && personalMessages.length > 0 && (
            <button
              className="settings-button"
              onClick={() => {
                setCopyOpen(true)
                setCopyIds([])
                setCopyText('')
              }}
            >
              Share selected local messages…
            </button>
          )}
          {copyOpen && active && writable && (
            <section className="shared-card">
              <h3>Review a copy for this audience</h3>
              <p>
                {active.members
                  ?.map((member) => member.display_name)
                  .join(', ')}{' '}
                can read the published copy. Select message text, then review it
                below. Attachments and tool logs are excluded.
              </p>
              {personalMessages.map((message) => (
                <label className="shared-copy-choice" key={message.id}>
                  <input
                    type="checkbox"
                    checked={copyIds.includes(message.id)}
                    onChange={(event) => {
                      const ids = event.target.checked
                        ? [...copyIds, message.id]
                        : copyIds.filter((id) => id !== message.id)
                      setCopyIds(ids)
                      setCopyText(
                        personalMessages
                          .filter((item) => ids.includes(item.id))
                          .map(
                            (item) =>
                              `${item.role === 'user' ? 'Me' : 'Collie'}: ${item.content}`
                          )
                          .join('\n\n')
                      )
                    }}
                  />
                  <span>
                    {message.role === 'user' ? 'Me' : 'Collie'}:{' '}
                    {message.content.slice(0, 160)}
                  </span>
                </label>
              ))}
              <label>
                Approved copy
                <textarea
                  value={copyText}
                  onChange={(event) => setCopyText(event.target.value)}
                />
              </label>
              <button
                className="settings-button"
                disabled={busy || !copyText.trim()}
                onClick={() =>
                  void perform(async () => {
                    await api.sendMessage(active.id, copyText, active.revision)
                    setCopyOpen(false)
                    await reconcile(active)
                  })
                }
              >
                Publish this reviewed copy
              </button>
              <button
                className="settings-button"
                onClick={() => setCopyOpen(false)}
              >
                Cancel
              </button>
            </section>
          )}
          {currentPrivate && (
            <aside className="private-result-drawer">
              <div>
                <strong>Private result · visible only to you</strong>
                <button
                  className="settings-icon-button"
                  aria-label="Close private result"
                  onClick={() => setPrivateResult(null)}
                >
                  <X size={14} />
                </button>
              </div>
              <p>{currentPrivate.state}</p>
              <label>
                Review what to publish
                <textarea
                  value={currentPrivate.content}
                  onChange={(event) =>
                    setPrivateResult({
                      ...currentPrivate,
                      content: event.target.value
                    })
                  }
                />
              </label>
              <button
                className="settings-button"
                disabled={
                  !currentPrivate.content ||
                  busy ||
                  !canPublishAccepted ||
                  currentPrivate.state === 'lease_lost'
                }
                onClick={() => void publish()}
              >
                Publish reviewed result to these participants
              </button>
            </aside>
          )}
          {active && (
            <div className="shared-composer">
              <div className="shared-input-wrap">
                <textarea
                  value={draft}
                  disabled={!writable}
                  onChange={(event) => setDraft(event.target.value)}
                  placeholder={
                    writable
                      ? 'Write for this audience…'
                      : 'This conversation is read-only.'
                  }
                  aria-label="Shared message"
                />
                {writable && /@[^\s]*$/.test(draft) && (
                  <div className="mention-picker">
                    {members
                      .filter((member) =>
                        member.display_name
                          .toLowerCase()
                          .includes(draft.split('@').pop()!.toLowerCase())
                      )
                      .map((member) => (
                        <button
                          key={member.id}
                          onClick={() => {
                            if (
                              active.members?.some(
                                (item) => item.id === member.id
                              )
                            ) {
                              setDraft(
                                draft.replace(
                                  /@[^\s]*$/,
                                  `@${member.display_name} `
                                )
                              )
                              setMentions((current) => ({
                                ...current,
                                [active.id]: [
                                  ...new Set([
                                    ...(current[active.id] || []),
                                    member.id
                                  ])
                                ]
                              }))
                            } else {
                              setQuery(member.display_name)
                              setNotice(
                                'This person is not a participant. Use Invite to share history with them.'
                              )
                            }
                          }}
                        >
                          @{member.display_name}
                          {active.members?.some((item) => item.id === member.id)
                            ? ''
                            : ' · invite first'}
                        </button>
                      ))}
                  </div>
                )}
              </div>
              <button
                className="settings-button"
                disabled={!writable || !draft.trim() || privateBusy}
                onClick={() => void runPrivate()}
              >
                {privateBusy ? 'Running privately…' : 'Ask my Collie privately'}
              </button>
              <button
                className="settings-button is-primary"
                disabled={!writable || !draft.trim() || sending}
                onClick={() => void send()}
              >
                <Send size={14} /> {sending ? 'Saving…' : 'Send'}
              </button>
            </div>
          )}
        </section>
      </div>
    </main>
  )
}
