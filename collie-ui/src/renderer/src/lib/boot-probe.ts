export type BootScreen = 'loading' | 'welcome' | 'app' | 'offline'

export interface BootProbeState {
  screen: BootScreen
  offlineMessage?: string
}

export interface BootProbeDependencies {
  getStatus: () => Promise<{ configured?: boolean }>
  injectSecrets: (generation: number) => Promise<number>
  configure: () => Promise<{ configured?: boolean }>
  wakeMessengers: () => Promise<unknown>
  isConnected: () => boolean
  applyState: (state: BootProbeState) => void
}

export interface BootProbeOptions {
  maximumAttempts?: number
  retryDelayMs?: number
}

const OFFLINE_MESSAGE =
  "Collie's engine didn't start up. Check if anything is blocking the connection and try restarting the app."

/** Serializes boot probes and commits results only for the latest core generation. */
export class BootProbeController {
  private generation = 0
  private active: Promise<void> | null = null
  private rerunRequested = false
  private disposed = false
  private readonly injectionByGeneration = new Map<number, Promise<number>>()
  private readonly configureByGeneration = new Map<number, Promise<{ configured?: boolean }>>()
  private readonly waits = new Map<ReturnType<typeof setTimeout>, () => void>()
  private readonly maximumAttempts: number
  private readonly retryDelayMs: number

  constructor(
    private readonly dependencies: BootProbeDependencies,
    options: BootProbeOptions = {}
  ) {
    this.maximumAttempts = options.maximumAttempts ?? 60
    this.retryDelayMs = options.retryDelayMs ?? 1000
  }

  requestProbe(newGeneration = false): Promise<void> {
    if (this.disposed) return Promise.resolve()
    if (newGeneration) this.generation += 1
    this.rerunRequested = true
    if (this.active) return this.active

    const work = this.drain()
    const active = work.finally(() => {
      if (this.active === active) this.active = null
    })
    this.active = active
    return active
  }

  dispose(): void {
    if (this.disposed) return
    this.disposed = true
    this.generation += 1
    this.rerunRequested = false
    for (const [timer, resolve] of this.waits) {
      clearTimeout(timer)
      resolve()
    }
    this.waits.clear()
  }

  private async drain(): Promise<void> {
    while (!this.disposed && this.rerunRequested) {
      this.rerunRequested = false
      const generation = this.generation
      await this.runGeneration(generation)
    }
  }

  private isCurrent(generation: number): boolean {
    return !this.disposed && generation === this.generation
  }

  private injectOnce(generation: number): Promise<number> {
    let injection = this.injectionByGeneration.get(generation)
    if (!injection) {
      injection = this.dependencies.injectSecrets(generation)
      this.injectionByGeneration.set(generation, injection)
    }
    return injection
  }

  private configureOnce(generation: number): Promise<{ configured?: boolean }> {
    let configuration = this.configureByGeneration.get(generation)
    if (!configuration) {
      configuration = this.dependencies.configure()
      this.configureByGeneration.set(generation, configuration)
    }
    return configuration
  }

  private commit(generation: number, state: BootProbeState): void {
    if (this.isCurrent(generation)) this.dependencies.applyState(state)
  }

  private async runGeneration(generation: number): Promise<void> {
    for (let attempt = 0; attempt < this.maximumAttempts; attempt += 1) {
      if (!this.isCurrent(generation)) return
      try {
        const status = await this.dependencies.getStatus()
        if (!this.isCurrent(generation)) return

        const secretCount = await this.injectOnce(generation)
        if (!this.isCurrent(generation)) return
        if (status.configured) {
          if (secretCount > 0) {
            await this.dependencies.wakeMessengers().catch(() => undefined)
            if (!this.isCurrent(generation)) return
          }
          this.commit(generation, { screen: 'app' })
          return
        }

        if (secretCount > 0) {
          try {
            const result = await this.configureOnce(generation)
            if (!this.isCurrent(generation)) return
            if (result.configured) {
              this.commit(generation, { screen: 'app' })
              return
            }
          } catch {
            if (!this.isCurrent(generation)) return
          }
        }
        this.commit(generation, { screen: 'welcome' })
        return
      } catch {
        if (!this.isCurrent(generation)) return
        if (attempt + 1 < this.maximumAttempts) await this.delay()
      }
    }

    if (!this.isCurrent(generation)) return
    this.commit(
      generation,
      this.dependencies.isConnected()
        ? { screen: 'welcome' }
        : { screen: 'offline', offlineMessage: OFFLINE_MESSAGE }
    )
  }

  private delay(): Promise<void> {
    if (this.disposed) return Promise.resolve()
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.waits.delete(timer)
        resolve()
      }, this.retryDelayMs)
      this.waits.set(timer, resolve)
    })
  }
}
