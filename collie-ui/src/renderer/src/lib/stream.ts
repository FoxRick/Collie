export function mergeStreamDelta(current: string, incoming: string): string {
  const delta = incoming.replace(/\u0000/g, '')
  if (!delta) return current
  if (!current) return delta

  // Some providers recover a stream by replaying the complete answer so far.
  if (delta.startsWith(current)) return delta

  // Ignore a meaningful chunk that was replayed verbatim.
  if (delta.length >= 8 && current.endsWith(delta)) return current

  // Join recovered chunks without repeating their shared boundary.
  const maxOverlap = Math.min(256, current.length, delta.length)
  for (let size = maxOverlap; size >= 8; size -= 1) {
    if (current.endsWith(delta.slice(0, size))) {
      return current + delta.slice(size)
    }
  }

  return current + delta
}

/**
 * Whether a delivered message only covers the tail of the accumulated stream.
 *
 * A mid-turn steer supersedes the in-flight answer: the runner streams it,
 * then delivers a follow-up response whose text is a suffix of everything the
 * UI accumulated (e.g. "kanban answer + steer answer" vs the steer answer).
 * The superseded part was already delivered as its own message, so the new
 * bubble must reveal from scratch instead of rewinding the old text.
 */
export function shouldResetStreamDisplay(accumulated: string, delivered: string): boolean {
  if (!accumulated || !delivered) return false
  if (accumulated.length <= delivered.length) return false
  return accumulated.endsWith(delivered)
}

