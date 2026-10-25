import { ChildProcess } from 'child_process'
import { EventEmitter } from 'events'
import { describe, expect, it, vi } from 'vitest'
import { type TreeKillStep, terminateProcessTree, treeKillSteps } from './process-tree'

class FakeChild extends EventEmitter {
  exitCode: number | null = null
  signalCode: NodeJS.Signals | null = null
  pid: number | undefined

  constructor(pid?: number) {
    super()
    this.pid = pid
  }

  exit(): void {
    this.exitCode = 0
    this.emit('exit', 0)
  }
}

function asChild(child: FakeChild): ChildProcess {
  return child as unknown as ChildProcess
}

describe('treeKillSteps', () => {
  it('signals the process group on POSIX', () => {
    expect(treeKillSteps('linux', 4242)).toEqual([
      { kind: 'signal', pid: -4242, signal: 'SIGTERM' },
      { kind: 'signal', pid: -4242, signal: 'SIGKILL' }
    ])
  })

  it('takes the tree down with taskkill on Windows', () => {
    expect(treeKillSteps('win32', 7)).toEqual([
      { kind: 'taskkill', args: ['/pid', '7', '/T'] },
      { kind: 'taskkill', args: ['/pid', '7', '/T', '/F'] }
    ])
  })
})

describe('terminateProcessTree', () => {
  it('stops once the child exits on the polite step', async () => {
    const child = new FakeChild(99)
    const steps: TreeKillStep[] = []
    const send = (step: TreeKillStep): void => {
      steps.push(step)
      child.exit()
    }

    await terminateProcessTree(asChild(child), { platform: 'linux', timeoutMs: 50, send })

    expect(steps).toEqual([{ kind: 'signal', pid: -99, signal: 'SIGTERM' }])
  })

  it('escalates when the polite step is ignored', async () => {
    const child = new FakeChild(4242)
    const steps: TreeKillStep[] = []
    const send = (step: TreeKillStep): void => {
      steps.push(step)
      if (step.kind === 'signal' && step.signal === 'SIGKILL') child.exit()
    }

    await terminateProcessTree(asChild(child), { platform: 'linux', timeoutMs: 5, send })

    expect(steps).toEqual([
      { kind: 'signal', pid: -4242, signal: 'SIGTERM' },
      { kind: 'signal', pid: -4242, signal: 'SIGKILL' }
    ])
  })

  it('escalates with a forced taskkill on Windows', async () => {
    const child = new FakeChild(7)
    const steps: TreeKillStep[] = []
    const send = (step: TreeKillStep): void => {
      steps.push(step)
      if (step.kind === 'taskkill' && step.args.includes('/F')) child.exit()
    }

    await terminateProcessTree(asChild(child), { platform: 'win32', timeoutMs: 5, send })

    expect(steps).toEqual([
      { kind: 'taskkill', args: ['/pid', '7', '/T'] },
      { kind: 'taskkill', args: ['/pid', '7', '/T', '/F'] }
    ])
  })

  it('gives up quietly on a child that survives both steps', async () => {
    const child = new FakeChild(5)
    const send = vi.fn()

    await terminateProcessTree(asChild(child), { platform: 'linux', timeoutMs: 5, send })

    expect(send).toHaveBeenCalledTimes(2)
  })

  it('does nothing for a child that already exited', async () => {
    const child = new FakeChild(1)
    child.exitCode = 0
    const send = vi.fn()

    await terminateProcessTree(asChild(child), { send })

    expect(send).not.toHaveBeenCalled()
  })

  it('does nothing when the child never got a pid', async () => {
    const child = new FakeChild(undefined)
    const send = vi.fn()

    await terminateProcessTree(asChild(child), { send })

    expect(send).not.toHaveBeenCalled()
  })
})
