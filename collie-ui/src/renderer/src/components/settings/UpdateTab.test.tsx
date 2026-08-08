// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import UpdateTab from './UpdateTab'

type RendererUpdateStatus = Awaited<ReturnType<Window['collie']['updateStatus']>>

const FAILED_STATUS: RendererUpdateStatus = {
  phase: 'current',
  currentVersion: '0.1.0-alpha.5',
  failedUpdate: { pendingVersion: '0.1.0-alpha.5', previousVersion: '0.1.0-alpha.4' }
}

function mockBridge(
  status: RendererUpdateStatus,
  dismiss: () => Promise<RendererUpdateStatus> = vi.fn(async () => ({ ...status, failedUpdate: null }))
): Window['collie'] {
  return {
    updateStatus: vi.fn(async () => status),
    onUpdateStatus: vi.fn(() => () => undefined),
    dismissUpdateFailure: dismiss,
    checkForUpdate: vi.fn(async () => status),
    downloadUpdate: vi.fn(async () => status),
    restartAndInstallUpdate: vi.fn(async () => ({ installed: false, blockedBy: [] })),
    updateActiveWork: vi.fn(async () => true),
    coreState: vi.fn(async () => ({ state: 'running', port: 3818, token: '', error: '' }))
  } as unknown as Window['collie']
}

let container: HTMLDivElement
let root: Root | null = null

beforeEach(() => {
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  container = document.createElement('div')
  document.body.appendChild(container)
})

afterEach(() => {
  act(() => {
    root?.unmount()
  })
  root = null
  container.remove()
})

async function renderTab(): Promise<void> {
  await act(async () => {
    root = createRoot(container)
    root.render(<UpdateTab />)
  })
}

describe('UpdateTab failed-update banner', () => {
  it('renders the failure banner from failedUpdate even when phase is current', async () => {
    window.collie = mockBridge(FAILED_STATUS)
    await renderTab()

    expect(container.textContent).toContain("didn't start properly")
    expect(container.textContent).toContain('Your chats and settings are safe')
    const button = [...container.querySelectorAll('button')].find((b) =>
      b.textContent?.includes('Keep this version')
    )
    expect(button).toBeTruthy()
  })

  it('hides the banner when failedUpdate is null', async () => {
    window.collie = mockBridge({ phase: 'current', currentVersion: '0.1.0-alpha.5', failedUpdate: null })
    await renderTab()

    expect(container.textContent).not.toContain("didn't start properly")
  })

  it('dismisses the notice through the bridge and clears the banner', async () => {
    const dismiss = vi.fn(async (): Promise<RendererUpdateStatus> => ({
      phase: 'current',
      currentVersion: '0.1.0-alpha.5',
      failedUpdate: null
    }))
    window.collie = mockBridge(FAILED_STATUS, dismiss)
    await renderTab()

    const button = [...container.querySelectorAll('button')].find((b) =>
      b.textContent?.includes('Keep this version')
    )
    await act(async () => {
      button!.click()
    })

    expect(dismiss).toHaveBeenCalledOnce()
    expect(container.textContent).not.toContain("didn't start properly")
  })
})
