// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import SharedWorkspace from './SharedWorkspace'
import type {
  CollaborationBootstrap,
  CollaborationFacade
} from '../lib/collaboration'

const roots: Root[] = []
const session = {
  id: 'session-1',
  title: 'Project room',
  organization_id: 'org-1',
  status: 'active',
  revision: 2,
  members: [{ id: 'member-1', display_name: 'Owner' }]
}
const organization = {
  id: 'org-1',
  name: 'Acme',
  members: [
    { id: 'member-1', display_name: 'Owner' },
    { id: 'member-2', display_name: 'Riley' }
  ]
}

function bootstrap(
  overrides: Partial<CollaborationBootstrap> = {}
): CollaborationBootstrap {
  return {
    account: { id: 'member-1', display_name: 'Owner' },
    organizations: [organization],
    sessions: [session],
    invites: [],
    ...overrides
  }
}

function facade(
  initial: CollaborationBootstrap = bootstrap()
): CollaborationFacade {
  return {
    bootstrap: vi.fn().mockResolvedValue(initial),
    createOrganization: vi.fn().mockResolvedValue({}),
    createSession: vi.fn().mockResolvedValue({ session }),
    invite: vi.fn().mockResolvedValue({}),
    acceptInvite: vi.fn().mockResolvedValue({}),
    acceptOrganizationInvite: vi.fn().mockResolvedValue({}),
    openSession: vi.fn().mockResolvedValue({ events: [] }),
    sendMessage: vi.fn().mockResolvedValue({ events: [] }),
    runPrivate: vi
      .fn()
      .mockResolvedValue({
        draftId: 'draft-1',
        sessionId: session.id,
        content: 'Private answer',
        state: 'ready'
      }),
    publishDraft: vi.fn().mockResolvedValue({}),
    listPrivateDrafts: vi.fn().mockResolvedValue([]),
    requestArchive: vi
      .fn()
      .mockResolvedValue({
        archive: {
          state: 'archive_pending',
          pending_participants: [{ id: 'member-2', display_name: 'Riley' }]
        }
      }),
    archiveStatus: vi.fn().mockResolvedValue({ state: 'archive_pending' }),
    saveArchive: vi
      .fn()
      .mockResolvedValue({
        state: 'archived_local',
        local_path: 'C:\\archives\\room'
      }),
    exportArchive: vi.fn().mockResolvedValue({ path: 'room.zip' }),
    importArchive: vi.fn().mockResolvedValue({}),
    listLocalArchives: vi.fn().mockResolvedValue([]),
    continueSession: vi
      .fn()
      .mockResolvedValue({
        session: { ...session, id: 'session-2', status: 'active' }
      }),
    installSlack: vi.fn().mockResolvedValue({}),
    linkSlackSender: vi.fn().mockResolvedValue({}),
    slackSettings: vi.fn().mockResolvedValue({ installations: [] }),
    selectSlackChannel: vi.fn().mockResolvedValue({}),
    editMessage: vi.fn().mockResolvedValue({}),
    deleteMessage: vi.fn().mockResolvedValue({}),
    inviteOrganization: vi.fn().mockResolvedValue({}),
    uploadFile: vi.fn().mockResolvedValue({}),
    listFiles: vi.fn().mockResolvedValue({ files: [] }),
    downloadFile: vi.fn().mockResolvedValue({}),
    stopSessionRun: vi.fn().mockResolvedValue({}),
    notifications: vi
      .fn()
      .mockResolvedValue({ notifications: [], next_cursor: 0 }),
    ackNotifications: vi.fn().mockResolvedValue({}),
    setRoutineDelivery: vi.fn().mockResolvedValue({}),
    resolveReconciliation: vi.fn().mockResolvedValue({}),
    revokeMember: vi.fn().mockResolvedValue({})
  }
}

function render(api: CollaborationFacade): HTMLElement {
  Object.defineProperty(window, 'account', {
    configurable: true,
    value: { collaboration: api }
  })
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  roots.push(root)
  act(() => root.render(<SharedWorkspace onBack={() => undefined} />))
  return container
}

async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
}

