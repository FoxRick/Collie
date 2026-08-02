import { describe, expect, it } from 'vitest'
import {
  PINNED_CONVERSATIONS_STORAGE_KEY,
  readPinnedConversationIds,
  reconcilePinnedConversationIds
} from './navigation'

describe('pinned conversation navigation state', () => {
  it('loads only unique, valid conversation ids', () => {
    const storage = {
      getItem: (key: string): string | null =>
        key === PINNED_CONVERSATIONS_STORAGE_KEY
          ? JSON.stringify(['first', '', 'first', 42, 'second'])
          : null
    }

    expect(readPinnedConversationIds(storage)).toEqual(['first', 'second'])
  })

  it('recovers from malformed local state', () => {
    expect(readPinnedConversationIds({ getItem: () => '{bad json' })).toEqual([])
  })

  it('removes pins for conversations that no longer exist', () => {
    expect(reconcilePinnedConversationIds(['kept', 'deleted'], ['other', 'kept'])).toEqual([
      'kept'
    ])
  })
})
