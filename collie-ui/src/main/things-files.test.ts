import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const testState = vi.hoisted(() => ({
  home: '',
  openPathResult: '',
  openPathCalls: [] as string[],
  showItemCalls: [] as string[],
  saveDialogResult: { canceled: true, filePath: '' }
}))

vi.mock('electron', () => ({
  dialog: {
    showSaveDialog: vi.fn(async () => testState.saveDialogResult)
  },
  shell: {
    openPath: vi.fn(async (path: string) => {
      testState.openPathCalls.push(path)
      return testState.openPathResult
    }),
    showItemInFolder: vi.fn((path: string) => {
      testState.showItemCalls.push(path)
    })
  }
}))

import {
  thingOpen,
  thingRead,
  thingSaveCopy,
  thingShowInFolder
} from './things-files'

function writeIndex(conversationId: string, records: unknown[]): void {
  const dir = join(testState.home, 'things')
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, `${conversationId}.json`), JSON.stringify({ things: records }), 'utf-8')
}

function record(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'th_a',
    title: 'Dog walk flyer',
    kind: 'document',
    path: '',
    size_bytes: 2048,
    created_at: 1_720_000_000,
    status: 'new',
    version: 1,
    ...overrides
  }
}

describe('things-files (trusted-ID boundary)', () => {
  let workspaceFile: string
  let projectDir: string
  let projectFile: string

  beforeEach(() => {
    testState.home = mkdtempSync(join(tmpdir(), 'collie-things-'))
    process.env.COLLIE_HOME = testState.home
    writeFileSync(join(testState.home, 'workspace.md'), '# Workspace thing', 'utf-8')
    workspaceFile = join(testState.home, 'workspace.md')
    // A deliverable in a user-approved project folder, OUTSIDE COLLIE_HOME.
    projectDir = mkdtempSync(join(tmpdir(), 'collie-project-'))
    projectFile = join(projectDir, 'project-notes.md')
    writeFileSync(projectFile, '# Project notes', 'utf-8')
    testState.openPathResult = ''
    testState.openPathCalls = []
    testState.showItemCalls = []
    testState.saveDialogResult = { canceled: true, filePath: '' }
  })

  afterEach(() => {
    delete process.env.COLLIE_HOME
    rmSync(testState.home, { recursive: true, force: true })
    if (projectDir) rmSync(projectDir, { recursive: true, force: true })
  })

  it('opens the registered path, not a renderer-supplied one', async () => {
    writeIndex('conv-1', [record({ id: 'th_a', path: workspaceFile })])
    // A forged path argument is ignored — the id decides.
    const result = await thingOpen('conv-1', 'th_a')
    expect(result).toBe('')
    expect(testState.openPathCalls).toEqual([workspaceFile])
  })

  it('surfaces OS open errors instead of swallowing them', async () => {
    writeIndex('conv-1', [record({ id: 'th_a', path: workspaceFile })])
    testState.openPathResult = 'The file is in use'
    const result = await thingOpen('conv-1', 'th_a')
    expect(result).toBe('The file is in use')
  })

  it('rejects unknown things and unknown conversations', async () => {
    writeIndex('conv-1', [record({ id: 'th_a', path: workspaceFile })])
    await expect(thingOpen('conv-1', 'th_ghost')).rejects.toThrow('no longer registered')
    await expect(thingOpen('conv-other', 'th_a')).rejects.toThrow('no longer registered')
  })

  it('rejects unsafe conversation ids (no path traversal)', async () => {
    writeIndex('conv-1', [record({ id: 'th_a', path: workspaceFile })])
    await expect(thingOpen('../../etc', 'th_a')).rejects.toThrow('Not a valid conversation')
  })

  it('rejects a registered thing whose file no longer exists', async () => {
    writeIndex('conv-1', [record({ id: 'th_a', path: join(testState.home, 'gone.md') })])
    await expect(thingOpen('conv-1', 'th_a')).rejects.toThrow()
  })

  it('previews text for registered things inside or outside COLLIE_HOME', async () => {
    writeIndex('conv-1', [
      record({ id: 'th_ws', path: workspaceFile }),
      record({ id: 'th_project', path: projectFile })
    ])
    const inside = await thingRead('conv-1', 'th_ws')
    expect(inside.kind).toBe('text')
    expect(inside.text).toContain('Workspace')

    // Previously blocked: registered project-folder deliverables now preview.
    const outside = await thingRead('conv-1', 'th_project')
    expect(outside.kind).toBe('text')
    expect(outside.text).toContain('Project notes')
  })

  it('previews images as data URLs', async () => {
    const png = join(testState.home, 'flyer.png')
    writeFileSync(png, Buffer.from([0x89, 0x50, 0x4e, 0x47]))
    writeIndex('conv-1', [record({ id: 'th_png', path: png, kind: 'image' })])
    const result = await thingRead('conv-1', 'th_png')
    expect(result.kind).toBe('image')
    expect(result.dataUrl).toMatch(/^data:image\/png;base64,/)
  })

  it('shows the registered file in the folder', async () => {
    writeIndex('conv-1', [record({ id: 'th_a', path: workspaceFile })])
    await thingShowInFolder('conv-1', 'th_a')
    expect(testState.showItemCalls).toEqual([workspaceFile])
  })

  it('save-copy defaults to the record title and copies the registered file', async () => {
    writeIndex('conv-1', [record({ id: 'th_a', title: 'Walk flyer', path: workspaceFile })])
    testState.saveDialogResult = { canceled: false, filePath: join(testState.home, 'copied.md') }
    const result = await thingSaveCopy('conv-1', 'th_a')
    expect(result.saved).toBe(true)
    expect(result.path).toBe(join(testState.home, 'copied.md'))
    expect(testState.showItemCalls).toEqual([])
  })

  it('save-copy cancel returns saved:false without copying', async () => {
    writeIndex('conv-1', [record({ id: 'th_a', title: 'Walk flyer', path: workspaceFile })])
    testState.saveDialogResult = { canceled: true, filePath: '' }
    const result = await thingSaveCopy('conv-1', 'th_a')
    expect(result).toEqual({ saved: false })
  })
})
