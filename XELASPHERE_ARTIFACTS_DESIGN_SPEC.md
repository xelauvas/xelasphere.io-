Right Code Editor Panel (Artifact / Code Sidebar) UI Spec

Recreate a pixel-close right-side “artifact / code editor” panel in a modern AI workspace UI. The panel must match a dark, developer-tool aesthetic with a top header containing context buttons, an artifact switcher dropdown, and a syntax-highlighted code editor that looks like Prism/Monaco-style highlighting.

1) Panel container (right sidebar)

A fixed right column that occupies ~45–55% of the viewport width.

Background: deep charcoal (near-black) with a slightly lighter inner panel tone.

A 1px subtle border on the left edge separating it from the main app.

Rounded corners on visible edges (10–14px radius).

Internal padding is minimal; most spacing is handled by header padding.

Smooth open/close transition: opacity + transform or width animation (fast and clean).

Panel uses its own scroll context (editor scrolls, not the whole page).

Color tokens (use these exact-ish values)

--bg: #0f1116 (overall app background behind panels)

--panel: #141824 (right panel base)

--panel2: #0f1320 (editor surface / deeper area)

--border: rgba(255,255,255,0.08)

--text: rgba(255,255,255,0.92)

--muted: rgba(255,255,255,0.62)

--muted2: rgba(255,255,255,0.45)

--accent: #3b82f6 (blue selection outline)

--accent2: rgba(59,130,246,0.18) (selection fill)

--hover: rgba(255,255,255,0.06)

--active: rgba(255,255,255,0.10)

2) Top header bar (sticky)

A sticky header at the top of the right panel:

Height: ~52–60px.

Background: --panel (slightly lighter than editor area).

Bottom border: 1px using --border.

Horizontal padding: 14–16px.

Layout: left group + right group, vertically centered.

2.1 Left header control (artifact switch button)

A rounded-square button ~36x36px.

Border: 1px --border, background transparent or rgba(255,255,255,0.02).

Icon: “list / hamburger / stacked lines”.

Hover: background becomes --hover, border slightly brighter.

Active/focus: thin --accent outline.

This button toggles the “Switch between artifacts” dropdown.

2.2 Right header controls (Copy / Publish / Close)

Right-aligned controls in this exact order:

Copy button:

Label: “Copy”

Has a small down caret to indicate menu.

Rounded (10–12px), subtle border.

Hover: slightly brighter background.

Publish button:

Label: “Publish”

Slightly more prominent (brighter fill or stronger border).

Hover: stronger highlight; looks like the primary action.

Close (X):

Icon-only button at far right.

Minimal chrome, hover background --hover.

Closes/collapses the whole right panel.

Buttons should look like compact “tool” buttons:

Text color --text, secondary icons --muted.

Font: system UI, 13–14px.

Spacing between buttons: 8–10px.

3) Artifact switcher dropdown (MUST be included and complete)

When clicking the left header button, show a floating dropdown anchored under it.

Dropdown container

Width: ~280–340px.

Background: #121826 (or --panel slightly darker).

Border: 1px --border.

Rounded corners: 12px.

Shadow: soft, subtle (no heavy glow).

Padding: 10–12px overall.

Appears above editor (high z-index).

Clicking outside closes it.

Dropdown title row

Top text: “Switch between artifacts”

Style: --muted, small caps feel, 12–13px, medium weight.

Slight spacing below title before list.

Artifact list items (3 items)
Each row is a selectable card-like row:

Padding: 10–12px.

Rounded corners: 10–12px.

Layout:

Primary line: artifact filename/title (brighter --text, medium weight).

Secondary line: small label (Code or Interactive artifact) in --muted2.

The three entries shown (exact labels):

script.js - Menu Editor JavaScript
Secondary label: Code

styles.css - Menu Editor Styles
Secondary label: Code

index.html - Menu Editor
Secondary label: Interactive artifact

Selected item styling

Background fill: --accent2.

Border: 1px solid --accent.

The card looks “outlined” in blue like the screenshot.

Non-selected hover: background --hover, border remains subtle.

4) Editor region (fills the panel below header)

Below the header, the panel is dominated by the editor area.

Editor container

Background: --panel2.

No extra toolbars inside the editor.

Uses monospace font (e.g., ui-monospace, SFMono-Regular, Menlo, Consolas).

Line height comfortable (1.5–1.65).

Editor scrollbars are thin and dark.

Prism/Monaco-like syntax highlighting (MUST look like it)
Whether using Prism.js or Monaco, ensure a modern dark highlight palette similar to:

Comments: muted gray-green (rgba(148,163,184,0.75) feel)

Keywords: purple/violet

Strings: warm yellow/orange

Numbers: light teal/cyan

Function names: bright blue

Types/consts: slightly different cyan/blue

The editor must look like a real code environment:

Line numbers visible on the left gutter.

Gutter background subtly darker than code area.

Active line highlight: faint rgba(255,255,255,0.04).

Selection highlight: rgba(59,130,246,0.25).

Code content look

The visible code area starts near the top with comment headings like // Configuration.

The code appears crisp with clear syntax colors and proper indentation.

The editor should be scrollable vertically with a visible scrollbar.

5) Micro-details that must not be missed

Header remains visible when scrolling code (sticky).

Divider line between header and editor (1px border).

Dropdown does not push layout; it overlays on top of editor.

Focus states:

Buttons and selected artifact show --accent ring/outline.

Smooth transitions:

Dropdown fade/slide in subtly.

Hover transitions ~120–180ms.

Typography:

Header buttons: 13–14px.

Dropdown title: 12–13px muted.

Artifact item title: 13–14px.

Artifact label: 12px muted.

Spacing:

Everything feels compact and aligned, like a professional IDE/tool panel.

6) Optional but matching the screenshot

A subtle “saved”/status indicator can appear faintly (e.g., bottom-right of editor or near header) but should not clutter.

Copy menu (if present visually): small dropdown with rounded corners and the same dark style.