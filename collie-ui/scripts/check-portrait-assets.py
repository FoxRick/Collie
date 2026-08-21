#!/usr/bin/env python3
"""Regression gate for Collie portrait/avatar artwork alignment.

Fails when the asset layout regresses:

1. Agent avatar sheet (`agents/dog-portrait-sheet-30.png`): no cell may have
   content touching the outer 4px (full-bleed art clips at the rounded
   avatar corners). Fixed sheets keep content inside a uniform ~16% margin.
2. Face-only portrait strips + stills (`portrait/*`): per-cell face content
   centers must stay close to the strip's median (transparent translation
   keeps the chat head centered on crossfade; drift = jump).

Requires Pillow: `python3 -m pip install pillow` (present on the VM rig).

Usage: python3 check-portrait-assets.py <repo-root> [--verbose]
Exit 0 = pass, 1 = regression found.
"""
import os
import sys

from PIL import Image

AGENT = "collie-ui/src/renderer/src/assets/agents/dog-portrait-sheet-30.png"
AGENT_CELLS = (6, 5)
STRIPS = {
    "idle-sheet.webp": (3, 2),
    "waiting-sheet.webp": (3, 2),
    "pointer-look-sheet.webp": (4, 4),
    "click-reaction-sheet.webp": (2, 2),
    "bone-completion-sheet.webp": (2, 2),
    "deep-work-glasses-sheet.webp": (3, 2),
}
STILLS = ["idle.webp", "happy.webp", "concerned.webp", "sleepy.webp", "thinking.webp"]

# Tolerances (fractions of cell size)
AGENT_EDGE_TOLERANCE_PX = 6  # content may not come within this many px of a cell edge
STRIP_X_SPAN_MAX = 0.10      # 10% of cell width
STRIP_Y_SPAN_MAX = 0.05      # 5% of cell height


def content_bbox(img, min_alpha=16):
    rgba = img.convert("RGBA")
    w, h = rgba.size
    px = rgba.load()
    min_x, min_y, max_x, max_y = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if px[x, y][3] >= min_alpha:
                if x < min_x: min_x = x
                if x > max_x: max_x = x
                if y < min_y: min_y = y
                if y > max_y: max_y = y
    if max_x < 0:
        return None
    return (min_x, min_y, max_x, max_y)


def border_dominant(img):
    """Most common color among the outer ring (the tile's frame/margin tone)."""
    from collections import Counter
    rgba = img.convert("RGBA")
    w, h = rgba.size
    cnt = Counter()
    for x in range(w):
        for y in (0, 1, h - 2, h - 1):
            r, g, b, a = rgba.getpixel((x, y))
            cnt[(r // 8 * 8, g // 8 * 8, b // 8 * 8, a // 16 * 16)] += 1
    for y in range(h):
        for x in (0, 1, w - 2, w - 1):
            r, g, b, a = rgba.getpixel((x, y))
            cnt[(r // 8 * 8, g // 8 * 8, b // 8 * 8, a // 16 * 16)] += 1
    return cnt.most_common(1)[0][0]


def color_bbox(img, bg, tol=48):
    """BBox of pixels differing from the border tone (opaque tiles only)."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    min_x, min_y, max_x, max_y = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if abs(r - bg[0]) <= tol and abs(g - bg[1]) <= tol and abs(b - bg[2]) <= tol:
                continue
            if x < min_x: min_x = x
            if x > max_x: max_x = x
            if y < min_y: min_y = y
            if y > max_y: max_y = y
    if max_x < 0:
        return None
    return (min_x, min_y, max_x, max_y)


def check_agent_sheet(root, verbose):
    path = os.path.join(root, AGENT)
    img = Image.open(path)
    w, h = img.size
    cols, rows = AGENT_CELLS
    cw, ch = w // cols, h // rows
    full_bleed = []
    for r in range(rows):
        for c in range(cols):
            cell = img.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch))
            bg = border_dominant(cell)
            bb = color_bbox(cell, bg)
            if bb is None:
                continue
            l, t, rr, b = bb
            if (l <= AGENT_EDGE_TOLERANCE_PX or t <= AGENT_EDGE_TOLERANCE_PX or
                    rr >= cw - AGENT_EDGE_TOLERANCE_PX or b >= ch - AGENT_EDGE_TOLERANCE_PX):
                full_bleed.append((r, c, bb))
    if verbose:
        print(f"agent sheet: {len(full_bleed)}/30 cells with content within "
              f"{AGENT_EDGE_TOLERANCE_PX}px of an edge")
    return full_bleed


def check_strip(root, rel, cols, rows, verbose):
    path = os.path.join(root, "collie-ui/src/renderer/src/assets/portrait", rel)
    img = Image.open(path)
    w, h = img.size
    cw, ch = w // cols, h // rows
    centers = []
    for r in range(rows):
        for c in range(cols):
            cell = img.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch))
            bb = content_bbox(cell)
            if bb:
                centers.append((((bb[0] + bb[2]) / 2) / cw, ((bb[1] + bb[3]) / 2) / ch))
    if not centers:
        return True, "no content"
    xs = [c[0] for c in centers]
    ys = [c[1] for c in centers]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)
    if verbose:
        print(f"{rel}: x-span {x_span:.3f} y-span {y_span:.3f}")
    return (x_span <= STRIP_X_SPAN_MAX and y_span <= STRIP_Y_SPAN_MAX), (
        f"x-span {x_span:.3f} > {STRIP_X_SPAN_MAX} or y-span {y_span:.3f} > {STRIP_Y_SPAN_MAX}")


def check_still(root, rel, verbose):
    path = os.path.join(root, "collie-ui/src/renderer/src/assets/portrait", rel)
    img = Image.open(path)
    # Stills participate in crossfades with strips; their face center must land
    # near the same fractional anchor. Report only (not gating — single frames
    # are less drift-prone and the anchor is a design constant).
    bb = content_bbox(img)
    if bb and verbose:
        print(f"{rel}: center=({((bb[0]+bb[2])/2)/img.size[0]:.3f}, {((bb[1]+bb[3])/2)/img.size[1]:.3f})")
    return True


def main():
    args = [a for a in sys.argv[1:] if a != "--verbose"]
    verbose = "--verbose" in sys.argv
    if not args:
        print("usage: check-portrait-assets.py <repo-root> [--verbose]")
        return 1
    root = args[0]
    failures = []

    fb = check_agent_sheet(root, verbose)
    if fb:
        failures.append(f"agent sheet: {len(fb)} full-bleed cells {fb[:5]}...")

    for rel, (cols, rows) in STRIPS.items():
        ok, why = check_strip(root, rel, cols, rows, verbose)
        if not ok:
            failures.append(f"{rel}: {why}")

    for rel in STILLS:
        check_still(root, rel, verbose)

    if failures:
        print("PORTRAIT ASSET CHECK FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("Portrait asset check: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
