# coneshare-alt — Feature & Dependency Inventory

Baseline: upstream `main` @ `a891a71`. Produced by static analysis of the source
(models, URL routes, Celery tasks, imports, package manifests). Nothing has been
changed in the code yet.

**How to use this doc:** mark each feature ID **KEEP / STRIP / MODIFY**. The
dependency notes under each one tell you what else is affected.

---

## 1. System architecture (services)

| ID | Service | Tech | Role | Coupling |
|----|---------|------|------|----------|
| S1 | `backend/` | Django 5.2 + DRF, JWT | The whole business API | Hub; everything talks to it |
| S2 | `core/` | Go (chi), ~600 LOC | File storage service: signed upload/download/preview URLs, copy/delete/file-info on `/storage` volume | Backend only (`documents/fileserver.py` client). **Required.** |
| S3 | `redis` | Redis 7 | Celery broker + Django cache | **Required** while Celery is used |
| S4 | `celery_worker` / `celery_beat` | Celery | Background jobs: previews, cloud import, webhooks, emails, nightly trash purge | **Required** (previews are async) |
| S5 | `video_worker` | Celery (separate queue) | ffmpeg → HLS video transcoding | Only for video preview (F2.6) |
| S6 | `frontend/` | React 19 + Vite + Tailwind + Radix | Admin/owner SPA + public viewer + public upload page | REST API only |
| S7 | `portal/` | Next.js | Marketing site, blog, SEO pages (incl. `zh`) | **None.** Standalone. |
| S8 | `mcp-server/` | Python MCP | AI-agent tools over the REST API (documents, datarooms, links, analytics, admin) | REST API only. Standalone. |
| S9 | `clamav` | ClamAV (optional profile) | Malware scanning of uploads | Only via `documents/malware_scan.py` (F7.3) |
| S10 | `e2e/` | Playwright | 2 spec files (auth, documents+sharing, datarooms) | Tests only |
| — | DB | SQLite default, Postgres via `DATABASE_URL` | | |

---

## 2. Backend dependency graph (Django apps)

Arrows = "imports from". **Bold** = module-level or model-level coupling
(hard). Others are inline/function-level imports (easier to cut).

```
                         ┌──────────────┐
                         │  core (L0)   │  Org, User, Group, APIKey, auth, settings, admin
                         └──────▲───────┘
                                │ (everything depends on core)
                         ┌──────┴───────┐
             ┌──────────►│ documents(L1)│◄───────────────┬──────────────┐
             │           └──┬────────▲──┘                │              │
             │   inline:    │        │ **vault_folder FK**│              │
             │   quota calc │        │ **DataroomDocument→Document**    │
             │              ▼        │                   │              │
             │           ┌───────────┴──┐           ┌────┴──────┐  ┌────┴────────┐
             │           │ datarooms(L2)│           │cloudfiles │◄─┤filerequests │
             │           └──▲────────┬──┘           └───────────┘  └────┬────────┘
             │   **FKs to   │        │ signals, stats                   │ events
             │   dataroom,  │        ▼                                  ▼
             │   DR doc/    │  ┌──────────────┐  **model save hook** ┌───────────┐
             └──────────────┴──┤ sharelinks   ├─────────────────────►│automations│
                               │   (L3 hub)   │◄─────────────────────┤           │
                               └──────▲───────┘   emails read views  └───────────┘
                                      │
                               ┌──────┴───────┐
                               │ analytics    │  (no models; read-only views over sharelinks)
                               └──────────────┘
```

Exact import counts (excluding tests/migrations):

