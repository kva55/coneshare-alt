"""
Turns the samples a viewer sent during a check-out into a report: where the cursor was, how long each
page was on screen, an averaged path per page, and the diagonal movements.

This shows where somebody *probably* read. A cursor is not an eye: people read with the pointer parked
somewhere else, and a phone or tablet has no cursor at all. The report says how much data it rests on so
it is not over-read.
"""
import math

GRID_COLS, GRID_ROWS = 20, 28        # heat grid over each page
PATH_POINTS = 12                     # points in the averaged path of a page
MAX_GAP_MS = 600                     # samples further apart than this are not one movement
MIN_SEGMENT = 25                     # ignore movements shorter than this (of 1000): hand tremor
DIAGONAL_MIN_DEG, DIAGONAL_MAX_DEG = 25, 65   # angle from horizontal that counts as diagonal
MAX_SEGMENTS_RETURNED = 150
STRAIGHT_MIN_SAMPLES = 5            # a straight run needs at least this many samples,
STRAIGHT_MIN_LENGTH = 80            # to be at least this long (of 1000),
STRAIGHT_EFFICIENCY = 0.97          # and stay this close to a line (distance covered / path travelled)
CAPTURE_TYPES = ('screenshot_key', 'print_attempt', 'save_attempt', 'devtools_key', 'devtools_open')
CAPTURE_WINDOW_MS = 5000            # what is looked at before and after a capture key
FOCUS_GAP_MS = 2500                 # no screen samples for this long after the key: the window probably lost focus


def _angle(dx, dy):
    return math.degrees(math.atan2(abs(dy), abs(dx)))


def _kind(dx, dy):
    a = _angle(dx, dy)
    return 'horizontal' if a < DIAGONAL_MIN_DEG else 'vertical' if a > DIAGONAL_MAX_DEG else 'diagonal'


def straight_runs(points):
    """
    Runs of consecutive cursor samples (``(t, x, y)``, one page, in time order) that stay on a straight line.
    Returns ``[(x1, y1, x2, y2, kind)]``. A run ends at a pause (a gap over MAX_GAP_MS) or when it bends.
    """
    runs, i, n = [], 0, len(points)
    while i < n - 1:
        j, path = i, 0.0
        best = None
        while j + 1 < n and points[j + 1][0] - points[j][0] <= MAX_GAP_MS:
            path += math.hypot(points[j + 1][1] - points[j][1], points[j + 1][2] - points[j][2])
            j += 1
            span = math.hypot(points[j][1] - points[i][1], points[j][2] - points[i][2])
            if path > 0 and span / path < STRAIGHT_EFFICIENCY:
                break
            if j - i + 1 >= STRAIGHT_MIN_SAMPLES and span >= STRAIGHT_MIN_LENGTH:
                best = (j, span)
        if best:
            end = best[0]
            runs.append((points[i][1], points[i][2], points[end][1], points[end][2],
                         _kind(points[end][1] - points[i][1], points[end][2] - points[i][2])))
            i = end
        else:
            i += 1
    return runs


def capture_windows(events, moves, views, clicks):
    """
    For each capture key (screenshot, print, save) in ``events`` (``{'type', 'ms', 'seconds'}``, ``ms`` on the same
    clock as the samples), what the cursor did in the 5 seconds before and after it: clicks, press-and-drag
    strokes, straight runs, and whether the window seems to have lost focus (no screen samples for a while,
    as when a snipping tool opens). Only a hint: the key itself is all the browser can see of a screenshot.
    """
    out = []
    for ev in events:
        if ev['type'] not in CAPTURE_TYPES:
            continue
        t = ev['ms']
        lo, hi = t - CAPTURE_WINDOW_MS, t + CAPTURE_WINDOW_MS

        def side(a, b):
            pts = [(m[0], m[2], m[3]) for m in moves if a <= m[0] < b and m[1] > 0]
            counts = {'horizontal': 0, 'vertical': 0, 'diagonal': 0}
            for run in straight_runs(pts):
                counts[run[4]] += 1
            downs = [c for c in clicks if a <= c[0] < b and c[4] == 0]
            drags = []
            for d in downs:
                up = next((c for c in clicks if c[4] == 1 and d[0] < c[0] <= b and c[1] == d[1]), None)
                if up and up[0] - d[0] >= 150 and math.hypot(up[2] - d[2], up[3] - d[3]) >= MIN_SEGMENT:
                    drags.append({'kind': _kind(up[2] - d[2], up[3] - d[3]), 'ms': up[0] - d[0],
                                  'length': round(math.hypot(up[2] - d[2], up[3] - d[3]))})
            return {'moves': len(pts), 'clicks': len(downs), 'drags': drags, 'straight': counts}

        before_views = [v for v in views if lo <= v[0] <= t]
        page = before_views[-1][1] if before_views else 0
        after_views = [v[0] for v in views if t < v[0] <= hi]
        gap = (after_views[0] - t) if after_views else CAPTURE_WINDOW_MS
        out.append({'type': ev['type'], 'seconds': ev.get('seconds'), 'page': page,
                    'before': side(lo, t + 1), 'after': side(t + 1, hi),
                    'focus_left_likely': gap >= FOCUS_GAP_MS})
    return out


