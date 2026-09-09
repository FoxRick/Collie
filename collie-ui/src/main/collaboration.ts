import { app, BrowserWindow, dialog, safeStorage, shell } from 'electron'
import { createHash, randomBytes, randomUUID } from 'crypto'
import {
  chmodSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync
} from 'fs'
import { basename, dirname, join } from 'path'
import { SUPABASE_ANON_KEY, SUPABASE_URL } from '../shared/account-config'
import type {
  SharedDraft,
  SharedSessionEvent,
  SharedSessionStatus
} from '../shared/collaboration'
import { getVerifiedAccount } from './account-auth'
import { commandWithCore } from './core-client'
import { collaborationIdentityBindToken } from './python'

const RPC_COMMANDS = new Set([
  'bootstrap',
  'create_organization',
  'invite_organization',
  'accept_organization_invite',
  'directory',
  'create_session',
  'invite',
  'accept_invite',
  'read_events',
  'append_message',
  'edit_message',
  'delete_message',
  'enroll_device',
  'list_pending_runs',
  'claim_run',
  'renew_run',
  'cancel_run',
  'stop_session_run',
  'resolve_reconciliation',
  'complete_run',
  'request_archive',
  'archive_manifest',
  'ack_archive',
  'archive_status',
  'continue_session',
  'revoke_member',
  'slack_settings',
  'begin_slack_link',
  'select_slack_channel',
  'notifications',
  'ack_notifications',
  'publish_routine'
])
type StoredDraft = {
  accountId: string
  runId: string
  fence: unknown
  sessionId: string
  content: string
  eventId: string
  messageId: string
}
const drafts = new Map<string, StoredDraft>()
const activeRuns = new Map<string, { runId: string; leaseToken: unknown }>()
const draftRenewals = new Map<string, ReturnType<typeof setInterval>>()
let boundIdentity = ''
let reconcilePromise: Promise<void> | null = null
let periodicReconcile: ReturnType<typeof setInterval> | null = null
type LocalIdentity = {
  id: string
  accessToken: string
  deviceId: string
  email?: string | null
}

function collaborationCore(
  type: string,
  payload: Record<string, unknown>
): Promise<unknown> {
  if (!type.startsWith('collaboration_'))
    throw new Error('Invalid collaboration core command.')
  return commandWithCore(type, {
    ...payload,
    identity_token: collaborationIdentityBindToken
  })
}

function serviceUrl(): string {
  const value = SUPABASE_URL.replace(/\/+$/, '')
  if (!value || !SUPABASE_ANON_KEY)
    throw new Error('Shared conversations are not enabled in this build yet.')
  return value
}

function deviceIdentity(): { id: string; publicKey: string } {
  const path = join(app.getPath('userData'), 'collaboration-device.json')
  try {
    const value = JSON.parse(readFileSync(path, 'utf8')) as {
      id?: unknown
      public_key?: unknown
    }
    if (
      typeof value.id === 'string' &&
      /^[0-9a-f-]{36}$/i.test(value.id) &&
      typeof value.public_key === 'string'
    ) {
      const publicKey = safeStorage.decryptString(
        Buffer.from(value.public_key, 'base64')
      )
      if (/^[A-Za-z0-9_-]{43}$/.test(publicKey))
        return { id: value.id, publicKey }
    }
  } catch {
    /* create it below */
  }
  if (!safeStorage.isEncryptionAvailable())
    throw new Error('Secure device enrollment is unavailable on this computer.')
  const id = randomUUID()
  const publicKey = randomBytes(32).toString('base64url')
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(
    path,
    JSON.stringify({
      id,
      public_key: safeStorage.encryptString(publicKey).toString('base64')
    }),
    { encoding: 'utf8', mode: 0o600 }
  )
  try {
    chmodSync(path, 0o600)
  } catch {
    /* best effort on non-POSIX filesystems */
  }
  return { id, publicKey }
}

function bindingPath(): string {
  return join(app.getPath('userData'), 'collaboration-binding.bin')
}
function draftsPath(): string {
  return join(app.getPath('userData'), 'collaboration-drafts.bin')
}
function saveBinding(
  accountId: string,
  localDeviceId: string,
  email?: string | null
): void {
  if (!safeStorage.isEncryptionAvailable()) return
  writeFileSync(
    bindingPath(),
    safeStorage.encryptString(
      JSON.stringify({ accountId, deviceId: localDeviceId, email })
    ),
    { mode: 0o600 }
  )
}
function readBinding(): LocalIdentity | null {
  try {
    if (!safeStorage.isEncryptionAvailable()) return null
    const value = JSON.parse(
      safeStorage.decryptString(readFileSync(bindingPath()))
    ) as { accountId?: unknown; deviceId?: unknown; email?: unknown }
    if (
      typeof value.accountId !== 'string' ||
      typeof value.deviceId !== 'string'
    )
      return null
    return {
      id: value.accountId,
      deviceId: value.deviceId,
      accessToken: '',
      email: typeof value.email === 'string' ? value.email : null
    }
  } catch {
    return null
  }
}
function loadDrafts(): void {
  if (drafts.size || !safeStorage.isEncryptionAvailable()) return
  try {
    const values = JSON.parse(
      safeStorage.decryptString(readFileSync(draftsPath()))
    ) as Record<string, StoredDraft>
    for (const [id, draft] of Object.entries(values)) drafts.set(id, draft)
  } catch {
    /* no readable drafts */
  }
}
function persistDrafts(): void {
  if (!safeStorage.isEncryptionAvailable()) return
  writeFileSync(
    draftsPath(),
    safeStorage.encryptString(JSON.stringify(Object.fromEntries(drafts))),
    { mode: 0o600 }
  )
}

