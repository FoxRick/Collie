# Collie Chat and Navigation Experience

**Status:** active product direction
**Date:** 2026-08-01

## Objective

Keep Collie's primary conversation experience calm, immediate, and easy to
navigate for nontechnical users. Image attachments should remain visible and
understandable, streamed responses should read like finished prose as they
arrive, and the sidebar should prioritize starting and returning to chats.

## Acceptance criteria

- The chat composer has no dedicated camera or screenshot button. Users add
  images by pasting them or choosing them through the upload control.
- A pasted or uploaded image appears as a thumbnail beside its filename in the
  composer before sending.
- Sent image attachments remain visible as thumbnails in the persisted chat.
  Activating a thumbnail with a pointer or keyboard opens an accessible larger
  preview that can be dismissed without losing the user's place in the chat.
- Assistant text streams at a smooth, readable pace without frame-like jumps,
  erratic reflow, or a blinking typing cursor.
- Live text catches up after provider bursts; a delivered answer appears in
  full immediately, without an artificial post-completion typing delay.
- Before text arrives, a compact activity line shows the current work. It is
  separate from the answer, with no overlapping skeleton placeholders.
- Scrolling back pauses automatic following. **Jump to latest** resumes it;
  switching conversations starts at the latest messages in that conversation.
- Markdown is rendered continuously while a response streams. Users do not see
  raw formatting markers such as `**` or `__` before styled text appears.
- The sidebar order is: **New Chat**, **Agents**, **Skills**, **Routines**,
  **Connectors**, then **General Chat** as a fixed row that never scrolls away,
  followed by pinned chats and recent chats in the scrolling area.
- Project groups sit below recent chats in the scrolling area. Each project
  collapses to its folder row; the active project stays expanded, and clicking
  a project row opens the project and expands its recent chats.
- The sidebar can be collapsed to a 64px icon-only rail (chevron button or
  Ctrl/Cmd+B) to free up chat space. Labels become tooltips; the choice
  survives restarts. Expanding restores the full navigation.
- Chats can be pinned and unpinned. Pinned chats stay in the pinned section and
  do not depend on recent-chat ordering.
- Chat search is a compact icon button at the top-right of the navigation pane,
  with an accessible label. The previous large **Sniff through chats** control
  is not shown.
- New chats default to **Execute**. **Plan** remains available and is clearly
  read-only rather than an extra review mode for ordinary conversation.
- The composer has an approval selector with **Ask me** and **Approve for me**.
  The latter applies only to explicitly eligible local operations; it never
  bypasses sends, payments, publishing, destructive work, account/capability
  changes, routines, or external writes.
- The composer has one compact **Files** selector that combines project-folder
  selection with local access scope: **Project folder only**, **Choose other
  folders…**, and **Full file access**. Project-folder-only is the default;
  chosen folders show the current selection. The approval selector remains
  separate because approval policy and file visibility are different controls.
- Selecting **Full file access** requires clear confirmation, lasts only for
  the current app session, and is described as local-file access only. It must
  not imply network, connector, payment, send, publish, destructive, account,
  or routine authority.

## Desktop tray

Collie keeps one tray icon per application profile. Opening the app again
restores and focuses the existing window, including when it was minimized or
hidden. A launch that does not acquire the single-instance lock does not start
another window, tray icon, or core runtime. Deliberately isolated test profiles
remain separate. The Windows app identity matches the installer shortcut for
taskbar grouping.

The native tray menu contains Open Collie, New chat, Recent chats (up to ten,
with the selected chat checked), Settings, and Exit Collie (Quit Collie on macOS
and Linux). Recent chats follow the sidebar's conversation order. Chat selection
shares the sidebar handler, including restoration of the saved project folder
and clearing it for General Chat. Switching chats does not stop background work.
Actions selected during startup wait for the chat shell.

Recent chats refresh through existing conversation/message events, including
first-message creation, renames, completion, and deletion. There is no tray
polling timer and streamed text tokens do not rebuild the menu. Main skips
native-menu rebuilds when the ten visible IDs, titles, and active selection are
unchanged. A blank new-chat view enters recents after its first saved message.
A checked menu item follows the authoritative chat selection; no recent entry
is checked when a blank or older chat is active. Selecting the current chat
again does not uncheck it.

Platform behavior:

- Windows: notification-area menu; clicking the icon restores the window.
  Closing hides the window while the tray exists. If tray construction fails,
  closing exits instead of hiding an app that cannot be reopened.
- macOS: menu-bar menu with a system paw template icon at 16 points, adapting
  to light/dark menu bars and Retina displays. Clicking opens the menu without
  raising the window. Closing hides the window when the tray exists, preserving
  the renderer and its event-driven recent-chat updates. The Dock can restore
  the window even while the core starts. Without a tray, the normal macOS
  close-window/keep-app-running behavior applies.
- Linux: native status-notifier/GTK menu; activation depends on the desktop.
  Every changed menu is reapplied with `setContextMenu`. Closing exits the app;
  minimizing keeps its taskbar entry available. This avoids hiding an app when
  a desktop constructs a tray object but has no visible tray host. A shell may
  still need a tray extension to expose the menu.

Exit/Quit shuts down supervised processes and removes the tray icon. macOS
keeps literal ampersands in titles; Windows/Linux escape menu mnemonics.
`desktop-tray.yml` runs focused navigation tests plus a real Electron smoke on
Windows, macOS, and Linux/X11, covering native menu presentation/selection,
image loading, tray lifecycle, and window close/restore. Linux desktop-specific
tray placement and native appearance still depend on the user's shell/theme;
this smoke is not a visual acceptance pass for every desktop environment.

Platform behavior reference: [Electron Tray](https://www.electronjs.org/docs/latest/api/tray).
