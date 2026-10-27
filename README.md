# 🏛️ Town Hall

**Volunteer management for organizations that run on people.**

Town Hall helps parishes, nonprofits and community groups plan events, staff them with trained volunteers, and recognize the people who show up. Volunteers find opportunities, complete the training each role needs, sign up for shifts, check in at a kiosk, and build an impact record over time. Staff get a console to run all of it, and they can plan events with Claude.

Built with Django 6, HTMX and plain CSS. No SPA and no build step.

---

## ✨ What it does

### For volunteers
- **Find opportunities.** Browse and search published events, filter by category, and see every role, its time window and how many spots are left.
- **Train, then serve.** Each role can require training modules (lessons with Markdown and video, plus quizzes). Sign-up opens once the required modules are complete. Completions can expire, so certifications stay current.
- **Earn impact points and level up.** Shifts, training and endorsements earn points. Points set your level, and the Recognition page shows the whole ladder.
- **A record you can share.** Profile, endorsements from peers and staff, external certificates, and a printable volunteer résumé.
- **Know your team.** On large events you see your area lead and shift lead, plus a link to your own task list.
- **Reminders.** Shift reminder emails go out the day before each shift, and there are gentle nudges to finish your profile.

### For staff (the Console)
- **Events and staffing.** Add role slots with headcounts and overstaffing limits, use public or invite-only slots, send invite links, watch check-ins live, and get reports.
- **Chain of command for big events.** Group slots into areas, assign area leads, and let Town Hall pick shift leads automatically (the sign-up with the most impact points, 48 h before the event).
- **Roles and training editors.** Set role templates, attach mandatory modules, and write lessons and quizzes.
- **Venues.** Addresses are geocoded automatically. Venues have features, opening hours, short-lived notices, and their own kiosk.
- **Permanent staff.** Ongoing roles, optionally tied to a venue, that only assigned people can clock into.
- **Points rules.** Adjust points per hour, role weights, reach caps and "clutch" bonuses (walk-ins, last-minute sign-ups, early or late shifts), then recalculate history.
- **Communication.** Transactional emails and event announcements through [listmonk](https://listmonk.app), with per-language templates that follow your brand colors.
- **Make it yours.** Theme colors, logo, home page hero, languages and time zone, all from the console.

### Kiosk check-in 🖥️
Put a tablet at the door. Volunteers enter their 6-digit ID code to start and end shifts, either for an event or at a venue for permanent roles. The kiosk never logs anyone into the site.

### Plan events with Claude 🤖
Town Hall includes an **MCP server** with its own OAuth 2.1 server. Add `https://<your-host>/mcp` as a custom connector in Claude (Settings → Connectors), and staff can ask Claude to draft an event, estimate headcounts from past events, set up areas, build task lists, build training modules from material you provide (it agrees a lesson plan with you, then writes lessons one at a time that must quote your material, and leaves new modules unpublished for review), write quizzes from lessons, and invite volunteers. The scope is deliberately limited: nothing can be deleted, and people's profiles and settings are read-only.

### Task lists with Bell Tower 🔔
Connect your organization's [Bell Tower](#integrations) server and every event gets a planning list, day-of lists, and one list per role. Volunteers join their role's list automatically when they sign up. In the console you can drag tasks between lists and merge lists.

### Speaks your volunteers' language 🌍
The interface ships in **English, Spanish, French, Ukrainian, Traditional Chinese and Tagalog**. Content fields (events, roles, lessons) are translatable, and an **Auto-translate** button fills in missing translations through LibreTranslate, Google, DeepL or MyMemory.

---

## 🚀 Quick start

You need Python 3.12 or newer.

```powershell
git clone https://github.com/Collert/town_hall.git
cd town_hall
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

copy .env.example .env               # macOS/Linux: cp .env.example .env
# set SECRET_KEY in .env to any long random string

python manage.py migrate
python manage.py runserver
```

Open <http://127.0.0.1:8000>. On a fresh database, Town Hall sends you to **`/setup/`**, where you create the first admin account and name your organization. It then opens an **admin guide** training module that walks you through the app.

### Want demo data instead?
On a freshly migrated database, run:

```powershell
python manage.py populate_dummy_data
```

This creates a demo parish with volunteers, events, roles and training, with dates relative to today. Sign in as `admin` / `admin`, or as a volunteer such as `sofia.melnyk` / `password123`. The **Ukrainian Heritage Festival** shows the chain of command on a large event.

---

## ⚙️ Configuration

Most settings live in the app itself, under **Console → Organization** (identity, theme, region, points, backend connections). Environment variables cover only what has to be known at startup:

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Django secret key. **Required.** |
| `ENVIRONMENT` | Set to `production` to turn off `DEBUG`, use `DATABASE_URL` and store uploads in S3. |
| `APP_ORIGIN` | Comma-separated host name(s), e.g. `townhall.example.org`. Used for allowed hosts and CSRF. |
| `DATABASE_URL` | Production database, e.g. `postgres://…`. Development uses local SQLite. |
| `S3_ENDPOINT_URL`, `S3_BUCKET_NAME`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Production media storage (any S3-compatible service; we use RustFS). Optional: `S3_REGION`, `S3_PUBLIC_URL`. |
| `LISTMONK_URL`, `LISTMONK_API_USER`, `LISTMONK_API_TOKEN` | Fallback email settings if they aren't set in the console. |
| `BELLTOWER_URL`, `BELLTOWER_TOKEN` | Only used by `populate_dummy_data --belltower`. |
| `LIBRETRANSLATE_URL`, `GOOGLE_TRANSLATE_API_KEY`, `DEEPL_API_KEY`, `MYMEMORY_EMAIL` | Fallback auto-translate providers. |
| `BUGSINK_DSN`, `BUGSINK_SEND_PII` | Error reporting (see below). |

Without a listmonk connection, emails are printed to the runserver console. That's handy in development.

### 🐛 Error reporting and privacy
In production, Town Hall sends **anonymous crash reports** to the project's Bugsink so bugs get fixed. A report contains only the traceback and environment: no IP addresses, cookies, request bodies or local variables. Nothing is sent in development or during tests.

- **Opt out:** set `BUGSINK_DSN=` (empty).
- **Use your own Bugsink or Sentry:** set `BUGSINK_DSN` to your DSN.
- **Include request details** (only on a deployment you own): `BUGSINK_SEND_PII=1`.

---

## 🚢 Deployment

The repo deploys with **[Nixpacks](https://nixpacks.com)** (`nixpacks.toml`), for example on Coolify or Railway. The build compiles translations and collects static files. At startup it runs migrations and serves the app with gunicorn, with static files served by WhiteNoise.

For a production deploy, set at least `ENVIRONMENT=production`, `SECRET_KEY`, `APP_ORIGIN`, `DATABASE_URL` and the `S3_*` variables. HTTPS is expected to end at your reverse proxy. Town Hall trusts `X-Forwarded-Proto`, so OAuth metadata and email links use `https`.

To run the scheduled jobs from cron instead of relying on request-time middleware:

```bash
python manage.py send_shift_reminders --base-url https://townhall.example.org
python manage.py send_profile_reminders --base-url https://townhall.example.org
python manage.py assign_shift_leads
```

---

## 🔌 Integrations

| Service | What Town Hall uses it for | Required? |
| --- | --- | --- |
| [listmonk](https://listmonk.app) | Transactional email, event announcements, newsletter sign-up, password reset | Recommended |
| Bell Tower | Shared task lists for events and roles | Optional |
| S3-compatible storage | Media uploads in production | Production |
| Nominatim (OpenStreetMap) | Geocoding venue and event addresses | Automatic |
| LibreTranslate / Google / DeepL / MyMemory | Auto-translating content | Optional |
| Claude (MCP) | AI-assisted event planning for staff | Optional |

---

## 🗂️ Project layout

| App | Responsibility |
| --- | --- |
| `base` | Users and profiles, theming, home, auth, notifications, email, venues, points and levels |
| `events` | Events, role slots, sign-ups, invites, chain of command, reminders |
| `jobs` | Roles, training requirements, shifts, kiosk |
| `education` | Training modules, lessons, quizzes, certificates, skills |
| `console` | The staff portal |
| `mcp_server` | MCP endpoint and OAuth server for the Claude connector |
| `api` | API-key-authenticated endpoints for kiosk devices (not routed yet) |
| `town_hall` | Project settings, URLs and startup site configuration |

Every page lives under a language prefix (`/en/…`, `/uk/…`). The MCP and OAuth endpoints (`/mcp`, `/oauth/…`, `/.well-known/…`) live outside it.

---

## 🛠️ Development

```powershell
python manage.py test                 # run the full test suite
python manage.py test console         # just the console (covers most screens and flows)
python manage.py makemigrations; python manage.py migrate
python manage.py makemessages -l es   # refresh a translation catalog
python manage.py compilemessages
python manage.py recalculate_points   # reapply the current points rules to past shifts
python manage.py create_admin_guide   # add the admin guide to an existing install
```

A few house rules (details in [`CLAUDE.md`](CLAUDE.md) and [`DESIGN.md`](DESIGN.md)):

- **Server-rendered first.** Use function-based views and Django templates, and HTMX for interactivity. Write JavaScript only when HTMX can't do the job.
- **No Tailwind, ever.** Each template gets its own CSS file with the same name, and colors always come from CSS variables, so themes and dark mode just work.
- **Design language.** Use tone shifts instead of borders, rounded corners (1rem or more), Manrope headlines with Inter body text, and Material Symbols icons.
- **Translate everything.** Wrap user-facing strings in `gettext` in Python and `{% trans %}` in templates.

---

## 🤝 Contributing

Issues and pull requests are welcome. Before opening a PR:

1. Run `python manage.py test` and make sure it passes.
2. Read `DESIGN.md` if you're touching the UI.
3. Wrap any new user-facing strings for translation.

---

<p align="center"><i>Made for the people who set up the chairs, run the registration desk, and stay to clean up. 💛</i></p>
