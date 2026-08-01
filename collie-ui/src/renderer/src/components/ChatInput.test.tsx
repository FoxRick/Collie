import { beforeEach, describe, expect, it, vi } from 'vitest'

const hooks = vi.hoisted(() => {
  type Cleanup = void | (() => void)
  type EffectRecord = { deps: readonly unknown[] | undefined; cleanup: Cleanup }
  type CallbackRecord = { deps: readonly unknown[]; callback: unknown }

  let stateValues: unknown[] = []
  let stateSetters: Array<(value: unknown) => void> = []
  let stateCalls: number[] = []
  let refs: Array<{ current: unknown }> = []
  let callbacks: CallbackRecord[] = []
  let effects: Array<EffectRecord | undefined> = []
  let stateIndex = 0
  let refIndex = 0
  let callbackIndex = 0
  let effectIndex = 0

  const sameDeps = (
    left: readonly unknown[] | undefined,
    right: readonly unknown[] | undefined
  ): boolean =>
    left !== undefined &&
    right !== undefined &&
    left.length === right.length &&
    left.every((value, index) => Object.is(value, right[index]))

  return {
    beginRender(): void {
      stateIndex = 0
      refIndex = 0
      callbackIndex = 0
      effectIndex = 0
    },
    reset(): void {
      for (const effect of effects) effect?.cleanup?.()
      stateValues = []
      stateSetters = []
      stateCalls = []
      refs = []
      callbacks = []
      effects = []
      this.beginRender()
    },
    unmount(): void {
      for (const effect of effects) effect?.cleanup?.()
      effects = []
    },
    useState<T>(initial: T | (() => T)): [T, (value: T | ((current: T) => T)) => void] {
      const index = stateIndex++
      if (!(index in stateValues)) {
        stateValues[index] = typeof initial === 'function' ? (initial as () => T)() : initial
        stateCalls[index] = 0
        stateSetters[index] = (next: unknown): void => {
          stateCalls[index] += 1
          stateValues[index] =
            typeof next === 'function'
              ? (next as (current: T) => T)(stateValues[index] as T)
              : next
        }
      }
      return [
        stateValues[index] as T,
        stateSetters[index] as (value: T | ((current: T) => T)) => void
      ]
    },
    useRef<T>(initial: T): { current: T } {
      const index = refIndex++
      if (!refs[index]) refs[index] = { current: initial }
      return refs[index] as { current: T }
    },
    useCallback<T>(callback: T, deps: readonly unknown[]): T {
      const index = callbackIndex++
      const previous = callbacks[index]
      if (previous && sameDeps(previous.deps, deps)) return previous.callback as T
      callbacks[index] = { callback, deps }
      return callback
    },
    useEffect(effect: () => Cleanup, deps?: readonly unknown[]): void {
      const index = effectIndex++
      const previous = effects[index]
      if (previous && sameDeps(previous.deps, deps)) return
      previous?.cleanup?.()
      effects[index] = { deps, cleanup: effect() }
    },
    stateValue<T>(index: number): T {
      return stateValues[index] as T
    },
    stateCallCount(index: number): number {
      return stateCalls[index] || 0
    }
  }
})

vi.mock('react', () => ({
  useCallback: hooks.useCallback,
  useEffect: hooks.useEffect,
  useRef: hooks.useRef,
  useState: hooks.useState
}))

vi.mock('lucide-react', () => ({
  ArrowUp: () => null,
  Camera: () => null,
  Check: () => null,
  ChevronDown: () => null,
  FileText: () => null,
  Folder: () => null,
  FolderPlus: () => null,
  Image: () => null,
  Lightbulb: () => null,
  LoaderCircle: () => null,
  Mic: () => null,
  Paperclip: () => null,
  Plus: () => null,
  Square: () => null,
  X: () => null
}))

vi.mock('../lib/i18n', () => ({ useT: () => (key: string) => key }))
vi.mock('../lib/audio', () => ({
  MICROPHONE_STORAGE_KEY: 'test-microphone',
  startLocalDictation: vi.fn()
}))

import ChatInput from './ChatInput'

describe('ChatInput compose command listener', () => {
  beforeEach(() => {
    hooks.reset()
    vi.unstubAllGlobals()
  })

  it('registers once across repeated renders, inserts once, and cleans up on unmount', () => {
    const eventTarget = Object.assign(new EventTarget(), {
      setTimeout: vi.fn(() => 1),
      clearTimeout: vi.fn()
    })
    const addListener = vi.spyOn(eventTarget, 'addEventListener')
    const removeListener = vi.spyOn(eventTarget, 'removeEventListener')
    const initialTypingChange = vi.fn()
    const latestTypingChange = vi.fn()
    vi.stubGlobal('window', eventTarget)
    vi.stubGlobal(
      'requestAnimationFrame',
      vi.fn((callback: FrameRequestCallback) => {
        callback(0)
        return 1
      })
    )

    const render = (busy: boolean, onTypingChange: (typing: boolean) => void): void => {
      hooks.beginRender()
      ChatInput({
        onSend: vi.fn(),
        onStop: vi.fn(),
        busy,
        mode: 'plan',
        onModeChange: vi.fn(),
        onProviderChange: vi.fn(),
        onProjectChange: vi.fn(),
        onAddProject: vi.fn(),
        onAddModel: vi.fn(),
        onTypingChange,
        onTranscribe: vi.fn().mockResolvedValue('')
      })
    }

    render(false, initialTypingChange)
    render(true, latestTypingChange)
    render(false, latestTypingChange)

    const composeAdds = addListener.mock.calls.filter(
      ([type]) => type === 'collie:compose-command'
    )
    expect(composeAdds).toHaveLength(1)

    const composeEvent = new Event('collie:compose-command')
    Object.defineProperty(composeEvent, 'detail', { value: '/agent researcher ' })
    eventTarget.dispatchEvent(composeEvent)

    expect(hooks.stateValue<string>(0)).toBe('/agent researcher ')
    expect(hooks.stateCallCount(0)).toBe(1)
    expect(initialTypingChange).not.toHaveBeenCalled()
    expect(latestTypingChange).toHaveBeenCalledTimes(1)
    expect(latestTypingChange).toHaveBeenCalledWith(true)

    render(false, latestTypingChange)
    expect(
      addListener.mock.calls.filter(([type]) => type === 'collie:compose-command')
    ).toHaveLength(1)

    hooks.unmount()
    const composeRemoves = removeListener.mock.calls.filter(
      ([type]) => type === 'collie:compose-command'
    )
    expect(composeRemoves).toHaveLength(1)
    expect(composeRemoves[0]?.[1]).toBe(composeAdds[0]?.[1])

    eventTarget.dispatchEvent(composeEvent)
    expect(hooks.stateCallCount(0)).toBe(1)
  })
})
