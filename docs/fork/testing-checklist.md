# Testing checklist

None of this could be *run* where it was written (no Docker, Node, Go or Django
there). Start with `./quickstart.sh check` (migrations match models) and `./quickstart.sh test`
(all suites), and expect to fix small things the first time. On the first start `./quickstart.sh` asks for the administrator's e-mail address and prints a generated password once. Sign in with it: you are sent to *Settings > Password* and nothing else works until you choose your own (check: while the notice is showing, calling any other API URL answers `403` with code `password_change_required`; after the change you are signed out and sign in again). Without a mail server, e-mails are printed to the backend log
(`./quickstart.sh logs`), which is where access codes, sign-in links and invitations appear. To try the e-mail features, run `./quickstart.sh testmail-demo` (see *E-mail* below); to send real e-mail, configure your own server.

## Core security

| Item | How to check |
|---|---|
| No hard-coded secrets | `grep -rn "SECRET_KEY\s*=" backend/backend/settings.py` shows env-driven values; the app refuses to start in production without them |
| SSRF / DNS rebinding | Webhook or SMTP host pointing at `127.0.0.1`, `169.254.169.254`, or a private range is refused unless in `OUTBOUND_ALLOWED_NETWORKS`; connection goes to the validated IP only (`core/ssrf.py`) |
| Command injection from compose | `docker-compose.yml` has no shell-interpolated user input; the core service takes its config from env |
| JWT never in localStorage | DevTools → Application: no tokens in Local/Session Storage; cookies `cs_access`/`cs_refresh` are HttpOnly; unsafe requests carry `X-CSRFToken` |

## Secure document viewer

| Item | How to check |
|---|---|
| No page URLs | Open a share link; Network tab shows POSTs to `/links/<slug>/pages/<n>/`, never an image URL; pasting that URL in a tab → 405 |
| Script-only | `curl -X POST …/pages/1/` without `X-Page-Request: 1` → 403 |
| Canvas rendering | Elements panel: pages are `<canvas>`, there is no `<img>`/`<object>` with a file source |
| Honest limit | A signed-in viewer can still screenshot - this is deterrence, not DRM |

## Share-link allow-list and access log

| Item | How to check |
|---|---|
| Allow-list | Link settings → *Allowed emails*; add two addresses → each gets an invitation (log) |
| Domain rule | Enter `@example.com` (or `example.com`) instead of addresses → anyone at that domain can request a code; no invitation is sent; subdomains are not covered; revoke it and sessions are cut off |
| Open links | A link with **nothing listed** is open: no email prompt, downloads work as configured. Listing anything restricts it; revoking the last entry does *not* reopen it. `SHARELINK_REQUIRE_ALLOWLIST=True` forces every link to be restricted |
| Access code | Open the link, enter an allowed email → "An email you provided has requested access." → code in the log → enter it → document opens |
| No enumeration | Enter an address that is **not** listed → the same message, no e-mail sent, and a *Request denied* entry in the log |
| Single-use / expiry | Reuse the code → rejected; wait past `ACCESS_CODE_TTL_MINUTES` → rejected; 5 wrong codes lock it |
| Revoke | Revoke an entry while that person is viewing → their next page request is refused |
| Access log | Link settings → *Access log*: attempts, requests, allowed, denied, granted; filter dropdown works |
| Viewer activity | Document page and the link's analytics page → *Viewer activity*: per viewer, time spent plus print / screenshot key / save page / inspect / copy / right-click / tab hidden / focus lost counts (attempts highlighted) |
| Behaviour signals | With the link open: Ctrl+P, copy, right-click, PrintScreen, switching tab each add a *Viewer behaviour* entry |
| Add a signal | `frontend/src/lib/viewerEvents.js` → append one detector; add its type to `CLIENT_EVENT_TYPES` in `sharelinks/events.py` |

## Analytics

Dashboard → *Access activity*: filter *Access requests*, *Requests (denied)*, *Viewer behaviour*; e-mail filter; API: `/api/v1/analytics/access-events/` and `…/summary/`.

## Index page and file requests (/checkout)

