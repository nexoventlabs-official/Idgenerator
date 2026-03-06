---
name: "Admin Dark Design System"
description: "Implementation details and specifications for the dark-themed Admin dashboard."
---

# Admin Dark Design System

This file documents the standardized UI/UX design rules implemented across the PuratchiThaai admin dashboard. When creating new admin pages or adding features to existing ones, strictly adhere to these guidelines to ensure consistency.

## 1. Typography
Two Google Fonts are used to establish a modern, clean hierarchy.

- **Headers & Display Elements:** `Bricolage Grotesque`
  - Usage: H1-H6, table headers, statistic values, badges, buttons.
  - Weight: Bold (700+) for metrics, Semi-Bold (600+) for headers.
- **Body & Data:** `Sora`
  - Usage: Paragraphs, labels, input fields, table cell data, general text.
  - Weight: Regular (400) or Medium (500).

---

## 2. Color Palette & CSS Variables
These variables must be used for any new CSS written across the admin section. Avoid hardcoding standard hex colors.

| Token | Value | Primary Usage |
|-------|-------|---------------|
| `--bg-root` | `#08080C` | Deepest pitch black, used for the main `body` background. |
| `--bg-surface` | `#121218` | Dark grey, used for main structural elements like cards, standard tables, modals, and the sidebar. |
| `--bg-surface-hover` | `#1A1A22` | Lighter dark grey for hover slates (e.g. table rows). |
| `--brand` | `#FF1E56` | The primary crimson/pink highlight color. Used for active navigation, main CTA buttons, and interactive icons. |
| `--brand-dark` | `#CC1845` | Deep version of brand color for hover states on buttons. |
| `--brand-glow` | `rgba(255, 30, 86, 0.15)` | Transparent crimson used for highlights, focus rings, and active pills. |
| `--text-primary` | `#F0F0F5` | Off-white used for core readability (paragraphs, data table cells). |
| `--text-secondary` | `#8A8B99` | Light grey for subtitles and less-important data. |
| `--text-muted` | `#55566A` | Darkest grey text for table headers, form labels, and placeholders. |
| `--border` | `rgba(255, 255, 255, 0.06)` | Extremely subtle white transparency for card borders, HRs, and input outlines. |
| `--border-active` | `rgba(255, 255, 255, 0.12)` | Slightly brighter border for hovers or active inputs. |

---

## 3. Structural Components

### A. Sidebar (`#sidebar`)
- Has a fixed width (`--sidebar-w`) of `260px`.
- Navigation links use muted colors with a transition on hover.
- **Active state:** Gains a `var(--brand-glow)` background and a heavy left border of `var(--brand)`.

### B. Top Navbar (`.top-bar`)
- Positioned sticky-top.
- Uses `backdrop-filter: blur(12px)` over a `.8` opacity `var(--bg-surface)`.

### C. Cards (`.card`)
- Background: `var(--bg-surface)`
- Border: `1px solid var(--border)`
- Border Radius: `var(--radius)` (14px).

---

## 4. Tables and Data Grids
All admin tables (`.voters-table`, `.req-table`) employ a standardized dark aesthetic.

- **Headers (`th`):** Uppercase, `0.72rem`, `var(--text-muted)` text color, letter-spaced.
- **Data Rows (`td`):** Middle-aligned vertically.
- **Row Hover:** Entire `tr` transitions background to `var(--bg-surface-hover)`.
- **Badges/Pills (`.ptc-badge`, `.gen-pill`, `.status-badge`):**
  - Use translucent backgrounds (`0.12` or `0.15` opacity) instead of solid colors.
  - E.g. Success/Confirmed is `rgba(102,187,106,0.12)` background, `#66BB6A` colored text.

---

## 5. Forms and Inputs
- Inputs have `var(--bg-root)` backgrounds (darker than the card).
- Borders use `var(--border)`.
- **Focus state:** Replaces default Bootstrap glow. Border becomes `var(--brand)` and adds a `var(--brand-glow)` box shadow.

---

## 6. Timezones & Timestamps
- **Rule:** ALL timestamps displayed to the user must be converted from UTC to Indian Standard Time (IST).
- **Client-Side:** Use the globally defined JavaScript function `toIST(utcIsoString)` (located in `base.html`).
- **Server-Side:** Use the custom Jinja template filter `{{ timestamp | to_ist }}`.

---

## 7. Title Encoding
- Do not use em-dashes (`—` or `—`) or special characters in `<title>` or header tags due to formatting issues leading to mojibake (`â€”`).
- Always use the standard hyphen (`-`).

---

## 8. Maintainability Guidelines
1. **Never** include a Jinja `{% block extra_css %}` inside a `<style>` block. Most auto-formatters do not recognize Jinja and will mangle the block. Always place `{% block extra_css %}` within its own dedicated `<style>` tag. Example:
   ```html
   <style>
      /* Main css */
   </style>
   <style>{% block extra_css %}{% endblock %}</style>
   ```
2. For pagination, always assign `per_page` like so to avoid template syntax errors in JavaScript:
   ```javascript
   var perPage = parseInt("{{ per_page }}") || 20;
   ```
