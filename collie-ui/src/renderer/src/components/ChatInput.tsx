import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ArrowUp,
  Check,
  ChevronDown,
  FileText,
  Folder,
  FolderPlus,
  Image as ImageIcon,
  Lightbulb,
  LoaderCircle,
  Mic,
  Paperclip,
  Plus,
  ShieldCheck,
  Square,
  X
} from 'lucide-react'
import { useT } from '../lib/i18n'
import type {
  AttachmentDraft,
  CommandCatalog,
  ApprovalPreset,
  ExecutionMode,
  FileAccessScope,
  ProviderInfo,
  TaskState
} from '../lib/ipc'
import {
  MICROPHONE_STORAGE_KEY,
  startLocalDictation,
  type LocalDictationRecorder
} from '../lib/audio'
import BrandLogo from './BrandLogo'
import TaskProgress from './tasks/TaskProgress'

const ROTATING_PROMPTS = [
  'Create a weekend shopping list and add tomatoes',
  'Plan three easy dinners for this week',
  'Draft a friendly follow-up after my meeting',
  'Help me compare two apartments',
  'Turn these notes into a clear to-do list',
  'Plan a relaxed Saturday with the kids',
  'Write an agenda for tomorrow’s team check-in',
  'Remind me to call Mum on Sunday',
  'Summarize this document in plain English',
  'Help me pack for a four-day city trip'
]

const PASTE_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif']
const PREVIEW_MAX_EDGE = 1280
const PREVIEW_MAX_BYTES = 248 * 1024
const PREVIEW_MIN_EDGE = 64
const PREVIEW_SOURCE_MAX_EDGE = 8192
const PREVIEW_SOURCE_MAX_PIXELS = 20_000_000

function isSafeRasterDataUrl(value: string): boolean {
  return /^data:image\/(?:png|jpeg|webp|gif);base64,/i.test(value)
}

export function fitWithinMaxEdge(
  width: number,
  height: number,
  maxEdge = PREVIEW_MAX_EDGE
): { width: number; height: number } {
  const safeWidth = Math.max(1, width)
  const safeHeight = Math.max(1, height)
  const scale = Math.min(1, maxEdge / Math.max(safeWidth, safeHeight))
  return {
    width: Math.max(1, Math.round(safeWidth * scale)),
    height: Math.max(1, Math.round(safeHeight * scale))
  }
}

export function dataUrlDecodedBytes(value: string): number {
  const separator = value.indexOf(',')
  if (separator < 0) return Number.POSITIVE_INFINITY
  const payload = value.slice(separator + 1)
  const padding = payload.endsWith('==') ? 2 : payload.endsWith('=') ? 1 : 0
  return Math.max(0, Math.floor((payload.length * 3) / 4) - padding)
}

function bytesFromDataUrl(value: string): Uint8Array | null {
  const separator = value.indexOf(',')
  if (separator < 0) return null
  try {
    const binary = atob(value.slice(separator + 1))
    const bytes = new Uint8Array(binary.length)
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
    return bytes
  } catch {
    return null
  }
}

function readJpegDimensions(bytes: Uint8Array): { width: number; height: number } | null {
  for (let index = 2; index + 8 < bytes.length;) {
    if (bytes[index] !== 0xff) {
      index += 1
      continue
    }
    while (bytes[index] === 0xff) index += 1
    const marker = bytes[index]
    index += 1
    if (marker === 0xd8 || marker === 0xd9 || (marker >= 0xd0 && marker <= 0xd7)) continue
    const length = (bytes[index] << 8) | bytes[index + 1]
    if (length < 2 || index + length > bytes.length) return null
    if ((marker >= 0xc0 && marker <= 0xc3) || (marker >= 0xc5 && marker <= 0xc7) || (marker >= 0xc9 && marker <= 0xcb) || (marker >= 0xcd && marker <= 0xcf)) {
      return {
        height: (bytes[index + 3] << 8) | bytes[index + 4],
        width: (bytes[index + 5] << 8) | bytes[index + 6]
      }
    }
    index += length
  }
  return null
}

