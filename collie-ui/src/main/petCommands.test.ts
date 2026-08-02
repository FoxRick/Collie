import { describe, expect, it } from 'vitest'
import { isAllowedPetCommand } from './petCommands'

describe('desktop pet command allowlist', () => {
  it('accepts live v2 states and existing controls', () => {
    for (const command of [
      'idle', 'working', 'review', 'completion', 'error', 'walk', 'sleep', 'happy',
      'concerned', 'wave', 'hide', 'show', 'roam', 'stay', 'quit', 'size:1.25'
    ]) expect(isAllowedPetCommand(command)).toBe(true)
  })

  it('rejects arbitrary commands', () => {
    expect(isAllowedPetCommand('walk_right')).toBe(false)
    expect(isAllowedPetCommand('launch-calculator')).toBe(false)
  })
})
