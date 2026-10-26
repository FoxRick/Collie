// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import ShoppingListCard from './ShoppingListCard'
import { collieClient } from '../../lib/ipc'

it('saves the actual item ID through the production client, restores done, and handles save failure', async () => {
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  const command = vi.spyOn(collieClient, 'command').mockResolvedValue({})
  const node = document.createElement('div'); document.body.append(node)
  const root = createRoot(node)
  const data = { list_name: 'QA groceries', categories: { Pantry: [{ id: 'oats-id', name: 'oats', done: true }] } }
  try {
    act(() => root.render(<ShoppingListCard data={data} />))
    const box = node.querySelector('button')!
    expect(box.getAttribute('aria-checked')).toBe('true')
    await act(async () => box.click())
    expect(command).toHaveBeenCalledWith('set_shopping_item_checked', { item_id: 'oats-id', checked: false })
    expect(box.getAttribute('aria-checked')).toBe('false')
    command.mockRejectedValueOnce(new Error('offline'))
    await act(async () => box.click())
    expect(box.getAttribute('aria-checked')).toBe('false')
    expect(node.textContent).toContain('save')
  } finally {
    act(() => root.unmount()); node.remove(); command.mockRestore()
  }
})
