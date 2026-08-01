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
