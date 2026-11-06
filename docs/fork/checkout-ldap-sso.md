# /checkout - planned LDAP / SSO authentication (design only)

**Status: planned, not implemented.** Today `/checkout` authenticates a guest by e-mail address: the guest must be assigned
to a datastore and signs in with a single-use, 15-minute link e-mailed to them
(`backend/checkout/services.py`).
This document describes how directory-backed sign-in would replace or extend that step without
changing anything after it (sessions, attempt limits, windows, logging).

## Where it plugs in

Everything after sign-in depends on exactly one thing: a `CheckoutSession` row whose `email`
identifies the visitor, referenced from the Django session (`checkout_session_id`). The
check-out rules never look at *how* the visitor proved who they are. A new method therefore only
has to do two things: establish a trustworthy e-mail, then call the same session-creation code
that `verify_code` uses. Add a `method` field to `CheckoutSession` (`otp`, `ldap`, `oidc`) so the
log and the admin dashboard can show how someone signed in.

The per-guest assignment stays as the first authorization gate in every method (for LDAP/SSO it could be
replaced by a directory group, see below). A directory group
check is added as a second, optional gate per policy (see "Policy changes").

## Option A - LDAP / Active Directory bind (username + password)

* Library: `ldap3` (pure Python; no system packages).
* Flow: visitor enters directory username + password on `/checkout`; the backend does a
  **service-account search** for the user's DN (`sAMAccountName` or `userPrincipalName`), then a
  **user bind** with the supplied password. A successful bind proves the password; the entry's
  `mail` attribute becomes the session e-mail; `memberOf` yields groups.
* Transport: LDAPS (636) or StartTLS only, certificate verification **on**, CA bundle from
  `LDAP_CA_FILE`. Refuse to start with plain `ldap://` unless `LDAP_ALLOW_INSECURE=true` (dev).
* Injection: escape the username with `ldap3.utils.conv.escape_filter_chars`; never build
  filters with string formatting. Reject empty passwords (an empty password makes many
  servers perform an *anonymous* bind that "succeeds").
* Throttling: reuse the per-email / per-IP request counters in `CheckoutEvent`; add a lockout
  that does not depend on the directory's own lockout so a spray cannot lock real users out
  of AD (count failures per IP and per username here, fail closed after N).
* Secrets: service-account DN and password come from environment / secret store
  (`LDAP_BIND_DN`, `LDAP_BIND_PASSWORD`); nothing in the repository or the database.
* Settings (all environment): `LDAP_URI`, `LDAP_BASE_DN`, `LDAP_USER_FILTER`,
  `LDAP_MAIL_ATTRIBUTE`, `LDAP_GROUP_ATTRIBUTE`, `LDAP_TIMEOUT_SECONDS`, `LDAP_CA_FILE`.
* Failure behaviour: directory unreachable → sign-in fails closed with the generic message;
  the OTP method (if enabled) keeps working, so check-out does not depend on the DC.

Trade-off: the application handles directory passwords. Prefer Option B where available.

## Option B - SSO with OpenID Connect or SAML (recommended)

* Protocol: OIDC authorization-code flow with PKCE against the organization's IdP (Entra ID /
  AD FS / Keycloak / Okta). SAML 2.0 is equivalent in shape; use `python3-saml` if OIDC is not
  available. The application never sees the user's password.
* Flow: `/checkout` → "Sign in with company account" → redirect to the IdP (`state` and `nonce`
  stored in the Django session) → callback verifies `state`, exchanges the code, validates the ID
  token signature (JWKS), `iss`, `aud`, `exp`, `nonce` → read `email` (require
  `email_verified` when the IdP sends it) and `groups` → create `CheckoutSession`.
* Library: `authlib` (small, no Django coupling). Callback URL fixed in IdP configuration
  (`OIDC_REDIRECT_URI`), exact match only.
* Settings (environment): `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`,
  `OIDC_SCOPES`, `OIDC_GROUPS_CLAIM`, `OIDC_REDIRECT_URI`.
* Outbound calls to the IdP (discovery, JWKS, token endpoint) must use the same pinned,
  SSRF-safe HTTP client as the rest of the application (`core/ssrf.py`) with the IdP host
  explicitly allowed through `OUTBOUND_ALLOWED_NETWORKS` when it is on a private network.
* Logout: end the local session; optional RP-initiated logout to the IdP.

## Policy changes (both options)

`CheckoutPolicy` gains:

| Field | Meaning |
|---|---|
| `auth_methods` | subset of `otp`, `ldap`, `oidc` accepted for this datastore |
| `required_groups` | directory/IdP groups of which the visitor must be a member (any-of) |

`start_checkout` already re-validates the session on every request via `get_session`; group
membership is stored on the session at sign-in and re-checked there, and the session lifetime
(`CHECKOUT_SESSION_MINUTES`) bounds how stale it can get. Revoking someone in the directory
therefore takes effect within one session lifetime; for immediate revocation add a short
re-validation interval that re-queries the directory (LDAP) or uses a back-channel logout (OIDC).

## Events to add

`auth_ldap_failed`, `auth_oidc_failed`, `auth_group_denied` (visitor authenticated but not in a
required group). All carry the method and the failure reason, never the password or tokens.

## Testing plan

* Unit: filter escaping, empty-password rejection, ID-token validation failures
  (bad signature, wrong `aud`, expired, wrong `nonce`), group gate, fail-closed on directory error.
* Integration: an OpenLDAP container (`osixia/openldap`) and Keycloak in `docker compose --profile
  test`; no real directory is needed to run the suite.
* Security: confirm that a failed LDAP/OIDC attempt cannot be distinguished from an unknown
  user in response body, status or timing beyond noise.
