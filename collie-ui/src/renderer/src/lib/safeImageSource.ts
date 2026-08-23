const BASE64_RASTER_IMAGE = /^data:image\/(?:gif|jpe?g|png|webp);base64,/i
const SCHEME_OR_NETWORK_PATH = /^(?:[a-z][a-z\d+.-]*:|[\\/]{2})/i
const ASCII_CONTROL = /[\u0000-\u001f\u007f]/

/**
 * Restrict untrusted image metadata to renderer-local paths or supported
 * inline raster images. Scheme-bearing and network-path inputs could make the
 * renderer contact an attacker-controlled host when assigned to an img src.
 */
export function safeImageSource(source: unknown): string | null {
  if (typeof source !== 'string') return null

  const value = source.trim()
  if (!value || ASCII_CONTROL.test(value)) return null
  if (BASE64_RASTER_IMAGE.test(value)) return value
  if (SCHEME_OR_NETWORK_PATH.test(value)) return null
  return value
}
