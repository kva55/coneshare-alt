# Running on an internal network

Two parts: **A** gives the system its own internal host name (for example `files.corp.example`) using the built-in HTTPS front door; **B** puts your own nginx in front instead (or in addition). Pick one front door; do not stack two that both terminate TLS for the same name without reading B.

Replace `files.corp.example` below with your name, `10.20.30.40` with the server's address, and `corp.example` with your internal zone.

## A. Adding an internal subdomain (built-in front door)

### 1. DNS

Create a record in your internal DNS (AD DNS, BIND, Unbound, Pi-hole, your router) that points the name at the server running Docker:

| Record | Name | Value |
|---|---|---|
| `A` | `files.corp.example` | `10.20.30.40` |
| `AAAA` (if you use IPv6) | `files.corp.example` | the server's IPv6 address |
| or `CNAME` | `files.corp.example` | `server01.corp.example` |

Check from a client: `nslookup files.corp.example` (or `dig files.corp.example`). Use a **split-horizon** zone if the same name also exists externally. Do not publish this name in public DNS unless you mean to.

### 2. A certificate for that name from your internal CA

The front door's certificate must list the name in its subjectAltName; browsers ignore the common name.

* **Using this repository's generator** (an internal CA is created for you; fine for a lab, see step 6 for production):

  ```bash
  rm -f certs/edge.crt certs/edge.key           # only if one was already made for localhost
  EDGE_HOSTNAMES=files.corp.example ./scripts/gen-certs.sh
  ```

* **Using your organisation's CA** (recommended): create a key and request, have the CA sign it with SAN `DNS:files.corp.example`, then place the results in `./certs`:

  ```bash
  openssl req -new -newkey rsa:2048 -nodes -keyout certs/edge.key -out edge.csr \
    -subj "/CN=files.corp.example" -addext "subjectAltName=DNS:files.corp.example"
  # submit edge.csr to your CA; save the issued certificate as certs/edge.crt
  # (leaf first, then any intermediates) and the CA chain as certs/ca.crt
  ```

  The file service and Redis need certificates valid for `core` and `redis` (see [container-security.md](container-security.md)); the generator makes them from the same CA, or have your CA issue them.

### 3. Tell the app its name

In `.env` (these are the only settings that carry the name):

```env
SITE_DOMAIN=https://files.corp.example          # no :8443 if you publish on 443 (step 4)
# optional, derived from SITE_DOMAIN when unset:
#ALLOWED_HOSTS=127.0.0.1,localhost,backend,files.corp.example
#CSRF_TRUSTED_ORIGINS=https://files.corp.example
```

`SITE_DOMAIN` is used in e-mailed links (access codes, check-out sign-in links), cookie scope and CSRF checks. A wrong value shows up as links that point at `localhost` or as login failing with a CSRF error.

### 4. Publish on the standard HTTPS port

By default the front door listens on `127.0.0.1:8443`, reachable only from the server itself. To serve the network, bind it to the server's address and the standard port:

```bash
HTTPS_PORT=443 ./quickstart.sh        # publishes 127.0.0.1:443
```

`docker-compose.yml` binds to `127.0.0.1`; to accept connections from other machines, change the `proxy` service's `ports` line to `"10.20.30.40:${HTTPS_PORT:-443}:8443"` (or `"443:8443"` for all interfaces). Allow inbound TCP 443 to the server in the host firewall, and only from the networks that should reach it.

### 5. Make clients trust the CA

Every device that opens the site needs the CA certificate (`certs/ca.crt`, or your corporate root) in its trust store, or users see a certificate warning:

| Platform | How |
|---|---|
| Windows (domain) | Group Policy: Computer Configuration > Windows Settings > Security Settings > Public Key Policies > Trusted Root Certification Authorities > Import |
| macOS / iOS | MDM profile, or Keychain Access > System > import, set "Always Trust" |
| Debian/Ubuntu | copy to `/usr/local/share/ca-certificates/corp-ca.crt`, run `sudo update-ca-certificates` |
| Firefox | uses its own store unless `security.enterprise_roots.enabled` is true |

Do not train users to click through certificate warnings; that defeats the encryption.

### 6. Check

```bash
curl --cacert certs/ca.crt https://files.corp.example/api/v1/languages/    # JSON, no TLS error
openssl s_client -connect files.corp.example:443 -servername files.corp.example </dev/null 2>/dev/null | openssl x509 -noout -subject -ext subjectAltName
```

Then sign in, request a check-out link for a guest and confirm the e-mailed link starts with `https://files.corp.example`.

### Other internal-network settings

| Setting | Why |
|---|---|
| `OUTBOUND_ALLOWED_NETWORKS=10.20.0.0/16` | the app refuses outbound connections to private addresses (SSRF protection). Add the networks your internal SMTP/IMAP server and webhook receivers live on. |
| `TRUSTED_PROXIES` | see B; matters as soon as anything sits between users and the app. |
| Internal mail | set the mail host in Notifications > E-mail setup; if it has no TLS, tick the explicit *allow an unencrypted connection* option (documented in the checklist). |

