// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ColliePortraitFrame from './ColliePortraitFrame'

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>
let frames: Map<number, FrameRequestCallback>
let images: Array<{ onload: (() => void) | null }>
let draw: ReturnType<typeof vi.fn>
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  Object.defineProperty(document, 'hidden', { configurable: true, value: false })
  frames = new Map()
  images = []
  let id = 0
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { frames.set(++id, cb); return id })
  vi.stubGlobal('cancelAnimationFrame', (key: number) => frames.delete(key))
  vi.stubGlobal('Image', class {
    onload = null
    onerror = null
    complete = true
    naturalWidth = 384
    naturalHeight = 384
    constructor() { images.push(this) }
  })
  draw = vi.fn()
  const ctx = new Proxy({ drawImage: draw, createRadialGradient: () => ({ addColorStop: vi.fn() }), getTransform: () => ({ a: 1 }), getImageData: () => ({ data: new Uint8ClampedArray(72 * 72 * 4) }) }, {
    get: (obj, key) => key in obj ? obj[key as keyof typeof obj] : vi.fn()
  })
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as unknown as CanvasRenderingContext2D)
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
function render(reducedMotion = false): void {
  act(() => root.render(<ColliePortraitFrame state="idle" reducedMotion={reducedMotion} fallbackSrc="idle.webp" />))
  act(() => images.forEach(image => image.onload?.()))
}
it('replaces the fallback after a successful canvas draw, leaving one dog', () => {
  render()
  act(() => { const batch = [...frames.values()]; frames.clear(); batch.forEach(cb => cb(16)) })
  expect(draw).toHaveBeenCalled()
  expect(host.querySelector('img')?.hidden ?? true).toBe(true)
})
it('stops JavaScript animation while the document is hidden and resumes one loop', () => {
  render()
  Object.defineProperty(document, 'hidden', { configurable: true, value: true })
  act(() => document.dispatchEvent(new Event('visibilitychange')))
  expect(frames.size).toBe(0)
  Object.defineProperty(document, 'hidden', { configurable: true, value: false })
  act(() => document.dispatchEvent(new Event('visibilitychange')))
  expect(frames.size).toBe(1)
})
it('draws a single reduced-motion frame without scheduling animation', () => {
  render(true)
  expect(draw).toHaveBeenCalled()
  expect(frames.size).toBe(0)
})
it('shows the fallback on context loss and restores a single canvas on recovery', () => {
  render()
  const canvas = host.querySelector('canvas')!
  act(() => canvas.dispatchEvent(new Event('contextlost', { cancelable: true })))
  expect(host.querySelector('img')!.hidden).toBe(false)
  expect(canvas.hidden).toBe(true)
  expect(frames.size).toBe(0)
  act(() => canvas.dispatchEvent(new Event('contextrestored')))
  expect(frames.size).toBe(1)
  act(() => { const batch = [...frames.values()]; frames.clear(); batch.forEach(cb => cb(16)) })
  expect(host.querySelector('img')!.hidden).toBe(true)
  expect(canvas.hidden).toBe(false)
})
it('keeps the fallback when the texture fails to load', () => {
  act(() => root.render(<ColliePortraitFrame state="idle" reducedMotion={false} fallbackSrc="missing.webp" />))
  act(() => (images[0] as unknown as { onerror: () => void }).onerror())
  expect(host.querySelector('img')!.hidden).toBe(false)
  expect(host.querySelector('canvas')!.hidden).toBe(true)
  expect(frames.size).toBe(0)
})
