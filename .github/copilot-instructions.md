# Project Guidelines

## Code Style
- **Python Framework:** Standard Django architecture (Models, Views, Templates). Use Django built-in methods (`get_object_or_404`, `messages`, translations `gettext_lazy`, etc.).
- **UI & Styling:** Follow the "Empowered Architect" design system (see `DESIGN.md`). Avoid clinical, flat aesthetics. Never use Tailwind, always use custom CSS. CSS files should be organized by feature (e.g., `training_directory.css`) and imported in the corresponding template. never do inline styles except for dynamic values that cannot be predefined in CSS.
- **Tonal Layering:** Use background color shifts (`surface_container` tiers) instead of 1px solid borders for structural boundaries (the "No-Line" rule).
- **Typography:** Use **Manrope** for display/headlines (editorial feel) and **Inter** for body/functional text.

## Architecture
- **Django Apps:** `base` (core settings, shared components, home), `education` (training modules, quizzes), `events` (calendar, event details), `jobs` (career pages).
- **Design System Tracker:** Reference `DESIGN.md` for UI implementation. Use "Glassmorphism" for floating overlays (`surface_variant` at 70% opacity with 20px backdrop-blur).

## Build and Test
- **Virtual Environment:** Activate via `.\.venv\Scripts\Activate.ps1` (for Windows).
- **Run Server:** `python manage.py runserver`
- **Database Migrations:** `python manage.py makemigrations` and `python manage.py migrate` (Local database is `db.sqlite3`).
- **Dependencies:** Install with `pip install -r requirements.txt`.

## Conventions
- **Colors:** Use colour variables that can be found in `base/templates/base/layout.html` (e.g., `var(--color-primary)`, `var(--color-text-primary)`). Avoid hardcoding hex values.
- **Elevation:** Do not use black (`#000000`) shadows. Stacking is the primary method of elevation. Ambient shadows should be tinted with `on_surface` (`#0e1e1e`).
- **Components:** Primary buttons use a gradient fill from secondary to secondary_container. 
- **Borders:** If a boundary is strictly required for accessibility, use a "Ghost Border" (`outline_variant` at 15% opacity). Never use 100% opaque lines.
