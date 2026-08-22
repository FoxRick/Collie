/**
 * CollieClient — typed client for the Python core's WebSocket IPC.
 * Auto-reconnects; queues commands until connected.
 */

export interface CollieMessage {
  id: string
  conversation_id: string
  role: 'user' | 'assistant'
  content: string
  card_type?: string | null
  card_data?: Record<string, unknown> | null
  attachments?: MessageAttachment[] | null
  /** Terminal task progress retained with the final assistant message. */
  task_state?: TaskState | null
  created_at: string
}

export interface MessageAttachment {
  name: string
  mime: string
  size: number
  preview_data_url?: string
}

/** One finished deliverable in the "Your things" panel (backend: ThingRecord). */
export interface Thing {
  id: string
  title: string
  kind: 'image' | 'document' | 'sheet' | 'pdf' | 'web' | 'file' | string
  path: string
  size_bytes: number
  /** Unix seconds (time.time()) as shipped by the backend — may arrive as number or string. */
  created_at: number | string
  status: string
  version: number
}

export interface AttachmentDraft extends MessageAttachment {
  data_url: string
}

export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
  archived: number
  execution_mode?: ExecutionMode
  project_path?: string | null
}

export type ExecutionMode = 'plan' | 'execute'
export type ApprovalPreset = 'ask' | 'allow'
export type FileAccessMode = 'selected_folder' | 'chosen_folders' | 'full_file_access'

export interface FileAccessScope {
  mode: FileAccessMode
  roots?: string[]
}

export interface ThinkingState {
  state: string
  phrase: string
  pet_animation: string
  conversation_id?: string
}

export type SubagentOutcome = 'ok' | 'error' | 'cancelled'

export interface ActiveAgent {
  id: string
  name: string
  phase: string
  task_description?: string
  conversation_id: string
  /** Wall-clock epoch ms (monotonic on the core converted for the UI). */
  started_at_ms?: number
  /** Present only on settled rows (recent activity). */
  ended_at_ms?: number
  outcome?: SubagentOutcome
}

export interface ProviderInfo {
  id: string
  name: string
  auth_type: string
  is_default: number
  model: string | null
  runtime_name?: string | null
  protocol?: 'openai' | 'anthropic' | null
  api_base?: string | null
  secret_name?: string | null
}

export interface ProviderCandidate {
  provider_id: string
  name: string
  auth_type: 'api-key'
  model?: string | null
  runtime_name?: string | null
  protocol?: 'openai' | 'anthropic'
  api_base?: string | null
  secret_name: string
  api_key?: string
}

export interface ProviderCandidateResult {
  provider?: ProviderInfo
  configured: boolean
  model?: string
  error?: string
  error_kind?: string
  validated?: boolean
  model_label?: string
  rolled_back?: boolean
  rollback_error?: string | null
  transaction_id?: string
}

export interface CatalogueProviderModel {
  id: string
  name: string
}

export interface CatalogueProvider {
  id: string
  name: string
  auth_type: string
  protocol: 'openai' | 'anthropic'
  api_base?: string | null
  default_model?: string | null
  key_prefixes: string[]
  tested: boolean
  help_url?: string | null
  models: CatalogueProviderModel[]
}

export interface RuntimeStatus {
  configured?: boolean
  model?: string
  workspace?: string
  providers?: ProviderInfo[]
  active_agents?: ActiveAgent[]
  /** Settled subagent rows (outcome + ended_at_ms), newest first. */
  recent_agents?: ActiveAgent[]
}

export interface ClearDataWarning {
  scope: 'database' | 'filesystem'
  target: string
  error: string
}

export interface ClearAllDataResult {
  cleared: boolean
  partial: boolean
  database_cleared: boolean
  filesystem_cleared: boolean
  warnings: ClearDataWarning[]
}

export interface ServiceField {
  key: string
  label: string
  secret: boolean
  placeholder: string
}

export interface ServiceInfo {
  id: string
  name: string
  category: string
  description: string
  auth: 'oauth' | 'api_key' | 'none'
  fields: ServiceField[]
  permissions: string[]
  available: boolean
  release_status: 'stable' | 'alpha' | 'coming_soon'
  note: string
  status: string
  account_info?: string | null
  connected_at?: string | null
  last_error?: string | null
}

