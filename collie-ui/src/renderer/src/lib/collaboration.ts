import type { LocalCollaborationEvent } from './ipc'

export interface CollaborationMember {
  id: string
  display_name: string
  email?: string | null
  role?: string
  status?: string
}
export interface CollaborationOrganization {
  id: string
  name: string
  member_count?: number
  members?: CollaborationMember[]
}
export interface CollaborationInvite {
  id: string
  kind?: 'session' | 'organization'
  session_id?: string
  organization_id?: string
  inviter_name?: string
  email?: string
  status: string
  created_at?: string
  audience_policy?: { summary: string; includes_existing_history: boolean }
  audience_policy_version?: number
}
export interface SharedNotification {
  notification_id: number
  session_id: string
  kind: string
  read_at?: string | null
}
export interface SharedRunState {
  run_id: string
  status: string
  requested_by: string
  created_at?: string
}
export interface CollaborationSession {
  id: string
  title: string
  organization_id: string
  status: string
  revision?: number
  membership_revision?: number
  members?: CollaborationMember[]
  unread?: number
  archive_status?: string
  quota?: CollaborationQuota
  run_state?: SharedRunState
}
export interface CollaborationQuota {
  used_bytes?: number
  limit_bytes?: number
  used_messages?: number
  limit_messages?: number
  reserved_bytes?: number
  warning?: string | null
}
export interface CollaborationEvent {
  id: string
  event_id?: string
  session_id?: string
  sequence: number
  seq?: number
  kind?: string
  message_id?: string
  author_id?: string
  author_name?: string
  role: 'user' | 'assistant' | 'system'
  content: string
  created_at: string
  revision?: number
  sync_status?: 'local' | 'pending' | 'synced' | 'error'
}
export interface ArchiveStatus {
  state: string
  pending_participants?: CollaborationMember[]
  progress?: number
  local_path?: string | null
  reclaimed?: boolean
  final_sequence?: number
}
export interface CollaborationBootstrap {
  organizations?: CollaborationOrganization[]
  sessions?: CollaborationSession[]
  invites?: CollaborationInvite[]
  account?: CollaborationMember
  feature_enabled?: boolean
  message?: string
  sync_error?: string
}
export interface SharedFile {
  file_id: string
  name: string
  byte_length: number
  version?: number
}

export function formatBytes(value?: number): string {
  if (!Number.isFinite(value) || !value || value < 1024)
    return `${Math.max(0, value || 0)} B`
  const units = ['KiB', 'MiB', 'GiB']
  let amount = value
  let unit = -1
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024
    unit += 1
  }
  return `${amount.toFixed(amount >= 10 ? 0 : 1)} ${units[unit]}`
}

export function canComposeSharedSession(status?: string): boolean {
  return status === 'active'
}

export function canInviteToSession(
  sessionId: string | undefined,
  memberId: string,
  members: Array<{ id: string }>
): boolean {
  return Boolean(
    sessionId && memberId && !members.some((member) => member.id === memberId)
  )
}

export function retainOptimisticEvent<T extends { sync_status?: string }>(
  events: T[],
  event: T
): T[] {
  return [...events, { ...event, sync_status: event.sync_status || 'pending' }]
}

