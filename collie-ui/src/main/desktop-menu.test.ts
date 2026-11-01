import { describe, expect, it, vi } from 'vitest'
import { DesktopActionQueue, desktopMenu } from './desktop-menu'
import type { MenuItemConstructorOptions } from 'electron'

describe('desktop tray menu', () => {
  it('opens the selected chat and marks the current chat', () => {
    const dispatch = vi.fn()
    const menu = desktopMenu({
      activeId: 'a',
      conversations: [{ id: 'a', title: 'Research & travel' }, { id: 'b', title: '' }]
    }, vi.fn(), dispatch, vi.fn(), 'win32')
    const recent = menu[2].submenu as MenuItemConstructorOptions[]
    expect(recent[0]).toMatchObject({ label: 'Research && travel', checked: true })
    expect(recent[1]).toMatchObject({ label: 'New chat', checked: false })
    ;(recent[1].click as () => void)()
    expect(dispatch).toHaveBeenCalledWith({ type: 'open-chat', conversationId: 'b' })
  })

  it('keeps quick actions available without saved chats', () => {
    const open = vi.fn(), dispatch = vi.fn(), quit = vi.fn()
    const menu = desktopMenu({ activeId: null, conversations: [] }, open, dispatch, quit)
    expect((menu[2].submenu as MenuItemConstructorOptions[])[0].enabled).toBe(false)
    for (const index of [0, 1, 4, 6]) (menu[index].click as () => void)()
    expect(open).toHaveBeenCalledOnce()
    expect(dispatch.mock.calls).toEqual([[{ type: 'new-chat' }], [{ type: 'settings' }]])
    expect(quit).toHaveBeenCalledOnce()
  })

  it.each(['win32', 'darwin', 'linux'] as const)('uses native menu choices on %s', (platform) => {
    const menu = desktopMenu({ activeId: 'a', conversations: [{ id: 'a', title: 'Work & travel' }] },
      vi.fn(), vi.fn(), vi.fn(), platform)
    const chat = (menu[2].submenu as MenuItemConstructorOptions[])[0]
    expect(chat).toMatchObject({
      type: 'radio', checked: true,
      label: platform === 'darwin' ? 'Work & travel' : 'Work && travel'
    })
    expect(menu[6].label).toBe(platform === 'win32' ? 'Exit Collie' : 'Quit Collie')
  })

  it('delivers the latest startup action once and queues again after a reload', () => {
    const send = vi.fn()
    const queue = new DesktopActionQueue(send)
    queue.dispatch({ type: 'new-chat' })
    queue.dispatch({ type: 'settings' })
    expect(send).not.toHaveBeenCalled()
    queue.setReady(true)
    queue.setReady(true)
    expect(send).toHaveBeenCalledExactlyOnceWith({ type: 'settings' })
    queue.dispatch({ type: 'new-chat' })
    expect(send).toHaveBeenLastCalledWith({ type: 'new-chat' })
    queue.setReady(false)
    queue.dispatch({ type: 'open-chat', conversationId: 'a' })
    expect(send).toHaveBeenCalledTimes(2)
    queue.setReady(true)
    expect(send).toHaveBeenLastCalledWith({ type: 'open-chat', conversationId: 'a' })
  })
})
