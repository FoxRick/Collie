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
    },
    setState<T>(index: number, value: T): void {
      stateValues[index] = value
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
  ShieldCheck: () => null,
  Square: () => null,
  X: () => null
}))

vi.mock('../lib/i18n', () => ({ useT: () => (key: string) => key }))
vi.mock('../lib/audio', () => ({
  MICROPHONE_STORAGE_KEY: 'test-microphone',
  startLocalDictation: vi.fn()
}))

import ChatInput, {
  buildImagePreviews,
  dataUrlDecodedBytes,
  fitWithinMaxEdge,
  rasterDimensionsFromDataUrl,
  withImagePreview
} from './ChatInput'
import type { AttachmentDraft } from '../lib/ipc'

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

function submissionProps(
  onSend: (text: string, attachments: AttachmentDraft[]) => Promise<boolean>
) {
  return {
    onSend,
    onStop: vi.fn(),
    busy: false,
    mode: 'execute' as const,
    onModeChange: vi.fn(),
    onProviderChange: vi.fn(),
    onProjectChange: vi.fn(),
    onAddProject: vi.fn(),
    onAddModel: vi.fn(),
    approvalPreset: 'ask' as const,
    onApprovalPresetChange: vi.fn(),
    fileAccessScope: { mode: 'selected_folder' as const },
    onFileAccessScopeChange: vi.fn(),
    onChooseFileAccessFolders: vi.fn(),
    onTypingChange: vi.fn(),
    onTranscribe: vi.fn().mockResolvedValue('')
  }
}