beforeAll(() => {
  ;(
    globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
  ).IS_REACT_ACT_ENVIRONMENT = true
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => {
  for (const root of roots.splice(0)) act(() => root.unmount())
  document.body.replaceChildren()
})

describe('SharedWorkspace collaboration states', () => {
  it('keeps a policy requiring consent from granting access', async () => {
    const api = facade(
      bootstrap({
        invites: [
          {
            id: 'invite-1',
            kind: 'session',
            status: 'pending',
            inviter_name: 'Owner',
            audience_policy: {
              summary: 'Read existing history',
              includes_existing_history: true
            },
            audience_policy_version: 3
          }
        ]
      })
    )
    const container = render(api)
    await settle()
    const accept = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Accept')
    ) as HTMLButtonElement
    expect(accept.disabled).toBe(true)
    const checkbox = container.querySelector(
      'input[type="checkbox"]'
    ) as HTMLInputElement
    act(() => {
      checkbox.click()
    })
    expect(accept.disabled).toBe(false)
    act(() => {
      accept.click()
    })
    await settle()
    expect(api.acceptInvite).toHaveBeenCalledWith('invite-1', true, 3)
  })

  it('shows archive waiting participants and disables the composer', async () => {
    const archived = { ...session, status: 'archive_pending' }
    const api = facade(bootstrap({ sessions: [archived] }))
    vi.mocked(api.openSession).mockResolvedValue({
      events: [],
      archive: {
        state: 'archive_pending',
        pending_participants: [{ id: 'member-2', display_name: 'Riley' }]
      }
    })
    const container = render(api)
    await settle()
    const room = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Project room')
    ) as HTMLButtonElement
    act(() => {
      room.click()
    })
    await settle()
    expect(container.textContent).toContain('Waiting for Riley')
    expect(
      (
        container.querySelector(
          'textarea[aria-label="Shared message"]'
        ) as HTMLTextAreaElement
      ).disabled
    ).toBe(true)
  })

  it.each(['active', 'closing'])(
    'publishes an accepted private draft through publishDraft while %s',
    async (status) => {
      const api = facade(bootstrap({ sessions: [{ ...session, status }] }))
      vi.mocked(api.listPrivateDrafts).mockResolvedValue([
        {
          draftId: 'draft-1',
          runId: 'run-1',
          sessionId: session.id,
          content: 'Private answer',
          state: 'ready'
        }
      ])
      const container = render(api)
      await settle()
      const room = Array.from(container.querySelectorAll('button')).find(
        (button) => button.textContent?.includes('Project room')
      ) as HTMLButtonElement
      act(() => {
        room.click()
      })
      await settle()
      const review = Array.from(container.querySelectorAll('button')).find(
        (button) => button.textContent?.includes('Review saved result')
      ) as HTMLButtonElement
      act(() => {
        review.click()
      })
      await settle()
      const publish = Array.from(container.querySelectorAll('button')).find(
        (button) => button.textContent?.includes('Publish reviewed result')
      ) as HTMLButtonElement
      act(() => {
        publish.click()
      })
      await settle()
      expect(api.publishDraft).toHaveBeenCalledWith('draft-1', 'Private answer')
      expect(api.sendMessage).not.toHaveBeenCalled()
    }
  )

  it('keeps the composer draft when local save fails', async () => {
    const api = facade()
    vi.mocked(api.sendMessage).mockRejectedValue(new Error('offline'))
    const container = render(api)
    await settle()
    const room = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Project room')
    ) as HTMLButtonElement
    act(() => {
      room.click()
    })
    await settle()
    const composer = container.querySelector(
      'textarea[aria-label="Shared message"]'
    ) as HTMLTextAreaElement
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        'value'
      )?.set
      setter?.call(composer, 'Keep this draft')
      composer.dispatchEvent(new Event('input', { bubbles: true }))
    })
    const send = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.includes('Send')
    ) as HTMLButtonElement
    act(() => {
      send.click()
    })
    await settle()
    expect(api.sendMessage).toHaveBeenCalled()
    expect(container.textContent).toContain('offline')
    expect(composer.value).toBe('Keep this draft')
  })
})
