import { describe, expect, it, vi } from 'vitest'
import {
  mergeStreamDelta,
  nextStreamReveal,
  shouldResetStreamDisplay,
  stableMarkdownStreamText,
  visibleStreamText
} from './stream'

describe('mergeStreamDelta', () => {
  it('appends ordinary token deltas', () => {
    expect(mergeStreamDelta('Hello', ' there')).toBe('Hello there')
  })

  it('accepts cumulative provider snapshots', () => {
    expect(mergeStreamDelta('Hello', 'Hello there')).toBe('Hello there')
  })

  it('drops a replayed chunk after recovery', () => {
    expect(mergeStreamDelta('A useful answer', 'ful answer continues')).toBe('A useful answer continues')
  })

  it('removes null control characters', () => {
    expect(mergeStreamDelta('Hello', '\u0000 there')).toBe('Hello there')
  })
})

describe('shouldResetStreamDisplay', () => {
  it('resets when the delivered message is a proper suffix of the stream', () => {
    // Mid-turn steer: the superseded answer + follow-up answer were both
    // streamed; the delivered message only covers the follow-up.
    expect(
      shouldResetStreamDisplay(
        'The long superseded answer. The follow-up answer.',
        'The follow-up answer.'
      )
    ).toBe(true)
  })

  it('does not reset when the delivered message IS the whole stream', () => {
    expect(shouldResetStreamDisplay('A normal answer', 'A normal answer')).toBe(false)
  })

  it('does not reset for an empty accumulated stream', () => {
    expect(shouldResetStreamDisplay('', 'An answer')).toBe(false)
    expect(shouldResetStreamDisplay(undefined as unknown as string, 'An answer')).toBe(false)
  })

  it('does not reset when the message is not a suffix', () => {
    expect(shouldResetStreamDisplay('Answer one', 'Answer two')).toBe(false)
  })
})

describe('visibleStreamText', () => {
  it('does not expose inline model reasoning', () => {
    expect(visibleStreamText('<think>private chain</think>Useful answer')).toBe('Useful answer')
  })

  it('holds an unfinished reasoning block out of the UI', () => {
    expect(visibleStreamText('<thinking>still working')).toBe('')
  })

  it('removes an unfinished reasoning block after visible answer text', () => {
    expect(visibleStreamText('Useful answer<think>private continuation')).toBe('Useful answer')
  })

  it('removes split control tags until they are complete', () => {
    expect(visibleStreamText('Answer <thin')).toBe('Answer ')
  })

  it('strips reasoning blocks', () => {
    expect(visibleStreamText('<reasoning>internal logic</reasoning>Answer')).toBe('Answer')
  })

  it('strips bracket-style thinking blocks', () => {
    expect(visibleStreamText('[thinking]internal[/thinking]Answer')).toBe('Answer')
  })

  it('holds unclosed bracket-style blocks', () => {
    expect(visibleStreamText('Answer[think]still internal')).toBe('Answer')
  })

  it('strips code-block thinking', () => {
    expect(visibleStreamText('```thinking\ninternal\n```\nAnswer')).toBe('\nAnswer')
  })

  it('holds unclosed code-block thinking', () => {
    expect(visibleStreamText('```thinking\nstill internal stuff')).toBe('')
  })

  it('removes partial close tag fragments like </thi', () => {
    expect(visibleStreamText('Answer </thi')).toBe('Answer ')
  })
})