function findElementByAriaLabel(
  value: unknown,
  ariaLabel: string
): { props: Record<string, unknown> } {
  if (Array.isArray(value)) {
    for (const item of value) {
      try {
        return findElementByAriaLabel(item, ariaLabel)
      } catch {
        // Keep looking through sibling elements.
      }
    }
  } else if (value && typeof value === 'object') {
    const props = (value as { props?: Record<string, unknown> }).props
    if (props?.['aria-label'] === ariaLabel) return { props }
    if (props && 'children' in props) return findElementByAriaLabel(props.children, ariaLabel)
  }
  throw new Error(`Element with aria-label ${ariaLabel} was not found.`)
}

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
        approvalPreset: 'ask',
        onApprovalPresetChange: vi.fn(),
        fileAccessScope: { mode: 'selected_folder' },
        onFileAccessScopeChange: vi.fn(),
        onChooseFileAccessFolders: vi.fn(),
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

  it('keeps upload, removes screenshot capture, and renders image thumbnails', () => {
    const eventTarget = Object.assign(new EventTarget(), {
      setTimeout: vi.fn(() => 1),
      clearTimeout: vi.fn()
    })
    vi.stubGlobal('window', eventTarget)
    vi.stubGlobal('document', new EventTarget())
    const props = {
      onSend: vi.fn(),
      onStop: vi.fn(),
      busy: false,
      mode: 'plan' as const,
      onModeChange: vi.fn(),
      onProviderChange: vi.fn(),
      onProjectChange: vi.fn(),
      onAddProject: vi.fn(),
      onAddModel: vi.fn(),
      approvalPreset: 'allow' as const,
      onApprovalPresetChange: vi.fn(),
      fileAccessScope: { mode: 'full_file_access' as const },
      onFileAccessScopeChange: vi.fn(),
      onChooseFileAccessFolders: vi.fn(),
      onTranscribe: vi.fn().mockResolvedValue('')
    }

    hooks.beginRender()
    let output = JSON.stringify(ChatInput(props))
    expect(output).toContain('Attach files')
    expect(output).not.toContain('Take screenshot')
    expect(output).toContain('Approve for me')
    expect(output).toContain('All local files')
    expect(output.match(/\"aria-label\":\"Files\"/g)).toHaveLength(1)
    expect(output).not.toContain('"role":"menu"')

    hooks.setState(3, 'approvals')
    hooks.beginRender()
    output = JSON.stringify(ChatInput(props))
    expect(output).toContain('bounded file edits and other eligible local changes that still need approval')
    expect(output).toContain('Eligible ordinary local actions can continue')
    expect(output).toContain('Consequential actions still ask')

    hooks.setState(3, 'files')
    hooks.beginRender()
    output = JSON.stringify(ChatInput(props))
    expect(output).toContain('Project folder')
    expect(output).toContain('Access for this chat')
    expect(output).toContain('Project folder only')
    expect(output).toContain('Collie Workspace')
    expect(output).toContain('Local text files anywhere on this computer')
    // Selecting a Files scope is consent for the content inside it —
    // including that a read may send the file's text to the model provider.
    // The disclosure must be visible at grant time, not only in a card that
    // in-scope reads no longer raise.
    expect(output).toContain('may be sent to')

    hooks.setState(1, [{
      name: 'photo.png',
      mime: 'image/png',
      size: 100,
      data_url: 'data:image/png;base64,original',
      preview_data_url: 'data:image/png;base64,thumbnail'
    }])
    hooks.beginRender()
    output = JSON.stringify(ChatInput(props))
    expect(output).toContain('attachment-thumbnail')
    expect(output).toContain('data:image/png;base64,thumbnail')
  })

  it('keeps the draft until the engine accepts the submission', async () => {
    const eventTarget = Object.assign(new EventTarget(), {
      setTimeout: vi.fn(() => 1),
      clearTimeout: vi.fn()
    })
    vi.stubGlobal('window', eventTarget)
    vi.stubGlobal('document', new EventTarget())
    const attachment: AttachmentDraft = {
      name: 'notes.txt',
      mime: 'text/plain',
      size: 5,
      data_url: 'data:text/plain;base64,aGVsbG8='
    }
    const onSend = vi.fn().mockResolvedValue(false)
    const onTypingChange = vi.fn()
    const props = {
      onSend,
      onStop: vi.fn(),
      busy: false,
      mode: 'execute' as const,
      onModeChange: vi.fn(),
      onProviderChange: vi.fn(),
      onProjectChange: vi.fn(),
      onAddProject: vi.fn(),
      onAddModel: vi.fn(),
      approvalPreset: 'ask' as const,
      onApprovalPresetChange: vi.fn(),
      fileAccessScope: { mode: 'selected_folder' as const },
      onFileAccessScopeChange: vi.fn(),
      onChooseFileAccessFolders: vi.fn(),
      onTypingChange,
      onTranscribe: vi.fn().mockResolvedValue('')
    }

    hooks.beginRender()
    ChatInput(props)
    hooks.setState(0, 'Send these notes')
    hooks.setState(1, [attachment])
    hooks.beginRender()
    let output = ChatInput(props)
    const firstSend = findElementByAriaLabel(output, 'chat.send').props.onClick as () => void
    firstSend()
    await Promise.resolve()

    expect(onSend).toHaveBeenCalledWith('Send these notes', [attachment])
    expect(hooks.stateValue(0)).toBe('Send these notes')
    expect(hooks.stateValue(1)).toEqual([attachment])
    expect(onTypingChange).not.toHaveBeenCalledWith(false)

    onSend.mockResolvedValueOnce(true)
    hooks.beginRender()
    output = ChatInput(props)
    const retrySend = findElementByAriaLabel(output, 'chat.send').props.onClick as () => void
    retrySend()
    await Promise.resolve()

    expect(hooks.stateValue(0)).toBe('')
    expect(hooks.stateValue(1)).toEqual([])
    expect(onTypingChange).toHaveBeenCalledWith(false)
  })

  it('single-flights rapid submits while an acknowledgement is pending', async () => {
    const eventTarget = Object.assign(new EventTarget(), {
      setTimeout: vi.fn(() => 1),
      clearTimeout: vi.fn()
    })
    vi.stubGlobal('window', eventTarget)
    vi.stubGlobal('document', new EventTarget())
    const acknowledgement = deferred<boolean>()
    const onSend = vi.fn(() => acknowledgement.promise)
    const props = submissionProps(onSend)

    hooks.beginRender()
    ChatInput(props)
    hooks.setState(0, 'Send once')
    hooks.beginRender()
    const output = ChatInput(props)
    const send = findElementByAriaLabel(output, 'chat.send').props.onClick as () => void

    send()
    send()
    expect(onSend).toHaveBeenCalledTimes(1)

    acknowledgement.resolve(false)
    await Promise.resolve()
    await Promise.resolve()
    send()
    expect(onSend).toHaveBeenCalledTimes(2)
  })

  it('removes sent attachment identities but keeps files added while send is pending', async () => {
    const eventTarget = Object.assign(new EventTarget(), {
      setTimeout: vi.fn(() => 1),
      clearTimeout: vi.fn()
    })
    vi.stubGlobal('window', eventTarget)
    vi.stubGlobal('document', new EventTarget())
    const sent: AttachmentDraft = {
      name: 'sent.txt',
      mime: 'text/plain',
      size: 4,
      data_url: 'data:text/plain;base64,c2VudA=='
    }
    const added: AttachmentDraft = {
      name: 'added.txt',
      mime: 'text/plain',
      size: 5,
      data_url: 'data:text/plain;base64,YWRkZWQ='
    }
    const acknowledgement = deferred<boolean>()
    const props = submissionProps(vi.fn(() => acknowledgement.promise))

    hooks.beginRender()
    ChatInput(props)
    hooks.setState(0, 'Send with files')
    hooks.setState(1, [sent])
    hooks.beginRender()
    const output = ChatInput(props)
    const send = findElementByAriaLabel(output, 'chat.send').props.onClick as () => void
    send()

    hooks.setState(1, [sent, added])
    hooks.beginRender()
    ChatInput(props)
    acknowledgement.resolve(true)
    await Promise.resolve()
    await Promise.resolve()

    expect(hooks.stateValue<AttachmentDraft[]>(1)).toEqual([added])
  })
})

