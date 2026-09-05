// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AccountOnboarding from './AccountOnboarding'

vi.mock('../components/CollieFace', () => ({ default: () => null }))

let root: Root
let container: HTMLDivElement
const signedOut = { signedIn: false, email: null, expiresAt: null, access: 'unknown' as const }
const signedIn = { ...signedOut, signedIn: true, email: 'test@example.com' }
const getState = vi.fn()
const startSignIn = vi.fn()

beforeEach(() => {
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  localStorage.clear()
  getState.mockReset().mockResolvedValue(signedOut)
  startSignIn.mockReset().mockResolvedValue(signedIn)
  Object.defineProperty(window, 'account', { configurable: true, value: { getState, startSignIn } })
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})

async function render(): Promise<void> {
  await act(async () => root.render(<AccountOnboarding><div>Choose your model</div></AccountOnboarding>))
}

async function click(label: string): Promise<void> {
  const button = [...container.querySelectorAll('button')].find((item) => item.textContent === label)
  expect(button).toBeDefined()
  await act(async () => button!.click())
}

describe('optional account onboarding', () => {
  it('puts the account choice before models and remembers guest use across mounts', async () => {
    await render()
    expect(container.textContent).toContain('Sign in or create an account')
    expect(container.textContent).not.toContain('Choose your model')
    await click('Continue as a guest')
    expect(container.textContent).toContain('Choose your model')
    expect(startSignIn).not.toHaveBeenCalled()
    await act(async () => root.unmount())
    root = createRoot(container)
    getState.mockClear()
    await render()
    expect(container.textContent).toContain('Choose your model')
    expect(getState).not.toHaveBeenCalled()
  })

  it('skips the account choice for an existing session', async () => {
    getState.mockResolvedValue(signedIn)
    await render()
    expect(container.textContent).toContain('Choose your model')
    expect(startSignIn).not.toHaveBeenCalled()
  })

  it('uses browser sign-in and advances only when signed in', async () => {
    startSignIn.mockResolvedValueOnce(signedOut)
    await render()
    await click('Sign in or create an account')
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("Sign-in didn't finish")
    expect(container.textContent).not.toContain('Choose your model')
    await click('Sign in or create an account')
    expect(container.textContent).toContain('Choose your model')
    expect(startSignIn).toHaveBeenCalledTimes(2)
  })

  it('allows guest use after account lookup and sign-in errors', async () => {
    getState.mockRejectedValue(new Error('offline'))
    startSignIn.mockRejectedValue(new Error('cancelled'))
    await render()
    await click('Sign in or create an account')
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
    await click('Continue as a guest')
    expect(container.textContent).toContain('Choose your model')
  })

  it('allows guest use while sign-in is pending and ignores its late result', async () => {
    let finish!: (state: typeof signedIn) => void
    startSignIn.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    await render()
    await click('Sign in or create an account')
    expect(container.querySelector('button')?.disabled).toBe(true)
    await click('Continue as a guest')
    await act(async () => finish(signedIn))
    expect(container.textContent).toBe('Choose your model')
  })

  it('does not block guests when account lookup never finishes', async () => {
    getState.mockReturnValue(new Promise(() => {}))
    await render()
    await click('Continue as a guest')
    expect(container.textContent).toContain('Choose your model')
  })

  it('continues when local preference storage is unavailable', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    await render()
    await click('Continue as a guest')
    expect(container.textContent).toContain('Choose your model')
  })
})
