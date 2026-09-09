import { mkdtempSync, rmSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  account: 'user-a',
  userData: '',
  calls: [] as Array<{ type: string; payload: Record<string, unknown> }>,
  replies: [] as Array<Response>,
  archives: [] as Array<Record<string, unknown>>
}))

vi.mock('electron', () => ({
  app: { getPath: () => state.userData },
  safeStorage: {
    isEncryptionAvailable: () => true,
    encryptString: (value: string) => Buffer.from(value),
    decryptString: (value: Buffer) => value.toString('utf8')
  },
  dialog: {},
  shell: { openExternal: vi.fn() },
  BrowserWindow: class {}
}))
vi.mock('../shared/account-config', () => ({
  SUPABASE_URL: 'https://test.supabase.co',
  SUPABASE_ANON_KEY: 'anon'
}))
vi.mock('./python', () => ({
  collaborationIdentityBindToken: 'main-only-capability'
}))
vi.mock('./account-auth', () => ({
  getVerifiedAccount: () =>
    Promise.resolve({
      id: state.account,
      email: null,
      accessToken: `token-${state.account}`
    })
}))
vi.mock('./core-client', () => ({
  commandWithCore: (type: string, payload: Record<string, unknown>) => {
    state.calls.push({ type, payload })
    if (type === 'collaboration_status')
      return Promise.resolve({
        available: true,
        cursor: 0,
        pending: [],
        archives: state.archives
      })
    if (type === 'collaboration_list_messages')
      return Promise.resolve({
        messages: [{ message_id: 'm1', content: 'hello' }]
      })
    if (type === 'collaboration_run_shared')
      return Promise.resolve({
        run_id: 'run-1',
        content: 'private',
        state: 'complete'
      })
    return Promise.resolve({})
  }
}))

const originalFetch = global.fetch

beforeEach(() => {
  state.account = 'user-a'
  state.calls = []
  state.replies = []
  state.archives = []
  state.userData = mkdtempSync(join(tmpdir(), 'collie-collaboration-'))
  global.fetch = vi.fn(async (url: string | URL, init?: RequestInit) => {
    if (String(url).endsWith('/auth/v1/user'))
      return new Response(JSON.stringify({ id: state.account }), {
        status: 200
      })
    const request = init?.body
      ? (JSON.parse(String(init.body)) as { p_command?: string })
      : {}
    if (request.p_command === 'enroll_device')
      return new Response(JSON.stringify({ ok: true }), { status: 200 })
    return (
      state.replies.shift() ??
      new Response(JSON.stringify({ ok: true }), { status: 200 })
    )
  }) as typeof fetch
})

afterEach(() => {
  global.fetch = originalFetch
  rmSync(state.userData, { recursive: true, force: true })
})

