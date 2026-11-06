# coneshare-alt

A hardened, minimal fork of [Coneshare](https://github.com/coneshare/coneshare) for sharing sensitive documents with people outside your organization. It keeps Coneshare's document, data-room and share-link core and changes how viewers are authenticated, watched, limited and deleted. It differs substantially from upstream; this README and [`docs/fork`](docs/fork/README.md) describe everything that does.

> The features here were written without being run in the author's environment. Run `./quickstart.sh check` and `./quickstart.sh test`, and work through [docs/fork/testing-checklist.md](docs/fork/testing-checklist.md), before relying on any of it.

## What it does

* **Viewing:** documents are only shown in a canvas by the app's own script. Pages are POSTed, never a URL; blur and watermark are applied on the server.
* **Share links:** allow-lists by address or domain, an e-mailed access code, NDA, password, watermark, view-only options. A link with no rules is open.
* **Check-out (`/checkout`):** per-guest access by e-mailed 15-minute single-use link, NDA, per-file approval, timed in-app viewing, session reports.
* **Monitoring and findings:** Print Screen, print, save and developer-tools signals, tamper and automation findings, configurable responses (ignore, log, notify, blur, end), a Wireshark-style filter over interactions.
* **Security settings:** e-mailed code limits, failed-sign-in alerts, optional request throttling, log-only probe detection, a sign-in notice, a master monitoring switch.
* **Data protection:** AES-256-GCM files at rest, TLS to the app, the file service and Redis (internal CA supported), `no-store` on everything dynamic, data retention with hard delete.
* **Not built:** end-to-end encryption (designed only, see [docs/fork/e2e-encryption.md](docs/fork/e2e-encryption.md)).

Every control, its default and where it is changed is in [docs/fork/README.md](docs/fork/README.md).

## Start

Needs Docker with the Compose plugin and `openssl`.

```bash
./quickstart.sh
```

It builds the stack, creates `.env` with fresh secrets, makes an internal CA and certificates in `./certs`, generates the storage key, runs migrations and creates an admin account (the address and a random password are printed at the end). Open `https://localhost:8443` and import `certs/ca.crt` into your browser to trust it.

There is no mail server in the stack: e-mails (access codes, sign-in links, approval requests) are printed in the backend log (`./quickstart.sh logs`) until you configure your own under *Admin > Notifications > E-mail setup* or `EMAIL_*` in `.env`.

| Command | |
|---|---|
| `./quickstart.sh test` | backend, frontend and Go tests, in the containers |
| `./quickstart.sh check` | migrations match the models |
| `./quickstart.sh encrypt-existing` | encrypt files stored before encryption was on |
| `./quickstart.sh logs` / `stop` / `reset` | follow logs / stop / delete local data |
| `./quickclose.sh` | remove containers, images, data and this folder |

`.env` holds the storage encryption key: **losing it makes stored files unreadable**, so back it up. Opt-outs (`TLS=off`, `STORAGE_ENCRYPTION=off`) are explicit and warned about; see [docs/fork/container-security.md](docs/fork/container-security.md). Running on an internal network, with your own subdomain or nginx: [docs/fork/internal-deployment.md](docs/fork/internal-deployment.md).

## How movement and behaviour detection works (transparency)

This fork can show an administrator where a guest's pointer went while a file was checked out. What is collected, how it becomes a report, and what it cannot tell you:

* **Collected, in the guest's browser:** the pointer's position on the page (as a fraction of the page, 0 to 1000 each way, never pixels) at most every 100 ms; which page is in the middle of the screen once a second; left-button presses. Only while the tab is visible and focused. No keystrokes, no page content, no screenshots.
* **Kept:** encrypted in the database, in bounded batches, only while the check-out is open (plus two minutes), deleted with the file by data retention.
* **Turned into:** time spent per page, a heat grid (20 x 28), a typical path, horizontal / vertical / diagonal strokes (diagonal = 25 to 65 degrees from horizontal), straight runs (at least 5 samples and 8% of the page, within 3% of a line), and, for each print, save, screenshot or devtools key, what the pointer did in the 5 seconds either side.
* **Why:** the aim is "where is reading happening" for sensitive files; fractions and no content answer that without collecting more than needed. Gaps over 0.6 s are not counted as dwell, so idle time is not counted as reading.
* **Not proof:** a pointer is not an eye; slow page loading currently looks like movement; browsers cannot see the Fn key or shortcuts the operating system swallows; a determined person can block or fake the reports. Treat it as a hint and read the confidence line on each report.
* **On by default, switchable:** *Admin > Security*. Guests are told by the sign-in notice that actions may be recorded.

Full detail, every threshold and the reasoning: [docs/fork/movement-detection.md](docs/fork/movement-detection.md).

## Documentation

| | |
|---|---|
| [docs/fork/README.md](docs/fork/README.md) | every control, default, and where to change it |
| [docs/fork/movement-detection.md](docs/fork/movement-detection.md) | how behaviour is collected and reports computed, and the limits |
| [docs/fork/signals.md](docs/fork/signals.md) | signals and findings, what each can and cannot see |
| [docs/fork/container-security.md](docs/fork/container-security.md) | encryption, TLS, internal CA, opt-outs |
| [docs/fork/caching-and-urls.md](docs/fork/caching-and-urls.md) | caching, headers, what is in addresses and logs |
| [docs/fork/data-retention.md](docs/fork/data-retention.md) | per-file and global deletion, hard delete |
| [docs/fork/internal-deployment.md](docs/fork/internal-deployment.md) | internal subdomain, nginx |
| [docs/fork/removed-from-upstream.md](docs/fork/removed-from-upstream.md) | what was removed |
| [docs/fork/testing-checklist.md](docs/fork/testing-checklist.md) | how to check each feature |
| [docs/fork/e2e-encryption.md](docs/fork/e2e-encryption.md), [docs/fork/checkout-ldap-sso.md](docs/fork/checkout-ldap-sso.md) | designs, not built |

## Licence and origin

MIT, see [LICENSE](LICENSE). Based on Coneshare (Copyright (c) 2025-2026 Coneshare). The probe-detection rule data in `backend/security/crs` comes from the OWASP Core Rule Set (Apache-2.0), see [its notice](backend/security/crs/NOTICE.md).
