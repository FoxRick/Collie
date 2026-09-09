export interface SharedSessionEvent {
  event_id: string
  session_id: string
  kind: 'message' | 'edit' | 'delete'
  message_id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  revision?: number
  seq?: number
  created_at?: string
  author_id?: string
  request_run?: boolean
  expected_revision?: number
  mentioned_user_ids?: string[]
  publication_kind?: 'routine_result'
  routine_id?: string
  audience_revision?: number
}

export interface SharedAudiencePolicy {
  summary: string
  includes_existing_history: boolean
}

export interface SharedSessionStatus {
  available: boolean
  cursor: number
  pending: SharedSessionEvent[]
  archives: Array<Record<string, unknown>>
}

export interface SharedDraft {
  draftId: string
  runId: string
  content: string
  state: string
}

export interface StoredSharedDraft extends SharedDraft {
  sessionId: string
  state: 'ready_for_review' | 'lease_lost'
}

export interface CollaborationBridge {
  bootstrap(): Promise<unknown>
  createOrganization(name: string): Promise<unknown>
  inviteOrganization(
    organizationId: string,
    userId: string,
    displayName: string
  ): Promise<unknown>
  acceptOrganizationInvite(inviteId: string): Promise<unknown>
  directory(organizationId: string, query: string): Promise<unknown>
  createSession(organizationId: string, title: string): Promise<unknown>
  invite(
    sessionId: string,
    memberId: string,
    audiencePolicy: SharedAudiencePolicy
  ): Promise<unknown>
  acceptInvite(
    inviteId: string,
    audienceConsent: boolean,
    policyVersion: number
  ): Promise<unknown>
  openSession(sessionId: string): Promise<unknown>
  sendMessage(
    sessionId: string,
    content: string,
    expectedRevision?: number,
    mentionedUserIds?: string[]
  ): Promise<unknown>
  editMessage(
    sessionId: string,
    messageId: string,
    content: string,
    expectedRevision: number
  ): Promise<unknown>
  deleteMessage(
    sessionId: string,
    messageId: string,
    expectedRevision: number
  ): Promise<unknown>
  uploadFile(
    sessionId: string,
    expectedSessionRevision: number,
    membershipRevision: number
  ): Promise<unknown>
  listFiles(sessionId: string): Promise<unknown>
  downloadFile(sessionId: string, fileId: string): Promise<unknown>
  runPrivate(sessionId: string, content: string): Promise<SharedDraft>
  publishDraft(draftId: string, content: string): Promise<unknown>
  listPrivateDrafts(): Promise<StoredSharedDraft[]>
  stopSessionRun(sessionId: string): Promise<unknown>
  resolveReconciliation(runId: string): Promise<unknown>
  requestArchive(sessionId: string): Promise<unknown>
  archiveStatus(sessionId: string): Promise<unknown>
  saveArchive(sessionId: string): Promise<unknown>
  exportArchive(
    archivePath: string
  ): Promise<{ canceled: boolean; path?: string }>
  importArchive(): Promise<{ canceled: boolean; archive?: unknown }>
  listLocalArchives(): Promise<Array<Record<string, unknown>>>
  continueSession(sessionId: string, title?: string): Promise<unknown>
  revokeMember(sessionId: string, memberId: string): Promise<unknown>
  installSlack(organizationId: string): Promise<{ opened: boolean }>
  linkSlackSender(installationId: string): Promise<unknown>
  selectSlackChannel(
    organizationId: string,
    installationId: string,
    channelId: string
  ): Promise<unknown>
  notifications(cursor?: number): Promise<unknown>
  ackNotifications(through: number): Promise<unknown>
  setRoutineDelivery(
    routineId: string,
    sessionId: string | null,
    audienceRevision?: number
  ): Promise<unknown>
  slackSettings(organizationId: string): Promise<unknown>
  linkSlackChannel(
    sessionId: string,
    installationId: string,
    channelId: string
  ): Promise<unknown>
}
