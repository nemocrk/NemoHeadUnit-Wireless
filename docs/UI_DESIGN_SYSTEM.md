# UI Design System — NemoHeadUnit-Wireless

## Design Direction

**Style**: Minimalist Scandinavian 2026 — calm, premium, distraction-free.  
**Reference**: Polestar / Volvo interior UI, MBUX dark theme.  
**Tone**: Matte dark surfaces, warm-white typography, surgical use of accent colour.  
**Principle**: every pixel either carries information or disappears.

---

## Colour Palette

| Token | Hex | OKLCH | Role |
|---|---|---|---|
| `--color-bg` | `#141414` | `oklch(0.10 0.00 0)` | Main surface — ui_shell background |
| `--color-surface` | `#1c1c1c` | `oklch(0.13 0.00 0)` | Widget backgrounds (navbar, panels) |
| `--color-surface-2` | `#242424` | `oklch(0.16 0.00 0)` | Elevated cards, active states |
| `--color-divider` | `#2e2e2e` | `oklch(0.20 0.00 0)` | Separator lines |
| `--color-border` | `rgba(255,255,255,0.06)` | — | Subtle widget edges |
| `--color-text` | `#f0ece4` | `oklch(0.93 0.01 80)` | Primary text — warm white |
| `--color-text-muted` | `#8a8680` | `oklch(0.57 0.01 80)` | Secondary labels, metadata |
| `--color-text-faint` | `#4a4844` | `oklch(0.33 0.01 80)` | Inactive / disabled |
| `--color-accent` | `#c8b89a` | `oklch(0.77 0.04 80)` | Warm sand — active icons, clock, CTAs |
| `--color-accent-dim` | `rgba(200,184,154,0.15)` | — | Accent highlight backgrounds |
| `--color-danger` | `#c0392b` | `oklch(0.46 0.18 27)` | Errors, critical alerts |
| `--color-success` | `#4a7c59` | `oklch(0.50 0.08 155)` | Connected, OK states |

### Usage rules

- **No pure white** (`#ffffff`) anywhere — always warm white (`--color-text`).
- **No pure black** — background is `#141414`, not `#000000`.
- **Accent sparingly**: clock, active icon, single CTA per screen. Never more than one accent element per viewport.
- **No blue, purple, or gradient accents** — warm sand only.

---

## Typography

| Token | Font | Weight | Size | Usage |
|---|---|---|---|---|
| `--font-display` | `DM Sans` | 300 Light | `--text-xl` + | Panel titles, clock |
| `--font-body` | `DM Sans` | 400 Regular | `--text-base` | Labels, status text |
| `--font-mono` | `DM Mono` | 400 | `--text-sm` | IP addresses, debug values |

