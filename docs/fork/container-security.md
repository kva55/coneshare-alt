# Container security: encryption at rest and in transit

This fork is **encrypted by default**. Nothing needs to be switched on; the opt-outs are explicit and warned about.

## What is encrypted

| Data | Protection | Where it is configured |
|---|---|---|
| Uploaded files (originals, converted files, page images) on the storage volume | AES-256-GCM, 64 KiB chunks, done by the file service (`core`) before anything touches disk. Tampering and truncation are detected. | `STORAGE_ENCRYPTION=required` (default), `STORAGE_ENCRYPTION_KEY` in `.env` |
| Browser to app | HTTPS (TLS 1.2+) at the front door (`proxy`, nginx) on `https://localhost:8443` | `scripts/gen-certs.sh`, `deploy/nginx/edge.conf`, `HTTPS_PORT` |
| App to file service | TLS, certificate verified against the internal CA | `CORE_API_URL=https://core:8080`, `INTERNAL_CA_FILE` |
| App and workers to Redis | TLS (Redis' plain port is off), verified against the internal CA | `REDIS_URL=rediss://redis:6379` |
| Cookies | `Secure` (HTTPS only) | `AUTH_COOKIE_SECURE`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` |
| Check-out session reports (cursor traces) | Encrypted database columns (django-cryptography, key derived from `SECRET_KEY`) | automatic |
| Share-link passwords, SMTP password | Encrypted database columns | automatic |

Redis stores no documents and nothing is persisted (`--save "" --appendonly no`).

## First start

```bash
./quickstart.sh
```

It creates `.env` with fresh secrets, creates an internal CA and certificates in `./certs` (git-ignored), generates `STORAGE_ENCRYPTION_KEY`, starts the stack and prints the HTTPS address. Import `certs/ca.crt` into your browser to remove the certificate warning.

### The storage key

* It is generated **once** and kept in `.env`. **If it is lost, stored files cannot be read.** Back it up somewhere that is not the data volume (a password manager, a secrets store).
* `./quickstart.sh reset` deletes `.env`, the key, the certificates and the data together.
* Files uploaded before encryption was switched on are served as they are until converted. `quickstart.sh` converts them when it generates a key for an existing installation; run it by hand any time with `./quickstart.sh encrypt-existing`.
* **Rotation is not built.** Changing the key means decrypting every file with the old key and re-encrypting it with the new one, and there is no tool for that yet. Treat the key as long-lived and protect it (see known limits).

## Using your own internal CA

Put these files in `./certs` before running `./quickstart.sh` (existing files are never overwritten):

| File | Purpose | Must be valid for |
|---|---|---|
| `ca.crt` | the CA certificate that signed the three below | n/a |
| `edge.crt`, `edge.key` | the HTTPS front door | the host name users type (set `EDGE_HOSTNAMES=app.example.org` when generating, or supply your own) |
| `core.crt`, `core.key` | the file service | `core` (subjectAltName `DNS:core`) |
| `redis.crt`, `redis.key` | Redis | `redis` (subjectAltName `DNS:redis`) |

`INTERNAL_CA_FILE` (default `/certs/ca.crt`) is the CA every internal connection is verified against. At start the backend adds it to the public CA bundle (it does not replace it), so calls to outside services, such as webhooks, keep working. The front-end development server trusts it through `NODE_EXTRA_CA_CERTS`.

Set `SITE_DOMAIN` to the public HTTPS address (`https://app.example.org`) in `.env` for e-mailed links and cookie scope.

## Opt-outs (both are logged loudly)

| Command | Effect |
|---|---|
| `STORAGE_ENCRYPTION=off ./quickstart.sh` | stored files are written as uploaded; the file service logs a warning |
| `TLS=off ./quickstart.sh` | no certificates, plain HTTP on `http://localhost:5173`, Redis and file service unencrypted, cookies not `Secure` |

If `.env` already says `SITE_DOMAIN=http://...`, later commands (`stop`, `logs`) follow it.

## Database

The development default is SQLite in the backend folder. For anything real, use PostgreSQL with `DATABASE_URL=postgres://...?sslmode=verify-full` and keep the database on an encrypted volume or use your provider's encryption at rest. This repository does not run a database container, so database-engine encryption is a deployment decision; the sensitive columns above are encrypted by the application whatever the engine.

## Known limits

* The development servers behind the front door (Vite on 5173, Django `runserver` on 8000) speak plain HTTP on the private compose network. Only the front door, Redis and the file service use TLS there. A production deployment (nginx + gunicorn, see `backend/docker.dist`) should also terminate TLS in front of the application server or run it on a private network.
* Port 8000 (API) is still published on `127.0.0.1` for development and is plain HTTP.
* Application-level encryption protects against a stolen disk, volume snapshot or backup. It does not protect against someone who has both the key and the data, or against a compromised running service.
* Key rotation for stored files is not implemented.
* While a PDF is rendered into pages, the backend container writes the source PDF to a temporary file on its own disk, unencrypted, and deletes it afterwards. Run the backend and workers on an encrypted volume, or mount a memory-backed `/tmp` (tmpfs), if that matters.
* Database contents are not generally encrypted by the application: only check-out report data, share-link passwords and the SMTP password are encrypted columns.
* Development keys in `./certs` are readable by the containers (mode 644).
* Nothing here has been run by the author of the change; run `./quickstart.sh test` (it includes the Go tests for the cipher) before relying on it.
