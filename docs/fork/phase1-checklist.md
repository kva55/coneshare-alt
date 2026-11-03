# Phase 1 (strip) — review checklist

Start the stack with `./quickstart.sh`, then log in at http://localhost:5173 as
`admin@coneshare.local` / `ConeshareAdmin!2026` (override with `ADMIN_EMAIL` /
`ADMIN_PASSWORD` env vars before the first run).

Run `./quickstart.sh check` (migrations match models) and `./quickstart.sh test`
(all test suites) first — neither could be run in the environment the changes
were made in.

When you are done: `./quickclose.sh` removes the containers, images, all
data and this folder (`--keep-code` keeps the source; it asks you to type
DELETE first, and warns about unpushed commits).

## Removed — confirm these are gone

| # | Item | How to check |
|---|------|--------------|
| 1 | Portal + MCP server | `portal/`, `mcp-server/` folders absent; no `portal`/`mcp_server` in `docker compose ps` |
| 2 | Cloud integrations | Documents → Upload menu shows only Files / Folder; no "Cloud storage" in the user menu; `/api/v1/cloud/providers/` → 404 |
| 3 | File requests + ClamAV | No "File Requests" in the sidebar; no "Request files" on folders; `/upload/<slug>` → not found; no Security alerts in Admin |
| 4 | Non-PDF previews | Upload a .docx / .png / .mp4: stored, listed, "preview not available"; upload a PDF: pages render |
| 5 | Client-side pdf.js | Share a PDF and open it: Network tab shows page images, never the .pdf file |
| 6 | Legacy v1 dataroom storage | No "Upgrade storage" banner/button anywhere; Admin → Datarooms filter has no "Legacy v1" |
| 7 | API keys | No "MCP & API keys" in settings or user menu; `/api/v1/api-keys/` → 404 |
| 8 | Public signup | No "Sign up" link on login; `/signup` → not found; `/api/v1/signup/` → 404 |
| 9 | Banner gallery + brand colours | Dataroom → Settings: banner modes are Single / None only; no colour pickers |
| 10 | Document versions | Document page: no upload-new-version button, no "Versions" menu item |
| 11 | Other languages | No language picker on login or viewer; localStorage has no `i18nextLng` |
| 12 | Swagger in production | Set `DEBUG=False` in `.env`, restart: `/api/schema/swagger/` → 404 |
| 13 | Broadcast email command | `docker compose exec backend python manage.py help` lists no `send_broadcast_email` |

## Kept — confirm still working

- Login / logout, admin user management, org branding, login activity
- Folders, uploads (single + folder upload), move, copy, trash/restore
- Datarooms: create, add content, folders, reorder, collaborators, transfer ownership, quota, single banner, logo override
- Share links for documents and datarooms: password, email verification, NDA, watermark, downloads, Q&A
- Analytics: dashboard, links, view sessions
- Automations: rules, destinations, delivery log (to be replaced in phase 7)

## Schema migrations added (hand-written)

- `core.0010_delete_apikey`
- `core.0011_english_only_user_language` (resets stored preferences to `en`)
- `datarooms.0008_remove_dataroom_storage_version`
- `datarooms.0009_remove_banner_gallery_and_brand_colors` (`gallery` → `single`)

`cloudfiles_*` and `filerequests_*` tables from an existing upstream database are
left in place; drop them manually if you migrated real data.