**Load via Google Fonts:**
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500&family=DM+Mono&display=swap" rel="stylesheet">
```

### Type scale (fluid)

```css
--text-xs:   clamp(0.75rem,  0.7rem  + 0.25vw, 0.875rem);  /* 12px — tiny labels */
--text-sm:   clamp(0.875rem, 0.8rem  + 0.35vw, 1rem);      /* 14px — icon labels */
--text-base: clamp(1rem,     0.95rem + 0.25vw, 1.125rem);   /* 16px — body */
--text-lg:   clamp(1.125rem, 1rem    + 0.75vw, 1.5rem);     /* 18px — section labels */
--text-xl:   clamp(1.5rem,   1.2rem  + 1.25vw, 2.25rem);    /* 24px — clock, titles */
```

### Rules

- **Clock**: `--font-display` weight 300, `--text-xl`, `--color-accent`, letter-spacing `0.08em`.
- **Icon labels**: `--font-body` weight 400, `--text-xs`, `--color-text-muted`, uppercase, tracking `0.12em`.
- **No bold headings** — weight contrast is achieved via opacity and size, not weight.

---

## Iconography

**Library**: Lucide Icons (thin stroke, `stroke-width: 1.5`).  
**Size**: 22×22px touch target minimum; visual icon 18×18px.  
**Colour**: `--color-text-muted` at rest → `--color-accent` active.  
**No filled icons** except the Home circle (single filled element, deliberate contrast).

```html
<!-- CDN -->
<script src="https://unpkg.com/lucide@latest"></script>
```

### Navbar icon set

| Icon | Lucide name | State active |
|---|---|---|
| Volume | `volume-2` | slider visible |
| Brightness | `sun` | slider visible |
| Home | `circle` (filled via CSS) | always accent |
| Clock | text render, no icon | always accent |
| Settings | `settings` | panel open |

---

## Spacing

4px base grid. All padding, margin, gap must use tokens.

```css
--space-1:  0.25rem;  /*  4px */
--space-2:  0.5rem;   /*  8px */
--space-3:  0.75rem;  /* 12px */
--space-4:  1rem;     /* 16px */
--space-6:  1.5rem;   /* 24px */
--space-8:  2rem;     /* 32px */
```

---

## Border Radius

```css
--radius-sm:   4px;   /* tight elements: badges, chips */
--radius-md:   8px;   /* inputs, small cards */
--radius-lg:   12px;  /* panels, overlays */
--radius-xl:   20px;  /* floating menus, bottom sheet */
--radius-full: 9999px;
```

---

## Component Specifications

### Navbar (`navbar_ui`)

```
Height:        60px
Background:    --color-surface  (rgba(28,28,28,0.96))
Top border:    1px solid rgba(255,255,255,0.06)
Padding:       0 24px
Layout:        flex, space-between, align-center
Backdrop:      blur(12px) — frosted glass effect over video content
```

Icon button spec:
```
Touch target:  44×44px minimum
Visual icon:   18×18px
Gap icon→label: 4px
State rest:    --color-text-muted
State active:  --color-accent
State press:   scale(0.92), transition 120ms ease-out
```

### Floating panel (bt_ui, config_ui)

```
Background:    --color-surface  rgba(28,28,28,0.97)
Border:        1px solid rgba(255,255,255,0.06)
Border-radius: --radius-xl
Padding:       24px
Backdrop:      blur(20px)
Shadow:        0 8px 32px rgba(0,0,0,0.6)
Animation:     slide-up 200ms cubic-bezier(0.16,1,0.3,1)
```

### Status indicator (HUD overlay, top-right)

```
Size:          8px circle
Color active:  --color-success
Color error:   --color-danger
Color idle:    --color-text-faint
Pulse anim:    opacity 0.4→1.0, 2s ease-in-out infinite (connected state only)
```

---

## Motion

**Philosophy**: motion confirms state changes, never decorates.

| Interaction | Duration | Easing | Property |
|---|---|---|---|
| Icon press feedback | 120ms | `ease-out` | `scale(0.92)` |
| Panel slide-in | 200ms | `cubic-bezier(0.16,1,0.3,1)` | `translateY` + `opacity` |
| Panel slide-out | 160ms | `ease-in` | `translateY` + `opacity` |
| Active icon colour | 180ms | `ease` | `color` |
| Status dot pulse | 2000ms | `ease-in-out` | `opacity` (infinite, connected only) |
| Volume/brightness slider reveal | 150ms | `ease-out` | `height` + `opacity` |

**No animations on**: background colour, border changes, text content updates.  
**Respect** `prefers-reduced-motion`: all transitions → `0.01ms`.

---

## PyQt6 Implementation Notes

### Background transparency on widget windows

```python
# Every widget_* window
self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)

def paintEvent(self, event):
    p = QPainter(self)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    # Navbar background — frosted glass simulation
    p.setBrush(QColor(28, 28, 28, 245))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRect(self.rect())
    # Top divider line
    p.setPen(QPen(QColor(255, 255, 255, 15), 1))
    p.drawLine(0, 0, self.width(), 0)
```

### Icon colour states

```python
ICON_REST   = QColor(0x8a, 0x86, 0x80)  # --color-text-muted
ICON_ACTIVE = QColor(0xc8, 0xb8, 0x9a)  # --color-accent
```

### Clock rendering

```python
# In navbar paintEvent
font = QFont("DM Sans", 18, QFont.Weight.Light)
font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.0)
p.setFont(font)
p.setPen(QColor(0xc8, 0xb8, 0x9a))  # --color-accent
p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "10:24")
```

---

## Anti-Patterns (Never Do)

- ❌ White backgrounds or light surfaces
- ❌ Coloured borders on panels (only rgba white at 6% opacity)
- ❌ More than one accent-coloured element per viewport
- ❌ Filled icons (except Home circle)
- ❌ Bold font weights
- ❌ Drop shadows on icons
- ❌ Gradient backgrounds or gradient buttons
- ❌ Blue, purple, or green accent colours
- ❌ Animations on content updates (only on state transitions)
- ❌ Rounded corners larger than `--radius-xl` (20px)
