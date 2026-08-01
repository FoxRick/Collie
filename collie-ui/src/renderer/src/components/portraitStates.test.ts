import { describe, expect, it } from 'vitest'
import { gazeEnabledForState, portraitStateForEngine } from './portraitStates'

describe('portrait state mapping', () => {
  it.each([
    ['startup', 'thinking'],
    ['planning', 'thinking'],
    ['processing', 'thinking'],
    ['searching', 'searching'],
    ['fetching', 'searching'],
    ['mapping', 'searching'],
    ['generating', 'writing'],
    ['summarizing', 'writing'],
    ['buddy', 'listening']
  ])('maps %s to %s', (engine, portrait) => {
    expect(portraitStateForEngine(engine)).toBe(portrait)
  })

  it('leaves terminal states to the transition timer', () => {
    expect(portraitStateForEngine('done')).toBeNull()
    expect(portraitStateForEngine('error')).toBeNull()
    expect(portraitStateForEngine('idle')).toBeNull()
  })

  it('disables gaze for expressions that own their eye direction', () => {
    expect(gazeEnabledForState('searching')).toBe(false)
    expect(gazeEnabledForState('sleepy')).toBe(false)
    expect(gazeEnabledForState('concerned')).toBe(false)
    expect(gazeEnabledForState('idle')).toBe(true)
  })
})
