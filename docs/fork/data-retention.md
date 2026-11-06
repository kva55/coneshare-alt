# Data retention and hard deletion

Admin > **Data retention** (and one field on each document page). Off by default.

## Two ways a file gets deleted

1. **Per file.** On the document page, *Delete this file permanently on* a date and time. This is separate from a share link's expiry: a link expiring only stops that link; this removes the file. The date always wins over the global policy (it can be later or earlier).
2. **Global policy**, for files with no date of their own:

| Mode | Meaning |
|---|---|
| No automatic deletion | default |
| Delete a set time after upload | presets 1 week, 1 month, 1 year, or custom N days/weeks/months/years |
| Delete everything on a date every year | e.g. every 1 January; every file uploaded before that date goes (Feb 29 is not allowed) |

The job runs hourly (Celery beat `retention.tasks.run_retention`) and can be run from the page ("Delete them now"; the page shows how many files are due). Files in the trash are included.

## What "hard delete" removes

* the stored original, converted file and page images, from the file service (not just unlinked: see overwriting below);
* the database rows for the document, its versions and pages, and, by cascade, its share links, access logs, viewer events, check-outs and **session-report data**;
* the owner's storage quota is credited back.

What stays: one line per deletion in the **Deletion log** (file name, reason, whether it was overwritten, success or error). It holds no content. A deletion that fails (file service down, overwrite failed) leaves the document in place and is retried at the next run.

## Overwriting

*Overwrite stored data before deleting it* is on by default. The file service overwrites the file in place with one pass of random bytes, flushes it to disk, truncates it, then removes it. This is the **Clear** technique of **NIST SP 800-88 Rev. 1** (single pass; the multi-pass patterns of older standards such as DoD 5220.22-M are not used, because NIST found them unnecessary for modern media). If the overwrite fails the file is kept and the failure is logged, so the system never claims data is gone when it is not.

Be clear about what an overwrite can and cannot do:

* On **SSDs and flash**, and on **copy-on-write or journalling file systems**, an in-place overwrite is not guaranteed to reach every physical copy (wear levelling, snapshots, journals). NIST's answer for this is **cryptographic erase**: the files are stored encrypted (this fork does that by default, see [container-security.md](container-security.md)), so destroying the key makes every copy unreadable. Deleting one file does not destroy the shared storage key; retire the key when you retire a whole volume.
* **Backups and volume snapshots** are not touched. Include them in your retention scheme.
* Copies the user already downloaded or a viewer already received are outside the system.
* Memory: the service streams in 1 MiB buffers that are reused and garbage collected; Go and Python give no way to guarantee zeroing process memory, and it is not attempted.

## API

`GET/PUT /api/v1/retention/settings/` (admin), `POST /api/v1/retention/run/`, `GET /api/v1/retention/log/`; per file: `PATCH /api/v1/documents/<id>/` with `delete_after` (ISO time in the future, or `null`).