| From → To | Where | Hardness |
|-----------|-------|----------|
| documents → datarooms | `services.py:125` quota calc (inline) | soft |
| documents → sharelinks | `views.py` view_sessions/stats actions, `serializers.py` (inline) | soft |
| datarooms → documents | models FK `vault_folder`, `DataroomDocument.document`; services, views | **hard** |
| datarooms → sharelinks | `signals.py`, `views.py`, `admin_views.py`, `serializers.py` | medium |
| sharelinks → documents | models FK `ShareLink.document`; views, archive, services | **hard** |
| sharelinks → datarooms | models FKs (`ShareLink.dataroom`, `ShareLinkDataroomSetting`, `DataroomVisit`, `QnAThread`); views, archive | **hard** |
| sharelinks → automations | `models.py:13` (save hook `ensure_default_email_automation`), `views.py:48` event dispatch | **hard (circular)** |
| automations → sharelinks | `emails.py` digests read `ViewSession`/`PageView` | **hard (circular)** |
| automations → datarooms | `serializers.py` (scope validation) | soft |
| analytics → sharelinks | all of it | **hard** (analytics is a view over sharelinks) |
| cloudfiles → documents | creates `Document`/`DocumentVersion` | hard (one-way, fine) |
| filerequests → documents | models FK `Folder`, `Document` | hard (one-way) |
| filerequests → cloudfiles | `UploadExportJob.connection` FK, export task | **hard** |
| filerequests → automations | event dispatch (3 sites) | soft |
| core → (all apps) | `admin_views.py`, mgmt commands `check_vault_integrity`, `reencrypt_data`, `create_test_data` | soft, but must be edited on any strip |

**Not modular by design:** `sharelinks ⇄ automations` is a cycle, and
`sharelinks` hard-depends on `datarooms`. The other relationships are one-way
and layer cleanly.

---

## 3. Feature inventory

Legend for **Strip effort**: 🟢 leaf, delete cleanly · 🟡 needs decoupling edits
· 🔴 foundational / deeply woven.

### F1. Platform & identity (`core`): can't be stripped, only trimmed

| ID | Feature | Notes / deps | Strip |
|----|---------|--------------|-------|
| F1.1 | Organizations (multi-tenant), users, user groups | Every model FKs `Organization` | 🔴 |
| F1.2 | JWT auth (login, refresh, logout/blacklist), set password | simplejwt | 🔴 |
| F1.3 | Public self-signup + email verification | `ENABLE_PUBLIC_SIGNUP` setting; Celery email | 🟢 |
| F1.4 | API keys (per-user) | `core/authentication.py`; MCP uses them | 🟢 |
| F1.5 | Login activity log (IP/geo/device) | geoip2 | 🟢 |
| F1.6 | Runtime app settings (DB-backed registry, 18 keys: preview limits, quotas, cloud creds, signup toggle, flatten downloads) | `core/settings_registry.py` | 🔴 (trim keys) |
| F1.7 | Admin console: settings, users (reset pw, recalc quota, export), org branding (logo), login activity, security alerts, admin dataroom mgmt | `core/admin_views.py` touches documents/datarooms/sharelinks/filerequests | 🟡 |
| F1.8 | Encrypted DB fields + key rotation command | django-cryptography-5; used by cloudfiles, automations | 🟡 |
| F1.9 | SSRF guard for outbound URLs | `core/ssrf.py`; used by webhooks/cloud | keep |
| F1.10 | Broadcast email command, vault integrity check, test-data seeding | management commands | 🟢 |
| F1.11 | i18n: 6 languages (en, de, fr, nl, ru, zh-Hans) backend + frontend | `locale/`, `frontend/src/locales`, `README_zh.md`, portal `zh/` | 🟡 (keep en only?) |
| F1.12 | OpenAPI schema + Swagger UI | drf-spectacular, `/api/schema/swagger/` | keep |
| F1.13 | Health check, Sentry error reporting | sentry-sdk (opt-in via env) | 🟢 |

### F2. Documents & files (`documents` + Go `core`): foundational