function unwrap(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object') return {}
  const object = value as Record<string, unknown>
  return object.data && typeof object.data === 'object'
    ? (object.data as Record<string, unknown>)
    : object
}

function normalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(normalize)
  if (!value || typeof value !== 'object') return value
  const source = value as Record<string, unknown>
  const result: Record<string, unknown> = {}
  for (const [key, item] of Object.entries(source))
    result[key] = normalize(item)
  if (typeof source.org_id === 'string') result.organization_id = source.org_id
  if (typeof source.invite_id === 'string') result.id = source.invite_id
  else if (typeof source.session_id === 'string') result.id = source.session_id
  else if (typeof source.user_id === 'string') result.id = source.user_id
  else if (typeof source.org_id === 'string') result.id = source.org_id
  return result
}

export async function collaborationBackend(
  command: string,
  payload: Record<string, unknown>,
  token?: string
): Promise<Record<string, unknown>> {
  if (!RPC_COMMANDS.has(command))
    throw new Error('That collaboration action is not available.')
  const accessToken = token ?? (await getVerifiedAccount()).accessToken
  const response = await fetch(
    `${serviceUrl()}/rest/v1/rpc/collaboration_command`,
    {
      method: 'POST',
      headers: {
        apikey: SUPABASE_ANON_KEY,
        Authorization: `Bearer ${accessToken}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ p_command: command, p_payload: payload }),
      signal: AbortSignal.timeout(20_000)
    }
  )
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const detail =
      body && typeof body === 'object' ? (body as Record<string, unknown>) : {}
    throw new Error(
      typeof detail.message === 'string'
        ? detail.message
        : typeof detail.error === 'string'
          ? detail.error
          : 'Shared service is unavailable.'
    )
  }
  return unwrap(body)
}

async function identity(cloudRequired = true): Promise<LocalIdentity> {
  let account: { id: string; accessToken: string; email?: string | null }
  try {
    account = await getVerifiedAccount()
  } catch (error) {
    const cached = readBinding()
    if (cloudRequired || !cached) throw error
    account = cached
  }
  const device = account.accessToken
    ? deviceIdentity()
    : { id: (account as LocalIdentity).deviceId, publicKey: '' }
  const localDeviceId = device.id
  const key = `${account.id}:${localDeviceId}`
  if (boundIdentity !== key) {
    await collaborationCore('collaboration_bind_identity', {
      bind_token: collaborationIdentityBindToken,
      account_id: account.id,
      device_id: localDeviceId
    })
    if (account.accessToken) {
      await collaborationBackend(
        'enroll_device',
        {
          device_id: localDeviceId,
          label: process.env.COMPUTERNAME || 'Collie desktop',
          public_key: device.publicKey
        },
        account.accessToken
      )
      saveBinding(account.id, localDeviceId, account.email)
    }
    boundIdentity = key
  }
  return {
    id: account.id,
    accessToken: account.accessToken,
    deviceId: localDeviceId,
    email: account.email
  }
}

export async function bootstrapCollaboration(): Promise<
  Record<string, unknown>
> {
  const account = await identity(false)
  try {
    const result = normalize(
      await collaborationBackend('bootstrap', {})
    ) as Record<string, unknown>
    const organizations = Array.isArray(result.organizations)
      ? (result.organizations as Array<Record<string, unknown>>)
      : []
    for (const organization of organizations) {
      if (typeof organization.id !== 'string') continue
      try {
        const directory = normalize(
          await collaborationBackend('directory', { org_id: organization.id })
        ) as { members?: unknown[] }
        organization.members = directory.members || []
      } catch {
        organization.members = []
      }
    }
    result.account = {
      id: account.id,
      display_name: account.email || 'You',
      email: account.email || null
    }
    await collaborationCore('collaboration_cache_bootstrap', {
      snapshot: result
    })
    return result
  } catch (error) {
    const cached = (await collaborationCore(
      'collaboration_get_cached_bootstrap',
      {}
    )) as { snapshot?: Record<string, unknown> }
    if (cached.snapshot)
      return {
        ...cached.snapshot,
        sync_error: error instanceof Error ? error.message : 'Waiting to sync.'
      }
    throw error
  }
}

async function localStatus(sessionId = ''): Promise<SharedSessionStatus> {
  return (await collaborationCore('collaboration_status', {
    session_id: sessionId,
    limit: 200
  })) as SharedSessionStatus
}

async function applyPage(
  sessionId: string,
  page: Record<string, unknown>
): Promise<void> {
  const events = Array.isArray(page.events)
    ? page.events
    : page.event && typeof page.event === 'object'
      ? [page.event]
      : []
  const eventSeq =
    events.length &&
    typeof (events[events.length - 1] as Record<string, unknown>).seq ===
      'number'
      ? Number((events[events.length - 1] as Record<string, unknown>).seq)
      : 0
  const highWater = Number(
    page.next_cursor ?? page.high_water ?? page.highWater ?? eventSeq
  )
  if (events.length || highWater)
    await collaborationCore('collaboration_apply_page', {
      session_id: sessionId,
      events,
      next_cursor: highWater
    })
}

export async function openSharedSession(
  sessionId: string
): Promise<Record<string, unknown>> {
  if (!sessionId) throw new Error('Choose a shared conversation.')
  const account = await identity(false)
  let cursor = Number((await localStatus(sessionId)).cursor || 0)
  let cloud: Record<string, unknown> = {}
  let syncError: string | undefined
  try {
    for (let pages = 0; pages < 50; pages += 1) {
      cloud = await collaborationBackend(
        'read_events',
        { session_id: sessionId, cursor, limit: 100 },
        account.accessToken || undefined
      )
      await applyPage(sessionId, cloud)
      const events = Array.isArray(cloud.events) ? cloud.events : []
      cursor = Number(cloud.next_cursor ?? cloud.high_water ?? cursor)
      if (cursor >= Number(cloud.high_water ?? cursor) || events.length === 0)
        break
    }
  } catch (error) {
    syncError = error instanceof Error ? error.message : 'Waiting to sync.'
  }
  const local = (await collaborationCore('collaboration_list_messages', {
    session_id: sessionId
  })) as Record<string, unknown>
  let files: unknown[] = []
  if (!syncError) {
    try {
      files = ((await listSharedFiles(sessionId)).files as unknown[]) || []
    } catch {
      /* message history remains usable */
    }
  }
  return {
    ...cloud,
    ...local,
    events: Array.isArray(local.messages) ? local.messages : [],
    files,
    next_cursor: cursor,
    ...(syncError ? { sync_error: syncError } : {})
  }
}

function stableUserIds(value: unknown): string[] {
  if (value === undefined) return []
  if (!Array.isArray(value) || value.length > 20)
    throw new Error('The mentioned people list is invalid.')
  const ids = [
    ...new Set(value.map((item) => (typeof item === 'string' ? item : '')))
  ]
  if (ids.some((id) => !/^[0-9a-f-]{36}$/i.test(id)))
    throw new Error('The mentioned people list is invalid.')
  return ids
}

export async function sendSharedMessage(
  sessionId: string,
  content: string,
  expectedRevision?: number,
  requestRun = false,
  mentionedUserIds?: string[]
): Promise<Record<string, unknown>> {
  const text = content.trim()
  if (!sessionId || !text || Buffer.byteLength(text, 'utf8') > 2 * 1024 * 1024)
    throw new Error('Write a message within the shared-session limit.')
  const account = await identity(false)
  const mentions = stableUserIds(mentionedUserIds)
  const event: SharedSessionEvent = {
    event_id: randomUUID(),
    session_id: sessionId,
    kind: 'message',
    message_id: randomUUID(),
    author_id: account.id,
    role: 'user',
    content: text,
    revision: 1,
    request_run: requestRun,
    mentioned_user_ids: mentions,
    created_at: new Date().toISOString()
  }
  await collaborationCore('collaboration_queue_event', {
    session_id: sessionId,
    event
  })
  try {
    const accepted = await collaborationBackend(
      'append_message',
      {
        session_id: sessionId,
        event_id: event.event_id,
        message_id: event.message_id,
        content: text,
        request_run: requestRun,
        mentioned_user_ids: mentions
      },
      account.accessToken || undefined
    )
    await openSharedSession(sessionId)
    return accepted
  } catch (error) {
    if (requestRun) throw error
    return {
      pending: true,
      event,
      error: error instanceof Error ? error.message : 'Waiting to sync.'
    }
  }
}

export async function changeSharedMessage(
  kind: 'edit_message' | 'delete_message',
  sessionId: string,
  messageId: string,
  content: string,
  expectedRevision: number
): Promise<Record<string, unknown>> {
  const account = await identity()
  if (!Number.isSafeInteger(expectedRevision) || expectedRevision < 1)
    throw new Error('The message revision is invalid.')
  const event: SharedSessionEvent = {
    event_id: randomUUID(),
    session_id: sessionId,
    kind: kind === 'edit_message' ? 'edit' : 'delete',
    message_id: messageId,
    author_id: account.id,
    role: 'user',
    content: kind === 'edit_message' ? content : '',
    expected_revision: expectedRevision,
    revision: expectedRevision + 1,
    created_at: new Date().toISOString()
  }
  await collaborationCore('collaboration_queue_event', {
    session_id: sessionId,
    event
  })
  try {
    const result = await collaborationBackend(
      kind,
      {
        session_id: sessionId,
        event_id: event.event_id,
        message_id: messageId,
        expected_revision: expectedRevision,
        ...(kind === 'edit_message' ? { content } : {})
      },
      account.accessToken
    )
    await openSharedSession(sessionId)
    return result
  } catch (error) {
    return {
      pending: true,
      event,
      error: error instanceof Error ? error.message : 'Waiting to sync.'
    }
  }
}

export async function reconcileCollaboration(): Promise<void> {
  if (reconcilePromise) return reconcilePromise
  reconcilePromise = (async () => {
    const account = await identity()
    const status = await localStatus()
    for (const event of status.pending || []) {
      try {
        const command =
          event.publication_kind === 'routine_result'
            ? 'publish_routine'
            : event.kind === 'edit'
              ? 'edit_message'
              : event.kind === 'delete'
                ? 'delete_message'
                : 'append_message'
        const accepted = await collaborationBackend(
          command,
          {
            session_id: event.session_id,
            event_id: event.event_id,
            message_id: event.message_id,
            ...(event.kind !== 'delete' ? { content: event.content } : {}),
            ...(event.publication_kind === 'routine_result'
              ? {
                  routine_id: event.routine_id,
                  audience_revision: event.audience_revision
                }
              : event.kind === 'message'
                ? {
                    request_run: Boolean(event.request_run),
                    mentioned_user_ids: event.mentioned_user_ids || []
                  }
                : { expected_revision: event.expected_revision })
          },
          account.accessToken
        )
        if (event.publication_kind === 'routine_result') {
          await openSharedSession(event.session_id)
          await collaborationCore('collaboration_mark_routine_delivery', {
            routine_id: event.routine_id,
            event_id: event.event_id,
            status: 'acknowledged'
          })
        } else await applyPage(event.session_id, accepted)
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        if (
          event.publication_kind === 'routine_result' &&
          /(FORBIDDEN|NOT_FOUND|SESSION_CLOSED|REVISION_CONFLICT|STALE|REVOKED)/i.test(
            message
          )
        ) {
          await collaborationCore('collaboration_mark_routine_delivery', {
            routine_id: event.routine_id,
            event_id: event.event_id,
            status: 'rejected',
            error: message
          }).catch(() => undefined)
        }
        // Offline and transient failures remain in the durable outbox.
      }
    }
    const boot = await collaborationBackend(
      'bootstrap',
      {},
      account.accessToken
    )
    for (const session of Array.isArray(boot.sessions)
      ? (boot.sessions as Array<Record<string, unknown>>)
      : []) {
      const sessionId =
        typeof session.session_id === 'string'
          ? session.session_id
          : typeof session.id === 'string'
            ? session.id
            : ''
      if (sessionId) {
        await openSharedSession(sessionId)
        if (session.status === 'archive_pending') {
          try {
            const archive = await collaborationBackend(
              'archive_status',
              { session_id: sessionId },
              account.accessToken
            )
            const ownReceipt = (
              Array.isArray(archive.recipients) ? archive.recipients : []
            ).find(
              (item) =>
                item &&
                typeof item === 'object' &&
                (item as Record<string, unknown>).user_id === account.id
            ) as Record<string, unknown> | undefined
            if (ownReceipt && ownReceipt.received !== true)
              await saveVerifiedArchive(sessionId)
          } catch {
            /* archive remains pending and retries on the next bounded pass */
          }
        }
      }
    }
    const pending = await collaborationBackend(
      'list_pending_runs',
      {},
      account.accessToken
    )
    for (const run of Array.isArray(pending.runs)
      ? (pending.runs as Array<Record<string, unknown>>)
      : []) {
      if (
        typeof run.run_id === 'string' &&
        typeof run.session_id === 'string' &&
        !activeRuns.has(run.session_id)
      ) {
        void executePendingRun(run.run_id, run.session_id).catch(
          () => undefined
        )
      }
    }
  })().finally(() => {
    reconcilePromise = null
  })
  return reconcilePromise
}

async function executePendingRun(
  runId: string,
  sessionId: string
): Promise<void> {
  const account = await identity()
  const claim = await collaborationBackend('claim_run', {
    run_id: runId,
    device_id: account.deviceId
  })
  const content =
    typeof claim.request_content === 'string' ? claim.request_content : ''
  if (!content) {
    await collaborationBackend('cancel_run', {
      run_id: runId,
      lease_token: claim.lease_token
    }).catch(() => undefined)
    return
  }
  activeRuns.set(sessionId, { runId, leaseToken: claim.lease_token })
  try {
    await applyPage(sessionId, {
      events: Array.isArray(claim.events) ? claim.events : [],
      next_cursor: claim.context_cutoff
    })
  } catch (error) {
    activeRuns.delete(sessionId)
    await collaborationBackend('cancel_run', {
      run_id: runId,
      lease_token: claim.lease_token
    }).catch(() => undefined)
    throw error
  }
  let fenced = false
  const timer = setInterval(() => {
    void collaborationBackend('renew_run', {
      run_id: runId,
      device_id: account.deviceId,
      lease_token: claim.lease_token
    })
      .then((renewed) =>
        collaborationCore('collaboration_control_run', {
          run_id: runId,
          action: 'renew',
          lease_token: claim.lease_token,
          lease_expires_at: renewed.lease_expires_at
        })
      )
      .catch(() => {
        fenced = true
        void collaborationCore('collaboration_control_run', {
          run_id: runId,
          action: 'fenced',
          lease_token: claim.lease_token
        }).catch(() => undefined)
      })
  }, 15_000)
  try {
    const result = (await collaborationCore('collaboration_run_shared', {
      mode: 'private_result',
      content,
      claim,
      published_history: []
    })) as Record<string, unknown>
    if (fenced || typeof result.content !== 'string')
      throw new Error('Run lease lost.')
    const draftId = randomUUID()
    drafts.set(draftId, {
      accountId: account.id,
      runId,
      fence: claim.lease_token,
      sessionId,
      content: result.content,
      eventId: randomUUID(),
      messageId: randomUUID()
    })
    draftRenewals.set(draftId, timer)
    persistDrafts()
  } catch (error) {
    clearInterval(timer)
    await collaborationBackend('cancel_run', {
      run_id: runId,
      lease_token: claim.lease_token
    }).catch(() => undefined)
    throw error
  } finally {
    activeRuns.delete(sessionId)
  }
}

export async function runSharedPrivately(
  sessionId: string,
  content: string
): Promise<SharedDraft> {
  const text = content.trim()
  if (!text) throw new Error('Write a request first.')
  const account = await identity()
  const admitted = await sendSharedMessage(sessionId, text, undefined, true)
  const runIdToClaim = String(admitted.run_id || '')
  if (!runIdToClaim)
    throw new Error(
      'The shared request was saved, but no execution was admitted.'
    )
  const claim = await collaborationBackend(
    'claim_run',
    { run_id: runIdToClaim, device_id: account.deviceId },
    account.accessToken
  )
  activeRuns.set(sessionId, {
    runId: String(claim.run_id),
    leaseToken: claim.lease_token
  })
  try {
    await applyPage(sessionId, {
      events: Array.isArray(claim.events) ? claim.events : [],
      next_cursor: claim.context_cutoff
    })
  } catch (error) {
    activeRuns.delete(sessionId)
    await collaborationBackend('cancel_run', {
      run_id: claim.run_id,
      lease_token: claim.lease_token
    }).catch(() => undefined)
    throw error
  }
  let leaseFailed = false
  const renew = setInterval(() => {
    void collaborationBackend('renew_run', {
      run_id: claim.run_id,
      device_id: account.deviceId,
      lease_token: claim.lease_token
    })
      .then((renewed) =>
        collaborationCore('collaboration_control_run', {
          run_id: claim.run_id,
          action: 'renew',
          lease_token: claim.lease_token,
          lease_expires_at: renewed.lease_expires_at
        })
      )
      .catch(() => {
        leaseFailed = true
        void collaborationCore('collaboration_control_run', {
          run_id: claim.run_id,
          action: 'fenced',
          lease_token: claim.lease_token
        }).catch(() => undefined)
      })
  }, 15_000)
  let result: Record<string, unknown>
  try {
    result = (await collaborationCore('collaboration_run_shared', {
      mode: 'private_result',
      content: text,
      claim,
      published_history: []
    })) as Record<string, unknown>
  } catch (error) {
    clearInterval(renew)
    activeRuns.delete(sessionId)
    await collaborationBackend('cancel_run', {
      run_id: claim.run_id,
      lease_token: claim.lease_token
    }).catch(() => undefined)
    throw error
  }
  activeRuns.delete(sessionId)
  if (leaseFailed) {
    clearInterval(renew)
    await collaborationBackend('cancel_run', {
      run_id: claim.run_id,
      lease_token: claim.lease_token
    }).catch(() => undefined)
    throw new Error(
      'The execution lease was lost. This run needs reconciliation before it can be retried.'
    )
  }
  const runId = String(result.run_id || claim.run_id || '')
  if (!runId || typeof result.content !== 'string') {
    clearInterval(renew)
    await collaborationBackend('cancel_run', {
      run_id: claim.run_id,
      lease_token: claim.lease_token
    }).catch(() => undefined)
    throw new Error(
      'The private shared run did not return a reviewable result.'
    )
  }
  const draftId = randomUUID()
  drafts.set(draftId, {
    accountId: account.id,
    runId,
    fence: claim.lease_token,
    sessionId,
    content: result.content,
    eventId: randomUUID(),
    messageId: randomUUID()
  })
  draftRenewals.set(draftId, renew)
  persistDrafts()
  return {
    draftId,
    runId,
    content: result.content,
    state: String(result.state || 'ready_for_review')
  }
}

export async function listPrivateDrafts(): Promise<
  Array<
    SharedDraft & {
      sessionId: string
      state: 'ready_for_review' | 'lease_lost'
    }
  >
> {
  const account = await identity(false)
  loadDrafts()
  const result: Array<
    SharedDraft & {
      sessionId: string
      state: 'ready_for_review' | 'lease_lost'
    }
  > = []
  for (const [draftId, draft] of drafts) {
    if (draft.accountId !== account.id) continue
    let state: 'ready_for_review' | 'lease_lost' = 'ready_for_review'
    try {
      await collaborationBackend('renew_run', {
        run_id: draft.runId,
        device_id: account.deviceId,
        lease_token: draft.fence
      })
      if (!draftRenewals.has(draftId)) {
        const timer = setInterval(() => {
          void collaborationBackend('renew_run', {
            run_id: draft.runId,
            device_id: account.deviceId,
            lease_token: draft.fence
          }).catch(() => {
            clearInterval(timer)
            draftRenewals.delete(draftId)
          })
        }, 15_000)
        draftRenewals.set(draftId, timer)
      }
    } catch {
      state = 'lease_lost'
    }
    result.push({
      draftId,
      runId: draft.runId,
      sessionId: draft.sessionId,
      content: draft.content,
      state
    })
  }
  return result
}

export async function stopSessionRun(
  sessionId: string
): Promise<Record<string, unknown>> {
  const active = activeRuns.get(sessionId)
  if (active)
    await collaborationCore('collaboration_control_run', {
      run_id: active.runId,
      action: 'cancel',
      lease_token: active.leaseToken
    }).catch(() => undefined)
  return collaborationBackend('stop_session_run', { session_id: sessionId })
}

export async function publishSharedDraft(
  draftId: string,
  content: string
): Promise<Record<string, unknown>> {
  const account = await identity()
  loadDrafts()
  const draft = drafts.get(draftId)
  if (!draft || draft.accountId !== account.id)
    throw new Error('That private draft is no longer available.')
  const selected = content.trim()
  if (!selected || Buffer.byteLength(selected, 'utf8') > 2 * 1024 * 1024)
    throw new Error('Choose a result within the publication limit.')
  const result = await collaborationBackend(
    'complete_run',
    {
      run_id: draft.runId,
      lease_token: draft.fence,
      event_id: draft.eventId,
      message_id: draft.messageId,
      content: selected
    },
    account.accessToken
  )
  const renewal = draftRenewals.get(draftId)
  if (renewal) clearInterval(renewal)
  draftRenewals.delete(draftId)
  drafts.delete(draftId)
  persistDrafts()
  await openSharedSession(draft.sessionId)
  return result
}

export async function saveVerifiedArchive(
  sessionId: string
): Promise<Record<string, unknown>> {
  const account = await identity()
  const descriptor = await collaborationBackend(
    'archive_manifest',
    { session_id: sessionId },
    account.accessToken
  )
  if (
    typeof descriptor.manifest_json !== 'string' ||
    typeof descriptor.digest !== 'string'
  )
    throw new Error('The archive manifest is unavailable.')
  const existing = (await localStatus()).archives.find(
    (item) =>
      item &&
      typeof item === 'object' &&
      (item as Record<string, unknown>).digest === descriptor.digest
  ) as Record<string, unknown> | undefined
  if (existing) {
    await collaborationBackend(
      'ack_archive',
      {
        session_id: sessionId,
        archive_revision: descriptor.archive_revision,
        device_id: account.deviceId,
        digest: descriptor.digest,
        final_seq: descriptor.final_seq,
        byte_length: descriptor.byte_length
      },
      account.accessToken
    )
    return {
      state: 'saved_locally',
      local_path: existing.path,
      digest: descriptor.digest,
      final_seq: descriptor.final_seq,
      byte_length: descriptor.byte_length
    }
  }
  const attachments: Array<{ file_id: string; path: string }> = []
  const tempRoot = join(app.getPath('temp'), `collie-archive-${randomUUID()}`)
  try {
    const parsedManifest = JSON.parse(descriptor.manifest_json) as {
      files?: Array<Record<string, unknown>>
    }
    const files = Array.isArray(descriptor.files)
      ? (descriptor.files as Array<Record<string, unknown>>)
      : parsedManifest.files || []
    if (files.length) mkdirSync(tempRoot, { recursive: true })
    for (const file of files) {
      if (
        typeof file.file_id !== 'string' ||
        !/^[0-9a-f-]{36}$/i.test(file.file_id)
      )
        throw new Error('The archive file list is incomplete.')
      const objectPath =
        typeof file.object_path === 'string' ? file.object_path : ''
      const rawUrl =
        typeof file.download_url === 'string'
          ? file.download_url
          : `${serviceUrl()}/storage/v1/object/authenticated/collaboration-files/${objectPath.split('/').map(encodeURIComponent).join('/')}`
      const url = new URL(rawUrl)
      const expected = new URL(serviceUrl())
      if (
        url.protocol !== 'https:' ||
        url.origin !== expected.origin ||
        !url.pathname.startsWith('/storage/v1/object/')
      )
        throw new Error('An archive file had an unsafe download address.')
      const response = await fetch(url, {
        headers: { Authorization: `Bearer ${account.accessToken}` },
        signal: AbortSignal.timeout(60_000)
      })
      if (!response.ok)
        throw new Error('A shared archive file could not be downloaded.')
      const declaredLength = Number(response.headers.get('content-length') || 0)
      if (declaredLength > 5 * 1024 * 1024)
        throw new Error('A shared archive file exceeded its limit.')
      const bytes = Buffer.from(await response.arrayBuffer())
      if (bytes.byteLength > 5 * 1024 * 1024)
        throw new Error('A shared archive file exceeded its limit.')
      const path = join(tempRoot, file.file_id)
      writeFileSync(path, bytes, { mode: 0o600 })
      attachments.push({ file_id: file.file_id, path })
    }
    const receipt = (await collaborationCore('collaboration_write_archive', {
      manifest_json: descriptor.manifest_json,
      digest: descriptor.digest,
      attachments
    })) as Record<string, unknown>
    await collaborationBackend(
      'ack_archive',
      {
        session_id: sessionId,
        archive_revision: descriptor.archive_revision,
        device_id: account.deviceId,
        digest: receipt.digest,
        final_seq: receipt.final_seq,
        byte_length: receipt.byte_length
      },
      account.accessToken
    )
    return { state: 'saved_locally', local_path: receipt.path, ...receipt }
  } finally {
    rmSync(tempRoot, { recursive: true, force: true })
  }
}

export async function uploadSharedFile(
  mainWindow: BrowserWindow | null,
  sessionId: string,
  expectedSessionRevision: number,
  membershipRevision: number
): Promise<Record<string, unknown>> {
  if (
    !Number.isSafeInteger(expectedSessionRevision) ||
    !Number.isSafeInteger(membershipRevision)
  )
    throw new Error('Refresh the conversation before attaching a file.')
  const options = {
    title: 'Share a file with this conversation',
    properties: ['openFile'] as Array<'openFile'>
  }
  const picked = mainWindow
    ? await dialog.showOpenDialog(mainWindow, options)
    : await dialog.showOpenDialog(options)
  if (picked.canceled || !picked.filePaths[0]) return { canceled: true }
  const account = await identity()
  const path = picked.filePaths[0]
  const name = basename(path)
  if (!name || name.length > 255 || /[\x00-\x1f\x7f]/.test(name))
    throw new Error('That filename cannot be shared.')
  const bytes = readFileSync(path)
  if (bytes.byteLength < 1 || bytes.byteLength > 5 * 1024 * 1024)
    throw new Error('Choose a file up to 5 MiB.')
  const fileId = randomUUID()
  const sha256 = createHash('sha256').update(bytes).digest('hex')
  const endpoint = `${serviceUrl()}/functions/v1/collaboration-files`
  const headers = {
    apikey: SUPABASE_ANON_KEY,
    Authorization: `Bearer ${account.accessToken}`
  }
  const reserve = await fetch(endpoint, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      action: 'reserve',
      session_id: sessionId,
      file_id: fileId,
      name,
      content_type: 'application/octet-stream',
      byte_length: bytes.byteLength,
      sha256,
      expected_session_revision: expectedSessionRevision,
      membership_revision: membershipRevision
    }),
    signal: AbortSignal.timeout(20_000)
  })
  if (!reserve.ok) throw new Error('The shared file could not be reserved.')
  const upload = await fetch(
    `${endpoint}?file_id=${encodeURIComponent(fileId)}`,
    {
      method: 'PUT',
      headers: { ...headers, 'Content-Type': 'application/octet-stream' },
      body: new Uint8Array(bytes),
      signal: AbortSignal.timeout(60_000)
    }
  )
  const result = (await upload.json().catch(() => null)) as Record<
    string,
    unknown
  > | null
  if (!upload.ok || !result) {
    await fetch(endpoint, {
      method: 'POST',
      headers: { ...headers, 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'cancel', file_id: fileId })
    }).catch(() => undefined)
    throw new Error('The shared file could not be uploaded.')
  }
  return { canceled: false, ...result }
}

async function sharedFileRequest(
  action: string,
  sessionId: string
): Promise<{
  account: LocalIdentity
  endpoint: string
  headers: Record<string, string>
  result: Record<string, unknown>
}> {
  const account = await identity()
  const endpoint = `${serviceUrl()}/functions/v1/collaboration-files`
  const headers = {
    apikey: SUPABASE_ANON_KEY,
    Authorization: `Bearer ${account.accessToken}`
  }
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, session_id: sessionId }),
    signal: AbortSignal.timeout(20_000)
  })
  const result = (await response.json().catch(() => null)) as Record<
    string,
    unknown
  > | null
  if (!response.ok || !result) throw new Error('Shared files are unavailable.')
  return { account, endpoint, headers, result }
}

export async function listSharedFiles(
  sessionId: string
): Promise<Record<string, unknown>> {
  return (await sharedFileRequest('list', sessionId)).result
}

export async function downloadSharedFile(
  mainWindow: BrowserWindow | null,
  sessionId: string,
  fileId: string
): Promise<Record<string, unknown>> {
  if (!/^[0-9a-f-]{36}$/i.test(fileId))
    throw new Error('That shared file is invalid.')
  const { endpoint, headers, result } = await sharedFileRequest(
    'list',
    sessionId
  )
  const descriptor = (Array.isArray(result.files) ? result.files : []).find(
    (item) =>
      item &&
      typeof item === 'object' &&
      (item as Record<string, unknown>).file_id === fileId
  ) as Record<string, unknown> | undefined
  if (
    !descriptor ||
    typeof descriptor.name !== 'string' ||
    typeof descriptor.sha256 !== 'string'
  )
    throw new Error('That shared file is unavailable.')
  const response = await fetch(
    `${endpoint}?file_id=${encodeURIComponent(fileId)}`,
    { headers, signal: AbortSignal.timeout(60_000) }
  )
  if (!response.ok) throw new Error('The shared file could not be downloaded.')
  const bytes = Buffer.from(await response.arrayBuffer())
  if (
    bytes.byteLength !== Number(descriptor.byte_length) ||
    bytes.byteLength > 5 * 1024 * 1024 ||
    createHash('sha256').update(bytes).digest('hex') !== descriptor.sha256
  )
    throw new Error('The downloaded shared file failed verification.')
  const options = {
    title: 'Save shared file',
    defaultPath: basename(descriptor.name),
    buttonLabel: 'Save copy'
  }
  const picked = mainWindow
    ? await dialog.showSaveDialog(mainWindow, options)
    : await dialog.showSaveDialog(options)
  if (picked.canceled || !picked.filePath) return { canceled: true }
  const temp = `${picked.filePath}.collie-tmp-${randomUUID()}`
  try {
    writeFileSync(temp, bytes, { mode: 0o600 })
    renameSync(temp, picked.filePath)
  } finally {
    rmSync(temp, { force: true })
  }
  return { canceled: false, path: picked.filePath, file_id: fileId }
}

export async function exportSharedArchive(
  mainWindow: BrowserWindow | null,
  archivePath: string
): Promise<{ canceled: boolean; path?: string }> {
  const known = (await localStatus()).archives.some(
    (item) =>
      item &&
      typeof item === 'object' &&
      (item as Record<string, unknown>).path === archivePath
  )
  if (!known) throw new Error('Choose a verified local archive.')
  const options = {
    title: 'Export shared conversation archive',
    defaultPath: `${basename(archivePath)}.zip`,
    filters: [{ name: 'Collie archive', extensions: ['zip'] }]
  }
  const result = mainWindow
    ? await dialog.showSaveDialog(mainWindow, options)
    : await dialog.showSaveDialog(options)
  if (result.canceled || !result.filePath) return { canceled: true }
  const saved = (await collaborationCore('collaboration_export_archive', {
    archive_path: archivePath,
    destination: result.filePath
  })) as { path: string }
  return { canceled: false, path: saved.path }
}

export async function importSharedArchive(
  mainWindow: BrowserWindow | null
): Promise<{ canceled: boolean; archive?: unknown }> {
  const options = {
    title: 'Import shared conversation archive',
    properties: ['openFile'] as Array<'openFile'>,
    filters: [{ name: 'Collie archive', extensions: ['zip'] }]
  }
  const result = mainWindow
    ? await dialog.showOpenDialog(mainWindow, options)
    : await dialog.showOpenDialog(options)
  if (result.canceled || !result.filePaths[0]) return { canceled: true }
  return {
    canceled: false,
    archive: await collaborationCore('collaboration_import_archive', {
      source: result.filePaths[0]
    })
  }
}

export async function installSlack(
  organizationId: string
): Promise<{ opened: boolean }> {
  const account = await identity()
  const response = await fetch(`${serviceUrl()}/functions/v1/slack-install`, {
    method: 'POST',
    headers: {
      apikey: SUPABASE_ANON_KEY,
      Authorization: `Bearer ${account.accessToken}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ org_id: organizationId }),
    signal: AbortSignal.timeout(20_000)
  })
  const body = (await response.json().catch(() => null)) as {
    url?: unknown
    error?: unknown
  } | null
  if (!response.ok || typeof body?.url !== 'string')
    throw new Error(
      typeof body?.error === 'string'
        ? body.error
        : 'Slack installation is unavailable.'
    )
  const url = new URL(body.url)
  if (
    url.protocol !== 'https:' ||
    url.hostname !== 'slack.com' ||
    url.pathname !== '/oauth/v2/authorize'
  )
    throw new Error('Slack returned an unsafe authorization address.')
  await shell.openExternal(url.toString())
  return { opened: true }
}

