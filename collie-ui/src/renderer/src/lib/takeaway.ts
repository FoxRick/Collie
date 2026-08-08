/**
 * Takeaway digest — a small UI-side "closer" for long assistant answers.
 *
 * The streamed markdown stays the source of truth; this module only derives
 * a compact summary from the *final* text so the card can never lose
 * information. Short answers get no card at all.
 *
 * Extraction rules (deliberately conservative — junk is dropped, never shown):
 *   - headings, list items, bold-led lines and blockquotes → key points
 *   - "Key: value" lines, "**Key**: value" lines and 2-cell table rows → chips
 *   - points containing 3+ numbers get a tiny sparkline series
 */

export interface TakeawayPoint {
  text: string
  /** Numeric series found in the point, kept for a tiny sparkline. */
  numbers?: number[]
}

export interface TakeawayChip {
  key: string
  value: string
}

export interface TakeawayDigest {
  points: TakeawayPoint[]
  chips: TakeawayChip[]
}

/** Answers shorter than this are too brief to need a recap. */
export const MIN_TAKEAWAY_ANSWER_LENGTH = 400
export const MAX_TAKEAWAY_POINTS = 5
export const MAX_TAKEAWAY_CHIPS = 4
const MAX_POINT_LENGTH = 140
const MAX_CHIP_VALUE_LENGTH = 80

function stripMarkdown(text: string): string {
  return text
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/~~([^~]+)~~/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function extractNumbers(text: string): number[] {
  const matches = text.match(/\d+(?:\.\d+)?/g)
  if (!matches) return []
  const numbers = matches.map((raw) => Number.parseFloat(raw)).filter(Number.isFinite)
  return numbers.slice(0, 8)
}

type Candidate = { kind: 'point'; text: string } | { kind: 'chip'; key: string; value: string }

/** Classify one markdown line into a point, a chip, or nothing. */
function classifyLine(line: string): Candidate | null {
  const trimmed = line.trim()
  if (!trimmed) return null
  if (/^(?:[-*_]{3,}|={3,})$/.test(trimmed)) return null

  const heading = /^#{1,6}\s+(.+)$/.exec(trimmed)
  if (heading) return { kind: 'point', text: heading[1] }

  const quote = /^>\s?(.+)$/.exec(trimmed)
  if (quote) return { kind: 'point', text: quote[1] }

  const bullet = /^[-*+]\s+(.+)$/.exec(trimmed)
  if (bullet) return { kind: 'point', text: bullet[1] }

  const numbered = /^\d+[.)]\s+(.+)$/.exec(trimmed)
  if (numbered) return { kind: 'point', text: numbered[1] }

  if (trimmed.startsWith('|')) {
    const cells = trimmed.split('|').map((cell) => cell.trim()).filter(Boolean)
    if (cells.length === 2 && cells[0] && cells[1]) {
      return { kind: 'chip', key: cells[0], value: cells[1] }
    }
    return null
  }

  const boldValue = /^\*\*([^*]+)\*\*\s*[:：]\s*(.+)$/.exec(trimmed)
  if (boldValue) return { kind: 'chip', key: boldValue[1], value: boldValue[2] }

  const keyValue = /^([A-Za-z][^:：\n]{1,48}?)\s*[:：]\s*(.+)$/.exec(trimmed)
  if (keyValue && trimmed.length <= 120) {
    return { kind: 'chip', key: keyValue[1], value: keyValue[2] }
  }

  const boldLead = /^\*\*([^*]+)\*\*/.exec(trimmed)
  if (boldLead) return { kind: 'point', text: trimmed }

  return null
}

function dedupeKey(text: string): string {
  return text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim()
}

/**
 * True when the line at `index` is mid-paragraph: the next non-blank line
 * continues the sentence (starts lowercase), so a "Key: value" match here is
 * probably wrapped prose, not a standalone chip.
 */
function continuesProse(lines: string[], index: number): boolean {
  for (let cursor = index + 1; cursor < lines.length; cursor += 1) {
    const next = lines[cursor].trim()
    if (!next) continue
    return /^[a-z]/.test(next)
  }
  return false
}

/**
 * Build a takeaway digest from the final markdown of an assistant answer.
 * Returns null when the answer is too short or has no extractable structure,
 * so short/plain answers render no card at all.
 */
export function buildTakeawayDigest(markdown: string): TakeawayDigest | null {
  if (!markdown || markdown.trim().length < MIN_TAKEAWAY_ANSWER_LENGTH) return null

  const points: TakeawayPoint[] = []
  const chips: TakeawayChip[] = []
  const seenPoints = new Set<string>()
  const seenChips = new Set<string>()

  const lines = markdown.split('\n')
  let inFence = false
  for (let index = 0; index < lines.length; index += 1) {
    const trimmed = lines[index].trim()
    if (/^```/.test(trimmed)) {
      inFence = !inFence
      continue
    }
    if (inFence) continue

    const candidate = classifyLine(trimmed)
    if (!candidate) continue

    if (candidate.kind === 'chip') {
      if (continuesProse(lines, index)) continue
      const key = stripMarkdown(candidate.key)
      const value = stripMarkdown(candidate.value)
      if (key.length < 2 || key.length > 40 || value.length < 1 || value.length > MAX_CHIP_VALUE_LENGTH) continue
      const dedupe = dedupeKey(key)
      if (seenChips.has(dedupe)) continue
      seenChips.add(dedupe)
      chips.push({ key, value })
      if (chips.length === MAX_TAKEAWAY_CHIPS) continue
    } else {
      if (points.length >= MAX_TAKEAWAY_POINTS) continue
      const clean = stripMarkdown(candidate.text)
      if (!clean) continue
      const dedupe = dedupeKey(clean)
      if (dedupe.length < 3 || seenPoints.has(dedupe)) continue
      seenPoints.add(dedupe)
      const truncated = clean.length > MAX_POINT_LENGTH ? `${clean.slice(0, MAX_POINT_LENGTH - 3).trimEnd()}…` : clean
      const numbers = extractNumbers(clean)
      points.push(numbers.length >= 3
        ? { text: truncated, numbers }
        : { text: truncated })
    }
  }

  if (points.length < 2) return null
  if (points.length < 3 && chips.length < 2) return null

  return { points, chips }
}