| ID | Feature | Notes / deps | Strip |
|----|---------|--------------|-------|
| F2.1 | Folder tree, documents, move, copy, ensure-paths (folder upload) | | 🔴 |
| F2.2 | Direct-to-storage uploads (request → signed URL → finalize) | Go core | 🔴 |
| F2.3 | Document versions (upload new, promote, list) | | 🟡 |
| F2.4 | Trash / soft-delete, restore, permanent delete, empty, nightly purge | celery_beat | 🟡 |
| F2.5 | Storage quotas (per-user / per-dataroom) | `FILE_SIZE_QUOTA_MB`; calls into datarooms | 🟡 |
| F2.6 | **Preview pipeline** (renderers): | Celery | |
| ‑ a | PDF → page images + link extraction + page text | pdf2image/poppler, pypdf | 🔴 if viewer kept |
| ‑ b | Office → PDF (LibreOffice headless) | `libreoffice` apt (~1 GB image) | 🟢 |
| ‑ c | Video → HLS stream | ffmpeg, `video_worker`, hls.js | 🟢 |
| ‑ d | HEIC → image transcode | pillow-heif | 🟢 |
| ‑ e | Spreadsheet preview | openpyxl | 🟢 |
| ‑ f | Markdown render | marked + dompurify (frontend) | 🟢 |
| ‑ g | Direct images, generic fallback | Pillow | keep |
| ‑ h | Alt engine: client-side pdf.js (`PDF_PREVIEW_ENGINE=pdfjs`) | pdfjs-dist | 🟢 (pick one engine) |
| F2.7 | Full-text search over page text (+ backfill command) | | 🟢 |
| F2.8 | Download (owner) | | keep |
| F2.9 | Rebuild preview, status polling | | keep |
| F2.10 | Malware scan helper (ClamAV INSTREAM) | used by filerequests only | 🟢 |

### F3. Datarooms (`datarooms`): 🔴 hard to strip once sharing is kept

| ID | Feature | Notes / deps |
|----|---------|--------------|
| F3.1 | Dataroom CRUD; content stored in a hidden "vault" `documents.Folder` (storage v2) | `vault_folder` FK |
| F3.2 | Dataroom folders/documents, add/remove/move, drag-reorder (`DataroomItemOrder`) | @dnd-kit/core |
| F3.3 | Direct upload into a dataroom | |
| F3.4 | Collaborators (single role "collaborator"), eligible-users list, ownership transfer | |
| F3.5 | Branding: banner, banner gallery (ordering), logo override, 3 brand colours | Pillow, ImageField (local media) |
| F3.6 | Per-dataroom storage quota, legacy v1→v2 storage upgrade | **v1 legacy code (~28 refs) is dead weight for a fresh fork** |
| F3.7 | File index toggle, Q&A toggle | ties to F4.7 |
| F3.8 | Dataroom stats / view sessions | reads sharelinks |
| F3.9 | Admin dataroom management | `datarooms/admin_views.py` |

To strip datarooms you'd also need to remove the `ShareLink.dataroom` FK,
`ShareLinkDataroomSetting`, `DataroomVisit`, the QnA dataroom FKs, the zip
download of a dataroom, automation scope `dataroom`, the quota branch in
documents, and frontend pages. That's a lot of edits, but it's mechanical.

### F4. Share links & secure viewer (`sharelinks`): the product's core, 3.6k-line views file

| ID | Feature | Notes / deps | Strip |
|----|---------|--------------|-------|
| F4.1 | Share link for a document **or** a dataroom; slug, expiry, active toggle | | 🔴 |
| F4.2 | Link templates (reusable presets) | `ShareLinkTemplate` | 🟢 |
| F4.3 | Access gates: password, require email, email verification (token), request access | | 🟡 each |
| F4.4 | NDA acceptance (versioned text, logged) | `NDAAcceptance` | 🟢 |
| F4.5 | Watermarking: server-rendered watermarked pages, flattened watermarked PDF download | reportlab, pypdf, pdf2image | 🟡 |
| F4.6 | Downloads: single file, dataroom folder zip, entire dataroom zip (streamed) | zipstream-ng | 🟡 |
| F4.7 | Q&A threads/messages between viewer and owner | `QnAThread/Message`; automation events | 🟡 |
| F4.8 | Per-item dataroom permissions on a link (`ShareLinkDataroomSetting`) | datarooms | 🟡 |
| F4.9 | Owner preview sessions (see what viewer sees) | | 🟢 |
| F4.10 | **Tracking:** viewers (identified by email), view sessions (IP, geo, UA), page views + duration, link clicks inside PDFs, dataroom visits, downloads | geoip2, ua-parser-js | 🔴 (analytics depends on it) |
| F4.11 | Legacy per-view notification email task | `sharelinks/tasks.py` **dead: only tests call it; superseded by automations email** | 🟢 delete |

