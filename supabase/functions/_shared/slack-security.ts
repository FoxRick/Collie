/** Raw-body verification and credential encryption. No client receives bot tokens. */
const encoder = new TextEncoder()
export function hex(bytes: ArrayBuffer): string {
  return Array.from(new Uint8Array(bytes), value => value.toString(16).padStart(2, '0')).join('')
}
export async function sha256(value: string): Promise<string> {
  return hex(await crypto.subtle.digest('SHA-256', encoder.encode(value)))
}
export function constantEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false
  let difference = 0
  for (let i = 0; i < left.length; i++) difference |= left.charCodeAt(i) ^ right.charCodeAt(i)
  return difference === 0
}
export async function verifySlack(
  body: string, timestamp: string | null, signature: string | null, secret: string,
  now = Date.now(),
): Promise<boolean> {
  if (!timestamp || !/^\d+$/.test(timestamp) || !signature || !/^v0=[a-f0-9]{64}$/.test(signature)) return false
  if (Math.abs(now / 1000 - Number(timestamp)) > 300) return false
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'])
  const expected = 'v0=' + hex(await crypto.subtle.sign('HMAC', key, encoder.encode(`v0:${timestamp}:${body}`)))
  return constantEqual(expected, signature)
}
function decode64(value: string): Uint8Array<ArrayBuffer> {
  return Uint8Array.from(atob(value), c => c.charCodeAt(0))
}
function encode64(value: Uint8Array): string {
  return btoa(String.fromCharCode(...value))
}
async function encryptionKey(secret: string): Promise<CryptoKey> {
  const bytes = decode64(secret)
  if (bytes.length !== 32) throw new Error('Invalid credential key configuration')
  return await crypto.subtle.importKey('raw', bytes, 'AES-GCM', false, ['encrypt', 'decrypt'])
}
export async function sealToken(token: string, secret: string, team: string): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12))
  const data = await crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData: encoder.encode(team) }, await encryptionKey(secret), encoder.encode(token))
  return `v1.${encode64(iv)}.${encode64(new Uint8Array(data))}`
}
export async function openToken(ciphertext: string, secret: string, team: string): Promise<string> {
  const [version, iv, data, extra] = ciphertext.split('.')
  if (version !== 'v1' || !iv || !data || extra) throw new Error('Invalid encrypted credential')
  return new TextDecoder().decode(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: decode64(iv), additionalData: encoder.encode(team) }, await encryptionKey(secret), decode64(data)))
}
