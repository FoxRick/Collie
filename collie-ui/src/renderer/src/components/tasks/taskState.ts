import type { TaskState } from '../../lib/ipc'

export type TaskSnapshotGenerations = Record<string, number>

/** Starts a new request generation, invalidating any older hydration result. */
export function beginTaskHydration(
  generations: TaskSnapshotGenerations,
  conversationId: string
): number {
  const next = (generations[conversationId] ?? 0) + 1
  generations[conversationId] = next
  return next
}

/** A pushed state supersedes outstanding read-through snapshots for this conversation. */
export function recordTaskStateEvent(
  generations: TaskSnapshotGenerations,
  conversationId: string
): void {
  beginTaskHydration(generations, conversationId)
}

export function isCurrentTaskHydration(
  generations: TaskSnapshotGenerations,
  conversationId: string,
  generation: number
): boolean {
  return generations[conversationId] === generation
}

export function isCurrentConversationEvent(
  eventConversationId: string,
  activeConversationId: string | null
): boolean {
  return eventConversationId === activeConversationId
}

/** Applies only complete, monotonic snapshots for one conversation. */
export function applyTaskState(
  previous: Record<string, TaskState>,
  conversationId: string,
  task: TaskState | null
): Record<string, TaskState> {
  if (!task) {
    if (!(conversationId in previous)) return previous
    const next = { ...previous }
    delete next[conversationId]
    return next
  }
  const existing = previous[conversationId]
  // Revisions are monotonic per task, not per conversation. A new checklist or
  // plan run may correctly begin at revision 1 after a terminal predecessor.
  if (existing && existing.id === task.id && existing.source === task.source && existing.revision >= task.revision) {
    return previous
  }
  return { ...previous, [conversationId]: task }
}