### F5. Analytics (`analytics`): 🟢 leaf (read-only over F4.10)

Dashboard summary, daily visits chart (recharts on HomePage), all-links list,
all view sessions, session detail. There are no models, so removing it means
deleting the app plus 3 frontend pages and the HomePage widgets.

### F6. Automations (`automations`): 🟡 circular with sharelinks

| ID | Feature |
|----|---------|
| F6.1 | Destinations: Slack, Discord, WeChat Work, FeiShu, generic webhook (POST/PUT, HMAC-signed), email |
| F6.2 | Rules scoped global / per share link / per dataroom |
| F6.3 | Events: `document_viewed`, `dataroom_opened`, `document_downloaded`, `email_identified`, `qna_thread_created/message_created/closed/reopened`, `file_request_uploaded/malware_detected/scan_failed` |
| F6.4 | Delivery log with retries + dead-letter; email digest coalescing (60 s debounce) |
| F6.5 | Assignments (assign delivery to a user) |
| F6.6 | Auto-provisioned default email rule when a link enables "email notifications" |

**Owner view-notification emails now go through automations.** If you strip
automations but keep "notify me when viewed", that path has to be rebuilt.
Decoupling: replace the `sharelinks.models` save hook and the `views.py`
dispatch with a signal or a no-op event bus.

### F7. File requests (`filerequests`): 🟢/🟡 leaf-ish

| ID | Feature |
|----|---------|
| F7.1 | Public upload page (`/upload/:slug`) into a chosen folder |
| F7.2 | Upload log, uploader info (geo) |
| F7.3 | Malware scanning (ClamAV), security threat events → admin "Security alerts" page |
| F7.4 | Auto-export uploads to a cloud provider (needs F8) |
| F7.5 | Emits automation events (F6) |

### F8. Cloud integrations (`cloudfiles`): 🟢 leaf (only filerequests export depends on it)

OAuth connections to **Dropbox, Google Drive, Nextcloud**; browse remote files,
import as document or as a new version, refresh from source, folder mapping,
size limit. Credentials are stored encrypted (F1.8). Deps: `dropbox`,
`google-api-python-client`, `google-auth-oauthlib`, `httpx`.

### F9. Peripheral

| ID | Feature | Strip |
|----|---------|-------|
| F9.1 | `portal/` marketing site + blog + SEO pages | 🟢 delete dir + compose service + `make portal*` + release-please entry |
| F9.2 | `mcp-server/` AI agent tools | 🟢 delete dir + compose service |
| F9.3 | `docs/`, `plans/`, `agent-mem/`, `.agent/`, `AGENTS.md`, `DESIGN.md` | review; mostly upstream's planning notes |
| F9.4 | `release-please` versioning + `release.yml` (publishes upstream images) | adapt to fork |
| F9.5 | Production image (`backend/docker.dist`: nginx + supervisor + gunicorn) | keep/adapt |

---

## 4. Frontend map (`frontend/src`)

Single SPA. Features are already split into `components/<feature>/` folders and
barely import from each other (`viewer` and `documents` are the shared base),
so stripping the frontend is easy: delete pages, routes, nav entries, and
`api.js` functions.

| Route | Page | Feature |
|-------|------|---------|
| `/login`, `/signup`, `/signup/verify` | Login/Signup | F1.2, F1.3 |
| `/view/:slug` | ShareLinkViewerPage | F4 (public viewer) |
| `/upload/:slug` | PublicUploadPage | F7 |
| `/` | HomePage (dashboard, recharts) | F5 |
| `/documents…`, `/documents/:id/versions` | Documents | F2 |
| `/documents/:id/links/:linkId` | ShareLinkAnalyticsPage | F4/F5 |
| `/trash` | TrashPage | F2.4 |
| `/datarooms`, `/datarooms/:id` | Datarooms | F3 |
| `/file-requests…` | FileRequests | F7 |
| `/analytics/links`, `/analytics/view-sessions` | Analytics | F5 |
| `/automations` | AutomationsPage | F6 |
| `/settings`, `/password`, `/integrations`, `/api-keys` | Settings | F1, F8, F1.4 |
| `/auth/:provider/callback` | Cloud OAuth callback | F8 |
| `/admin/{settings,branding,users,datarooms,login-activity,security-alerts}` | Admin | F1.7, F3.9, F7.3 |

