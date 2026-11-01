import { describe, expect, it } from 'vitest'
import { portraitStateForEngine } from './portraitStates'

describe('portrait state mapping', () => {
  it.each([
    ['startup', 'working'],
    ['planning', 'thinking'],
    ['processing', 'working'],
    ['searching', 'review'],
    ['fetching', 'review'],
    ['mapping', 'review'],
    ['generating', 'writing'],
    ['summarizing', 'writing'],
    ['buddy', 'delegating'],
    ['awaiting_approval', 'waiting']
  ])('maps %s to %s', (engine, portrait) => {
    expect(portraitStateForEngine(engine)).toBe(portrait)
  })

  it('leaves terminal states to the transition timer', () => {
    expect(portraitStateForEngine('done')).toBeNull()
    expect(portraitStateForEngine('error')).toBeNull()
    expect(portraitStateForEngine('idle')).toBeNull()
  })

})
