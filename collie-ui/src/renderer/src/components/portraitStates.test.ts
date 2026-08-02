import { describe, expect, it } from 'vitest'
import {
  FACE_ONLY_ASSET_MANIFEST,
  gazeEnabledForState,
  portraitFramesFor,
  portraitStateForEngine,
  supportsFaceOnlyDeepWork
} from './portraitStates'

describe('portrait state mapping', () => {
  it.each([
    ['startup', 'working'],
    ['planning', 'working'],
    ['processing', 'working'],
    ['searching', 'review'],
    ['fetching', 'review'],
    ['mapping', 'review'],
    ['generating', 'working'],
    ['summarizing', 'working'],
    ['buddy', 'review'],
    ['awaiting_approval', 'waiting']
  ])('maps %s to %s', (engine, portrait) => {
    expect(portraitStateForEngine(engine)).toBe(portrait)
  })

  it('leaves terminal states to the transition timer', () => {
    expect(portraitStateForEngine('done')).toBeNull()
    expect(portraitStateForEngine('error')).toBeNull()
    expect(portraitStateForEngine('idle')).toBeNull()
  })

  it('disables gaze for expressions that own their eye direction', () => {
    expect(gazeEnabledForState('review')).toBe(true)
    expect(gazeEnabledForState('sleepy')).toBe(false)
    expect(gazeEnabledForState('error')).toBe(false)
    expect(gazeEnabledForState('idle')).toBe(true)
  })

  it('enables deep work only through the dedicated face-only glasses strip', () => {
    expect(supportsFaceOnlyDeepWork()).toBe(true)
    expect(FACE_ONLY_ASSET_MANIFEST.deepWorkGlasses.frameCount).toBe(6)
    expect(portraitFramesFor('deep_work_glasses')).toHaveLength(6)
  })

  it('indexes the 16-way face-only pointer sheet in clockwise order', () => {
    expect(FACE_ONLY_ASSET_MANIFEST.pointerLook).toMatchObject({
      columns: 4,
      rows: 4,
      frameCount: 16
    })
    expect(portraitFramesFor('pointer_look', 12)[0]?.sourceIndex).toBe(12)
  })
})