describe('ChatInput image preview bounds', () => {
  it('fits large images within 1280px without distorting their aspect ratio', () => {
    expect(fitWithinMaxEdge(4000, 2000)).toEqual({ width: 1280, height: 640 })
    expect(fitWithinMaxEdge(600, 800)).toEqual({ width: 600, height: 800 })
  })

  it('calculates decoded base64 bytes without counting the data URL header or padding', () => {
    expect(dataUrlDecodedBytes('data:image/png;base64,YQ==')).toBe(1)
    expect(dataUrlDecodedBytes('data:image/png;base64,YWI=')).toBe(2)
    expect(dataUrlDecodedBytes('data:image/png;base64,YWJj')).toBe(3)
    expect(dataUrlDecodedBytes('not-a-data-url')).toBe(Number.POSITIVE_INFINITY)
  })

  it('reads source dimensions before the renderer decodes an image', () => {
    const png = new Uint8Array(24)
    png.set([0x89, 0x50, 0x4e, 0x47], 0)
    png.set([0, 0, 0x20, 0], 16)
    png.set([0, 0, 0x10, 0], 20)
    const dataUrl = `data:image/png;base64,${btoa(String.fromCharCode(...png))}`
    expect(rasterDimensionsFromDataUrl(dataUrl, 'image/png')).toEqual({ width: 8192, height: 4096 })
    expect(rasterDimensionsFromDataUrl('data:image/png;base64,not-valid', 'image/png')).toBeNull()
  })

  it('keeps an oversized image filename-only without constructing an Image', async () => {
    const png = new Uint8Array(24)
    png.set([0x89, 0x50, 0x4e, 0x47], 0)
    png.set([0, 0, 0x20, 1], 16)
    png.set([0, 0, 0x20, 1], 20)
    const attachment: AttachmentDraft = {
      name: 'large.png',
      mime: 'image/png',
      size: png.length,
      data_url: `data:image/png;base64,${btoa(String.fromCharCode(...png))}`
    }
    await expect(withImagePreview(attachment)).resolves.toEqual(attachment)
  })

  it('previews selected images sequentially', async () => {
    const attachments: AttachmentDraft[] = [
      { name: 'one.png', mime: 'image/png', size: 1, data_url: 'data:image/png;base64,YQ==' },
      { name: 'two.png', mime: 'image/png', size: 1, data_url: 'data:image/png;base64,YQ==' }
    ]
    const started: string[] = []
    let releaseFirst: (() => void) | undefined
    const preview = vi.fn(async (attachment: AttachmentDraft) => {
      started.push(attachment.name)
      if (attachment.name === 'one.png') await new Promise<void>((resolve) => { releaseFirst = resolve })
      return attachment
    })
    const pending = buildImagePreviews(attachments, preview)
    expect(started).toEqual(['one.png'])
    releaseFirst?.()
    await expect(pending).resolves.toEqual(attachments)
    expect(started).toEqual(['one.png', 'two.png'])
  })
})