export interface ConnectorCatalogItem {
  id: string
  name: string
  category: string
  description: string
  auth: 'oauth' | 'api_key' | 'none'
  driver: 'official_mcp' | 'official_api' | 'bundled_mcp' | 'custom_mcp'
  capabilities: string[]
  permissions: string[]
  featured: boolean
  available: boolean
  release_status: 'stable' | 'alpha' | 'coming_soon'
  note: string
  status: string
  connection_count: number
}

export interface ConnectorConnection {
  id: string
  provider_id: string
  provider_name: string
  display_name?: string | null
  account_label?: string | null
  driver: string
  auth_type: string
  status:
    | 'disconnected'
    | 'authorizing'
    | 'testing'
    | 'connected'
    | 'auth_required'
    | 'attention'
    | 'failed'
    | 'revoking'
  granted_scopes: string[]
  enabled_capabilities: string[]
  enabled_tools: string[]
  tool_policy: Record<string, string>
  connected_at?: string | null
  updated_at?: string | null
  last_verified_at?: string | null
  last_error_code?: string | null
  last_error_message?: string | null
  permissions: string[]
  capabilities: string[]
  route: string
}

export interface Subagent {
  id: string
  name: string
  description: string
  system_prompt: string
  filename: string
  created_at: string
  updated_at: string
  execution_posture: 'read_only' | 'inherit'
}

export interface SubagentStarter {
  name: string
  description: string
  system_prompt: string
  execution_posture?: 'read_only' | 'inherit'
}

export interface CollieSkill {
  name: string
  description: string
  source: 'workspace' | 'builtin' | string
  available: boolean
  unavailable_reason: string
  requirements?: {
    bins: string[]
    env: string[]
    missing_bins: string[]
    missing_env: string[]
  }
}

export interface SlashCommand {
  name: string
  description: string
  usage: string
  category: string
}

export interface CommandCatalog {
  commands: SlashCommand[]
  agents: Array<{
    name: string
    description: string
    execution_posture: 'read_only' | 'inherit'
  }>
  skills: CollieSkill[]
}

export interface CollieAutomation {
  id: string
  name: string
  description?: string
  schedule?: string
  action_type?: string
  enabled: number
  routine_status?: 'enabled' | 'paused' | 'needs_attention'
  next_run_at?: string | null
  last_success_at?: string | null
  last_failure_at?: string | null
  plan_id?: string | null
  plan_version?: number | null
}

export interface ApprovalRequest {
  id: string
  action: string
  resource: string
  risk: string
  display_json: string
  run_id?: string | null
}

export interface ApprovalRule {
  id: string
  action: string
  resource_pattern: string
  effect: 'allow' | 'deny'
  scope_type: string
  scope_value?: string | null
}

export interface CollieRun {
  id: string
  status: string
  trigger_type: string
  scheduled_for?: string | null
  started_at?: string | null
  finished_at?: string | null
  error_message?: string | null
}

/** One recorded agent turn (PR 1 run records / telemetry). */
export interface TurnEvent {
  id: string
  conversation_id?: string | null
  session_key?: string | null
  turn_kind: string
  provider?: string | null
  model?: string | null
  status: string
  error_message?: string | null
  tokens_in?: number
  tokens_out?: number
  latency_ms?: number | null
  tool_count?: number
  started_at: string
  finished_at?: string | null
}

/** One recorded tool call within a turn (PR 1 run records / telemetry). */
export interface ToolEvent {
  id: string
  turn_id: string
  tool_name: string
  action?: string | null
  resource?: string | null
  input_summary?: string | null
  output_summary?: string | null
  status: string
  error_message?: string | null
  latency_ms?: number | null
  started_at: string
  finished_at?: string | null
}

/** One snapshotted artifact edit (PR 2 versioned rollback rail). */
export interface ArtifactVersion {
  id: string
  artifact_type: string
  artifact_key: string
  version: number
  before_text?: string | null
  after_text?: string | null
  diff_text?: string | null
  evidence_json?: string | null
  source: string
  status: string
  created_at: string
}

export interface RollbackArtifactResult {
  rolled_back: boolean
  version_id: string
  artifact_type: string
  artifact_key: string
  version: number
}

/** One validated Gardener suggestion (proposed artifact change). */
export interface GardenerSuggestion {
  artifact_type: 'subagent' | 'agents' | 'vision' | 'memory_dream'
  artifact_key: string
  proposed_text: string
  rationale: string
  evidence_ids: string[]
}

