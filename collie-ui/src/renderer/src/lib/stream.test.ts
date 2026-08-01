import { describe, expect, it } from 'vitest'
import { mergeStreamDelta, visibleStreamText } from './stream'

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