export interface CollaborationFacade {
  bootstrap: () => Promise<CollaborationBootstrap>
  createOrganization: (name: string) => Promise<unknown>
  createSession: (
    orgId: string,
    title: string
  ) => Promise<{ session?: CollaborationSession }>
  invite: (
    sessionId: string,
    memberId: string,
    policy: { summary: string; includes_existing_history: boolean }
  ) => Promise<unknown>
  acceptInvite: (
    inviteId: string,
    audienceConsent: boolean,
    policyVersion: number
  ) => Promise<unknown>
  openSession: (
    sessionId: string
  ) => Promise<{
    events?: LocalCollaborationEvent[]
    high_water?: number
    archive?: ArchiveStatus
    files?: SharedFile[]
    sync_error?: string
    run_state?: SharedRunState
  }>
  sendMessage: (
    sessionId: string,
    content: string,
    expectedRevision?: number,
    mentionedUserIds?: string[]
  ) => Promise<{
    event?: LocalCollaborationEvent
    events?: LocalCollaborationEvent[]
    high_water?: number
  }>
  runPrivate: (
    sessionId: string,
    content: string
  ) => Promise<{
    draftId: string
    runId?: string
    content?: string
    state?: string
  }>
  publishDraft: (draftId: string, content: string) => Promise<unknown>
  listPrivateDrafts: () => Promise<
    Array<{
      draftId: string
      runId: string
      sessionId: string
      content: string
      state: string
    }>
  >
  requestArchive: (sessionId: string) => Promise<{ archive?: ArchiveStatus }>
  archiveStatus: (sessionId: string) => Promise<ArchiveStatus>
  saveArchive: (sessionId: string) => Promise<ArchiveStatus>
  exportArchive: (archivePath: string) => Promise<{ path: string }>
  importArchive: () => Promise<unknown>
  listLocalArchives: () => Promise<Array<Record<string, unknown>>>
  continueSession: (
    sessionId: string
  ) => Promise<{ session?: CollaborationSession }>
  installSlack: (orgId: string) => Promise<unknown>
  linkSlackSender: (
    installationId: string
  ) => Promise<{ code?: string; message?: string }>
  slackSettings: (
    orgId: string
  ) => Promise<{
    installations?: Array<{
      installation_id: string
      team_id: string
      status: string
    }>
  }>
  selectSlackChannel: (
    orgId: string,
    installationId: string,
    channelId: string
  ) => Promise<unknown>
  editMessage: (
    sessionId: string,
    messageId: string,
    content: string,
    expectedRevision: number
  ) => Promise<unknown>
  deleteMessage: (
    sessionId: string,
    messageId: string,
    expectedRevision: number
  ) => Promise<unknown>
  inviteOrganization: (
    orgId: string,
    userId: string,
    displayName: string
  ) => Promise<unknown>
  uploadFile: (
    sessionId: string,
    expectedRevision: number,
    membershipRevision: number
  ) => Promise<unknown>
  listFiles: (sessionId: string) => Promise<{ files?: SharedFile[] }>
  downloadFile: (sessionId: string, fileId: string) => Promise<unknown>
  stopSessionRun: (sessionId: string) => Promise<unknown>
  resolveReconciliation: (runId: string) => Promise<unknown>
  revokeMember: (sessionId: string, memberId: string) => Promise<unknown>
  acceptOrganizationInvite: (inviteId: string) => Promise<unknown>
  notifications: () => Promise<{
    notifications?: SharedNotification[]
    next_cursor: number
  }>
  ackNotifications: (through: number) => Promise<unknown>
  setRoutineDelivery: (
    routineId: string,
    sessionId: string | null,
    audienceRevision?: number
  ) => Promise<unknown>
}

/** Apply canonical edits/tombstones, retaining pending local rows returned by main. */
export function sharedMessages(
  rows: Array<
    Partial<LocalCollaborationEvent> & { seq?: number; sync_status?: string }
  >,
  members: CollaborationMember[] = []
): CollaborationEvent[] {
  const authors = new Map(
    members.map((member) => [member.id, member.display_name])
  )
  const messages = new Map<string, CollaborationEvent>()
  for (const row of rows) {
    const id = row.message_id || row.event_id
    if (!id) continue
    if (row.kind === 'delete' || (row as { deleted?: boolean }).deleted) {
      messages.delete(id)
      continue
    }
    const prior = messages.get(id)
    messages.set(id, {
      id,
      event_id: row.event_id,
      author_id: row.author_id,
      sequence: prior?.sequence ?? Number(row.seq || 0),
      role: row.role || prior?.role || 'user',
      content: row.content || '',
      author_name:
        row.role === 'assistant'
          ? `Collie · requested by ${authors.get(row.author_id || '') || 'member'}`
          : authors.get(row.author_id || '') || row.author_id || 'Member',
      created_at: prior?.created_at || row.created_at || '',
      revision: row.revision,
      sync_status: (row.sync_status ||
        (row.seq ? 'synced' : 'pending')) as CollaborationEvent['sync_status']
    })
  }
  return [...messages.values()]
}

export function sharedCollaboration(): CollaborationFacade {
  const value: {
    [K in keyof CollaborationFacade]: (
      ...args: Parameters<CollaborationFacade[K]>
    ) => Promise<unknown>
  } = window.account.collaboration
  if (!value)
    throw new Error(
      'Shared conversations are not available in this desktop build.'
    )
  return value as unknown as CollaborationFacade
}
