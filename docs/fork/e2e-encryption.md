# End-to-end encryption (design, not yet built)

**Status: designed, not implemented.** Nothing in the application encrypts in the browser yet; do not describe the system as end-to-end encrypted. What exists today is encryption at rest and in transit ([container-security.md](container-security.md)), in which the server can read files in memory while it renders them.

## Decisions taken

* **Per-file, selectable for any file**, tied to authentication.
* The **hardened renderer stays** for files that are not E2E. For E2E files the same protections (tiles, blur, watermark, focus lease, capture signals) run **in the browser**, as deterrence, on top of the E2E guarantee.
* **Key delivery:** the key lives in the URL fragment (`#k=...`), which browsers never send to a server, or is e-mailed separately. The ciphertext is served only after the existing e-mail login, NDA, approval and time-window checks.
* **Database:** ciphertext and wrapped keys only; extra column encryption is a **server configuration** choice.
* The viewer is meant to display and not retain. No browser can enforce non-retention: a viewer who is shown plaintext can photograph or record it. E2E removes the server and the database from the trust boundary; it does not make viewing leak-proof.

## What it costs

The server cannot read an E2E file, so it cannot generate previews, page images or tiles, scan for malware, extract text, or search it. Rendering therefore moves to the uploader's browser (page images rendered there and encrypted one by one) and decryption to the viewer's browser.

## Planned steps

1. Browser crypto module (AES-256-GCM, chunked, key generation, fragment encoding) with tests.
2. Upload: encrypt in the browser, store ciphertext plus `e2e` metadata; database constraint that an E2E file has no plaintext derivatives.
3. Viewer: fetch ciphertext after authentication, decrypt, paint to canvas with client-side tiles and watermark.
4. Key delivery in the link flow and check-out; admin setting for column encryption.