---

## 5. Dependency cleanup

### Remove now (unused / dead)

| Package | Where | Evidence |
|---------|-------|----------|
| `django-storages`, `boto3` | backend | No imports. Only referenced via `DEFAULT_FILE_STORAGE` in the `STORAGE_TYPE=MINIO` branch, **a setting Django 5.1+ removed**, so the MinIO path is already silently broken. Either delete it, or rewrite it with `STORAGES` if you want S3. |
| `@dnd-kit/sortable` | frontend | 0 imports |
| `@radix-ui/react-icons` | frontend | 1 icon in `PasswordInput.jsx`; `lucide-react` is used everywhere else, so swap that icon |
| `sharelinks.tasks.send_view_notification_email_task` (+ its templates/tests) | backend code | only called from tests |
| Commented `RegisterView` route; empty `tests.py`/`admin.py`/`models.py` stubs in `analytics`, `cloudfiles` | backend | stubs |
| Dataroom storage **v1** legacy paths + `upgrade_storage` actions | backend/frontend | a fresh fork has no v1 data |
| `nano`, `wget` in backend Dockerfile | image | not needed at runtime (`curl` covers healthchecks) |

### Move to dev-only (`requirements-dev.txt`)

`pytest`, `pytest-django`, `pytest-bdd`, `openapi-spec-validator` (used only
by a `make` target). `@vitest/ui` is already a devDependency (used only by
`test:ui`) and is optional.

### Drop explicit pins (transitive)

`asgiref`, `sqlparse` are pulled in by Django.

### Remove only if the feature is stripped

| Feature stripped | Backend deps freed | System/image | Frontend deps freed |
|------------------|-------------------|--------------|---------------------|
| F8 Cloud | `dropbox`, `google-api-python-client`, `google-auth-oauthlib` (httpx stays, core uses it) | | |
| F2.6b Office preview | | `libreoffice` (largest item in image) | |
| F2.6c Video | | `ffmpeg`, `video_worker` service | `hls.js` |
| F2.6d HEIC | `pillow-heif` | | |
| F2.6e Spreadsheet | `openpyxl` | | |
| F2.6f Markdown | | | `marked`, `dompurify` |
| F2.6h pdf.js engine | | | `pdfjs-dist` (+ worker polyfill) |
| F4.5 Watermark | `reportlab` (pypdf/pdf2image stay for PDF preview) | | |
| F4.6 Zip downloads | `zipstream-ng` | | |
| F4.10/F1.5 Geo | `geoip2` | GeoIP DB mount | |
| F5 Analytics | | | `recharts` |
| F6 + F8 both gone | `django-cryptography-5` (+ `reencrypt_data` command) | | |
| F7.3 Malware | | `clamav` service | |
| F1.11 i18n (en only) | locale files | `fonts-noto-cjk` (if no CJK docs) | `i18next*`, `react-i18next` (bigger refactor: every string goes through `t()`, so keeping the lib with only `en` is the cheaper option) |
| F1.13 Sentry | `sentry-sdk` | | |

---

## 6. Things worth deciding before stripping

1. **Is the share-link viewer (F4) the core of your product?** If yes,
   documents + sharelinks + Go core are the fixed spine, and everything
   else is optional.
2. **Datarooms (F3): keep or strip?** This is the biggest structural decision,
   because sharelinks is woven into it.
3. **Notifications:** if automations (F6) goes, do you still need "email me
   when someone views"? If so, that needs a small replacement.
4. **Multi-tenant orgs:** keep (cheap, already everywhere) or single-tenant?
5. **Preview formats:** which file types must be viewable? This drives image
   size more than anything else (LibreOffice, ffmpeg).
