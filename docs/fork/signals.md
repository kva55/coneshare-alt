# Viewer signals, findings and what they can and cannot show

Everything here is reported by the viewer's browser, or inferred by the server from how requests arrive. It shows what was **reported**; a missing signal proves nothing. Each signal has an action (ignore, log, notify, blur, end) under Admin > Security, and the whole family is switched off by the monitoring master switch.

## The signals that matter most

| Signal | What it is | What it cannot see |
|---|---|---|
| Print Screen | The PrintScreen key (press, or only the release: Windows often swallows the press) and common chords (Win+Shift+S, Cmd+Shift+3/4/5). The log records the modifiers held (`plain`, `alt`, `ctrl`, `meta`, `shift`) and whether only the release was seen. | **Fn is invisible to browsers.** A laptop turns Fn+PrtSc into a plain PrintScreen before the page sees it, so it is counted as PrintScreen. Shortcuts the operating system swallows (many macOS and snipping-tool paths), phone screenshots and cameras are never seen. |
| Print | Ctrl/Cmd+P and the browser's `beforeprint` event | Printing from the browser menu may still fire `beforeprint`, but a print-to-PDF extension may not |
| Save page | Ctrl/Cmd+S | File > Save from the menu |
| Inspect / source (keys) | F12, Ctrl+Shift+I/J/C, Ctrl+U | Opening the tools from the menu (see next) |
| Developer tools open | The window is much smaller than its frame (docked tools), or the console evaluates an object the page logged (Chrome family). Checked every 1.5 s, reported once per opening. | Undocked tools in some browsers, Firefox's console. A large sidebar can trip the size check. Not run on phones. |

Other signals (copy, right-click, drag, tab hidden, focus lost) are collected but shown behind a switch in the viewer activity table: they matter less. A held key counts once, not once per repeat.

## Findings about the browser and the request

| Finding | Meaning |
|---|---|
| `tamper_detected` | `fetch`, XMLHttpRequest, canvas read-back or `Function.prototype.toString` in the page no longer look native. User-script managers do this; it means "worth a look". |
| `automation_detected` | `navigator.webdriver`, a headless user agent, chromedriver or Playwright globals; the server also flags script user agents (curl, python-requests, headless browsers) |
| `non_browser_request` | A page request that did not come from the viewer's own script (missing the viewer header or the browser's fetch metadata) |
| `tiles_without_cursor` | Dozens of blur tiles fetched a minute into a check-out while no cursor movement was ever reported |

All are findings, not proof. A careful script can send what a browser sends.

## Session reports (check-outs)

Per check-out, as described in the checklist, plus:

* **Straight runs:** stretches where the pointer held a line (at least 5 samples, at least 8% of the page, within 3% of straight), counted as horizontal, vertical or diagonal.
* **Clicks:** left button down and up with position; a press-and-drag shows as a stroke between them.
* **Around print, save, screenshot and devtools keys:** for each such key, what the pointer did in the 5 seconds before and after: samples, clicks, drags, straight runs, and *window probably lost focus* (no screen samples for 2.5 s after the key, as when a snipping tool opens). The browser's clock and the server's are aligned from the clock value each batch carries (error well under a second). The drag of an operating-system snipping tool is outside the page and is not seen; what shows is the pointer stopping and focus leaving.

## Performance settings

Cutting blur tiles used to re-fetch, decode and re-watermark the page for every request. Two small in-memory caches now remove that: `PAGE_SOURCE_CACHE_BYTES` (default 32 MiB of compressed pages) and `PAGE_IMAGE_CACHE_ITEMS` (default 3 finished images, per viewer), both for `PAGE_CACHE_SECONDS` (default 120). Set any to 0 to switch it off. Access checks always run before the cache is consulted; the caches live only in the process's memory.