/** One append-only memory write (ProfileStore mutation), newest first. */
export interface MemoryJournalEntry {
  id: number
  kind: 'fact' | 'person' | 'date' | string
  subject: string
  action: 'add' | 'update' | 'delete' | string
  value?: unknown
  created_at: string
}

/** A user-facing progress snapshot. It deliberately excludes tool traffic and model reasoning. */
export interface TaskStep {
  key: string
  title: string
  status: 'pending' | 'in_progress' | 'completed' | 'blocked' | 'skipped' | 'failed'
  summary?: string | null
  error_message?: string | null
  started_at?: string | null
  finished_at?: string | null
}

export interface TaskState {
  id: string
  source: 'checklist' | 'plan_run'
  status: string
  revision: number
  title: string
  completed_count: number
  total_count: number
  current_step_key?: string | null
  created_at?: string | null
  updated_at?: string | null
  completed_at?: string | null
  steps: TaskStep[]
}

export interface ChangePlanResult {
  requested: boolean
  conversation_id: string
  run_id: string
  plan_id: string
  plan_version: number
  execution_mode: 'plan'
  status: 'pending_safe_boundary' | 'cancelled'
}

export interface MessengerInfo {
  id: string
  label: string
  emoji: string
  secrets: string[]
  enabled: boolean
  configured: boolean
  running: boolean
  connected: boolean
  deliver_automations: boolean
  error?: string | null
  approved: string[]
  pending: Array<{ code: string; sender_id: string }>
  qr?: string | null
  last_chat_id: string
}

export type CollieEvent =
  | { type: 'ready'; protocol: number; phrase: string }
  /** Local client event emitted when a WebSocket (re)connects. */
  | { type: 'connection_opened' }
  | ({ type: 'thinking' } & ThinkingState)
  | { type: 'delta'; conversation_id: string; text: string }
  | { type: 'message'; conversation_id: string; message: CollieMessage }
  | { type: 'artifact'; conversation_id: string; artifact: Thing }
  | { type: 'card'; conversation_id: string; card_type: string; card_data: Record<string, unknown> }
  | { type: 'conversation_updated'; conversation: Conversation }
  | { type: 'error'; conversation_id?: string; id?: string; message: string; detail?: string }
  | { type: 'ok'; id?: string; data: Record<string, unknown> }
  | { type: 'messenger_qr'; messenger: string; qr: string }
  | { type: 'messenger_status'; messenger: string; status: string; error?: string }
  | { type: 'messenger_pairing'; messenger: string }
  | {
      type: 'automation'
      automation_id: string
      name: string
      conversation_id?: string
      content?: string
    }
  | { type: 'approval_requested'; approval: ApprovalRequest }
  | { type: 'approval_resolved'; approval: ApprovalRequest }
  | { type: 'plan_updated'; plan: Record<string, unknown> }
  | { type: 'task_state'; conversation_id: string; task: TaskState }
  | { type: 'routine_updated'; routine: CollieAutomation }
  | { type: 'run_started' | 'run_completed' | 'run_failed'; run: CollieRun }
  | {
      type: 'run_step_updated'
      conversation_id: string
      step: {
        run_id: string
        step_key: string
        status: string
        title: string
        output_summary?: string | null
        error_message?: string | null
      }
    }
  | {
      type:
        | 'connector_auth_started'
        | 'connector_status_changed'
        | 'connector_connected'
        | 'connector_failed'
        | 'connector_removed'
        | 'connector_tools_changed'
      provider_id?: string
      connection_id?: string
      status?: string
      message?: string
      origin?: string
    }

type Listener = (event: CollieEvent) => void

export class CollieClient {
  private ws: WebSocket | null = null
  private url: string
  private token: string | null = null
  private listeners = new Set<Listener>()
  private pending = new Map<string, (event: CollieEvent) => void>()
  private queue: string[] = []
  private seq = 0
  private closed = false
  connected = false

  constructor(port = 3818, token?: string | null) {
    this.url = `ws://127.0.0.1:${port}`
    this.token = token || null
  }

  /** Re-point at the core's actual port/token (from main-process coreState). */
  applyEndpoint(port: number, token?: string | null): void {
    this.url = `ws://127.0.0.1:${port}`
    this.token = token || null
    if (this.ws) {
      const socket = this.ws
      this.ws = null
      socket.close()
    }
  }