export async function clearCollaborationIdentity(): Promise<void> {
  for (const timer of draftRenewals.values()) clearInterval(timer)
  draftRenewals.clear()
  drafts.clear()
  boundIdentity = ''
  rmSync(bindingPath(), { force: true })
  try {
    await collaborationCore('collaboration_bind_identity', {
      bind_token: collaborationIdentityBindToken,
      account_id: '',
      device_id: ''
    })
  } catch {
    /* core may be stopping */
  }
}

export function startCollaborationReconciliation(): void {
  if (periodicReconcile) return
  void reconcileCollaboration().catch(() => undefined)
  periodicReconcile = setInterval(() => {
    void reconcileCollaboration().catch(() => undefined)
  }, 30_000)
}

export function stopCollaborationReconciliation(): void {
  if (periodicReconcile) clearInterval(periodicReconcile)
  periodicReconcile = null
}

export async function beginSlackSenderLink(
  installationId: string
): Promise<Record<string, unknown>> {
  return collaborationBackend('begin_slack_link', {
    installation_id: installationId
  })
}

export async function selectSlackChannel(
  organizationId: string,
  installationId: string,
  channelId: string
): Promise<Record<string, unknown>> {
  return collaborationBackend('select_slack_channel', {
    org_id: organizationId,
    installation_id: installationId,
    channel_id: channelId
  })
}

export async function setRoutineDelivery(
  routineId: string,
  sessionId: string | null,
  audienceRevision?: number
): Promise<unknown> {
  const account = await identity()
  if (sessionId === null)
    return collaborationCore('collaboration_set_routine_delivery', {
      routine_id: routineId,
      shared_delivery: null
    })
  if (!Number.isSafeInteger(audienceRevision) || Number(audienceRevision) < 1)
    throw new Error(
      'Refresh the shared conversation before choosing it for routine delivery.'
    )
  return collaborationCore('collaboration_set_routine_delivery', {
    routine_id: routineId,
    shared_delivery: {
      session_id: sessionId,
      audience_revision: Number(audienceRevision),
      creator_account_id: account.id
    }
  })
}

export async function listLocalArchives(): Promise<unknown[]> {
  return ((await localStatus()).archives || []) as unknown[]
}