| Item | How to check |
|---|---|
| Index page | Signed out, open `/`: two cards, *Sign in to Coneshare* and *File request* ("Enter your email to see accessible documents") |
| Assign guests | Sidebar → *Check Out Center* (admins): offer a datastore, add guests by **e-mail address** (each is told by e-mail), tick the files they can request. There is no domain setting |
| Enabled | A datastore starts **disabled** and refuses every guest (no sign-in link is mailed). It shows a red *Disabled - guests cannot sign in* badge and an *Enable now* button; the switch applies immediately, no Save. The check-out log's *Details* column says `reason=datastore_disabled` when a guest was turned away for this |
| Sign-in link | Enter a guest's address on the index page → e-mail with a link (single use, **15 minutes**, only for `/checkout`). Any other address: the same message, nothing is sent, *Denied* in the log |
| Link safety | Opening the link shows "Continue as g***@…" and only the button signs you in (mail scanners that open links cannot use it up). Reuse or expiry → "invalid or has expired" |
| Datastores | After signing in: the datastores you were given; a click drops down the files |
| NDA | With *NDA* on, the text must be accepted before requesting; editing the text asks everyone again |
| Request / open | *Request* → *Open*: the file is viewed in the app (pages in canvases, watermarked with your address and IP, no link, no download unless the datastore allows it); a countdown runs; *Check in* ends it early |
| Approval (experimental) | Tick *Requires admin approval…* on a file: the request shows *Waiting for approval*, the admin queue gets it (and the notify target is e-mailed); *Approve* → the guest is e-mailed and can open it; *Deny* blocks it |
| Limits | Max requests per guest per 24 h and the viewing time (≤ 5 min by default); signing in again does not reset them |
| Revoking a guest | Their open window ends at once |
| LDAP / SSO | Design only: `docs/fork/checkout-ldap-sso.md` |

## Access settings (on the file, not on the link)

Who can open a file and how it is protected is set on the **document or datastore**, never on the link: *Document page, Access settings*; a datastore file's menu, *Check-out access*, then *Who can open it...*; the upload dialog; or *Edit access settings* inside the link dialog. They cover allowed people (addresses and `@domain`), asking for or verifying the e-mail, password, NDA (versioned text), watermark, "never allow download" and the viewing protections (blur when unfocused, restricted pane).

| Check | How |
|---|---|
| Exists before any link | Set a password on a document that has no link, then create a link: opening it asks for the password |
| Every link inherits | Create a second link: same rules. Change a rule: both links follow at once |
| Link dialog is only about the link | *Create New Link* has name, e-mail notification, download, Q&A and expiry, plus *Edit access settings* |
| Stricter wins | An older link with its own password plus a document rule: both apply |
| Revoke | Remove an address from the file's list: that person's open session ends on the next request |
| Check-out follows the same rules | A checked-out file is blurred on the server with the restricted pane, refuses pages while the viewer is not in focus (423), uses the file's watermark text and never allows download when the file forbids it |
| Zoom | The check-out viewer zooms 50 to 300 %; clicking the percentage resets to 100 % |

Older links keep their own settings (the stricter of the two applies); only the link dialog stopped offering them.

## Check Out Center

Sidebar → *Check Out Center* (it moved out of the admin settings; `/admin/checkout` redirects there). Per datastore: enable it, add guests, set the viewing time, request limit, NDA text and default download rule, then the table of **every file in the datastore**, including one that was just uploaded:

| Column | Meaning |
|---|---|
| Visible | Shown to guests at all. A new upload is listed straight away (marked *new*) and stays hidden until you tick this |
| Admin approval | Each request waits in the approval queue (experimental) |
| NDA | Yes / No / Datastore default (the datastore's NDA text, accepted once per version) |
| Download | Yes / No / Datastore default (off unless allowed) |

**Setting a file's access when it is uploaded:** upload into a datastore (Datarooms) and a *Set check-out access for the new file(s)* dialog opens straight away (one choice applies to every file of that upload; *Later* skips it and the file stays hidden). The same dialog is in each file's menu as *Check-out access*, for anyone who manages the datastore, not only admins. Saving creates the datastore's check-out policy switched off if it has none; the dialog warns while the datastore is disabled or has no guests and links to this page.

No share link is involved at any point: these settings belong to the file in the datastore. Guests view it in the app; pictures and **Photoshop PSD files** are drawn on the fly as one watermarked page (flattened, at most 2400 px on the long edge, files up to 100 MB). A file whose preview is still being prepared says so and refreshes by itself; a failed preview says that; other types say they cannot be viewed in the browser (and point to Download when it is allowed).

## Interaction Activity

Sidebar → *Interaction Activity*: share-link events and check-out events across every datastore and file (admins see everything; members see their own links). Type a display filter and press Enter:

```
event == "print_attempt" && email contains "guest"
attempt && !(datastore == "Legal")
event in {save_attempt devtools_key screenshot_key} and time >= -24h
(source == checkout || link == "Room link") && ip startswith "10."
```

Fields `event category source email ip datastore file link time` and the flag `attempt`; operators `== != contains startswith`, `in {…}`, `> >= < <=` (time only), `&& || !` (or `and or not`) and parentheses; times as `2026-01-31`, `2026-01-31T10:00` or `-30m / -24h / -7d`. A mistake is shown with its position. Click any value in the table to filter on it, Alt-click to exclude it; click an event count to add it; presets and *Live* refresh are above the table.

**Capture reliability** (viewer behaviour signals, for share links and checked-out files): keys are matched by physical key (so layouts and Caps Lock don't matter), listeners run in the capture phase, repeated attempts are all kept (only a key's down/up pair is merged), failed reports are retried and handed to the browser's beacon when the page goes away. Still undetectable by any web page: *File → Save* from the menu, OS/phone screenshot tools, a camera.

## Security settings

Admin → *Security*:

| Item | How to check |
|---|---|
| Request throttling | *Security, Request throttling*: **off by default**. When switched on there are three per-minute limits: viewing (pages, tiles, focus reports, signals; default 1500), signed-in users (600) and everyone else (300). Over the limit the caller gets "Too many requests. Try again in N seconds..." naming the limits and where to change them. The old fixed limit (1000 requests per *day* per address, shared by every anonymous visitor) is gone; password attempts and e-mailed codes keep their own limits whatever this switch says |
| Print / screenshot / save attempts | *Security, Print, screenshot and save attempts*: for each signal pick **Ignore** (not even logged), **Log only** (default), **Log and e-mail me** (one e-mail per viewer and signal per 10 minutes), **Log and cover the page until the viewer continues**, or **Log and end the viewing** (a check-out is ended, a share-link viewer is locked out for the configured minutes). Routine focus changes (tab hidden, window blur) default to Ignore. A page can only see keys and print events; menu commands, OS screenshot tools and cameras are invisible to it |
| Code request limits | Set *Per e-mail address* to 2 → a third request for a share-link code or a file-request link is ignored (logged as throttled) |
| Failed sign-in alerts | Set an alert address; fail a login 3 times (or an access code) → one e-mail to the alert address and, if ticked, the targeted account; *Send test alert* checks the address |
| Suspicious requests (experimental) | Switch on. Rules are grouped like the **OWASP Core Rule Set** (911 methods, 913 scanners, 920 protocol, 921 header injection, 930 file inclusion, 931 RFI, 932 command execution, 934 generic, 941 XSS, 942 SQLi, 943 session fixation; 933 PHP and 944 Java start switched off) using the CRS data lists. Tick or untick whole groups or single rules. The log shows rule, matched text, request and whether a sign-in cookie was present. Never blocks. Bodies of small JSON/form requests are inspected too |
| Scan coverage | The dev server answers paths like `/.env` itself, so `frontend/security-edge.js` reports them to the backend (`/api/v1/security/inspect/`, internal token). Behind nginx, mirror requests to the same endpoint: `location / { mirror /_inspect; ... }  location = /_inspect { internal; proxy_pass http://backend:8000/api/v1/security/inspect/; proxy_set_header X-Internal-Token <INTERNAL_API_TOKEN>; ... }` (send method, uri, ip and headers as JSON, e.g. with `njs` or a small sidecar) |
| Alerts | One digest e-mail per cool-down with counts per rule, top sources and examples; events during the cool-down go out in the next digest (checked every minute) |

## Notifications

| Item | How to check |
|---|---|
| Removed | No Slack / Discord / WeChat / FeiShu anywhere in the UI or code; no rules/destinations/assignments |
| E-mail setup | *Notifications → E-mail setup* (admin): enter SMTP, *Send test e-mail to me*; the password is never shown again |
| Email on view | Link settings → *Email me* on; open the link as a viewer → one e-mail (repeat views within 10 min collapse) |
| Webhook | *Notifications → Webhooks*: add an HTTPS URL, pick events; *Send test*; the receiver verifies `X-Coneshare-Signature` = `sha256=HMAC(secret, raw body)` |
| Secrets | Webhook secrets are derived from `SECRET_KEY` (not stored) - *Rotate secret* changes them; the SMTP password is encrypted in the database |

## Experimental link options (done on the server)

Link settings → *Experimental viewing protections*.

| Option | What the server does | How to check |
|---|---|---|
| **Restricted viewing pane** | The page endpoint returns a **blurred** image (watermark drawn after the blur, so it stays sharp). Clear pixels leave the server only as small feathered patches around the cursor: `POST …/pages/<n>/spotlight/` (≈10 % of the page width, rate limited to `SPOTLIGHT_REQUESTS_PER_MINUTE`, logged as `spotlight_limit`) | Network tab: the `pages/<n>/` response is blurred. Move the mouse: small `spotlight/` PNGs come back. Removing the blur in DevTools shows nothing sharp - there is none |
| **Blur when not in focus** | The viewer heartbeats its focus to `POST …/focus/`. While unfocused the server answers page requests with 423 and the viewer empties its canvases and covers the page | Switch tab: requests for pages stop (423), canvases are empty; click back: pages reload |

What this does **not** do (it cannot): hide pixels from the browser that is allowed to show them. Whatever a viewer's screen can display has been delivered to their browser, so a photo of the screen always works, and a script driving a valid session could request enough spotlight patches to rebuild a page (slowed by the rate limit and visible in the access log). The pages are only ever sent blurred, and no URL is usable outside the viewer's own script, but this is deterrence, not DRM.

## E-mail

### Demo mail server (`./quickstart.sh testmail-demo`)

Starts a mail server and Roundcube (webmail, http://localhost:8081) next to the stack and points the app's outgoing mail at it. It creates `admin@lab.local` (application admin and mailbox), `user@lab.local` (application member and mailbox) and `guest@lab.local` (**mailbox only**, no application account, for use as a share-link viewer or check-out guest). One generated password, shown once, for all three. `./testmail-demo.sh check` sends a test message through the app and confirms it arrives; `./testmail-demo.sh down` removes the demo and puts e-mail back to the backend log. Demo only: no TLS, no spam filtering; its files are in the git-ignored `.testmail-demo/`.

### Without it

There is no mail server in the normal stack. By default the app prints every e-mail to the backend log:
`./quickstart.sh logs` and look for the `Subject:` line. To use your own mail server, set it in
*Admin > Notifications > E-mail setup* (host, port, credentials, sender) or `EMAIL_*` in `.env`.

Checks:
1. *E-mail setup > Send test e-mail to me* reaches your inbox.
2. The page has an explicit *Allow an unencrypted connection* opt-in (without it TLS is required and passwords are never sent unencrypted) and an *Inbox check* (IMAP) that sends and receives a test message.
3. The app refuses outbound connections to private addresses (SSRF protection). If your mail server is on a private network, list that network in `OUTBOUND_ALLOWED_NETWORKS` in `.env`.
4. Allow-list test: as admin, share a document, add a guest address (with the demo, `guest@lab.local`) to the allow-list: an invitation e-mail is produced. Open the link in a private window, enter the guest's address: an access code e-mail is produced. Enter an address that is not listed: the same message appears, nothing is sent, and *Request denied* shows in the access log.

## Sign-in notice and check-out session reports

**Notice before sign-in** (*Security settings → Notice before sign-in*): on by default, text editable, shown on the staff sign-in page, the landing page and the `/checkout` page. The default text is the wording you supplied, including its "You actions" typo; correct it in the settings box (or tell me and I will change the default). Public endpoint: `GET /api/v1/security/login-notice/`.

**Session report** (*Check Out Center → Sessions and reports → Session report*):
1. Settings: *Security → Check-out session reports → Record cursor movement during check-outs* (on by default; the sign-in notice tells guests they may be recorded).
2. Check a file out as `guest@example.com`, move the cursor over the pages, scroll, press a blocked key (print), check in. As admin open the session report: timeline of logged events, per-page map (blue = cursor time, green dashed = averaged path, orange = diagonal strokes), counts of horizontal/vertical/diagonal strokes, and a *confidence* line saying how many samples it rests on.
3. Switch the setting off, repeat: a new session records nothing and the report says tracing is off.

What is stored: per page, the cursor position as a fraction of the page (0..1000) every 100 ms while the tab is focused, and which page is at the centre of the screen once a second; at most 400 batches per check-out, 20 batches a minute. Nothing typed. The report is drawn on blank pages of the right shape; it never contains the document.

Limits to keep in mind: a cursor is not an eye (people read with the pointer parked elsewhere; touch screens have none), so this is a hint of where somebody read, not proof; diagonal strokes (25 to 65 degrees) are shown as a pattern worth a look, not as a finding. Migrations: `security.0004`, `checkout.0004`.

## Encryption, retention and the monitoring switch

* `./quickstart.sh` now generates certificates and a storage key; the app is at `https://localhost:8443`. See [container-security.md](container-security.md). Checks: the file `~/.coneshare-data/**` starts with `CSE1` and contains no readable text (`head -c 4 file`; `strings file | head`); `./quickstart.sh test` includes the Go cipher tests; `docker compose exec redis redis-cli ping` fails without TLS (the plain port is off).
* Data retention: see [data-retention.md](data-retention.md). Check: set a file's delete date a few minutes ahead (Python shell can shorten it), run *Delete them now*, confirm the file, its links and its report are gone and the log shows "overwritten". Migrations `documents.0011`, `retention.0001`, `security.0004`, `checkout.0005`, `checkout.0006`.
* Monitoring switch (Admin > Security): turn off, open a checked-out file, press a print key: nothing is logged, the session report says tracing is off. Turn on: logged again.
* Session-report data is encrypted in the database: `sqlite3 backend/db.sqlite3 "select moves_data from checkout_checkouttracechunk limit 1"` shows no readable numbers.

## Signals, findings and report additions

See [signals.md](signals.md). Checks: press PrintScreen (and Alt+PrintScreen) in a viewer: the viewer activity table shows Print Screen with `plain`/`alt` and "release only" counts; open the browser's developer tools docked: "Devtools open" appears within a few seconds; with a script user agent (`curl -H 'X-Page-Request: 1' ...` while signed in) `automation_detected` or `non_browser_request` appears; in a check-out press a screenshot key, then open its session report: "Around print, save and screenshot keys" lists what the pointer did either side, with straight runs and drags. Migration `checkout.0007`.

## NDA template and link

*Use the standard template* (next to the NDA text box in a file's Access settings and in the Check Out Center datastore settings) fills in the standard view-only, need-to-know wording: no screenshots, no OCR, no extraction, no AI/LLM uploads, AUP consequences, and a note that viewing is recorded. Edit it freely; any change makes a new version that guests must accept again. *Admin > Security > Link in the NDA window*: an address (and label) shown under the agreement in every NDA window, opening in a new tab; only `http://` and `https://` addresses are accepted; empty means no link. Migration `security.0005`.

## Approval e-mails, caching and addresses

* A request for a file that needs approval now e-mails the file's owner, the datastore's group address (if set) and, when the owner is not an administrator, the organization's administrators, whatever the datastore's notify setting is. Check: tick *Requires admin approval* on a file, request it as `guest@example.com`, read the owner's and admin's mailbox in Roundcube.
* Caching and addresses: see [caching-and-urls.md](caching-and-urls.md), including a `curl` check for the headers. The e-mailed check-out link now looks like `https://.../checkout#token=...`.

## Document activity includes check-outs

The *Viewer activity* table on a document page now lists check-out guests as well as share-link viewers, with a **Via** column (*Check-out* or *Share link*), so a document with no share link at all still shows who opened it through a check-out, for how long, and which keys were pressed (Print Screen with variants, print, save, devtools). A person who used both ways appears as two rows. Shown to the file's owner and to administrators. Check-out events recorded before this change are matched by file name; new ones carry the document id.

## Known gaps / follow-ups

* `ShareLink.password` is *encrypted* (reversible) via django-cryptography, as upstream; hashing it would be a safer change but needs a data migration.
* UI-configured SMTP: the host is validated and pinned at connect time; the environment-configured SMTP path is not pinned (it is operator-controlled).
* LDAP / SSO for `/checkout` is documented, not built.