  connect(): void {
    if (this.closed) return
    if (
      this.ws &&
      (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)
    ) {
      return
    }
    let socket: WebSocket
    try {
      socket = this.token
        ? new WebSocket(this.url, [`collie-${this.token}`])
        : new WebSocket(this.url)
      this.ws = socket
    } catch {
      this.retry()
      return
    }
    socket.onopen = () => {
      if (this.ws !== socket) return
      this.connected = true
      for (const raw of this.queue.splice(0)) socket.send(raw)
      for (const listener of this.listeners) listener({ type: 'connection_opened' })
    }
    socket.onmessage = (e) => {
      let event: CollieEvent
      try {
        event = JSON.parse(String(e.data)) as CollieEvent
      } catch {
        return
      }
      const id = 'id' in event ? (event.id as string | undefined) : undefined
      if (id && this.pending.has(id)) {
        const resolve = this.pending.get(id)!
        this.pending.delete(id)
        resolve(event)
        // Command replies (ok AND error) are consumed by the caller — never
        // fanned out to listeners where a background error would leak into
        // the chat as a conversation error.
        return
      }
      for (const listener of this.listeners) listener(event)
    }
    socket.onclose = () => {
      if (this.ws !== socket) return
      this.ws = null
      this.connected = false
      this.retry()
    }
    socket.onerror = () => {
      socket.close()
    }
  }

  private retry(): void {
    if (this.closed) return
    setTimeout(() => this.connect(), 1200)
  }

  close(): void {
    this.closed = true
    this.ws?.close()
  }

