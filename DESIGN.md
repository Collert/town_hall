# Design System Specification

## 1. Overview & Creative North Star
This design system is built upon the Creative North Star of **"The Empowered Architect."** It moves away from the clinical, flat aesthetics of traditional management software and instead adopts a high-end editorial feel that balances authoritative structure with organic warmth. 

To achieve this, the system rejects rigid, line-heavy grids in favor of **Tonal Layering**. By using overlapping surfaces, intentional asymmetry in layouts, and a sophisticated contrast between deep teals and vibrant oranges, we create an environment that feels both professional and deeply human. The design should feel less like a "tool" and more like a premium, curated experience for those who build communities.

## 2. Colors
Our palette is rooted in the depth of the forest and the energy of a spark. It is designed to be immersive rather than distracting.

### Color Strategy
*   **The "No-Line" Rule:** 1px solid borders are strictly prohibited for sectioning. Structural boundaries must be defined solely by background color shifts (e.g., a `surface_container_low` section sitting on a `surface` background).
*   **Surface Hierarchy & Nesting:** Treat the UI as physical layers of fine paper. Use the `surface_container` tiers (Lowest to Highest) to define importance. An inner card should use `surface_container_lowest` to "lift" off a `surface_container_low` background.
*   **The "Glass & Gradient" Rule:** For floating modals or navigation overlays, utilize Glassmorphism (`surface_variant` at 70% opacity with a 20px backdrop-blur).
*   **Signature Textures:** Main CTAs and hero backgrounds should utilize subtle linear gradients—transitioning from `primary` (#00434d) to `primary_container` (#005c69)—to add "soul" and dimension.

### Key Tokens
*   **Primary (Deep Teal):** `#00434d` — Use for high-authority branding and primary containers.
*   **Secondary (Vibrant Orange):** `#ac3509` — Reserved strictly for action-oriented CTAs and critical highlights.
*   **Background (Mint Tint):** `#ebfdfc` — The soft, breathable canvas for the entire platform.
*   **Surface Tiers:** Use `surface_container` (`#dff1f0`) through `surface_dim` (`#ccdedd`) to create environmental depth.

## 3. Typography
The typography system uses a pairing of **Manrope** for expressive editorial moments and **Inter** for high-utility data.

*   **Display & Headlines (Manrope):** These are our "Editorial Voices." With a generous scale (up to `display-lg` at 3.5rem), use these to create intentional white space and clear messaging. The tight tracking and geometric builds of Manrope convey modern authority.
*   **Body & Titles (Inter):** These are our "Workhorses." Inter is used for all functional text, ensuring maximum readability in complex volunteer schedules and training modules.
*   **Visual Hierarchy:** Establish dominance through scale, not just weight. A `headline-lg` in `primary` color provides more "organizational feel" than a bolded small font.

## 4. Elevation & Depth
In this design system, shadows are an accent, not a structural necessity.

*   **The Layering Principle:** Stacking is the primary method of elevation. A `surface_container_highest` (#d4e6e5) element placed on a `background` (#ebfdfc) creates a natural, soft-edge elevation.
*   **Ambient Shadows:** For floating elements (like a "Start Tracking" button), use a 32px blur with 6% opacity. The shadow color must be a tinted version of `on_surface` (#0e1e1e) to ensure the shadow feels like a natural part of the mint-toned environment.
*   **The "Ghost Border" Fallback:** If a boundary is required for accessibility, use the `outline_variant` token at 15% opacity. Never use 100% opaque lines.
*   **Glassmorphism:** Use `surface_container_low` with a `backdrop-filter: blur(12px)` for mobile navigation and dropdown menus to keep the user grounded in their previous context.

## 5. Components

### Buttons
*   **Primary:** A gradient fill (`secondary` to `secondary_container`) with `lg` (2rem) or `full` roundness. Padding: `spacing-2` vertical, `spacing-4` horizontal.
*   **Tertiary:** No background or border. Use `primary` text with a small icon. Interaction is shown through a subtle `surface_variant` background fill on hover.

### Cards & Modules
*   **Container:** Use `surface_container_lowest` with `md` (1.5rem) roundedness. 
*   **Layout:** Strictly avoid divider lines. Use `spacing-3` to separate headers from body content.
*   **Image Integration:** Photos of volunteers should use `DEFAULT` (1rem) rounded corners to feel integrated into the "modular" vibe.

### Input Fields
*   **Styling:** Use a `surface_container_high` background with a "Ghost Border" (15% opacity `outline`). 
*   **Focus State:** Transition the border to 100% `primary` and add a soft 4px `primary_fixed` outer glow.

### Specialized Components
*   **Impact Tracker:** A custom high-contrast module using `headline-lg` numbers in `primary` color, nested within a `surface_container_highest` pill shape.
*   **Training Chips:** Small `label-md` badges with `full` roundedness, using `tertiary_fixed` (#84f5e8) for a soft "completed" state.

## 6. Do's and Don'ts

### Do
*   **Do** use asymmetrical margins (e.g., a wider left margin for headlines) to create a premium editorial rhythm.
*   **Do** allow elements to overlap (e.g., an image card slightly breaking the boundary of a teal background section) to create depth.
*   **Do** use icons for Roles, Training, and Time Tracking consistently, utilizing the `outline` color token.

### Don't
*   **Don't** use black (#000000) for shadows or text. Always use the `on_surface` (#0e1e1e) or `primary` variants to maintain the tonal "Mint/Teal" world.
*   **Don't** use standard 4px or 8px border radii. This system relies on "Modular Softness"—stick to `DEFAULT` (1rem) or larger.
*   **Don't** use dividers to separate list items. Use a subtle background toggle between `surface` and `surface_container_low`.