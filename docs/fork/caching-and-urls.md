# Caching, headers and sensitive data in addresses

The aim: no document, page image, report or personal data is stored by a browser, a proxy or a log beyond what is needed, and nothing sensitive is carried in an address.

## Caching controls

| Where | Control | Notes |
|---|---|---|
| Every dynamic response of the application (API answers, errors, reports, page images, tiles) | `Cache-Control: no-store, private, max-age=0`, `Pragma: no-cache`, `Expires: 0` | `core/nocache.py`, a middleware in front of everything. A view that sets its own policy keeps it. Static files and `/media/` (logos) are exempt: they hold no document or personal data. |
| File service (`core`): downloads and previews | `Cache-Control: no-store, private, max-age=0`, `Referrer-Policy: no-referrer`, `Cross-Origin-Resource-Policy: same-origin`, `X-Content-Type-Options: nosniff` | Go test `TestDownloadAndPreviewAreNeverCacheable` |
| HTTPS front door (nginx) | for `/api/`, `/api-auth/`, `/files/` the upstream's cache headers are removed and `no-store` is set again, so a proxy or an upstream cannot weaken it | `deploy/nginx/edge.conf` |
| Production image | `index.html` is `no-cache, no-store`; `/assets/` (hashed JavaScript and CSS, no data) is cached for a year | `backend/docker.dist/runtime/coneshare.nginx.conf` |
| Page images | delivered by `POST`, painted into a canvas, never a URL; `no-store` as above | no address to cache, share or revisit |
| Sign-out (staff and check-out guests) | `Clear-Site-Data: "cache"` asks the browser to drop what it cached for the site | Chrome and Firefox; Safari ignores it |
| Back button | `no-store` pages are kept out of the browser's back/forward cache, so Back after sign-out does not show a document again | |
| `Referrer-Policy` | `no-referrer` site-wide (was `same-origin`) | no address, and nothing in it, is sent to any other page, including the NDA policy link, which also carries `rel="noopener noreferrer"` |
| In the server's memory | page bytes and tile images for 2 minutes at most (`PAGE_CACHE_SECONDS`, 0 switches off); never on disk | see [signals.md](signals.md) |

Not controllable from the server: a person's own screenshot, a browser extension that saves pages, or a proxy that ignores `no-store` (a TLS-intercepting corporate proxy can store what it decrypts; configure it not to cache these paths).

## What is in addresses

| Address | What it carries | Control | Residual risk |
|---|---|---|---|
| `/checkout#token=...` (e-mailed sign-in link) | a single-use, 15-minute token | in the **fragment**: browsers never send a fragment to a server, so it is not in access logs or a Referer. The page reads it once and removes it from the address bar immediately, and viewing the page does not use it up (a "Continue" click does). Links in older e-mails used `?token=`; they still work and are removed the same way. | the e-mail itself, and the address while the page loads |
| Share link `/s/<slug>` | the link's slug, which is the capability to reach the gate | by design; every gate (e-mailed code, password, NDA, expiry) is checked on each request | whoever holds the link reaches the gate, not the document |
| `?view_session_id=...` in the data-room viewer and as an NDA check | the id of an anonymous visit, accepted for the NDA step only | masked in the probe log; `Referrer-Policy: no-referrer`; access logs below | it is in browser history and in any log that keeps query strings. Removing it needs the anonymous NDA flow reworked (a cookie-bound session instead of an id in the address); not done. |
| `/files/download/<token>`, `/files/preview/<token>` | a signed, expiring file link (1 hour, 5 minutes for previews) issued to a signed-in owner | `no-store`; `no-referrer`; logged as `/files/download/[token]` | a link copied from the address bar works until it expires |
| Admin lists (`?email=`, Interaction Activity `?filter=`) | an e-mail address or filter typed by an administrator | staff only | in staff browser history |
| Document ids, page numbers | opaque ids and numbers | not secrets | none |

Request bodies (not addresses) carry passwords, access codes and NDA acceptance.

## Logs

* HTTPS front door: `deploy/nginx/edge.conf` logs method, path (without the query string), status and size; file links appear as `/files/download/[token]`.
* Production image: `backend/docker.dist/runtime/nginx/nginx.conf` does the same, and omits the Referer; gunicorn's access log omits the query string (`access_log_format`).
* Probe detection masks `token`, `code`, `password`, `secret`, `key`, `otp`, `view_session_id`, `session` and `auth` values before storing a query string.
* Django's development server (`runserver`) logs full addresses: development only.

## Checking it yourself

```bash
curl -sI https://localhost:8443/api/v1/languages/ --cacert certs/ca.crt | grep -i -E "cache-control|pragma|expires|referrer"
# expect: no-store, private, max-age=0 ... no-referrer
```

In a browser: DevTools > Network, open a check-out, click a request to `/pages/1/` (or a document download) and confirm `Cache-Control: no-store`; sign out and press Back: the document must not reappear. After requesting a sign-in link, the e-mail's address contains `#token=`, and the address bar shows `/checkout` with no token a moment after the page opens.