def build_report(moves, views, page_sizes=None, clicks=None, events=None):
    """
    ``moves``: [[t_ms, page, x, y], ...] and ``views``: [[t_ms, page, y], ...], both ordered by time.
    ``page_sizes``: {page_number: (width, height)} used only to draw each page at its true shape.
    """
    moves = sorted((m for m in moves if len(m) == 4), key=lambda m: m[0])
    views = sorted((v for v in views if len(v) == 3), key=lambda v: v[0])
    pages = {}

    def page(n):
        if n not in pages:
            w, h = (page_sizes or {}).get(n) or (None, None)
            pages[n] = {'page': n, 'width': w, 'height': h, 'seconds_in_view': 0.0, 'seconds_cursor_over': 0.0,
                        'samples': 0, 'heat': [[0.0] * GRID_COLS for _ in range(GRID_ROWS)],
                        '_pts': [], 'segments': {'horizontal': 0, 'vertical': 0, 'diagonal': 0},
                        'straight': {'horizontal': 0, 'vertical': 0, 'diagonal': 0}, 'diagonal': []}
        return pages[n]

    # time on screen: each view sample stands for the time until the next one (capped, so a closed laptop counts as nothing)
    for i, (t, n, _y) in enumerate(views):
        if n < 1:
            continue
        nxt = views[i + 1][0] if i + 1 < len(views) else t + 1000
        page(n)['seconds_in_view'] += min(max(nxt - t, 0), 3000) / 1000

    totals = {'horizontal': 0, 'vertical': 0, 'diagonal': 0}
    for i, (t, n, x, y) in enumerate(moves):
        if n < 1:
            continue
        p = page(n)
        p['samples'] += 1
        p['_pts'].append((t, x, y))
        nxt = moves[i + 1] if i + 1 < len(moves) else None
        dt = min((nxt[0] - t) if nxt else 100, MAX_GAP_MS) / 1000
        col = min(GRID_COLS - 1, max(0, int(x * GRID_COLS / 1000)))
        row = min(GRID_ROWS - 1, max(0, int(y * GRID_ROWS / 1000)))
        p['heat'][row][col] += dt
        p['seconds_cursor_over'] += dt
        if nxt and nxt[1] == n and 0 < nxt[0] - t <= MAX_GAP_MS:
            dx, dy = nxt[2] - x, nxt[3] - y
            if math.hypot(dx, dy) >= MIN_SEGMENT:
                a = _angle(dx, dy)
                kind = 'horizontal' if a < DIAGONAL_MIN_DEG else 'vertical' if a > DIAGONAL_MAX_DEG else 'diagonal'
                p['segments'][kind] += 1
                totals[kind] += 1
                if kind == 'diagonal' and len(p['diagonal']) < MAX_SEGMENTS_RETURNED:
                    p['diagonal'].append([x, y, nxt[2], nxt[3]])

    out = []
    for n in sorted(pages):
        p = pages[n]
        pts = p.pop('_pts')
        for run in straight_runs(pts):
            p['straight'][run[4]] += 1
        p['path'] = _average_path(pts)
        p['seconds_in_view'] = round(p['seconds_in_view'], 1)
        p['seconds_cursor_over'] = round(p['seconds_cursor_over'], 1)
        p['heat'] = [[round(c, 2) for c in row] for row in p['heat']]
        p['heat_max'] = max((c for row in p['heat'] for c in row), default=0)
        out.append(p)

    straight_total = {k: sum(p['straight'][k] for p in out) for k in ('horizontal', 'vertical', 'diagonal')}
    moved = sum(totals.values())
    total_samples = sum(p['samples'] for p in out)
    return {
        'grid': {'cols': GRID_COLS, 'rows': GRID_ROWS},
        'pages': out,
        'movement': {**totals, 'diagonal_share': round(totals['diagonal'] / moved, 3) if moved else 0.0,
                     'straight': straight_total},
        'capture_windows': capture_windows(events or [], moves, views, [c for c in (clicks or []) if len(c) == 5]),
        'basis': {
            'cursor_samples': total_samples,
            'view_samples': len(views),
            'confidence': ('none' if total_samples == 0 else 'low' if total_samples < 150 else 'medium' if total_samples < 1500 else 'good'),
            'note': ('A pointer is not an eye. This shows where the cursor was and which page was on screen; '
                     'people often read with the pointer parked elsewhere, and touch screens have no pointer. '
                     'Diagonal strokes (about 25 to 65 degrees) are shown because a cursor dragged along a line '
                     'of text, or swept across a table, tends to travel that way; they are a hint, not proof.'),
        },
    }


def _average_path(pts):
    """The page's samples split into PATH_POINTS consecutive time slices; each slice is its mean position."""
    if len(pts) < 2:
        return [[x, y] for _t, x, y in pts]
    k = min(PATH_POINTS, len(pts))
    size = len(pts) / k
    path = []
    for i in range(k):
        chunk = pts[int(i * size):int((i + 1) * size)] or [pts[-1]]
        path.append([round(sum(c[1] for c in chunk) / len(chunk)), round(sum(c[2] for c in chunk) / len(chunk))])
    return path
