# UI/UX

All UI work must stay consistent with the Honeydew design system in `web_app/static/style.css`.

## Cross-browser and cross-device

The app serves Chrome, Firefox, and Safari—including iOS Safari—on desktop, tablet, and phone.

- Design mobile-first and verify layouts at narrow phone widths around 375px. Prefer responsive units and the existing `@media (max-width: 768px)` breakpoint over fixed sizing.
- Avoid WebKit-only or Chrome-only CSS without fallbacks. Check baseline support before relying on newer APIs such as `:has()`, container queries, or View Transitions.
- Account for iOS Safari safe-area insets, dynamic bottom chrome, `100vh` differences, and touch interactions. Do not hide critical controls behind hover.
- Keep touch targets approximately 40px or larger.
- Test fixed bottom controls with the mobile keyboard open.
- Thumbnail grids must render tiny placeholder sources and put real URLs in data attributes. Lazy-load with `IntersectionObserver`, serialize requests with a small stagger, and retry failures with cache-busting query parameters. Define stagger and retry constants in `ConfigManager`.

## Design tokens

Always use the existing CSS variables instead of hardcoded values.

### Colors

- `--hw-bg-primary`: `#F0FFF0`
- `--hw-bg-secondary`: `#FAF9F6`
- `--hw-bg-cream`: `#F5F5DC`
- `--hw-sage`: `#87A878`
- `--hw-sage-light`: `#A8C686`
- `--hw-sage-dark`: `#6B8E5A`
- `--hw-forest`: `#2D4A3E`
- `--hw-moss`: `#4A5D4A`
- `--hw-peach`: `#F4A261`
- `--hw-gold`: `#E9C46A`
- `--hw-coral`: `#E07A5F`
- `--hw-terracotta`: `#D4866A`
- `--hw-text-primary`: `#2D4A3E`
- `--hw-text-secondary`: `#5A6B5A`
- `--hw-text-muted`: `#8A9A8A`
- `--hw-border`: `rgba(135, 168, 120, 0.2)`

### Other tokens

- Gradients: `--hw-gradient-warm`, `--hw-gradient-sage`, `--hw-gradient-golden`, `--hw-gradient-soft`
- Shadows: `--hw-shadow-sm`, `--hw-shadow-md`, `--hw-shadow-lg`, `--hw-shadow-glow`
- Radii: `--hw-radius-sm` (8px), `--hw-radius-md` (12px), `--hw-radius-lg` (16px), `--hw-radius-xl` (24px), `--hw-radius-full` (9999px)
- Transitions: `--hw-transition-fast` (0.2s), `--hw-transition-base` (0.3s), `--hw-transition-slow` (0.5s); all use `cubic-bezier(0.4, 0, 0.2, 1)`

## Typography

- Body: `Nunito` at weights 400–700
- Headings: `Playfair Display` at weights 600–700
- Code: `SF Mono`, monospace

The web fonts are loaded by `root_base.html`.

## Components

- Buttons use `--hw-radius-md`, `0.625rem 1.25rem` padding, gradient fills, and a `translateY(-2px)` hover lift.
- Cards use `rgba(255,255,255,0.9)`, `--hw-radius-lg`, `--hw-shadow-md`, and a `translateY(-4px)` hover lift.
- Forms use `rgba(255,255,255,0.8)`, a 2px sage border, `--hw-radius-md`, and a `rgba(135,168,120,0.15)` focus ring.
- Navbars use `rgba(255,255,255,0.7)`, `backdrop-filter: blur(12px)`, and `--hw-radius-lg`.
- Dropdowns and modals use `rgba(255,255,255,0.98)`, `backdrop-filter: blur(12px)`, and `--hw-radius-md`.
- Empty states use a centered 4rem icon at 0.3 opacity and muted text in a vertical flex layout.

## Animation

- Interactive elements use `--hw-transition-fast` or `--hw-transition-base`.
- Entry animation uses `fadeInUp`: opacity from 0 to 1 and vertical translation from 20px to 0 over 0.5s.
- Glow animation uses `pulse-glow` with a two-second infinite cycle.

## Templates and utilities

Template hierarchy:

`root_base.html` → `subpage_base.html` → subapp base → page template

Available utility classes include `.text-sage`, `.text-forest`, `.text-peach`, `.bg-honeydew`, `.bg-sage`, `.border-sage`, `.shadow-soft`, and `.rounded-xl`.