function readWebpDimensions(bytes: Uint8Array): { width: number; height: number } | null {
  const text = (start: number, end: number): string => String.fromCharCode(...bytes.slice(start, end))
  if (bytes.length < 30 || text(0, 4) !== 'RIFF' || text(8, 12) !== 'WEBP') return null
  const kind = text(12, 16)
  if (kind === 'VP8X') {
    return {
      width: 1 + bytes[24] + (bytes[25] << 8) + (bytes[26] << 16),
      height: 1 + bytes[27] + (bytes[28] << 8) + (bytes[29] << 16)
    }
  }
  if (kind === 'VP8 ' && bytes.length >= 30 && bytes[23] === 0x9d && bytes[24] === 0x01 && bytes[25] === 0x2a) {
    return {
      width: (bytes[26] | (bytes[27] << 8)) & 0x3fff,
      height: (bytes[28] | (bytes[29] << 8)) & 0x3fff
    }
  }
  if (kind === 'VP8L' && bytes.length >= 25 && bytes[20] === 0x2f) {
    return {
      width: 1 + bytes[21] + ((bytes[22] & 0x3f) << 8),
      height: 1 + (bytes[22] >> 6) + (bytes[23] << 2) + ((bytes[24] & 0x0f) << 10)
    }
  }
  return null
}

/** Read raster dimensions before asking Chromium to allocate an image surface. */
export function rasterDimensionsFromDataUrl(
  value: string,
  mime: string
): { width: number; height: number } | null {
  const bytes = bytesFromDataUrl(value)
  if (!bytes) return null
  if (
    mime === 'image/png' &&
    bytes.length >= 24 &&
    bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47
  ) {
    return {
      width: (bytes[16] << 24) | (bytes[17] << 16) | (bytes[18] << 8) | bytes[19],
      height: (bytes[20] << 24) | (bytes[21] << 16) | (bytes[22] << 8) | bytes[23]
    }
  }
  if (mime === 'image/gif' && bytes.length >= 10) {
    return { width: bytes[6] | (bytes[7] << 8), height: bytes[8] | (bytes[9] << 8) }
  }
  if (mime === 'image/jpeg' && bytes[0] === 0xff && bytes[1] === 0xd8) return readJpegDimensions(bytes)
  if (mime === 'image/webp') return readWebpDimensions(bytes)
  return null
}

export async function withImagePreview(attachment: AttachmentDraft): Promise<AttachmentDraft> {
  if (!PASTE_IMAGE_TYPES.includes(attachment.mime) || !isSafeRasterDataUrl(attachment.data_url)) {
    return attachment
  }
  try {
    const sourceDimensions = rasterDimensionsFromDataUrl(attachment.data_url, attachment.mime)
    if (
      !sourceDimensions ||
      sourceDimensions.width < 1 ||
      sourceDimensions.height < 1 ||
      sourceDimensions.width > PREVIEW_SOURCE_MAX_EDGE ||
      sourceDimensions.height > PREVIEW_SOURCE_MAX_EDGE ||
      sourceDimensions.width * sourceDimensions.height > PREVIEW_SOURCE_MAX_PIXELS
    ) {
      return attachment
    }
    const image = new window.Image()
    image.decoding = 'async'
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve()
      image.onerror = () => reject(new Error('Could not preview image.'))
      image.src = attachment.data_url
    })
    let dimensions = fitWithinMaxEdge(image.naturalWidth, image.naturalHeight)
    const canvas = document.createElement('canvas')
    const context = canvas.getContext('2d')
    if (!context) return attachment
    // PNG preserves transparency. JPEG/WebP keep their compact native encoding;
    // GIF previews become a safe, static PNG frame.
    const outputMime = attachment.mime === 'image/jpeg' || attachment.mime === 'image/webp'
      ? attachment.mime
      : 'image/png'
    let quality = 0.88
    for (let attempt = 0; attempt < 16; attempt += 1) {
      canvas.width = dimensions.width
      canvas.height = dimensions.height
      context.clearRect(0, 0, canvas.width, canvas.height)
      context.drawImage(image, 0, 0, canvas.width, canvas.height)
      const preview = canvas.toDataURL(outputMime, quality)
      if (isSafeRasterDataUrl(preview) && dataUrlDecodedBytes(preview) <= PREVIEW_MAX_BYTES) {
        return { ...attachment, preview_data_url: preview }
      }
      if (outputMime !== 'image/png' && quality > 0.52) {
        quality -= 0.12
      } else {
        const longestEdge = Math.max(dimensions.width, dimensions.height)
        if (longestEdge <= PREVIEW_MIN_EDGE) break
        dimensions = fitWithinMaxEdge(
          dimensions.width,
          dimensions.height,
          Math.max(PREVIEW_MIN_EDGE, Math.floor(longestEdge * 0.8))
        )
        quality = 0.82
      }
    }
  } catch {
    // Preview generation is cosmetic; the original validated attachment can still be sent.
  }
  return attachment
}