describe('stableMarkdownStreamText', () => {
  it('heals trailing emphasis without changing valid literal markers', () => {
    expect(stableMarkdownStreamText('This is **still arriving')).toBe('This is **still arriving**')
    expect(stableMarkdownStreamText('This is **ready** now')).toBe('This is **ready** now')
    expect(stableMarkdownStreamText('This is __also arriving')).toBe('This is __also arriving__')
    expect(stableMarkdownStreamText('This is _emphasis arriving')).toBe('This is _emphasis arriving_')
    expect(stableMarkdownStreamText('Keep snake_case visible')).toBe('Keep snake_case visible')
    expect(stableMarkdownStreamText('Keep a__b visible')).toBe('Keep a__b visible')
    expect(stableMarkdownStreamText('Escaped \\**marker stays')).toBe('Escaped \\**marker stays')
    expect(stableMarkdownStreamText('* list item')).toBe('* list item')
  })

  it('heals unfinished code and ignores marker text inside it', () => {
    expect(stableMarkdownStreamText('Run `npm **te')).toBe('Run `npm **te`')
    expect(stableMarkdownStreamText('Before\n```ts\nconst value = "**"')).toBe(
      'Before\n```ts\nconst value = "**"\n```'
    )
    expect(stableMarkdownStreamText('`**literal**`')).toBe('`**literal**`')
  })

  it('continues suppressing hidden reasoning', () => {
    expect(stableMarkdownStreamText('<think>private</think>Useful **answer')).toBe('Useful **answer**')
  })

  it('holds incomplete links and images until their target closes', () => {
    expect(stableMarkdownStreamText('See [the guide')).toBe('See the guide')
    expect(stableMarkdownStreamText('See [the guide](https://example.')).toBe('See the guide')
    expect(stableMarkdownStreamText('See [the guide](https://example.com)')).toBe(
      'See [the guide](https://example.com)'
    )
  })
})

describe('nextStreamReveal', () => {
  it('catches up with a large burst in under one second of display ticks', () => {
    const content = 'A large provider burst. '.repeat(400)
    let displayed = ''
    for (let frame = 0; frame < 30; frame += 1) displayed = nextStreamReveal(displayed, content)
    expect(displayed).toBe(content)
  })

  it('never cuts an emoji in half at the reveal boundary', () => {
    expect(nextStreamReveal('', 'Hi 🐶 there', 4)).toBe('Hi 🐶')
  })

  it('reveals ordinary text at a bounded cadence without losing content', () => {
    const content = 'A smooth complete response'
    let displayed = ''
    for (let frame = 0; frame < 20 && displayed !== content; frame += 1) {
      const next = nextStreamReveal(displayed, content, 4)
      expect(next.startsWith(displayed)).toBe(true)
      expect(next.length - displayed.length).toBeLessThanOrEqual(4)
      displayed = next
    }
    expect(displayed).toBe(content)
  })

  it('stays bounded through very long open code and link spans', () => {
    for (const content of [`\`code ${'x'.repeat(10_000)}`, `[label ${'x'.repeat(10_000)}`]) {
      const next = nextStreamReveal('', content, 17)
      expect(next.length).toBe(17)
      expect(stableMarkdownStreamText(next)).not.toMatch(/(^|[^\\])\[$/)
    }
  })

  it('paces rapid deltas to the exact terminal text and supports cancellation', () => {
    vi.useFakeTimers()
    let raw = mergeStreamDelta('', 'A rapid ')
    raw = mergeStreamDelta(raw, '**stream**')
    const terminal = 'A rapid **stream** with an exact ending.'
    let displayed = ''
    let committed = ''
    const timer = setInterval(() => {
      displayed = nextStreamReveal(displayed, terminal, 5)
      if (displayed === visibleStreamText(terminal)) committed = terminal
    }, 32)
    vi.advanceTimersByTime(32)
    expect(displayed.length).toBeLessThan(terminal.length)
    expect(committed).toBe('')
    vi.advanceTimersByTime(1_000)
    expect(committed).toBe(terminal)
    clearInterval(timer)

    displayed = ''
    const cancelled = setInterval(() => {
      displayed = nextStreamReveal(displayed, raw, 5)
    }, 32)
    vi.advanceTimersByTime(32)
    clearInterval(cancelled)
    const atSwitch = displayed
    vi.advanceTimersByTime(500)
    expect(displayed).toBe(atSwitch)
    vi.useRealTimers()
  })
})