describe('protected collaboration orchestration', () => {
  it('normalizes bootstrap IDs and puts the main-only capability on every core call', async () => {
    state.replies.push(
      new Response(
        JSON.stringify({
          organizations: [{ org_id: 'o1' }],
          sessions: [{ session_id: 's1', org_id: 'o1' }],
          invites: [{ invite_id: 'i1', session_id: 's1' }]
        }),
        { status: 200 }
      )
    )
    const { bootstrapCollaboration } = await import('./collaboration')
    const result = (await bootstrapCollaboration()) as {
      organizations: Array<{ id: string }>
      sessions: Array<{ id: string; organization_id: string }>
      invites: Array<{ id: string }>
    }
    expect(result.organizations[0].id).toBe('o1')
    expect(result.sessions[0]).toMatchObject({
      id: 's1',
      organization_id: 'o1'
    })
    expect(result.invites[0].id).toBe('i1')
    expect(
      state.calls.every(
        (call) => call.payload.identity_token === 'main-only-capability'
      )
    ).toBe(true)
  })

  it('reuses the same protected device key across enrollment cycles', async () => {
    state.replies.push(
      new Response(
        JSON.stringify({ organizations: [], sessions: [], invites: [] }),
        { status: 200 }
      )
    )
    state.replies.push(
      new Response(
        JSON.stringify({ organizations: [], sessions: [], invites: [] }),
        { status: 200 }
      )
    )
    const { bootstrapCollaboration, clearCollaborationIdentity } = await import(
      './collaboration'
    )
    await bootstrapCollaboration()
    const firstEnroll = (global.fetch as ReturnType<typeof vi.fn>).mock.calls
      .map((call) =>
        call[1]?.body
          ? (JSON.parse(String(call[1].body)) as Record<string, unknown>)
          : {}
      )
      .find((body) => body.p_command === 'enroll_device') as {
      p_payload: { device_id: string; public_key: string }
    }
    await clearCollaborationIdentity()
    await bootstrapCollaboration()
    const enrollments = (global.fetch as ReturnType<typeof vi.fn>).mock.calls
      .map((call) =>
        call[1]?.body
          ? (JSON.parse(String(call[1].body)) as Record<string, unknown>)
          : {}
      )
      .filter((body) => body.p_command === 'enroll_device') as Array<{
      p_payload: { device_id: string; public_key: string }
    }>
    expect(enrollments).toHaveLength(2)
    expect(enrollments[1].p_payload).toMatchObject(firstEnroll.p_payload)
    expect(firstEnroll.p_payload.public_key).toMatch(/^[A-Za-z0-9_-]{43}$/)
  })

  it('applies the backend next_cursor and returns materialized local events', async () => {
    state.replies.push(
      new Response(
        JSON.stringify({
          events: [{ event_id: 'e1' }],
          next_cursor: 1,
          high_water: 9
        }),
        { status: 200 }
      )
    )
    state.replies.push(
      new Response(
        JSON.stringify({ events: [], next_cursor: 1, high_water: 9 }),
        { status: 200 }
      )
    )
    const { openSharedSession } = await import('./collaboration')
    const result = await openSharedSession('s1')
    const apply = state.calls.find(
      (call) => call.type === 'collaboration_apply_page'
    )
    expect(apply?.payload).toMatchObject({ next_cursor: 1 })
    expect(apply?.payload).not.toHaveProperty('high_water')
    expect(result.events).toEqual([{ message_id: 'm1', content: 'hello' }])
  })

  it('queues before the network and reports a durable pending event on failure', async () => {
    state.replies.push(new Response('offline', { status: 503 }))
    const { sendSharedMessage } = await import('./collaboration')
    const mentioned = '11111111-1111-4111-8111-111111111111'
    const result = await sendSharedMessage('s1', 'keep me', undefined, false, [
      mentioned
    ])
    const queued = state.calls.find(
      (call) => call.type === 'collaboration_queue_event'
    )
    expect(queued).toBeDefined()
    expect(result).toMatchObject({ pending: true })
    expect((result.event as Record<string, unknown>).content).toBe('keep me')
    expect(
      (result.event as Record<string, unknown>).mentioned_user_ids
    ).toEqual([mentioned])
  })

  it('reuses a verified archive digest before sending its authenticated receipt', async () => {
    state.archives = [{ digest: 'abc', path: 'verified/local/archive' }]
    state.replies.push(
      new Response(
        JSON.stringify({
          manifest_json: '{}',
          digest: 'abc',
          archive_revision: 2,
          final_seq: 7,
          byte_length: 123
        }),
        { status: 200 }
      )
    )
    state.replies.push(
      new Response(JSON.stringify({ ok: true }), { status: 200 })
    )
    const { saveVerifiedArchive } = await import('./collaboration')
    const result = await saveVerifiedArchive('s1')
    expect(result).toMatchObject({
      state: 'saved_locally',
      local_path: 'verified/local/archive',
      digest: 'abc'
    })
    expect(
      state.calls.some((call) => call.type === 'collaboration_write_archive')
    ).toBe(false)
    const receipt = (global.fetch as ReturnType<typeof vi.fn>).mock.calls
      .map((call) =>
        call[1]?.body
          ? (JSON.parse(String(call[1].body)) as Record<string, unknown>)
          : {}
      )
      .find((body) => body.p_command === 'ack_archive') as {
      p_payload: Record<string, unknown>
    }
    expect(receipt.p_payload).toMatchObject({
      session_id: 's1',
      archive_revision: 2,
      digest: 'abc',
      final_seq: 7,
      byte_length: 123
    })
  })

  it('materializes an edited claim through its cutoff before private execution', async () => {
    const claimEvents = [
      {
        event_id: 'e1',
        session_id: 's1',
        seq: 1,
        kind: 'message',
        message_id: 'm1',
        author_id: 'user-a',
        role: 'user',
        content: 'old',
        revision: 1,
        created_at: '2026-09-09T00:00:00Z'
      },
      {
        event_id: 'e2',
        session_id: 's1',
        seq: 2,
        kind: 'edit',
        message_id: 'm1',
        author_id: 'user-a',
        role: 'user',
        content: 'new',
        revision: 2,
        created_at: '2026-09-09T00:01:00Z'
      }
    ]
    state.replies.push(
      new Response(JSON.stringify({ run_id: 'run-1' }), { status: 200 })
    )
    state.replies.push(
      new Response(
        JSON.stringify({ events: [], next_cursor: 0, high_water: 0 }),
        { status: 200 }
      )
    )
    state.replies.push(
      new Response(JSON.stringify({ files: [] }), { status: 200 })
    )
    state.replies.push(
      new Response(
        JSON.stringify({
          run_id: 'run-1',
          session_id: 's1',
          requester_id: 'user-a',
          credential_owner_id: 'user-a',
          executor_device_id: 'device',
          audience_revision: 1,
          context_cutoff: 2,
          lease_token: 'lease',
          lease_expires_at: '2099-01-01T00:00:00Z',
          events: claimEvents
        }),
        { status: 200 }
      )
    )
    const { clearCollaborationIdentity, runSharedPrivately } = await import(
      './collaboration'
    )
    await runSharedPrivately('s1', 'new')
    const appliedIndex = state.calls.findIndex(
      (call) =>
        call.type === 'collaboration_apply_page' &&
        call.payload.next_cursor === 2
    )
    const runIndex = state.calls.findIndex(
      (call) => call.type === 'collaboration_run_shared'
    )
    expect(appliedIndex).toBeGreaterThanOrEqual(0)
    expect(appliedIndex).toBeLessThan(runIndex)
    expect(state.calls[runIndex].payload.published_history).toEqual([])
    await clearCollaborationIdentity()
  })
})