export async function buildImagePreviews(
  attachments: AttachmentDraft[],
  preview: (attachment: AttachmentDraft) => Promise<AttachmentDraft> = withImagePreview
): Promise<AttachmentDraft[]> {
  const previews: AttachmentDraft[] = []
  for (const attachment of attachments) {
    // Decode one source at a time so four legitimate photos do not compete
    // for large renderer image surfaces.
    previews.push(await preview(attachment))
  }
  return previews
}

interface Props {
  onSend: (text: string, attachments: AttachmentDraft[]) => void
  onStop: () => void
  busy: boolean
  steering?: boolean
  mode: ExecutionMode
  onModeChange: (mode: ExecutionMode) => void
  model?: string
  workspace?: string
  projects?: string[]
  providers?: ProviderInfo[]
  onProviderChange: (providerId: string) => void
  onProjectChange: (path: string) => void
  onAddProject: () => void
  onAddModel: () => void
  approvalPreset: ApprovalPreset
  onApprovalPresetChange: (preset: ApprovalPreset) => void
  fileAccessScope: FileAccessScope
  onFileAccessScopeChange: (scope: FileAccessScope) => void
  onChooseFileAccessFolders: () => void
  onTypingChange?: (isTyping: boolean) => void
  onTranscribe: (audio: string) => Promise<string>
  commandCatalog?: CommandCatalog
  taskProgress?: TaskState | null
  /** Focus the composer once on mount (fresh onboarding straight into chat). */
  autofocus?: boolean
}

