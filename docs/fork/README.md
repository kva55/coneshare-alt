# coneshare-alt: what this fork changes

This is a hardened, minimal fork of Coneshare for sharing sensitive documents. It differs from the original in what it removes, how it authenticates viewers, how it watches and limits them, and how it stores and deletes data. This page lists every control and where it is changed. Settings live in the admin area unless a file or environment variable is named.

**Status of this document:** the features below were written without being run in the author's environment. Run `./quickstart.sh check` and `./quickstart.sh test`, and follow [testing-checklist.md](testing-checklist.md), before relying on any of it.

## Start

```bash
./quickstart.sh          # builds, creates .env, certificates and keys, migrates, creates accounts
```

Open `https://localhost:8443` (import `certs/ca.crt` to trust it). The admin account and its password are printed at the end of `./quickstart.sh`. On the first start it asks for the administrator's address and shows a generated password once, which must be changed at first sign-in. There is no mail server in the normal stack; e-mails are printed in the backend log, and `./quickstart.sh testmail-demo` adds a demo mail server and webmail (see [testing-checklist.md](testing-checklist.md)).

## Controls

| Area | What it does | Default | Where | Detail |
|---|---|---|---|---|
| Encryption at rest | AES-256-GCM for stored files | **on** | `.env` `STORAGE_ENCRYPTION`, `STORAGE_ENCRYPTION_KEY` | [container-security.md](container-security.md) |
| TLS in transit | HTTPS front door, TLS to file service and Redis, internal CA support | **on** | `certs/`, `INTERNAL_CA_FILE`, `TLS=off` | [container-security.md](container-security.md) |
| Report encryption | Check-out session-report data encrypted in the database | **on** | automatic | [container-security.md](container-security.md) |
| End-to-end encryption | Browser-side encryption per file | **not built** | n/a | [e2e-encryption.md](e2e-encryption.md) |
| Data retention | Per-file delete date, global policy, hard delete with NIST 800-88 Clear overwrite | off | Admin > Data retention; document page | [data-retention.md](data-retention.md) |
| Monitoring master switch | Viewer behaviour, capture responses, cursor tracing, interaction analysis | **on** | Admin > Security | below |
| Sign-in notice | Compliance notice before sign-in, editable text | **on** | Admin > Security | below |
| Session reports | Per check-out: timeline, time on page, cursor heat, averaged path, diagonal strokes | **on** (needs monitoring on) | Admin > Security; Check Out Center > Sessions | [testing-checklist.md](testing-checklist.md) |
| Capture signals and responses | Print Screen (with variants), print, save, devtools keys and devtools open, copy, right-click, drag, focus: ignore, log, notify, blur or end | log | Admin > Security | [signals.md](signals.md) |
| No caching, headers, nothing sensitive in addresses | `no-store` on every dynamic response and file download, `Clear-Site-Data` on sign-out, `no-referrer`, tokens in URL fragments, log redaction | **on** | automatic | [caching-and-urls.md](caching-and-urls.md) |
| Tamper and automation findings | Patched browser functions, webdriver or headless browsers, requests that are not from the viewer's script, tiles fetched with no cursor | log | Admin > Security | [signals.md](signals.md) |
| Request throttling | Optional per-minute limits with a clear message | off | Admin > Security | checklist |
| E-mailed code limits and failed-sign-in alerts | Per address and per IP limits; alert after repeated failures | on | Admin > Security | checklist |
| Probe detection | Log-only detection of scans using OWASP CRS-derived rule groups, digest alerts | off | Admin > Security | checklist |
| Share-link access | Allow-lists (addresses, whole domains), e-mailed access code, NDA, password, watermark, blur, restricted pane; links with no rules are open | per link | file's Access settings | checklist |
| Check-out (`/checkout`) | Per-guest access, e-mailed 15-minute single-use link, NDA, per-file approval, timed in-app viewing | off per datastore | Check Out Center | checklist |
| Viewing | Pages painted to a canvas by script; no page URL; server-side blur and tiles; client signals | n/a | per file | checklist |
| Interaction Activity | Wireshark-style filter language over interactions | on | Interaction Activity page | checklist |
| Webhook and e-mail | One signed generic webhook, "e-mail me when viewed", SMTP and IMAP test | n/a | Notifications | checklist |
| Outbound requests | SSRF and DNS-rebinding protection, pinned connections | on | `OUTBOUND_ALLOWED_NETWORKS` | checklist |
| Tokens | JWT only in HttpOnly cookies, never in browser storage | on | n/a | checklist |

### Monitoring master switch

Admin > Security > *Monitoring and interaction analysis*. When off, the app stops collecting viewer signals (print, copy, screenshot keys, focus changes), cursor tracing and the data behind Interaction Activity and session reports. Because the responses (blur, end) depend on those signals, they stop too. It does **not** stop access records (who asked, who was admitted or refused), failed-sign-in alerts or probe detection. Data already collected is kept (use Data retention to remove it).

### Sign-in notice

Admin > Security > *Notice before sign-in*: shown on the sign-in page, the landing page and `/checkout` before anyone authenticates. The default wording is the text supplied with the requirement, including its typo ("You actions"); edit it in the settings.

## Deploying

* [container-security.md](container-security.md): encryption, TLS, internal CA, opt-outs.
* [movement-detection.md](movement-detection.md): exactly what behaviour is collected, how reports are computed, why, and the limits.
* [caching-and-urls.md](caching-and-urls.md): caching, headers, what is and is not in addresses and logs.
* [internal-deployment.md](internal-deployment.md): giving the system an internal subdomain, and using nginx as the proxy.

## What was removed

See [removed-from-upstream.md](removed-from-upstream.md).
