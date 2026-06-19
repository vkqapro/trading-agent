---
name: Luminous Obsidian
colors:
  surface: '#131314'
  surface-dim: '#131314'
  surface-bright: '#3a393a'
  surface-container-lowest: '#0e0e0f'
  surface-container-low: '#1c1b1c'
  surface-container: '#201f20'
  surface-container-high: '#2a2a2b'
  surface-container-highest: '#353436'
  on-surface: '#e5e2e3'
  on-surface-variant: '#b9cacb'
  inverse-surface: '#e5e2e3'
  inverse-on-surface: '#313031'
  outline: '#849495'
  outline-variant: '#3b494b'
  surface-tint: '#00dbe9'
  primary: '#dbfcff'
  on-primary: '#00363a'
  primary-container: '#00f0ff'
  on-primary-container: '#006970'
  inverse-primary: '#006970'
  secondary: '#ffffff'
  on-secondary: '#283500'
  secondary-container: '#c3f400'
  on-secondary-container: '#556d00'
  tertiary: '#fff2fd'
  on-tertiary: '#520071'
  tertiary-container: '#f4ccff'
  on-tertiary-container: '#9900d0'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#7df4ff'
  primary-fixed-dim: '#00dbe9'
  on-primary-fixed: '#002022'
  on-primary-fixed-variant: '#004f54'
  secondary-fixed: '#c3f400'
  secondary-fixed-dim: '#abd600'
  on-secondary-fixed: '#161e00'
  on-secondary-fixed-variant: '#3c4d00'
  tertiary-fixed: '#f8d8ff'
  tertiary-fixed-dim: '#ecb2ff'
  on-tertiary-fixed: '#320047'
  on-tertiary-fixed-variant: '#74009f'
  background: '#131314'
  on-background: '#e5e2e3'
  surface-variant: '#353436'
typography:
  display-lg:
    fontFamily: Hanken Grotesk
    fontSize: 48px
    fontWeight: '600'
    lineHeight: '1.1'
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Hanken Grotesk
    fontSize: 32px
    fontWeight: '600'
    lineHeight: '1.2'
    letterSpacing: -0.01em
  headline-md:
    fontFamily: Hanken Grotesk
    fontSize: 24px
    fontWeight: '500'
    lineHeight: '1.3'
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: '1.6'
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: '1.5'
  label-md:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '500'
    lineHeight: '1.2'
    letterSpacing: 0.05em
  label-sm:
    fontFamily: JetBrains Mono
    fontSize: 10px
    fontWeight: '400'
    lineHeight: '1.2'
  headline-lg-mobile:
    fontFamily: Hanken Grotesk
    fontSize: 28px
    fontWeight: '600'
    lineHeight: '1.2'
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  base: 4px
  container-margin: 24px
  gutter: 16px
  card-padding: 20px
  section-gap: 32px
---

## Brand & Style

This design system is built for high-stakes trading environments where precision, clarity, and focus are paramount. It adopts a **Luminous Dark** aesthetic—a fusion of **Corporate Modern** structural rigor and **Glassmorphism** depth.

The brand personality is sophisticated and technical, designed to feel like a high-end instrument. It leverages a deep, monochromatic foundation to allow vibrant data visualizations to pop, creating a "glow-on-dark" effect that reduces eye strain during long trading sessions. The interface evokes a sense of "premium intelligence" through the use of translucent layers, ultra-thin borders, and subtle radial glows that guide the user’s eye to critical market movements.

## Colors

The palette is anchored by a deep obsidian background, creating a void-like space for high-contrast data. 

- **Primary (Cyan):** Used for primary actions, active trends, and focus states. It carries a soft outer glow in data visualizations.
- **Secondary (Lime):** Used for "Success" states, buy indicators, and secondary data clusters to provide a sharp, energetic contrast.
- **Tertiary (Magenta/Purple):** Reserved for specialized indicators, alternative data sets, or heatmaps.
- **Neutral:** A range of deep charcoals and semi-transparent whites. 

Color is used sparingly but intensely. Non-critical information remains in muted greys, while active data points utilize the full saturation of the accent colors.

## Typography

The typographic hierarchy balances editorial impact with technical precision. 

- **Headlines:** Use **Hanken Grotesk** for a sharp, modern feel that remains legible at large scales.
- **Body:** **Inter** provides the necessary neutrality and high readability for densly packed financial data and tooltips.
- **Technical/Labels:** **JetBrains Mono** is used for all numerical data, timestamps, and ticker symbols. The monospaced nature ensures that fluctuating numbers don't cause layout jumps and feel like "code-level" data.

Text colors should primarily be `Pure White` (90% opacity) for headers and `Slate Grey` (60% opacity) for secondary information.

## Layout & Spacing

The layout follows a **Fluid Grid** system optimized for information density without feeling cluttered. 

- **Grid Model:** A 12-column grid is used for desktop, collapsing to 1 column for mobile. 
- **Density:** Elements are spaced using a 4px baseline. Components like data tables use "Compact" spacing (8px gutters), while dashboard cards use "Default" spacing (16px gutters) to allow the glassmorphism effects breathing room.
- **Safe Zones:** A 24px margin is maintained around the viewport edges to ensure the UI feels framed and premium.

## Elevation & Depth

Hierarchy is achieved through **Glassmorphic Stacking** rather than traditional drop shadows.

1.  **Level 0 (Canvas):** Pure black `#050505`.
2.  **Level 1 (Cards):** Semi-transparent surfaces with a `16px` or `32px` backdrop blur. These surfaces feature a `1px` solid border (`rgba(255,255,255,0.08)`) to define edges.
3.  **Level 2 (Active States/Modals):** Increased transparency and a subtle inner glow. Hovering over a card should increase the border opacity to `0.2`.
4.  **Luminous Accents:** Critical interactive elements (like the 'Connect Wallet' button or active chart points) use a radial background glow in the primary color to simulate an emitted light source from behind the glass.

## Shapes

The design system uses a **Rounded** shape language to soften the "hard tech" feel. 

- **Primary Containers:** 1rem (16px) corner radius for main dashboard cards and modules.
- **Small Elements:** 0.5rem (8px) for input fields, buttons, and dropdowns.
- **Indicators:** Pills (fully rounded) for status chips and toggle switches.

All shapes should maintain the thin, 1px border profile to preserve the "crisp" aesthetic.

## Components

### Buttons
- **Primary:** Solid white background with black text. On hover, a faint Cyan glow effect appears behind the button.
- **Ghost:** Transparent background with a `1px` border. Text in Cyan or Lime depending on the action intent.

### Cards
- Standard containers use the `surface_card` variable with a `20px` padding. Headers within cards should have a thin bottom separator.

### Input Fields
- Dark backgrounds (`rgba(0,0,0,0.4)`) with a subtle `1px` border. On focus, the border transitions to the Primary Cyan color with a soft outer glow.

### Data Visualization
- **Line Charts:** Use a 2px stroke width with a gradient fill below the line that fades to transparent.
- **Chips/Badges:** Small, pill-shaped with low-opacity background tints (e.g., 10% Cyan background with 100% Cyan text).

### Lists & Tables
- Row hover states should utilize a slight increase in background brightness (`+2%`) rather than a color change, keeping the focus on the data.