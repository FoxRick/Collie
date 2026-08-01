import { StringDecoder } from 'string_decoder'

export type CoreProtocolMessage =
  | { kind: 'ready'; port: number | null }
  | { kind: 'fatal'; error: string }
  | { kind: 'log' }

export class LineBuffer {
  private readonly decoder = new StringDecoder('utf8')
  private remainder = ''

  push(chunk: Buffer | string): string[] {
    this.remainder += typeof chunk === 'string' ? chunk : this.decoder.write(chunk)
    const lines: string[] = []
    let newline = this.remainder.indexOf('\n')
    while (newline !== -1) {
      let line = this.remainder.slice(0, newline)
      if (line.endsWith('\r')) line = line.slice(0, -1)
      lines.push(line)
      this.remainder = this.remainder.slice(newline + 1)
      newline = this.remainder.indexOf('\n')
    }
    return lines
  }

  flush(): string[] {
    this.remainder += this.decoder.end()
    if (!this.remainder) return []
    const trailingLine = this.remainder
    this.remainder = ''
    return [trailingLine]
  }
}

export function parseCoreProtocolLine(line: string): CoreProtocolMessage {
  const trimmed = line.trim()
  if (trimmed.startsWith('COLLIE_READY')) {
    try {
      const info = JSON.parse(trimmed.slice('COLLIE_READY'.length).trim()) as { port?: unknown }
      const port = typeof info.port === 'number' && info.port > 0 ? info.port : null
      return { kind: 'ready', port }
    } catch {
      return { kind: 'ready', port: null }
    }
  }
  if (trimmed.startsWith('COLLIE_FATAL')) {
    return {
      kind: 'fatal',
      error: trimmed.slice('COLLIE_FATAL'.length).trim() || 'Core failed to boot.'
    }
  }
  return { kind: 'log' }
}

/**
 * Tracks abnormal exits in a rolling window. A READY marker also starts a
 * healthy-operation window, but never restores crash budget by itself.
 */
export class RestartBudget {
  private abnormalExits: number[] = []
  private healthySince: number | null = null

  constructor(
    readonly maximumAbnormalExits = 3,
    readonly healthyWindowMs = 5 * 60 * 1000
  ) {}

  markReady(now: number): void {
    if (this.healthySince === null) this.healthySince = now
  }

  decayAfterSustainedHealth(now: number): boolean {
    if (this.healthySince === null || now - this.healthySince < this.healthyWindowMs) {
      return false
    }
    this.abnormalExits = []
    return true
  }

  recordExit(code: number | null, intentional: boolean, now: number): boolean {
    this.healthySince = null
    if (intentional || code === 0) return false
    const cutoff = now - this.healthyWindowMs
    this.abnormalExits = this.abnormalExits.filter((exitAt) => exitAt > cutoff)
    this.abnormalExits.push(now)
    return this.abnormalExits.length < this.maximumAbnormalExits
  }

  get abnormalExitCount(): number {
    return this.abnormalExits.length
  }
}

export function coreExitError(code: number | null, reportedError: string): string {
  if (reportedError) return reportedError
  return code === null ? 'Core exited unexpectedly.' : `Core exited with code ${code}`
}
