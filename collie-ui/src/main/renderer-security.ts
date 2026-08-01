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
