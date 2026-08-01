import type { CollieBridge } from './index'

declare global {
  interface Window {
    collie: CollieBridge
  }
}

export {}
