export type AppView =
  | 'chat'
  | 'agents'
  | 'skills'
  | 'loops'
  | 'connectors'
  | 'settings'

export const PINNED_CONVERSATIONS_STORAGE_KEY = 'collie.pinnedConversations'

export function readPinnedConversationIds(storage: Pick<Storage, 'getItem'>): string[] {
  try {
    const value: unknown = JSON.parse(storage.getItem(PINNED_CONVERSATIONS_STORAGE_KEY) || '[]')
    if (!Array.isArray(value)) return []
    return [...new Set(value.filter((id): id is string => typeof id === 'string' && id.length > 0))]
  } catch {
    return []
  }
}

export function reconcilePinnedConversationIds(
  pinnedIds: readonly string[],
  conversationIds: readonly string[]
): string[] {
  const available = new Set(conversationIds)
  return pinnedIds.filter((id) => available.has(id))
}
