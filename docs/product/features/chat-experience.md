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

Right-click the tray icon for Open Collie, New chat, Recent chats (up to ten,
with the selected chat checked), Settings, and Exit Collie (Quit Collie on macOS and Linux). Recent chats follow
the sidebar's conversation order and refresh with its conversation list. New
chat and recent-chat selection use the same navigation as the sidebar; switching
chats does not stop background work. Actions selected during startup wait for
the chat shell. Closing the window hides it; Exit Collie shuts down the app and
its supervised processes and removes the tray icon.

Recent chats refresh through existing conversation/message events, including
first-message creation, renames, completion, and deletion. There is no tray
polling timer and streamed text tokens do not rebuild the menu. Main skips
native-menu rebuilds when the ten visible IDs, titles, and active selection are
unchanged. A blank new-chat view enters recents after its first saved message.

Menus use native Electron controls. Windows shows the menu in its notification
area; macOS uses the top menu bar and clicking the icon opens the menu without
raising the window; Linux uses the desktop's status-notifier/GTK tray, with
activation depending on the desktop environment. The current chat uses a radio
checkmark so selecting it again cannot toggle the checkmark off. macOS keeps
literal ampersands in titles; Windows/Linux escape menu mnemonics. Every changed
menu is reapplied with `setContextMenu`, as required by Linux. If tray construction fails on Windows/Linux, closing the window exits instead
of hiding an app that cannot be reopened. Linux shells may still need a tray
extension even when construction succeeds. Native macOS/Linux appearance and
tray availability still need target-OS QA.

Platform behavior reference: [Electron Tray](https://www.electronjs.org/docs/latest/api/tray).