export default function ChatInput({
  onSend,
  onStop,
  busy,
  steering = false,
  mode,
  onModeChange,
  model,
  workspace,
  projects = [],
  providers = [],
  onProviderChange,
  onProjectChange,
  onAddProject,
  onAddModel,
  approvalPreset,
  onApprovalPresetChange,
  fileAccessScope,
  onFileAccessScopeChange,
  onChooseFileAccessFolders,
  onTypingChange,
  onTranscribe,
  commandCatalog,
  taskProgress,
  autofocus = false
}: Props): React.JSX.Element {
  const [text, setText] = useState('')
  const [attachments, setAttachments] = useState<AttachmentDraft[]>([])
  const [attachmentError, setAttachmentError] = useState('')
  const [openMenu, setOpenMenu] = useState<'model' | 'files' | 'approvals' | null>(null)
  const [voiceState, setVoiceState] = useState<'idle' | 'recording' | 'transcribing'>('idle')
  const [voiceError, setVoiceError] = useState('')
  const [commandIndex, setCommandIndex] = useState(0)
  const [promptIndex, setPromptIndex] = useState(0)
  const [promptLength, setPromptLength] = useState(0)
  const [deletingPrompt, setDeletingPrompt] = useState(false)
  const recorderRef = useRef<LocalDictationRecorder | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const footerRef = useRef<HTMLDivElement>(null)
  const onTypingChangeRef = useRef(onTypingChange)
  onTypingChangeRef.current = onTypingChange
  const t = useT()
  const activeProvider = providers.find((item) => item.is_default === 1)
  const folderName = workspace?.split(/[\\/]/).filter(Boolean).at(-1)
  const filesLabel = fileAccessScope.mode === 'full_file_access'
    ? 'All local files'
    : fileAccessScope.mode === 'chosen_folders'
      ? `${fileAccessScope.roots?.length || 0} folder${fileAccessScope.roots?.length === 1 ? '' : 's'}`
      : folderName || 'Files'
  const animatedPlaceholder = `e.g. ${ROTATING_PROMPTS[promptIndex].slice(0, promptLength)}`
  const commandSuggestions = (() => {
    if (!text.startsWith('/') || text.includes('\n')) return []
    const lower = text.toLowerCase()
    if (lower.startsWith('/agent ')) {
      const query = text.slice(7).trim().toLowerCase()
      return (commandCatalog?.agents || [])
        .filter((item) => item.name.toLowerCase().includes(query))
        .map((item) => ({
          key: `agent-${item.name}`,
          label: item.name,
          description: item.description,
          value: `/agent ${item.name} `
        }))
    }
    if (lower.startsWith('/skill ')) {
      const query = text.slice(7).trim().toLowerCase()
      return (commandCatalog?.skills || [])
        .filter((item) => item.name.toLowerCase().includes(query))
        .map((item) => ({
          key: `skill-${item.name}`,
          label: item.name,
          description: item.description,
          value: `/skill ${item.name} `
        }))
    }
    if (text.includes(' ')) return []
    const query = text.slice(1).toLowerCase()
    return (commandCatalog?.commands || [])
      .filter((item) => item.name.includes(query))
      .map((item) => ({
        key: item.name,
        label: `/${item.name}`,
        description: item.description,
        value: item.usage.includes('<') ? `/${item.name} ` : `/${item.name}`
      }))
  })().slice(0, 9)

  const chooseCommand = useCallback((value: string): void => {
    setText(value)
    setCommandIndex(0)
    onTypingChangeRef.current?.(true)
    requestAnimationFrame(() => textareaRef.current?.focus())
  }, [])

  useEffect(() => {
    if (!autofocus) return
    // Short delay: the shell may still be settling after connect.
    const timer = window.setTimeout(() => {
      textareaRef.current?.focus()
    }, 200)
    return () => window.clearTimeout(timer)
  }, [autofocus])

  useEffect(() => {
    if (text || steering) return
    const phrase = ROTATING_PROMPTS[promptIndex]
    const complete = promptLength >= phrase.length
    const empty = promptLength === 0
    const delay = complete && !deletingPrompt ? 1400 : deletingPrompt ? 22 : 42
    const timer = window.setTimeout(() => {
      if (complete && !deletingPrompt) {
        setDeletingPrompt(true)
      } else if (empty && deletingPrompt) {
        setDeletingPrompt(false)
        setPromptIndex((current) => (current + 1) % ROTATING_PROMPTS.length)
      } else {
        setPromptLength((current) => current + (deletingPrompt ? -1 : 1))
      }
    }, delay)
    return () => window.clearTimeout(timer)
  }, [deletingPrompt, promptIndex, promptLength, steering, text])

  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(Math.max(textarea.scrollHeight, 72), 184)}px`
    textarea.style.overflowY = textarea.scrollHeight > 184 ? 'auto' : 'hidden'
  }, [text])

  useEffect(() => {
    if (!openMenu) return
    const closeOnOutsideClick = (event: PointerEvent): void => {
      if (!footerRef.current?.contains(event.target as Node)) setOpenMenu(null)
    }
    const closeOnEscape = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') setOpenMenu(null)
    }
    document.addEventListener('pointerdown', closeOnOutsideClick)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsideClick)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [openMenu])

  useEffect(() => () => {
    void recorderRef.current?.cancel()
  }, [])

  useEffect(() => {
    const compose = (event: Event): void => {
      chooseCommand((event as CustomEvent<string>).detail)
    }
    window.addEventListener('collie:compose-command', compose)
    return () => window.removeEventListener('collie:compose-command', compose)
  }, [chooseCommand])

  useEffect(() => {
    setCommandIndex(0)
  }, [text])

    const submit = (): void => {
      const trimmed = text.trim()
      if (!trimmed && attachments.length === 0) return
      if (steering && attachments.length > 0) {
        // Mid-turn steering cannot carry files — never drop them silently.
        setAttachmentError(
          'Attachments only go with a new message. Finish or stop the current task first.'
        )
        return
      }
      onSend(trimmed, attachments)
      setText('')
      onTypingChange?.(false)
      setAttachments([])
      setAttachmentError('')
    }

  const pickAttachments = async (): Promise<void> => {
    setAttachmentError('')
    try {
      const picked = await window.collie.pickAttachments()
      if (picked.length === 0) return
      const withPreviews = await buildImagePreviews(picked)
      setAttachments((current) => {
        const remaining = Math.max(0, 4 - current.length)
        return [...current, ...withPreviews.slice(0, remaining)]
      })
    } catch (error) {
      setAttachmentError(error instanceof Error ? error.message : 'I could not attach that file.')
    }
  }

  const stopDictation = async (): Promise<void> => {
    const recorder = recorderRef.current
    if (!recorder) return
    recorderRef.current = null
    setVoiceState('transcribing')
    try {
      const audio = await recorder.stop()
      const transcript = (await onTranscribe(audio)).trim()
      setText((current) => current.trim() ? `${current.trimEnd()} ${transcript}` : transcript)
      onTypingChange?.(Boolean(transcript))
    } catch (error) {
      setVoiceError(error instanceof Error ? error.message : 'I could not hear that clearly.')
    } finally {
      setVoiceState('idle')
    }
  }

  const toggleDictation = async (): Promise<void> => {
    setVoiceError('')
    if (voiceState === 'recording') {
      await stopDictation()
      return
    }
    if (voiceState !== 'idle') return
    try {
      recorderRef.current = await startLocalDictation(
        () => void stopDictation(),
        localStorage.getItem(MICROPHONE_STORAGE_KEY) || undefined
      )
      setVoiceState('recording')
    } catch (error) {
      setVoiceError(
        error instanceof Error ? error.message : 'I could not open the microphone.'
      )
    }
  }

  const formatSize = (size: number): string =>
    size >= 1024 * 1024
      ? `${(size / (1024 * 1024)).toFixed(1)} MB`
      : `${Math.max(1, Math.round(size / 1024))} KB`

  const imagePreview = (attachment: AttachmentDraft): string | null => {
    const source = attachment.preview_data_url || attachment.data_url
    return PASTE_IMAGE_TYPES.includes(attachment.mime) && isSafeRasterDataUrl(source)
      ? source
      : null
  }

  const addPastedAttachment = useCallback(
    (draft: AttachmentDraft): boolean => {
      if (draft.size > 6 * 1024 * 1024) {
        setAttachmentError(`${draft.name} is larger than 6 MB.`)
        return false
      }
      setAttachments((current) => {
        if (current.length >= 4) return current
        return [...current, draft]
      })
      return true
    },
    []
  )

  const readClipboardBlob = (blob: Blob): Promise<AttachmentDraft> =>
    new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onloadend = () => {
        const dataUrl = reader.result as string
        resolve({
          name: `pasted-image.${blob.type.split('/')[1] || 'png'}`,
          mime: blob.type,
          size: blob.size,
          data_url: dataUrl
        })
      }
      reader.onerror = () => reject(new Error('Could not read clipboard image.'))
      reader.readAsDataURL(blob)
    })

  const handlePaste = useCallback(
    (event: React.ClipboardEvent): void => {
      const items = event.clipboardData?.items
      if (!items) return
      const imageItems: DataTransferItem[] = []
      for (let i = 0; i < items.length; i++) {
        const item = items[i]
        if (item.kind === 'file' && PASTE_IMAGE_TYPES.includes(item.type)) {
          imageItems.push(item)
        }
      }
      if (imageItems.length === 0) return
      event.preventDefault()
      setAttachmentError('')
      void (async () => {
        const remaining = Math.max(0, 4 - attachments.length)
        if (remaining === 0) {
          setAttachmentError('You can attach up to 4 files per message.')
          return
        }
        for (const item of imageItems.slice(0, remaining)) {
          const file = item.getAsFile()
          if (!file) continue
          try {
            const draft = await withImagePreview(await readClipboardBlob(file))
            addPastedAttachment(draft)
          } catch (error) {
            setAttachmentError(
              error instanceof Error ? error.message : 'Could not paste that image.'
            )
          }
        }
      })()
    },
    [attachments.length, addPastedAttachment]
  )

  return (
    <div className="composer-wrap mx-auto w-full max-w-3xl px-4 pb-5 pt-2" onPaste={handlePaste}>
      <div className="execution-mode" role="group" aria-label="Execution mode">
        {(['plan', 'execute'] as const).map((item) => (
          <button
            key={item}
            type="button"
            className={mode === item ? 'is-active' : ''}
            aria-pressed={mode === item}
            disabled={busy}
            onClick={() => onModeChange(item)}
          >
            {item === 'plan' ? 'Plan' : 'Execute'}
          </button>
        ))}
        <span>
          {mode === 'plan'
            ? 'Read-only until you approve a plan'
            : 'Actions follow your approval rules'}
        </span>
      </div>
      {attachments.length > 0 && (
        <div className="attachment-tray" aria-label="Files ready to send">
          {attachments.map((attachment, index) => {
            const preview = imagePreview(attachment)
            return (
              <div key={`${attachment.name}-${index}`} className="attachment-chip">
                {preview ? (
                  <img className="attachment-thumbnail" src={preview} alt="" />
                ) : attachment.mime.startsWith('image/') ? (
                  <ImageIcon size={15} />
                ) : (
                  <FileText size={15} />
                )}
                <span><b>{attachment.name}</b><small>{formatSize(attachment.size)}</small></span>
                <button
                  type="button"
                  onClick={() =>
                    setAttachments((current) => current.filter((_, itemIndex) => itemIndex !== index))
                  }
                  aria-label={`Remove ${attachment.name}`}
                >
                  <X size={13} />
                </button>
              </div>
            )
          })}
        </div>
      )}
      {commandSuggestions.length > 0 && (
        <div className="slash-palette" role="listbox" aria-label="Collie commands">
          <div className="slash-palette-heading">Commands</div>
          {commandSuggestions.map((item, index) => (
            <button
              type="button"
              role="option"
              aria-selected={index === commandIndex}
              className={index === commandIndex ? 'is-selected' : ''}
              key={item.key}
              onPointerDown={(event) => event.preventDefault()}
              onClick={() => chooseCommand(item.value)}
            >
              <b>{item.label}</b>
              <span>{item.description}</span>
            </button>
          ))}
          <small>↑↓ choose · Tab insert · Enter run</small>
        </div>
      )}
      {taskProgress ? (
        <TaskProgress task={taskProgress} onStop={onStop} attachedToComposer />
      ) : null}
      <div className={`composer flex items-end gap-2 px-3 py-2${
        taskProgress ? ' composer--with-task-progress' : ''
      }`}>
        <button
          type="button"
          className="composer-tool"
          title="Attach"
          aria-label="Attach files"
          disabled={busy || attachments.length >= 4}
          onClick={() => void pickAttachments()}
        >
          <Paperclip size={17} />
        </button>
        <textarea
          ref={textareaRef}
          rows={3}
          value={text}
          placeholder={steering ? 'Add instructions while Collie works…' : animatedPlaceholder}
          aria-label={t('chat.inputLabel')}
          onFocus={(event) => onTypingChange?.(event.currentTarget.value.trim().length > 0)}
          onBlur={() => onTypingChange?.(false)}
          onChange={(event) => {
            setText(event.target.value)
            onTypingChange?.(event.target.value.trim().length > 0)
          }}
          onKeyDown={(event) => {
            if (commandSuggestions.length > 0) {
              if (event.key === 'ArrowDown') {
                event.preventDefault()
                setCommandIndex((current) => (current + 1) % commandSuggestions.length)
                return
              }
              if (event.key === 'ArrowUp') {
                event.preventDefault()
                setCommandIndex(
                  (current) => (current - 1 + commandSuggestions.length) % commandSuggestions.length
                )
                return
              }
              if (event.key === 'Tab') {
                event.preventDefault()
                chooseCommand(commandSuggestions[commandIndex]?.value || text)
                return
              }
            }
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              submit()
            }
          }}
          className="flex-1 resize-none bg-transparent px-1 py-1.5 outline-none"
        />
        <button
          type="button"
          className={`composer-tool composer-mic${voiceState === 'recording' ? ' is-recording' : ''}`}
          title={
            voiceState === 'recording'
              ? 'Stop dictation'
              : voiceState === 'transcribing'
                ? 'Transcribing locally'
                : 'Dictate with local voice'
          }
          aria-label={voiceState === 'recording' ? 'Stop dictation' : 'Start dictation'}
          aria-pressed={voiceState === 'recording'}
          disabled={busy || voiceState === 'transcribing'}
          onClick={() => void toggleDictation()}
        >
          {voiceState === 'transcribing'
            ? <LoaderCircle size={17} className="voice-spinner" />
            : <Mic size={17} />}
        </button>
        {busy ? (
          <button
            type="button"
            onClick={onStop}
            title="Stop response"
            aria-label="Stop response"
            className="composer-stop shrink-0 transition"
          >
            <Square size={13} fill="currentColor" />
          </button>
        ) : null}
        <button
          type="button"
          onClick={submit}
          disabled={!text.trim() && attachments.length === 0}
          title={steering ? 'Add instructions to this task' : t('chat.send')}
          aria-label={steering ? 'Add instructions to this task' : t('chat.send')}
          className="composer-send shrink-0 transition disabled:opacity-35"
        >
          <ArrowUp size={18} strokeWidth={2.4} />
        </button>
      </div>
      {(attachmentError || voiceError) && (
        <p className="composer-error" role="alert">{attachmentError || voiceError}</p>
      )}
      <div className="composer-footer" ref={footerRef}>
        <div className="composer-context" aria-label="Chat context">
          <div className="context-menu-wrap">
            <button
              type="button"
              className="context-button"
              aria-expanded={openMenu === 'model'}
              aria-controls="model-context-popover"
              disabled={busy}
              onClick={() => setOpenMenu(openMenu === 'model' ? null : 'model')}
            >
              <Lightbulb size={13} />
              <span>{model || activeProvider?.model || 'Add model'}</span>
              <ChevronDown size={11} aria-hidden="true" />
            </button>
            {openMenu === 'model' && (
              <div id="model-context-popover" className="context-popover context-popover--model" role="group" aria-label="Intelligence options">
                <div className="context-popover-heading">Intelligence</div>
                {providers.map((item) => (
                  <button
                    type="button"
                    aria-pressed={item.id === activeProvider?.id}
                    key={item.id}
                    onClick={() => {
                      setOpenMenu(null)
                      onProviderChange(item.id)
                    }}
                  >
                    <BrandLogo brand={item.name} name={item.name} size={28} />
                    <span>
                      <b>{item.model || item.name}</b>
                      <small>{item.name} · {item.auth_type === 'api-key' ? 'API key' : 'Sign-in'}</small>
                    </span>
                    {item.id === activeProvider?.id && <Check size={14} />}
                  </button>
                ))}
                <button
                  type="button"
                  className="context-add"
                  onClick={() => {
                    setOpenMenu(null)
                    onAddModel()
                  }}
                >
                  <Plus size={14} /> Add model
                </button>
              </div>
            )}
          </div>

          <span className="context-divider" aria-hidden="true" />

          <div className="context-menu-wrap">
            <button
              type="button"
              className="context-button"
              aria-label="Files"
              aria-expanded={openMenu === 'files'}
              aria-controls="files-context-popover"
              disabled={busy}
              onClick={() => setOpenMenu(openMenu === 'files' ? null : 'files')}
            >
              <Folder size={13} />
              <span>{filesLabel}</span>
              <ChevronDown size={11} aria-hidden="true" />
            </button>
            {openMenu === 'files' && (
              <div id="files-context-popover" className="context-popover context-popover--files" role="group" aria-label="File and folder options">
                <div className="context-popover-heading">Project folder</div>
                <button
                  type="button"
                  aria-pressed={!workspace}
                  onClick={() => {
                    setOpenMenu(null)
                    onProjectChange('')
                  }}
                >
                  <span>
                    <b>General Chat</b>
                    <small>No project folder</small>
                  </span>
                  {!workspace && <Check size={14} />}
                </button>
                {projects.map((path) => (
                  <button
                    type="button"
                    aria-pressed={path === workspace}
                    key={path}
                    onClick={() => {
                      setOpenMenu(null)
                      onProjectChange(path)
                    }}
                  >
                    <span>
                      <b>{path.split(/[\\/]/).filter(Boolean).at(-1) || path}</b>
                      <small>{path}</small>
                    </span>
                    {path === workspace && <Check size={14} />}
                  </button>
                ))}
                <button
                  type="button"
                  role="menuitem"
                  className="context-add"
                  onClick={() => {
                    setOpenMenu(null)
                    onAddProject()
                  }}
                >
                  <FolderPlus size={14} /> New project
                </button>
                <div className="context-popover-heading context-popover-heading--section">
                  Access for this chat
                </div>
                <p className="context-popover-note">
                  Files Collie reads inside this scope may be sent to{' '}
                  {activeProvider?.name || 'your model provider'}.
                </p>
                <button
                  type="button"
                  aria-pressed={fileAccessScope.mode === 'selected_folder'}
                  onClick={() => {
                    setOpenMenu(null)
                    onFileAccessScopeChange({ mode: 'selected_folder' })
                  }}
                >
                  <span>
                    <b>Project folder only</b>
                    <small>{workspace || 'Collie Workspace'}</small>
                  </span>
                  {fileAccessScope.mode === 'selected_folder' && <Check size={14} />}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setOpenMenu(null)
                    onChooseFileAccessFolders()
                  }}
                >
                  <span>
                    <b>Choose other folders…</b>
                    <small>Pick one or more local folders Collie may use.</small>
                  </span>
                </button>
                {fileAccessScope.mode === 'chosen_folders' && fileAccessScope.roots?.length ? (
                  <ul className="file-access-roots" aria-label="Folders Collie can use">
                    {fileAccessScope.roots.map((root) => <li key={root}>{root}</li>)}
                  </ul>
                ) : null}
                <button
                  type="button"
                  aria-pressed={fileAccessScope.mode === 'full_file_access'}
                  onClick={() => {
                    setOpenMenu(null)
                    onFileAccessScopeChange({ mode: 'full_file_access' })
                  }}
                >
                  <span>
                    <b>Full file access</b>
                    <small>Local text files anywhere on this computer.</small>
                  </span>
                  {fileAccessScope.mode === 'full_file_access' && <Check size={14} />}
                </button>
              </div>
            )}
          </div>

          <span className="context-divider" aria-hidden="true" />

          <div className="context-menu-wrap">
            <button
              type="button"
              className="context-button"
              aria-expanded={openMenu === 'approvals'}
              aria-controls="approval-context-popover"
              disabled={busy}
              onClick={() => setOpenMenu(openMenu === 'approvals' ? null : 'approvals')}
            >
              <ShieldCheck size={13} />
              <span>{approvalPreset === 'allow' ? 'Approve for me' : 'Ask me'}</span>
              <ChevronDown size={11} aria-hidden="true" />
            </button>
            {openMenu === 'approvals' && (
              <div id="approval-context-popover" className="context-popover context-popover--safety" role="group" aria-label="Approval options">
                <div className="context-popover-heading">Approvals</div>
                <button
                  type="button"
                  aria-pressed={approvalPreset === 'ask'}
                  onClick={() => {
                    setOpenMenu(null)
                    onApprovalPresetChange('ask')
                  }}
                >
                  <span>
                    <b>Ask me</b>
                    <small>Collie asks before bounded file edits and other eligible local changes that still need approval.</small>
                  </span>
                  {approvalPreset === 'ask' && <Check size={14} />}
                </button>
                <button
                  type="button"
                  aria-pressed={approvalPreset === 'allow'}
                  onClick={() => {
                    setOpenMenu(null)
                    onApprovalPresetChange('allow')
                  }}
                >
                  <span>
                    <b>Approve for me</b>
                    <small>Eligible ordinary local actions can continue. Consequential actions still ask.</small>
                  </span>
                  {approvalPreset === 'allow' && <Check size={14} />}
                </button>
              </div>
            )}
          </div>
        </div>
        <p className="composer-hint">Enter to send · Shift + Enter for a new line</p>
      </div>
    </div>
  )
}
