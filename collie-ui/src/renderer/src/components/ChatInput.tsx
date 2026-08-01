import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ArrowUp,
  Camera,
  Check,
  ChevronDown,
  FileText,
  Folder,
  FolderPlus,
  Image,
  Lightbulb,
  LoaderCircle,
  Mic,
  Paperclip,
  Plus,
  Square,
  X
} from 'lucide-react'
import { useT } from '../lib/i18n'
import type {
  AttachmentDraft,
  CommandCatalog,
  ExecutionMode,
  ProviderInfo
} from '../lib/ipc'
import {
  MICROPHONE_STORAGE_KEY,
  startLocalDictation,
  type LocalDictationRecorder
} from '../lib/audio'
import BrandLogo from './BrandLogo'

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
  onTypingChange?: (isTyping: boolean) => void
  onTranscribe: (audio: string) => Promise<string>
  commandCatalog?: CommandCatalog
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
  onTypingChange,
  onTranscribe,
  commandCatalog
}: Props): React.JSX.Element {
  const [text, setText] = useState('')
  const [attachments, setAttachments] = useState<AttachmentDraft[]>([])
  const [attachmentError, setAttachmentError] = useState('')
  const [openMenu, setOpenMenu] = useState<'model' | 'project' | null>(null)
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
      setAttachments((current) => {
        const remaining = Math.max(0, 4 - current.length)
        return [...current, ...picked.slice(0, remaining)]
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

  const PASTE_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif']

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
            const draft = await readClipboardBlob(file)
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

  const captureScreenshot = async (): Promise<void> => {
    setAttachmentError('')
    try {
      const screenshot = await window.collie.captureScreenshot()
      if (!screenshot) return
      addPastedAttachment(screenshot)
    } catch (error) {
      setAttachmentError(
        error instanceof Error ? error.message : 'Could not take a screenshot.'
      )
    }
  }

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
          {attachments.map((attachment, index) => (
            <div key={`${attachment.name}-${index}`} className="attachment-chip">
              {attachment.mime.startsWith('image/') ? <Image size={15} /> : <FileText size={15} />}
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
          ))}
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
      <div className="composer flex items-end gap-2 px-3 py-2">
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
        <button
          type="button"
          className="composer-tool"
          title="Take screenshot"
          aria-label="Take screenshot"
          disabled={busy || attachments.length >= 4}
          onClick={() => void captureScreenshot()}
        >
          <Camera size={17} />
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
              aria-haspopup="menu"
              disabled={busy}
              onClick={() => setOpenMenu(openMenu === 'model' ? null : 'model')}
            >
              <Lightbulb size={13} />
              <span>{model || activeProvider?.model || 'Add model'}</span>
              <ChevronDown size={11} aria-hidden="true" />
            </button>
            {openMenu === 'model' && (
              <div className="context-popover context-popover--model" role="menu">
                <div className="context-popover-heading">Intelligence</div>
                {providers.map((item) => (
                  <button
                    type="button"
                    role="menuitemradio"
                    aria-checked={item.id === activeProvider?.id}
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
                  role="menuitem"
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
              aria-expanded={openMenu === 'project'}
              aria-haspopup="menu"
              onClick={() => setOpenMenu(openMenu === 'project' ? null : 'project')}
            >
              <Folder size={13} />
              <span>{folderName || 'General Chat'}</span>
              <ChevronDown size={11} aria-hidden="true" />
            </button>
            {openMenu === 'project' && (
              <div className="context-popover context-popover--project" role="menu">
                <div className="context-popover-heading">Project folder</div>
                <button
                  type="button"
                  role="menuitemradio"
                  aria-checked={!workspace}
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
                    role="menuitemradio"
                    aria-checked={path === workspace}
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
              </div>
            )}
          </div>
        </div>
        <p className="composer-hint">Enter to send · Shift + Enter for a new line</p>
      </div>
    </div>
  )
}