  on(listener: Listener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  private sendRaw(frame: Record<string, unknown>): void {
    const raw = JSON.stringify(frame)
    if (this.connected && this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(raw)
    } else {
      this.queue.push(raw)
    }
  }

  /** Fire a command and await its ok/error reply. */
  command<T = Record<string, unknown>>(
    type: string,
    payload: Record<string, unknown> = {},
    timeoutMs = 120_000
  ): Promise<T> {
    const id = `c${++this.seq}`
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id)
        reject(new Error('Collie took too long to answer.'))
      }, timeoutMs)
      this.pending.set(id, (event) => {
        clearTimeout(timer)
        if (event.type === 'ok') resolve(event.data as T)
        else reject(new Error((event as { message?: string }).message || 'error'))
      })
      this.sendRaw({ type, id, ...payload })
    })
  }

  chat(
    conversationId: string | null,
    content: string,
    attachments: AttachmentDraft[] = [],
    executionMode: ExecutionMode = 'execute',
    projectPath?: string,
    fileAccessScope: FileAccessScope = { mode: 'selected_folder' }
  ): Promise<{ conversation_id: string; command_handled?: boolean }> {
    return this.command('chat', {
      conversation_id: conversationId ?? '',
      content,
      attachments,
      execution_mode: executionMode,
      project_path: projectPath,
      file_access_scope: fileAccessScope
    })
  }

  setFileAccessScope(
    conversationId: string,
    fileAccessScope: FileAccessScope
  ): Promise<{ applied: boolean; file_access_scope: FileAccessScope }> {
    return this.command('set_file_access_scope', {
      conversation_id: conversationId,
      file_access_scope: fileAccessScope
    })
  }

  transcribe(audio: string): Promise<{ text: string }> {
    return this.command('transcribe', { audio }, 600_000)
  }

  setExecutionMode(conversationId: string, executionMode: ExecutionMode): Promise<unknown> {
    return this.command('set_execution_mode', {
      conversation_id: conversationId,
      execution_mode: executionMode
    })
  }

  approvePlan(
    planId: string,
    version: number,
    planHash: string
  ): Promise<{ plan: Record<string, unknown>; run: CollieRun }> {
    return this.command('approve_plan', {
      plan_id: planId,
      version,
      plan_hash: planHash
    })
  }

  changePlan(conversationId: string, runId: string): Promise<ChangePlanResult> {
    // `id` is reserved for the authenticated command envelope generated by command().
    return this.command('change_plan', { conversation_id: conversationId, run_id: runId })
  }

  listConversations(): Promise<{ conversations: Conversation[] }> {
    return this.command('list_conversations')
  }

  listThings(conversationId: string): Promise<{ things: Thing[] }> {
    return this.command('list_things', { conversation_id: conversationId })
  }

  getMessages(conversationId: string): Promise<{ messages: CollieMessage[] }> {
    return this.command('get_messages', { conversation_id: conversationId })
  }

  getActiveTask(conversationId: string): Promise<{ task: TaskState | null }> {
    return this.command('get_active_task', { conversation_id: conversationId })
  }

  getRunRecords(opts?: {
    conversation_id?: string
    session_key?: string
    since?: string
    limit?: number
  }): Promise<{ turns: TurnEvent[] }> {
    return this.command('get_run_records', opts ?? {})
  }

  getToolEvents(opts?: {
    turn_id?: string
    tool_name?: string
    limit?: number
  }): Promise<{ tool_events: ToolEvent[] }> {
    return this.command('get_tool_events', opts ?? {})
  }

  listVersions(opts?: {
    artifact_type?: string
    artifact_key?: string
    limit?: number
  }): Promise<{ versions: ArtifactVersion[] }> {
    return this.command('list_versions', opts ?? {})
  }

  rollbackArtifact(versionId: string): Promise<RollbackArtifactResult> {
    return this.command('rollback_artifact', { version_id: versionId })
  }

  /** One-tap undo of local file changes made in a conversation. */
  undoFileChanges(
    conversationId: string,
    entryIds?: string[]
  ): Promise<{
    undone: Array<{ id: string; path: string }>
    errors: Array<{ id: string; path: string; message: string }>
  }> {
    return this.command('undo_file_changes', {
      conversation_id: conversationId,
      entry_ids: entryIds ?? []
    })
  }

  /** Manual trigger: run one Dream consolidation pass (Settings -> Memory). */
  runDream(): Promise<{
    changed: boolean
    reason?: string
    pending?: boolean
    version_id?: string | null
    diff?: string
    cursor?: string
    message?: string
  }> {
    return this.command('run_dream', {})
  }

  /** Past Dream consolidations (memory_dream versions), newest first. */
  getDreamHistory(): Promise<{ versions: ArtifactVersion[] }> {
    return this.command('get_dream_history', {})
  }

  /** Pending Dream proposal state (Settings -> Memory self-review section). */
  getDreamPending(): Promise<{
    pending: boolean
    diff_text?: string | null
    created_at?: string | null
  }> {
    return this.command('get_dream_pending', {})
  }

  /** Approve the pending Dream proposal: re-validated, applied, versioned. */
  applyDreamProposal(): Promise<{
    applied: boolean
    reason?: string
    version_id?: string | null
    diff_text?: string
  }> {
    return this.command('apply_dream_proposal', {})
  }

  /** Dismiss the pending Dream proposal without applying it. */
  dismissDreamProposal(): Promise<{ dismissed: boolean }> {
    return this.command('dismiss_dream_proposal', {})
  }

  /** Recent memory writes (kind/subject/action/value), newest first. */
  getMemoryJournal(limit = 50): Promise<{ entries: MemoryJournalEntry[] }> {
    return this.command('get_memory_journal', { limit })
  }

  /** Manual trigger: run one Gardener pass (evidence -> suggestions). */
  runGardener(): Promise<{
    suggestions: GardenerSuggestion[]
    rejected?: Array<{ reason: string; artifact_type: string; artifact_key: string }>
    message?: string
  }> {
    return this.command('run_gardener', {})
  }

  /** Approve one suggestion: re-validated, applied, versioned (undoable). */
  applyGardenerSuggestion(
    suggestion: GardenerSuggestion
  ): Promise<{
    applied: boolean
    no_change?: boolean
    version_id?: string | null
    diff_text?: string
    artifact_type: string
    artifact_key: string
  }> {
    return this.command('apply_gardener_suggestion', { suggestion })
  }

  stopConversation(
    conversationId: string
  ): Promise<{
    stopped: boolean
    cancelled_subagents: number
    cancelled_approvals: number
  }> {
    return this.command('stop', { conversation_id: conversationId })
  }

  steerConversation(conversationId: string, content: string): Promise<{ accepted: boolean }> {
    return this.command('steer', { conversation_id: conversationId, content })
  }

  newConversation(title = 'New chat'): Promise<Conversation> {
    return this.command('new_conversation', { title })
  }

  getStarterConversation(conversationId?: string | null): Promise<{
    conversation: Conversation
    greeted: boolean
  }> {
    return this.command('get_starter_conversation', {
      conversation_id: conversationId ?? ''
    })
  }

  getProviderCatalogue(): Promise<{
    providers: CatalogueProvider[]
    snapshot: {
      schema_version?: number
      generated_at?: string
      source_url?: string
      source_sha256?: string
      source_providers_count?: number
    }
    refresh: {
      available: boolean
      version?: string
      sha256?: string
      refreshed_at?: string
    }
  }> {
    return this.command('get_provider_catalogue')
  }

  refreshProviderCatalogue(url?: string): Promise<{
    refreshed: boolean
    error?: string
    version?: string
    sha256?: string
    refreshed_at?: string
    providers_count?: number
  }> {
    return this.command('refresh_provider_catalogue', { url })
  }

  rollbackProviderCatalogue(): Promise<{ rolled_back: boolean; error?: string }> {
    return this.command('rollback_provider_catalogue')
  }

  detectProviderForKey(apiKey: string): Promise<{
    detected: boolean
    provider_id: string | null
    reason?: string
    candidates?: string[]
  }> {
    return this.command('detect_provider_for_key', { api_key: apiKey })
  }

  detectModels(
    apiBase: string,
    protocol: 'openai' | 'anthropic' = 'openai',
    apiKey?: string
  ): Promise<{ detected: boolean; error?: string | null; models: string[] }> {
    return this.command('detect_models', {
      api_base: apiBase,
      protocol,
      api_key: apiKey
    })
  }

  detectLocalModels(): Promise<{ available: boolean; models: string[] }> {
    return this.command('detect_local_models')
  }

  renameConversation(conversationId: string, title: string): Promise<unknown> {
    return this.command('rename_conversation', { conversation_id: conversationId, title })
  }

  deleteConversation(conversationId: string): Promise<unknown> {
    return this.command('delete_conversation', { conversation_id: conversationId })
  }

  getStatus(timeoutMs = 120_000): Promise<RuntimeStatus> {
    return this.command('get_status', {}, timeoutMs)
  }

  /** Lightweight roster for poll-heavy surfaces (Agents tab live section). */
  getSubagentActivity(timeoutMs = 5_000): Promise<{
    active_agents: ActiveAgent[]
    recent_agents: ActiveAgent[]
  }> {
    return this.command('get_subagent_activity', {}, timeoutMs)
  }

  getSettings(): Promise<{ settings: Record<string, unknown> }> {
    return this.command('get_settings')
  }

  setApprovalPreset(preset: ApprovalPreset): Promise<{ preset: ApprovalPreset }> {
    return this.command('set_approval_preset', { preset })
  }

  setApiKey(provider: string, key: string): Promise<unknown> {
    return this.command('set_api_key', { provider, key })
  }

  upsertProvider(provider: {
    provider_id: string
    name: string
    auth_type: string
    model?: string | null
    runtime_name?: string | null
    protocol?: 'openai' | 'anthropic'
    api_base?: string | null
    secret_name?: string | null
    is_default?: boolean
  }): Promise<{ provider: ProviderInfo }> {
    return this.command('upsert_provider', provider)
  }

  activateProvider(
    providerId: string
  ): Promise<ProviderCandidateResult> {
    return this.command('activate_provider', { provider_id: providerId }, 60_000)
  }

  configureProviderCandidate(candidate: ProviderCandidate): Promise<ProviderCandidateResult> {
    return this.command('configure_provider_candidate', { ...candidate }, 60_000)
  }

  finalizeProviderCandidate(transactionId: string): Promise<{ finalized: boolean }> {
    return this.command('finalize_provider_candidate', { transaction_id: transactionId })
  }

  rollbackProviderCandidate(
    transactionId: string
  ): Promise<{ rolled_back: boolean; rollback_error?: string | null }> {
    return this.command('rollback_provider_candidate', { transaction_id: transactionId }, 60_000)
  }

  deleteProvider(
    providerId: string
  ): Promise<{ deleted: boolean; default_provider?: ProviderInfo | null }> {
    return this.command('delete_provider', { provider_id: providerId }, 60_000)
  }

  oauthLogin(provider: 'chatgpt' | 'claude'): Promise<{ signed_in: boolean }> {
    return this.command('oauth_login', { provider }, 300_000)
  }

  cancelOAuthLogin(provider: 'chatgpt' | 'claude'): Promise<{ cancelled: boolean }> {
    return this.command('cancel_oauth', { provider })
  }

  oauthLogout(provider: 'chatgpt' | 'claude'): Promise<{ signed_in: boolean }> {
    return this.command('oauth_logout', { provider })
  }

  authStatus(provider: 'chatgpt' | 'claude'): Promise<{ signed_in: boolean }> {
    return this.command('auth_status', { provider })
  }

  configure(): Promise<{ configured: boolean; model?: string; error?: string }> {
    return this.command('configure', {}, 60_000)
  }

  readFile(path: string): Promise<{ content: string }> {
    return this.command('read_file', { path })
  }

  writeFile(
    path: string,
    content: string
  ): Promise<{ saved: boolean; version_id?: string | null; diff_text?: string | null }> {
    return this.command('write_file', { path, content })
  }

  listAutomations(): Promise<{ automations: CollieAutomation[] }> {
    return this.command('list_automations')
  }

  toggleAutomation(automationId: string, enabled: boolean): Promise<{ toggled: boolean }> {
    return this.command('toggle_automation', { automation_id: automationId, enabled })
  }

  createAutomation(
    description: string,
    name?: string,
    timezone?: string
  ): Promise<{ automation: CollieAutomation }> {
    return this.command('create_automation', { description, name, timezone })
  }

  updateAutomation(
    automationId: string,
    description: string,
    name?: string,
    timezone?: string
  ): Promise<{ automation: CollieAutomation }> {
    return this.command('update_automation', { automation_id: automationId, description, name, timezone })
  }

  deleteAutomation(automationId: string): Promise<{ deleted: boolean }> {
    return this.command('delete_automation', { automation_id: automationId })
  }

  listRoutines(): Promise<{ routines: CollieAutomation[] }> {
    return this.command('list_routines')
  }

  createRoutineFromPlan(
    planId: string,
    version: number,
    planHash: string,
    scheduleDescription: string,
    timezone: string
  ): Promise<{ routine: CollieAutomation }> {
    return this.command('create_routine', {
      plan_id: planId,
      version,
      plan_hash: planHash,
      schedule_description: scheduleDescription,
      timezone
    })
  }

  pauseRoutine(routineId: string): Promise<{ routine: CollieAutomation }> {
    return this.command('pause_routine', { routine_id: routineId })
  }

  resumeRoutine(routineId: string): Promise<{ routine: CollieAutomation }> {
    return this.command('resume_routine', { routine_id: routineId })
  }

  runRoutineNow(routineId: string): Promise<{ run: CollieRun }> {
    return this.command('run_routine_now', { routine_id: routineId })
  }

  listRoutineRuns(routineId: string): Promise<{ runs: CollieRun[] }> {
    return this.command('list_routine_runs', { routine_id: routineId })
  }

  retryRoutineRun(runId: string): Promise<{ run: CollieRun }> {
    return this.command('retry_routine_run', { run_id: runId })
  }

  listPendingApprovals(): Promise<{ approvals: ApprovalRequest[] }> {
    return this.command('list_pending_approvals')
  }

  resolveApproval(
    approvalId: string,
    resolution: 'allow_once' | 'allow_run' | 'allow_scope' | 'reject',
    scopeType?: string,
    scopeValue?: string
  ): Promise<{ approval: ApprovalRequest }> {
    return this.command('resolve_approval', {
      approval_id: approvalId,
      resolution,
      scope_type: scopeType,
      scope_value: scopeValue
    })
  }

  listApprovalRules(): Promise<{ rules: ApprovalRule[] }> {
    return this.command('list_approval_rules')
  }

  deleteApprovalRule(ruleId: string): Promise<{ deleted: boolean }> {
    return this.command('delete_approval_rule', { rule_id: ruleId })
  }

  approveAllForRun(runId: string): Promise<{ rule: ApprovalRule }> {
    return this.command('approve_all_for_run', { run_id: runId })
  }

  searchMessages(query: string): Promise<{ results: Array<CollieMessage> }> {
    return this.command('search_messages', { query })
  }

  exportData(): Promise<{ path: string }> {
    return this.command('export_data', {}, 60_000)
  }

  clearAllData(): Promise<ClearAllDataResult> {
    return this.command('clear_all_data', { confirm: true })
  }

  listSubagents(): Promise<{ subagents: Subagent[]; starters: SubagentStarter[] }> {
    return this.command('list_subagents')
  }

  createSubagent(
    name: string,
    description: string,
    systemPrompt?: string,
    executionPosture: 'read_only' | 'inherit' = 'read_only'
  ): Promise<{ subagent: Subagent; prompt_written_by_collie: boolean }> {
    // Collie may ask the model to write the prompt — allow a slow round trip.
    return this.command(
      'create_subagent',
      {
        name,
        description,
        system_prompt: systemPrompt ?? '',
        execution_posture: executionPosture
      },
      120_000
    )
  }

  updateSubagent(
    subagentId: string,
    updates: {
      name?: string
      description?: string
      system_prompt?: string
      execution_posture?: 'read_only' | 'inherit'
    }
  ): Promise<{ subagent: Subagent }> {
    return this.command('update_subagent', { subagent_id: subagentId, ...updates })
  }

  deleteSubagent(subagentId: string): Promise<{ deleted: boolean }> {
    return this.command('delete_subagent', { subagent_id: subagentId })
  }

  cancelSubagent(conversationId: string): Promise<{ cancelled: number }> {
    return this.command('cancel_subagent', { conversation_id: conversationId })
  }

  listSkills(): Promise<{ skills: CollieSkill[] }> {
    return this.command('list_skills')
  }

  listCommands(): Promise<CommandCatalog> {
    return this.command('list_commands')
  }

  getSkill(name: string): Promise<{ skill: CollieSkill }> {
    return this.command('get_skill', { name })
  }

  listServices(): Promise<{ services: ServiceInfo[] }> {
    return this.command('list_services')
  }

  connectService(
    serviceId: string,
    credentials?: Record<string, string>
  ): Promise<{ service_id: string; status: string; reconfigured?: boolean }> {
    // OAuth services block on a browser round trip — give them 5 minutes.
    return this.command('connect_service', { service_id: serviceId, credentials }, 300_000)
  }

  disconnectService(serviceId: string): Promise<{ service_id: string; status: string }> {
    return this.command('disconnect_service', { service_id: serviceId })
  }

  listConnectorCatalog(): Promise<{ connectors: ConnectorCatalogItem[] }> {
    return this.command('list_connector_catalog')
  }

  listConnectorConnections(): Promise<{ connections: ConnectorConnection[] }> {
    return this.command('list_connector_connections')
  }

  getConnector(connectionId: string): Promise<{ connection: ConnectorConnection }> {
    return this.command('get_connector', { connection_id: connectionId })
  }

  beginConnectorAuth(
    providerId: string,
    replaceConnectionId?: string
  ): Promise<{
    provider_id: string
    connection_id: string
    status: string
    reconfigured?: boolean
    flow_id?: string
  }> {
    return this.command(
      'begin_connector_auth',
      {
        provider_id: providerId,
        origin: 'connectors_ui',
        replace_connection_id: replaceConnectionId
      },
      300_000
    )
  }

  cancelConnectorAuth(
    connectionId: string
  ): Promise<{ connection_id: string; cancelled: boolean }> {
    return this.command('cancel_connector_auth', { connection_id: connectionId })
  }

  testConnector(connectionId: string): Promise<{ connection: ConnectorConnection }> {
    return this.command('test_connector', { connection_id: connectionId }, 120_000)
  }

  updateConnector(
    connectionId: string,
    patch: {
      display_name?: string
      enabled_capabilities?: string[]
      approval_preference?: string
    }
  ): Promise<{ connection: ConnectorConnection }> {
    return this.command('update_connector', { connection_id: connectionId, ...patch })
  }

  removeConnector(
    connectionId: string
  ): Promise<{ connection_id: string; status: string; reconfigured?: boolean }> {
    return this.command('remove_connector', {
      connection_id: connectionId,
      origin: 'connectors_ui'
    })
  }

  listConnectorTools(connectionId: string): Promise<{ tools: Array<Record<string, unknown>> }> {
    return this.command('list_connector_tools', { connection_id: connectionId })
  }

  getMessengers(): Promise<{ messengers: MessengerInfo[] }> {
    return this.command('get_messengers')
  }

  setMessenger(
    messenger: string,
    updates: { enabled?: boolean; deliver_automations?: boolean }
  ): Promise<{ messengers: MessengerInfo[] }> {
    return this.command('set_messenger', { messenger, ...updates }, 60_000)
  }

  setMessengerSecret(messenger: string, key: string, value: string): Promise<unknown> {
    return this.command('set_messenger_secret', { messenger, key, value })
  }

  approvePairing(
    code: string
  ): Promise<{ approved: boolean; messenger: string; confirmed: boolean }> {
    return this.command('approve_pairing', { code })
  }

  denyPairing(code: string): Promise<{ denied: boolean }> {
    return this.command('deny_pairing', { code })
  }

  revokeMessengerSender(messenger: string, senderId: string): Promise<{ revoked: boolean }> {
    return this.command('revoke_messenger_sender', { messenger, sender_id: senderId })
  }
}

export const collieClient = new CollieClient(3818)