export function visibleStreamText(content: string): string {
  let visible = content.replace(/\u0000/g, '')

  const blockTags = ['think', 'thinking', 'thought', 'reasoning']
  const blockPattern = new RegExp(
    `<(${blockTags.join('|')})>[\\s\\S]*?<\\/\\1>`,
    'gi'
  )
  visible = visible.replace(blockPattern, '')

  const unclosedPattern = new RegExp(
    `<(${blockTags.join('|')})>[\\s\\S]*$`,
    'i'
  )
  visible = visible.replace(unclosedPattern, '')

  const orphanPattern = new RegExp(
    `^\\s*<\\/?(${blockTags.join('|')})>\\s*`,
    'i'
  )
  visible = visible.replace(orphanPattern, '')

  const bracketBlockPattern = /\[(?:think|thinking|thought|reasoning)\][\s\S]*?\[\/(?:think|thinking|thought|reasoning)\]/gi
  visible = visible.replace(bracketBlockPattern, '')

  const bracketUnclosedPattern = /\[(?:think|thinking|thought|reasoning)\][\s\S]*$/i
  visible = visible.replace(bracketUnclosedPattern, '')

  const codeBlockThinking = /```thinking[\s\S]*?```/gi
  visible = visible.replace(codeBlockThinking, '')
  const unclosedCodeThinking = /```thinking[\s\S]*$/i
  visible = visible.replace(unclosedCodeThinking, '')

  visible = visible.replace(/^\s*<\|?channel\|?>\s*/i, '')

  const tags = [...blockTags]
  const tagFragments = tags.flatMap((t) => {
    const frags: string[] = []
    for (let i = 1; i <= t.length; i++) {
      frags.push(t.slice(0, i))
    }
    return frags
  })
  const openFragmentPattern = new RegExp(
    `(?:<|\\[)(${tagFragments.join('|')})>?\\]?$`,
    'i'
  )
  visible = visible.replace(openFragmentPattern, '')
  const closeFragmentPattern = new RegExp(
    `<\\/(${tagFragments.join('|')})>?$`,
    'i'
  )
  visible = visible.replace(closeFragmentPattern, '')

  return visible
}

function isEscaped(content: string, index: number): boolean {
  let slashes = 0
  for (let cursor = index - 1; cursor >= 0 && content[cursor] === '\\'; cursor -= 1) slashes += 1
  return slashes % 2 === 1
}

function healTrailingLink(content: string, protectedIndexes: Set<number>): string {
  const match = /(!?)\[([^\]\n]*)(?:\]\(([^)\n]*))?$/.exec(content)
  if (!match || protectedIndexes.has(match.index) || isEscaped(content, match.index)) return content
  return content.slice(0, match.index) + match[2]
}

/**
 * Produce renderable live Markdown without changing the paced source text.
 * Incomplete constructs are healed only for ReactMarkdown: delimiters are
 * closed invisibly and unfinished links temporarily render as their label.
 */
export function stableMarkdownStreamText(content: string): string {
  const visible = visibleStreamText(content)
  const protectedIndexes = new Set<number>()
  const delimiters: string[] = []
  let fence = ''
  let inlineCode = ''

  for (let index = 0; index < visible.length;) {
    if (isEscaped(visible, index)) {
      const escapedCharacter = visible[index]
      index += 1
      while (visible[index] === escapedCharacter) index += 1
      continue
    }

    if (fence) {
      protectedIndexes.add(index)
      if (visible.startsWith(fence, index)) {
        for (let offset = 0; offset < fence.length; offset += 1) protectedIndexes.add(index + offset)
        index += fence.length
        fence = ''
      } else {
        index += 1
      }
      continue
    }

    if (inlineCode) {
      protectedIndexes.add(index)
      if (visible.startsWith(inlineCode, index)) {
        for (let offset = 0; offset < inlineCode.length; offset += 1) protectedIndexes.add(index + offset)
        index += inlineCode.length
        inlineCode = ''
      } else {
        index += 1
      }
      continue
    }

    if (visible.startsWith('```', index)) {
      fence = '```'
      protectedIndexes.add(index)
      protectedIndexes.add(index + 1)
      protectedIndexes.add(index + 2)
      index += 3
      continue
    }

    if (visible[index] === '`') {
      let run = 1
      while (visible[index + run] === '`') run += 1
      inlineCode = '`'.repeat(run)
      for (let offset = 0; offset < run; offset += 1) protectedIndexes.add(index + offset)
      index += run
      continue
    }

    const marker = ['**', '__', '~~', '*', '_'].find((value) => visible.startsWith(value, index))
    if (!marker) {
      index += 1
      continue
    }

    const before = visible[index - 1] ?? ''
    const after = visible[index + marker.length] ?? ''
    const intrawordUnderscore = marker.includes('_') && /\w/.test(before) && /\w/.test(after)
    const listBullet = (marker === '*' || marker === '_') && (!before || before === '\n') && /\s/.test(after)
    if (intrawordUnderscore || listBullet) {
      index += marker.length
      continue
    }

    const canOpen = Boolean(after) && !/\s/.test(after)
    const canClose = Boolean(before) && !/\s/.test(before)
    if (delimiters[delimiters.length - 1] === marker && canClose) delimiters.pop()
    else if (canOpen) delimiters.push(marker)
    index += marker.length
  }

  const healed = healTrailingLink(visible, protectedIndexes)
  const codeClosure = fence ? `\n${fence}` : inlineCode
  return healed + [...delimiters].reverse().join('') + codeClosure
}

/** Reveal a bounded amount per paint while never ending on unstable Markdown. */
export function nextStreamReveal(
  displayed: string,
  content: string,
  maxCharacters?: number
): string {
  const target = visibleStreamText(content)
  if (target === displayed) return target
  let shared = 0
  const sharedLimit = Math.min(displayed.length, target.length)
  while (shared < sharedLimit && displayed[shared] === target[shared]) shared += 1
  const start = shared === displayed.length ? displayed.length : shared
  // Catch up after provider bursts instead of adding seconds of fake latency.
  // Explicit limits remain useful for callers that need a fixed reveal size.
  const step = maxCharacters ?? Math.max(18, Math.ceil((target.length - start) / 4))
  let end = start + Math.max(1, step)
  // Never split a UTF-16 surrogate pair (emoji and many non-Latin characters).
  if (end < target.length && /[\uD800-\uDBFF]/.test(target[end - 1])) end += 1
  return target.slice(0, end)
}
