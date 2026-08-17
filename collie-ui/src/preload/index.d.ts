import type { AccountBridge, CollieBridge } from './index'

declare global {
  interface Window {
    collie: CollieBridge
    account: AccountBridge
  }
}

export {}
