# How movement and behaviour detection works

This page says exactly what is collected, how it is turned into a report, why each choice was made, and what the result can and cannot tell you. Every number below is a constant in the code; the file is named so you can check.

## The short version

While a guest has a file checked out, their browser reports **where the pointer is on the page** about ten times a second, **which page is in the middle of the screen** once a second, and **left-button presses**. Nothing else about what they do is collected. An administrator can open a **session report** afterwards that shows where the pointer spent time, a typical path, straight and diagonal strokes, and what the pointer did around print, save, screenshot and developer-tools keys. It is a hint about where somebody read. It is not eye tracking and it is not proof of anything.

It is **on by default** (guests are told by the sign-in notice that actions may be recorded), and can be switched off in one place: *Admin > Security > Monitoring*, or only the cursor tracing under *Check-out session reports*.

## What the browser collects (`frontend/src/hooks/useCheckoutTrace.js`)

| Sample | When | What it holds |
|---|---|---|
| Pointer position | on mouse or finger movement, at most one every **100 ms** | time, page number, x and y as a fraction of that page (**0 to 1000** in each direction). Over no page: page 0. |
| Page in view | once a **second** | time, page number and the point on it at the vertical centre of the scrolling area |
| Left button | button down and button up | time, page, x, y, down or up (right and middle buttons are ignored) |

Rules that keep it narrow:

* **Only while the tab is visible and the window has focus.** A background tab produces nothing.
* **Positions are fractions of the page, never pixels**, so zooming or resizing changes nothing, and nothing about the screen size or layout is sent.
* **Nothing typed, nothing read from the page, no screenshots.** The page text and images are never part of a sample.
* Samples are batched and sent every **10 seconds** (and when the viewer closes), at most 600 per kind per request.
* Touch screens: a finger drag counts as the pointer, so phones are not mistaken for "no cursor".

## What the server keeps (`backend/checkout/services.py`, `models.py`)

* Each value must be an integer in range; positions are clamped to 0 to 1000; at most 600 samples per kind per request, **20 requests a minute** per session, **400 batches** per check-out.
* Accepted only while the check-out is open and for **two minutes** after it ends (so the last batch is not lost).
* Stored **encrypted** in the database (django-cryptography), as text holding JSON, one row per batch. Deleted with the file by [data retention](data-retention.md). When the monitoring switch or tracing is off, nothing is stored.
* Each batch also carries the browser's clock when it was sent. With the server's receipt time this puts the samples on the server's clock (error well under a second), which is how they are lined up with logged events.

## How the report is computed (`backend/checkout/trace_report.py`)

Computed on request from the stored samples; nothing derived is stored.

| Output | How |
|---|---|
| **Time cursor over a page, and the heat grid** | each page is a 20 × 28 grid. A pointer sample adds the time until the next sample (capped at **600 ms**, so a pause is not counted as dwell) to its cell. The last sample counts 100 ms. |
| **Time on screen** | each "page in view" sample stands for the time until the next one, capped at **3 s** (a closed laptop counts as nothing) |
| **Averaged path** | a page's pointer samples are split into 12 consecutive time slices; each slice's mean position is one point of the line. It shows the general direction of travel, not a trace. |
| **Strokes: horizontal, vertical, diagonal** | the move between two consecutive samples (at most 600 ms apart, same page, at least **25** units long, to ignore hand tremor). Angle from horizontal: under **25°** horizontal, over **65°** vertical, otherwise diagonal. |
| **Straight runs** | a stretch of consecutive samples (no gap over 600 ms) of at least **5** samples and **80** units (8% of the page) that stays within 3% of a straight line (distance covered ÷ distance travelled ≥ **0.97**), classed horizontal, vertical or diagonal by its overall angle |
| **Around capture keys** | for each print, save, screenshot, devtools key or devtools-open event: the **5 seconds before and after**: pointer samples, left clicks, press-and-drag strokes (at least 150 ms and 25 units), straight runs, and *window probably lost focus* if no screen sample arrives for **2.5 s** after the key (as when a snipping tool opens) |
| **Confidence** | by the number of pointer samples: none, under 150 low, under 1500 medium, otherwise good. Shown so a report is not over-read. |

## Why it is built this way

