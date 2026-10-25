import { ChildProcess, spawnSync } from 'child_process'

/** How long a child gets to exit politely before it is killed outright. */
export const GRACEFUL_EXIT_MS = 1500

export type TreeKillStep =
  | { kind: 'taskkill'; args: string[] }
  | { kind: 'signal'; pid: number; signal: NodeJS.Signals }

/**
 * Teardown steps for one platform, in escalation order.
 *
 * Windows has no process group to signal, so the tree goes through taskkill.
 * POSIX children are spawned detached, which makes each one a group leader —
 * a negative pid then reaches the MCP servers the core started.
 */
export function treeKillSteps(
  platform: NodeJS.Platform,
  pid: number
): [TreeKillStep, TreeKillStep] {
  if (platform === 'win32') {
    return [
      { kind: 'taskkill', args: ['/pid', String(pid), '/T'] },
      { kind: 'taskkill', args: ['/pid', String(pid), '/T', '/F'] }
    ]
  }
  return [
    { kind: 'signal', pid: -pid, signal: 'SIGTERM' },
    { kind: 'signal', pid: -pid, signal: 'SIGKILL' }
  ]
}

export function runTreeKillStep(step: TreeKillStep): void {
  try {
    if (step.kind === 'taskkill') {
      spawnSync('taskkill', step.args, { windowsHide: true, stdio: 'ignore' })
    } else {
      process.kill(step.pid, step.signal)
    }
  } catch {
    // The process, or its whole group, has already exited.
  }
}

function exitedWithin(child: ChildProcess, timeoutMs: number): Promise<boolean> {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve(true)
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), timeoutMs)
    timer.unref()
    child.once('exit', () => {
      clearTimeout(timer)
      resolve(true)
    })
  })
}

/**
 * Stop a child along with everything it spawned.
 *
 * Killing only the direct child leaves the core's MCP servers running — they
 * hold the sessions open past the app's exit, and on a fixed port they keep
 * the IPC socket itself. A polite step is always followed by a forced one, so
 * a child that ignores the first cannot hold up quitting.
 */
export async function terminateProcessTree(
  child: ChildProcess,
  options: {
    timeoutMs?: number
    platform?: NodeJS.Platform
    send?: (step: TreeKillStep) => void
  } = {}
): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) return
  const pid = child.pid
  if (pid === undefined) return
  const send = options.send ?? runTreeKillStep
  const timeoutMs = options.timeoutMs ?? GRACEFUL_EXIT_MS
  const [polite, forced] = treeKillSteps(options.platform ?? process.platform, pid)
  send(polite)
  if (await exitedWithin(child, timeoutMs)) return
  send(forced)
  await exitedWithin(child, timeoutMs)
}
