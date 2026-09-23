from __future__ import annotations


def _connected_components(image, box, predicate):
    left, top, right, bottom = [int(v) for v in box]
    left = max(0, left)
    top = max(0, top)
    right = min(image.width, right)
    bottom = min(image.height, bottom)
    points = set()
    px = image.load()
    for y in range(top, bottom):
        for x in range(left, right):
            if predicate(*px[x, y]):
                points.add((x, y))
    components = []
    while points:
        seed = points.pop()
        stack = [seed]
        items = [seed]
        while stack:
            x, y = stack.pop()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                           (1, 1), (1, -1), (-1, 1), (-1, -1)):
                q = (x + dx, y + dy)
                if q in points:
                    points.remove(q)
                    stack.append(q)
                    items.append(q)
        xs = [p[0] for p in items]
        ys = [p[1] for p in items]
        components.append({
            "area": len(items),
            "left": min(xs),
            "top": min(ys),
            "width": max(xs) - min(xs) + 1,
            "height": max(ys) - min(ys) + 1,
        })
    return components


def _gold(red, green, blue):
    return (red >= 190 and 110 <= green <= 225 and blue <= 125
            and red >= green + 18 and green >= blue + 35)


def _radio_ink(red, green, blue):
    # Empty radio rings are antialiased grey/blue/red. Exclude the near-white
    # page background and the gold star fill.
    return (min(red, green, blue) < 235 and max(red, green, blue) < 250
            and not _gold(red, green, blue))


def _screen_to_image(rect, window_rect):
    return {
        "left": rect["left"] - window_rect["left"],
        "top": rect["top"] - window_rect["top"],
        "width": rect["width"],
        "height": rect["height"],
    }


def detect_visual_score_rows(screenshot_path, window_rect, heading_rect,
                             band_bottom_screen, client_rect=None,
                             bottom_exclusion=140):
    """Detect the five vertical score rows from rendered pixels only.

    WeChat's Chromium accessibility tree keeps stale roster/calendar nodes alive,
    so score targets must not come from UIA descendants. A valid expanded section
    must show exactly five gold-star rows with star counts 1,2,3,4,5 and five
    aligned empty radio circles to their left. The returned click points are the
    radio-circle centres.
    """
    try:
        from PIL import Image
        image = Image.open(screenshot_path).convert("RGB")
    except Exception as exc:
        return {
            "layout": "none",
            "rows": [],
            "reason": "cannot open screenshot: {}".format(exc),
        }

    h = _screen_to_image(heading_rect, window_rect)
    search_left = max(0, h["left"] + 35)
    search_right = min(image.width, h["left"] + 260)
    search_top = max(0, h["top"] + h["height"] + 45)
    search_bottom = min(
        image.height,
        int(band_bottom_screen - window_rect["top"] - 8),
    )
    if client_rect:
        safe_bottom_screen = (
            client_rect["top"] + client_rect["height"] - bottom_exclusion
        )
        search_bottom = min(
            search_bottom,
            int(safe_bottom_screen - window_rect["top"] - 4),
        )
    if search_bottom <= search_top + 40:
        return {
            "layout": "none",
            "rows": [],
            "reason": "score search band too small",
        }

    components = _connected_components(
        image,
        (search_left, search_top, search_right, search_bottom),
        _gold,
    )
    stars = [
        c for c in components
        if 7 <= c["width"] <= 22
        and 7 <= c["height"] <= 22
        and 25 <= c["area"] <= 180
    ]
    if not stars:
        return {
            "layout": "none",
            "rows": [],
            "reason": "no visual star components found",
        }

    stars.sort(key=lambda c: c["top"] + c["height"] / 2.0)
    clusters = []
    for star in stars:
        cy = star["top"] + star["height"] / 2.0
        if clusters and abs(cy - clusters[-1]["cy"]) <= 5:
            clusters[-1]["stars"].append(star)
            n = len(clusters[-1]["stars"])
            clusters[-1]["cy"] = (
                clusters[-1]["cy"] * (n - 1) + cy
            ) / n
        else:
            clusters.append({"cy": cy, "stars": [star]})
    for row in clusters:
        row["stars"].sort(key=lambda c: c["left"])
    clusters = [row for row in clusters if 1 <= len(row["stars"]) <= 5]

    matches = []
    for start in range(max(0, len(clusters) - 4)):
        window = clusters[start:start + 5]
        counts = [len(row["stars"]) for row in window]
        if counts != [1, 2, 3, 4, 5]:
            continue
        gaps = [
            window[index + 1]["cy"] - window[index]["cy"]
            for index in range(4)
        ]
        if not all(25 <= gap <= 90 for gap in gaps):
            continue
        first_lefts = [row["stars"][0]["left"] for row in window]
        if max(first_lefts) - min(first_lefts) > 8:
            continue
        matches.append(window)

    if len(matches) != 1:
        return {
            "layout": "none",
            "rows": [],
            "reason": "visual 1..5 star pattern matches={} (need exactly 1)".format(
                len(matches)
            ),
        }

    matched = matches[0]
    output_rows = []
    radio_xs = []
    for score, row in enumerate(matched, 1):
        first_star = row["stars"][0]
        cy = row["cy"]
        radio_box = (
            max(0, h["left"] + 2),
            max(0, int(cy - 16)),
            max(0, first_star["left"] - 8),
            min(image.height, int(cy + 17)),
        )
        radio_components = _connected_components(image, radio_box, _radio_ink)
        radios = [
            c for c in radio_components
            if 12 <= c["width"] <= 24
            and 12 <= c["height"] <= 24
            # The redesigned WOWKIDS radio outline renders as a 24x24 ring
            # with roughly 190 non-white pixels at this display scale.
            and 25 <= c["area"] <= 220
            and 0.65 <= c["width"] / float(c["height"]) <= 1.45
        ]
        if len(radios) != 1:
            return {
                "layout": "none",
                "rows": [],
                "reason": "score {} radio candidates={} (need 1)".format(
                    score, len(radios)
                ),
            }

        radio = radios[0]
        rcx = radio["left"] + radio["width"] / 2.0
        rcy = radio["top"] + radio["height"] / 2.0
        radio_xs.append(rcx)
        click_x = int(round(rcx + window_rect["left"]))
        click_y = int(round(rcy + window_rect["top"]))
        output_rows.append({
            "score": score,
            "star_count": len(row["stars"]),
            "row_y": int(round(cy + window_rect["top"])),
            "click_point": [click_x, click_y],
            "radio_rect": {
                "left": radio["left"] + window_rect["left"],
                "top": radio["top"] + window_rect["top"],
                "width": radio["width"],
                "height": radio["height"],
            },
            "star_rects": [
                {
                    "left": c["left"] + window_rect["left"],
                    "top": c["top"] + window_rect["top"],
                    "width": c["width"],
                    "height": c["height"],
                }
                for c in row["stars"]
            ],
        })

    if max(radio_xs) - min(radio_xs) > 5:
        return {
            "layout": "none",
            "rows": [],
            "reason": "radio circles are not x-aligned",
        }

    if client_rect:
        safe_bottom = (
            client_rect["top"] + client_rect["height"] - bottom_exclusion
        )
        for row in output_rows:
            x, y = row["click_point"]
            if not (
                client_rect["left"] <= x < client_rect["left"] + client_rect["width"]
                and client_rect["top"] <= y < safe_bottom
            ):
                return {
                    "layout": "none",
                    "rows": [],
                    "reason": "score click point outside safe client area",
                }

    return {
        "layout": "vertical-visual",
        "rows": output_rows,
        "reason": "visually verified 1..5 stars and five aligned radio circles",
    }
