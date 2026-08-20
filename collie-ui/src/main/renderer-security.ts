export interface RendererFrameLike {
  url: string
}

export interface MediaPermissionDetailsLike {
  isMainFrame: boolean
  requestingUrl: string
  mediaTypes?: Array<'video' | 'audio'>
}

/**
 * Renderer trust is deliberately exact. URL parsing normalizes harmless syntax
 * such as an omitted trailing slash while keeping origin, path, query, and hash
 * comparisons strict.
 */
export function isTrustedRendererUrl(candidate: string, trustedRendererUrl: string): boolean {
  try {
    const candidateUrl = new URL(candidate)
    const trustedUrl = new URL(trustedRendererUrl)
    if (
      candidateUrl.username ||
      candidateUrl.password ||
      trustedUrl.username ||
      trustedUrl.password
    ) {
      return false
    }
    return candidateUrl.href === trustedUrl.href
  } catch {
    return false
  }
}

/**
 * Only https URLs without embedded credentials may leave the app.
 *
 * Shared by the new-window handler and the `collie:open-external` IPC so
 * both external-open paths enforce the same policy: https only, no
 * `http://` fallback, no credentials smuggled in the authority.
 */
export function isSafeExternalUrl(raw: string): boolean {
  let parsed: URL
  try {
    parsed = new URL(raw)
  } catch {
    return false
  }
  if (parsed.protocol !== 'https:') return false
  if (parsed.username || parsed.password) return false
  return true
}

export function isTrustedIpcSender(
  senderFrame: RendererFrameLike | null,
  mainFrame: RendererFrameLike | null,
  trustedRendererUrl: string
): boolean {
  return (
    senderFrame !== null &&
    mainFrame !== null &&
    senderFrame === mainFrame &&
    isTrustedRendererUrl(senderFrame.url, trustedRendererUrl)
  )
}

export function guardIpcHandler<TEvent, TArgs extends unknown[], TResult>(
  isTrusted: (event: TEvent) => boolean,
  handler: (...args: TArgs) => TResult
): (event: TEvent, ...args: TArgs) => TResult {
  return (event: TEvent, ...args: TArgs): TResult => {
    if (!isTrusted(event)) {
      throw new Error('Rejected: call came from an unexpected frame or renderer URL.')
    }
    return handler(...args)
  }
}

export function shouldAllowAudioPermission(
  requestingWebContents: unknown,
  mainWebContents: unknown,
  permission: string,
  details: MediaPermissionDetailsLike,
  trustedRendererUrl: string
): boolean {
  return (
    requestingWebContents === mainWebContents &&
    permission === 'media' &&
    details.isMainFrame &&
    isTrustedRendererUrl(details.requestingUrl, trustedRendererUrl) &&
    Array.isArray(details.mediaTypes) &&
    details.mediaTypes.length > 0 &&
    details.mediaTypes.every((mediaType) => mediaType === 'audio')
  )
}