* **Fractions, not pixels, and no content.** The aim is "where is reading happening", which a position on the page answers. Pixels would tie the data to one screen and add nothing; content would be a privacy cost with no benefit to that aim.
* **About 10 samples a second.** Enough to see a stroke along a line of text or a sweep across a table; low enough that a 10-minute reading is about 6,000 samples, a few tens of kilobytes. More would cost storage and bandwidth for no extra insight.
* **Capped gaps.** Without them, a guest who left for lunch would show hours of "reading" on the last page.
* **Diagonal and straight strokes.** Asked for as a compliance aid: a pointer dragged along a line of text, or swept corner to corner, tends to move that way, and such movement is also what selecting or sweeping an area for a capture looks like. They are shown as patterns worth a look, never as findings.
* **Compare to the keys, not to a verdict.** The capture-key windows put the key, which is all a browser can see of a screenshot, next to what the pointer did, so a person can judge.
* **Encrypted, bounded, deletable.** Reports describe attention, so they are stored encrypted, capped in size and removed with the file.

## What it cannot tell you

* **A pointer is not an eye.** People read with the pointer parked elsewhere, or not near the text; touch screens and keyboard scrolling produce little pointer movement (the once-a-second "page in view" sample still shows which page was on screen).
* **Angles are measured in page-fraction space,** not on the physical screen. A page is taller than wide, so "45°" in the report is about 38° on a letter-size page. The classes are for comparing strokes with each other.
* **Slow loading looks like movement.** Samples taken while pages were still rendering are *not yet* excluded: a guest who waits and wiggles the pointer shows up as strokes and dwell. Treat a report from a slow session with care. (Planned: mark and exclude samples taken before a page has loaded.)
* **Multiple monitors, other windows, other devices, phones used as cameras:** not seen.
* **The Fn key is invisible to browsers.** Fn+PrintScreen reaches the page as PrintScreen, or not at all. The operating system swallows some screenshot shortcuts (many macOS and snipping-tool paths) before the page sees them. A screenshot key proves a key was pressed, not that a picture was taken, and no key proves none was.
* A person who knows the page can stop sending samples (block the requests) or send false ones. The server findings below make careless attempts visible; they do not stop a careful one.

## Signals and findings (`frontend/src/lib/viewerEvents.js`, `integrity.js`, `backend/sharelinks/integrity.py`)

Separate from the cursor trace. Each signal is an entry in a list of small detectors; each has an action (ignore, log, notify, blur, end) in *Admin > Security*.

| Signal | How it is detected |
|---|---|
| Print | Ctrl/Cmd+P and the browser's `beforeprint` event |
| Print Screen | the PrintScreen key (press, or only the release: Windows often swallows the press), plus Win+Shift+S and Cmd+Shift+3/4/5. Recorded with the modifiers held and whether only the release was seen. |
| Save page | Ctrl/Cmd+S |
| Inspect / source (keys) | F12, Ctrl+Shift+I/J/C, Ctrl+U |
| Developer tools open | every 1.5 s: the browser window is more than 200 px larger than its content (docked tools), or the console evaluates an object the page logged (Chrome family). Reported once per opening. Not run on phones. |
| Copy, right-click, drag, tab hidden, window blur | the matching browser events (collected, shown as secondary; tab and focus changes are ignored by default) |
| Browser tampering | every 60 s: `fetch`, XMLHttpRequest, canvas read-back and `Function.prototype.toString` no longer look like native code. User-script managers do this, so it means "look", not "guilty". |
| Automation | `navigator.webdriver`, a headless user agent, chromedriver or Playwright globals; the server also flags script user agents (curl, python-requests, headless browsers) |
| Not the viewer's script | a page request without the viewer's header or the browser's fetch metadata |
| Tiles without a cursor | 40 or more blur-tile requests, over a minute into a check-out, and no pointer sample ever received |

Key listeners run in the capture phase so the page cannot swallow them; the same signal within 250 ms is merged (a key's down and up); a held key counts once. All of this is **best-effort information from an untrusted browser**: it shows what a viewer's page reported, and absence proves nothing.

## Turning it off, and where it goes

* Everything above: *Admin > Security > Monitoring and interaction analysis* (off stops signals, their responses, tracing and the analysis; access records and security alerts stay).
* Only the cursor trace: *Admin > Security > Check-out session reports*.
* Removal: [data retention](data-retention.md) deletes a file's reports with it.