## B. Using nginx as the proxy

There are two situations.

### B1. Your nginx in front of this stack (TLS ends at your nginx)

Run your nginx on the server (or another host) with the certificate for your internal name, and send traffic to the stack's front door or straight to the services. Simplest and safest: let the stack's own `proxy` keep running and have your nginx talk TLS to it, **or** disable the `proxy` profile and publish the frontend yourself. Example for the development stack (frontend dev server on 5173, published by `docker-compose.plain.yml`):

```nginx
map $http_upgrade $connection_upgrade { default upgrade; '' close; }

server {
    listen 443 ssl;
    http2 on;
    server_name files.corp.example;

    ssl_certificate     /etc/nginx/tls/files.corp.example.crt;   # leaf, then intermediates
    ssl_certificate_key /etc/nginx/tls/files.corp.example.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_session_tickets off;

    client_max_body_size 600m;                     # the file service allows 500 MiB by default (MAX_UPLOAD_BYTES)
    add_header Strict-Transport-Security "max-age=31536000" always;

    location / {
        proxy_pass http://127.0.0.1:5173;          # or https://127.0.0.1:8443 with proxy_ssl_verify, see below
        proxy_http_version 1.1;
        proxy_set_header Host              $http_host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $remote_addr;   # replace, do not append: clients cannot forge it
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade           $http_upgrade;  # Vite hot reload (development only)
        proxy_set_header Connection        $connection_upgrade;
        proxy_read_timeout 300s;
    }
}

server { listen 80; server_name files.corp.example; return 301 https://$host$request_uri; }
```

If your nginx talks to the stack's own TLS front door instead (so the hop between them stays encrypted), use:

```nginx
proxy_pass https://127.0.0.1:8443;
proxy_ssl_server_name on;
proxy_ssl_name files.corp.example;                  # the front-door certificate must list this name (part A, step 2)
proxy_ssl_trusted_certificate /etc/nginx/tls/ca.crt;
proxy_ssl_verify on;
```

**Tell the app to trust your nginx.** The app only believes `X-Forwarded-For` / `X-Real-IP` when the connecting address is in `TRUSTED_PROXIES`. The default is `127.0.0.1,::1,172.16.0.0/12` (loopback and Docker). Internal networks are usually `10.x` or `192.168.x`, which are deliberately **not** trusted by default. If your nginx runs on another host or its address is not in the default list, add it, or every user appears to come from the proxy (access logs, allow-lists by address, failed-sign-in alerts and probe detection all use the client address):

```env
TRUSTED_PROXIES=127.0.0.1,::1,172.16.0.0/12,10.20.30.5
#REAL_IP_HEADER=X-Forwarded-For       # or X-Real-IP if that is all your proxy sends
```

Then set `SITE_DOMAIN=https://files.corp.example`.

### B2. nginx inside the production image

The production image (`backend/Dockerfile`, `backend/docker.dist`) already runs nginx, gunicorn and the file service together. Its nginx config (`backend/docker.dist/runtime/coneshare.nginx.conf`) serves the built front end, sends `/api/` to gunicorn on `localhost:9998`, `/files` to the file service on `localhost:8080`, and listens on **port 80 only**. Terminate TLS in one of two ways:

1. **TLS at a proxy in front** (B1) and keep the image on 80 inside a private network. Easiest.
2. **TLS inside the image**: edit the `server` block to

   ```nginx
   listen 443 ssl;
   http2 on;
   ssl_certificate     /home/coneshare/runtime/tls/server.crt;
   ssl_certificate_key /home/coneshare/runtime/tls/server.key;
   ssl_protocols       TLSv1.2 TLSv1.3;
   ```

   mount the certificate and key read-only at that path, publish 443, and set `SITE_DOMAIN=https://files.corp.example`.

In this single-container layout the file service is on `localhost`, so the compose-stack defaults do not apply: set `CORE_API_URL=http://localhost:8080` (loopback, same container), do not set `TLS_CERT_FILE`/`TLS_KEY_FILE`, and keep `STORAGE_ENCRYPTION=required` with a key, which works the same. Point `REDIS_URL` at your Redis; if it uses TLS (`rediss://`), set `INTERNAL_CA_FILE` to the CA that signed it.

### Checks for either case

* `curl -I https://files.corp.example/` returns `200` and `strict-transport-security`.
* Sign in; cookies show as `Secure` and `HttpOnly` in the browser's storage panel (they must **not** be visible to scripts).
* An upload larger than your `client_max_body_size` fails with 413: raise it to match `MAX_UPLOAD_BYTES`.
* Admin > Security > events (or the access log) shows real client addresses, not the proxy's. If it shows the proxy's, fix `TRUSTED_PROXIES`.
* Large PDF viewing works through the proxy (page requests are `POST` with script headers; do not strip `X-Page-Request` or fetch-metadata headers).
