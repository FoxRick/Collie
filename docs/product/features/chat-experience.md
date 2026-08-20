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
