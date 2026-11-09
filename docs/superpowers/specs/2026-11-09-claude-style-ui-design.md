# XelaSphere UI: claude.ai-style polish

Date: 2026-11-09 · Branch: `feat/claude-ui` (based on the production snapshot c377601)

## Goal

Make xelasphere.com look and behave like claude.ai (reference: the owner's screenshots of
claude.ai in dark mode), in both themes, on desktop and mobile, while keeping the XelaSphere
name and logo. No Anthropic names, logos or assets.

## Decisions (owner)

- Restyle in place: tokens + a final stylesheet + targeted template/JS edits. All existing
  JS modules keep working; no rewrite of the shell.
- Default model stays Auto; "Xela Qwen3.6 27B (Local GPU)" is selectable in a model menu
  and the choice is remembered. The owner's GPU gets no public traffic by default.

## Gap list (current → target)

### Global
- Fonts: Inter for UI, a serif (Source Serif 4) for assistant prose and the greeting.
- Palette: claude.ai warm neutrals. Dark: canvas #262624, sidebar #1f1e1d, raised #30302e,
  text #f5f4ef / #c2c0b6 / #9c9a92, hairline rgba(222,220,209,.15). Light: canvas #faf9f5,
  sidebar #f5f4ed, raised #ffffff, text #141413 / #3d3d3a / #73726c. Accent #d97757
  (terracotta) for send button and focus; links in text color with underline.
- Radii 8 (rows), 12 (cards/code), 20 (composer); soft shadows; 150 ms transitions.

### Sidebar
- Header: "XelaSphere" wordmark + collapse toggle; incognito ghost becomes a quiet icon.
- Nav rows with icons: New chat (accent plus), Search, Chats, Projects, Images, Customize.
- Section labels "Starred" / "Recents" small and muted (no boxed "PROJECTS" header).
- Chat rows: one line, sentence case (no Title Case), hover/selected rounded fill,
  no left accent bar; row menu on hover.
- Footer: avatar initials circle + name + plan + chevron opens the account menu
  (Settings, Theme light/dark/system, Upgrade, Help, Log out).

### Top bar (conversation)
- Left: conversation title + chevron (rename / star / delete menu). Right: artifacts
  toggle and "Share". Chat/Code/Think pills removed. Floating owl theme toggle removed
  (theme lives in the account menu and settings).

### Empty state
- Serif greeting with the XelaSphere mark ("Good evening, Ștefan"), composer centered under
  it, suggestions as small chips under the composer (same actions as today's cards).

### Composer
- Rounded box with textarea; bottom row inside: + (attach/actions menu), tools
  (search toggle), mic, voice; right: model menu button ("Auto ▾" / "Xela Qwen3.6 27B ▾")
  and accent send button (arrow up; stop while streaming).
- Under the box: "XelaSphere can make mistakes. Check important info." (muted, small).
- Model menu: Auto, Xela (Local GPU), DeepSeek Pro, DeepSeek Flash, "More models" with the
  rest of the selectable catalog; name + one-line description + check on the active one.
  Selection persists (localStorage) and is sent as today (`ghostModel.getSelectedModel()`).

### Messages
- User: right-aligned bubble in raised neutral (not black), 16px radius, sans.
- Assistant: no bubble, serif 16.5px/1.65, max column ~720px; headings sans semibold.
- Code blocks: 12px radius, header with lowercase language label + copy; no traffic lights;
  compact padding; inline code subtle.
- Actions under assistant message: copy, read aloud, thumbs up/down, retry (muted icons).
- Reasoning: collapsible "Thinking" block, muted.

### Artifact panel
- Header: title + version select (left), Preview/Code toggle, copy, download, share,
  close (right). Footer bar removed.

### Other surfaces
- Search modal, settings modal, chats page, projects page/modal, images page, toasts,
  context menus, guest banner/modal: same tokens, radii and type.
- Auth pages (login, register, reset, complete reset): claude.ai-style centered column on
  the theme background, serif headline, full-width "Continue with …" provider buttons,
  email form, accent primary button. Follow the saved/system theme.
- Dashboard, profile edit, upgrade, API keys, shared artifact, error pages: tokens + type.

### Mobile
- Sidebar as drawer, top bar: menu, title, new chat; composer fixed at bottom with the same
  controls; no monospace suggestion list.

## Constraints
- Production is updated only by copying files: deploy = copy the changed templates/static
  files from this branch (they started equal to production) + restart, with backups.
- Verification on the isolated dev instance (tools/dev/devserver.py: fake Firestore/Redis,
  dev login, real local model through the tunnel): screenshots desktop 1440×900 and mobile
  390×844, both themes, guest + signed-in, empty + conversation + artifact panel.

## Out of scope
- Backend behavior, routing logic, new features beyond the model menu and title bar actions.
