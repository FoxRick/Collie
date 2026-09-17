export type ConnectorTransport = 'streamable_http' | 'sse'
export type ConnectorAuthStrategy = 'none' | 'token' | 'headers' | 'oauth'

export interface ConnectorFailure {
  code: string
  message: string
  recovery_action: string
  stage: string
  retryable: boolean
}

export interface ConnectorDefinitionInput {
  name?: string
  endpoint: string
  transport?: ConnectorTransport
  auth_strategy?: ConnectorAuthStrategy
  header_name?: string
  oauth_registration?: 'automatic' | 'preregistered'
  client_id?: string
  scopes?: string[]
  redirect_uri?: string
  resource?: string
  issuer?: string
  allow_private_network?: boolean
}

export interface ConnectorDefinitionPreview extends ConnectorDefinitionInput {
  name: string
  transport: ConnectorTransport
  auth_strategy: ConnectorAuthStrategy
  has_secret: boolean
  requires_secret: boolean
}

export interface ConnectorDefinitionValidation {
  valid: boolean
  preview: ConnectorDefinitionPreview | null
  warnings: string[]
  errors: ConnectorFailure[]
}

export interface ConnectorImportEntry {
  key: string
  name: string
  definition: ConnectorDefinitionInput
  preview: ConnectorDefinitionPreview
  /** Opaque, short-lived handle; the renderer cannot exchange it for the secret value. */
  secret_handle?: string
}

export interface ConnectorImportPreview {
  definitions: ConnectorImportEntry[]
  unsupported: Array<{ path: string; reason: string }>
  warnings: string[]
}

/** Secret input exists only for the duration of the protected main-process invocation. */
export type ConnectorSecretInput =
  | { token: string }
  | { headers: Record<string, string> }

export type ConnectorCredentialSubmission = {
  action: 'begin_auth'
  definition_id: string
  display_name?: string
  origin?: 'connectors_ui' | 'chat'
  secret?: ConnectorSecretInput
  import_secret_handle?: string
} | {
  action: 'reconnect'
  connection_id: string
  operation_id?: string
  operation_revision?: number
  origin?: 'connectors_ui' | 'chat'
  secret?: ConnectorSecretInput
}

export interface ConnectorOperationResult {
  definition_id?: string
  provider_id?: string
  connection_id?: string
  operation_id?: string
  operation_revision?: number
  auth_strategy?: ConnectorAuthStrategy
  requires_secret?: boolean
  status?: string
  failure?: ConnectorFailure | null
  reconfigured?: boolean
}

export interface ConnectorToolInfo {
  name: string
  description?: string
  input_schema?: Record<string, unknown>
  schema_hash?: string
  enabled?: boolean
  previously_enabled?: boolean
  review_status?: 'new' | 'changed' | 'reviewed'
  risk?: string
}

export interface ConnectorToolInspection {
  connection_id: string
  total: number
  tools: ConnectorToolInfo[]
}

export interface ConnectorImportFilePreview extends ConnectorImportPreview {
  file_name: string
}
