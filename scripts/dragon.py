#!/usr/bin/env python3
"""Draw a pixel-art red Chinese dragon from a GitHub account's public activity.

A companion to git-koi: it reads public contribution data and turns it into a
looping, transparent GIF.

  total contributions   -> growth stage (length, horns, legs, mane, whiskers)
  top languages         -> colours of the dorsal fins and mane
  longest streak        -> golden scale bands along the body
  current streak        -> size and fire of the pearl the dragon chases
  yesterday's activity  -> behaviour (resting, drifting, swimming, soaring)

Usage:  GH_TOKEN=... python3 scripts/dragon.py <login> [--output dragon/dragon.gif]
        python3 scripts/dragon.py --snapshot snap.json   (render saved data offline)
"""

import argparse
import datetime as dt
import json
import math
import os
import random
import sys
import tempfile
import urllib.request

from PIL import Image, ImageDraw

W, H = 192, 96          # logical pixel-art canvas
SCALE = 4               # output is 768x384
FRAMES = 48
FRAME_MS = 70

OUTLINE = (52, 8, 12)
RED_DARK = (140, 18, 24)
RED = (200, 34, 38)
RED_LIGHT = (236, 84, 70)
BELLY = (246, 196, 92)
BELLY_DARK = (214, 150, 52)
GOLD = (250, 204, 64)
GOLD_DARK = (196, 140, 30)
IVORY = (246, 236, 210)
EYE = (255, 236, 120)
BLACK = (20, 6, 8)
FALLBACK_LANGS = [(53, 114, 165), (241, 224, 90), (49, 120, 198)]

STAGE_THRESHOLDS = [0, 50, 200, 500, 1000, 2500]   # total contributions per stage
STAGE_LENGTH = [56, 76, 94, 110, 124, 138]
STAGE_RADIUS = [3.0, 3.6, 4.2, 4.8, 5.3, 5.8]


# ---------------------------------------------------------------- data

def graphql(token, query, variables):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "User-Agent": "dragon-readme"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.load(resp)
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    return body["data"]


def collect(login, token):
    """Return a snapshot dict of public activity through the last complete UTC day."""
    yesterday = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    info = graphql(token, """
      query($login: String!) {
        user(login: $login) {
          createdAt
          repositories(first: 100, ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC) {
            nodes { languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
              edges { size node { name color } } } }
          }
        }
      }""", {"login": login})["user"]

    langs = {}
    for repo in info["repositories"]["nodes"]:
        for edge in repo["languages"]["edges"]:
            name, color = edge["node"]["name"], edge["node"]["color"]
            size, _ = langs.get(name, (0, color))
            langs[name] = (size + edge["size"], color)
    top = sorted(langs.items(), key=lambda kv: -kv[1][0])[:3]

    days = {}
    start = dt.date.fromisoformat(info["createdAt"][:10])
    while start <= yesterday:
        end = min(start + dt.timedelta(days=364), yesterday)
        cal = graphql(token, """
          query($login: String!, $from: DateTime!, $to: DateTime!) {
            user(login: $login) { contributionsCollection(from: $from, to: $to) {
              contributionCalendar { weeks { contributionDays { date contributionCount } } } } }
          }""", {"login": login, "from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z"})
        for week in cal["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]:
            for day in week["contributionDays"]:
                if day["date"] <= yesterday.isoformat():
                    days[day["date"]] = day["contributionCount"]
        start = end + dt.timedelta(days=1)

    return {
        "login": login,
        "through": yesterday.isoformat(),
        "languages": [{"name": n, "color": c} for n, (_, c) in top],
        "days": dict(sorted(days.items())),
    }


def derive(snap):
    counts = list(snap["days"].values())
    total = sum(counts)
    longest = run = 0
    for c in counts:
        run = run + 1 if c else 0
        longest = max(longest, run)
    current = 0
    for c in reversed(counts):
        if not c:
            break
        current += 1
    yesterday = counts[-1] if counts else 0
    stage = sum(total >= t for t in STAGE_THRESHOLDS)  # 1..6
    if yesterday == 0:
        behaviour = "resting"
    elif yesterday < 4:
        behaviour = "drifting"
    elif yesterday < 10:
        behaviour = "swimming"
    else:
        behaviour = "soaring"
    colors = []
    for lang in snap["languages"]:
        if lang.get("color"):
            h = lang["color"].lstrip("#")
            colors.append(tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)))
    colors = (colors + FALLBACK_LANGS)[:3]
    return {
        "total": total, "stage": stage, "longest": longest, "current": current,
        "yesterday": yesterday, "behaviour": behaviour, "colors": colors,
    }


# ---------------------------------------------------------------- drawing

BEHAVIOUR = {
    #            amplitude, wave cycles per loop, wavelength factor
    "resting":  (5.0, 1, 0.9),
    "drifting": (8.5, 1, 0.75),
    "swimming": (12.0, 2, 0.62),
    "soaring":  (15.0, 2, 0.55),
}


def spine(t, L, amp, cycles, wl, head_x, cy):
    """Points from head (index 0) to tail along a travelling wave."""
    phase = 2 * math.pi * cycles * t
    lam = L * wl
    pts = []
    for i in range(int(L) + 1):
        s = float(i)
        env = 0.35 + 0.65 * (s / L)
        x = head_x - s
        y = cy + amp * env * math.sin(2 * math.pi * s / lam - phase) \
            + 1.5 * math.sin(phase + 1.0)
        pts.append((x, y))
    return pts


def frames_for(d):
    stage, colors = d["stage"], d["colors"]
    L = STAGE_LENGTH[stage - 1]
    R = STAGE_RADIUS[stage - 1]
    amp, cycles, wl = BEHAVIOUR[d["behaviour"]]
    head_x = W / 2 + L / 2 - 4
    cy = H / 2 + 1
    bands = min(8, d["longest"] // 5)
    pearl_r = 1.5 + min(4.0, d["current"] / 3)
    pearl_fire = d["current"] > 0

    out = []
    for f in range(FRAMES):
        t = f / FRAMES
        phase = 2 * math.pi * t
        rng = random.Random(f)          # deterministic flicker, loops cleanly
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        pts = spine(t, L, amp, cycles, wl, head_x, cy)

        def radius(i):
            s = i / L
            return max(1.0, R * (1.0 - 0.8 * s ** 1.4) * (0.85 + 0.15 * math.sin(math.pi * min(1, s * 4))))

        def frame_at(i):
            j0, j1 = max(0, i - 1), min(len(pts) - 1, i + 1)
            tx, ty = pts[j0][0] - pts[j1][0], pts[j0][1] - pts[j1][1]
            n = math.hypot(tx, ty) or 1
            tx, ty = tx / n, ty / n
            return (tx, ty), (ty, -tx)        # forward, up

        def disc(cx, cy_, r, col):
            dr.ellipse([cx - r, cy_ - r, cx + r, cy_ + r], fill=col)

        # --- dorsal fins (language colours), behind the body
        if stage >= 2:
            for n, i in enumerate(range(8, len(pts) - 8, 6)):
                (fx, fy), (ux, uy) = frame_at(i)
                r = radius(i)
                x, y = pts[i]
                rise = (3.5 + (1.5 if stage >= 4 else 0)) * (1.0 - 0.45 * (i / L)) \
                    + 0.8 * math.sin(phase * 2 + i * 0.4)
                col = colors[n % len(colors)]
                tri = [(x + ux * (r - 1) + fx * 3, y + uy * (r - 1) + fy * 3),
                       (x + ux * (r - 1) - fx * 4, y + uy * (r - 1) - fy * 4),
                       (x + ux * (r + rise) - fx * 4.5, y + uy * (r + rise) - fy * 4.5)]
                dr.polygon(tri, fill=col, outline=OUTLINE)

        # --- legs, behind the body
        if stage >= 2:
            for k, frac in enumerate((0.16, 0.22, 0.58, 0.64)):
                i = int(L * frac)
                (fx, fy), (ux, uy) = frame_at(i)
                x, y = pts[i]
                r = radius(i)
                swing = math.sin(phase * cycles + k * 1.7) * 2.2
                kx, ky = x - ux * (r + 3.5) + fx * swing, y - uy * (r + 3.5) + fy * swing
                px, py = kx + fx * 3 - ux * 2, ky + fy * 3 - uy * 2
                dr.line([(x, y), (kx, ky), (px, py)], fill=OUTLINE, width=3)
                dr.line([(x, y), (kx, ky), (px, py)], fill=RED_DARK if k % 2 else RED, width=1)
                claw = GOLD if stage >= 6 else IVORY
                for c in (-1, 0, 1):
                    dr.point((px + fx * 1 + ux * c * 0.9, py + fy * 1 + uy * c * 0.9 - 0.5), fill=claw)

        # --- tail tuft
        if stage >= 5:
            tx_, ty_ = pts[-1]
            for k in range(5):
                a = math.pi + (k - 2) * 0.35 + 0.25 * math.sin(phase * 2 + k)
                ln = 5 + (k % 2) * 2
                dr.line([(tx_, ty_), (tx_ + math.cos(a) * ln, ty_ + math.sin(a) * ln)],
                        fill=GOLD_DARK if k % 2 else GOLD, width=2)

        # --- body: outline, fill, belly, highlight
        for i in range(len(pts) - 1, -1, -1):
            disc(*pts[i], radius(i) + 1, OUTLINE)
        for i in range(len(pts) - 1, -1, -1):
            disc(*pts[i], radius(i), RED)
        for i in range(len(pts) - 1, -1, -1):
            (fx, fy), (ux, uy) = frame_at(i)
            r = radius(i)
            x, y = pts[i]
            if r >= 1.6:
                disc(x - ux * r * 0.45, y - uy * r * 0.45, r * 0.5, BELLY)
                disc(x + ux * r * 0.5, y + uy * r * 0.5, r * 0.28, RED_LIGHT)
        # scales: small darker chevrons
        for i in range(4, len(pts) - 3, 3):
            (fx, fy), (ux, uy) = frame_at(i)
            x, y = pts[i]
            r = radius(i)
            off = (i // 3) % 2
            dr.point((x + ux * (off + 0.5) - fx * 0.5, y + uy * (off + 0.5) - fy * 0.5), fill=RED_DARK)
            if r > 2.5:
                dr.point((x - ux * r * 0.45 + fx * 0.5, y - uy * r * 0.45 + fy * 0.5), fill=BELLY_DARK)
        # golden bands = longest streak
        for b in range(bands):
            i = int(L * (0.18 + 0.7 * (b + 0.5) / max(bands, 1)))
            (fx, fy), (ux, uy) = frame_at(i)
            x, y = pts[i]
            r = radius(i)
            dr.line([(x + ux * r, y + uy * r), (x + ux * r * 0.1, y + uy * r * 0.1)], fill=GOLD, width=1)

        # --- head
        (fx, fy), (ux, uy) = frame_at(0)
        hx, hy = pts[0]
        hr = R * 1.5

        def P(a, b):  # local (forward, up) -> canvas
            return (hx + fx * a + ux * b, hy + fy * a + uy * b)

        # mane (behind head), language colour + gold
        if stage >= 3:
            for k in range(7):
                a = -0.5 - k * 1.8
                sway = math.sin(phase * 2 + k) * 1.5
                col = colors[k % len(colors)] if k % 2 else GOLD
                dr.polygon([P(a, hr * 0.5), P(a - 3, hr * 0.3), P(a - 5 + sway, hr + 4 + (k % 2) * 1.5)],
                           fill=col, outline=OUTLINE)
        # horns
        if stage >= 2:
            hl = 4 + stage * 1.5
            for side, shade in ((0.0, GOLD_DARK), (1.5, IVORY)):
                base = P(-0.5 - side, hr * 0.75)
                tip = P(-hl - side, hr * 0.75 + hl * 0.5)
                dr.line([base, tip], fill=OUTLINE, width=3)
                dr.line([base, tip], fill=shade, width=1)
                if stage >= 4:
                    mid = P(-hl * 0.55 - side, hr * 0.8 + hl * 0.33)
                    dr.line([mid, P(-hl * 0.45 - side, hr * 0.8 + hl * 0.7)], fill=shade, width=1)
        open_jaw = 1.2 if d["behaviour"] == "soaring" else (0.4 if d["behaviour"] == "swimming" else 0)
        snout = hr * 1.9
        # lower jaw
        dr.polygon([P(0, -hr * 0.2), P(snout * 0.9, -hr * 0.35 - open_jaw),
                    P(snout * 0.8, -hr * 0.85 - open_jaw), P(0, -hr * 0.9)],
                   fill=BELLY, outline=OUTLINE)
        # skull + upper snout
        dr.ellipse([hx - hr - 1, hy - hr - 1, hx + hr + 1, hy + hr + 1], fill=OUTLINE)
        dr.polygon([P(-hr * 0.4, hr * 0.95), P(snout, hr * 0.25), P(snout + 1, -hr * 0.25),
                    P(-hr * 0.4, -hr * 0.6)], fill=RED, outline=OUTLINE)
        dr.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=RED)
        dr.line([P(hr * 0.2, hr * 0.55), P(snout * 0.85, hr * 0.15)], fill=RED_LIGHT)
        # nostril
        dr.point(P(snout - 0.5, hr * 0.15), fill=BLACK)
        # eye (closed while resting)
        ex, ey = P(hr * 0.35, hr * 0.35)
        if d["behaviour"] == "resting":
            dr.line([(ex - 1, ey), (ex + 1, ey)], fill=BLACK)
        else:
            dr.rectangle([ex - 1, ey - 1, ex + 1, ey + 1], fill=EYE)
            dr.point((ex + fx * 0.6, ey + fy * 0.6), fill=BLACK)
        # brow ridge
        dr.line([P(-hr * 0.2, hr * 0.75), P(hr * 0.9, hr * 0.6)], fill=GOLD)
        # whiskers: long, trailing, animated
        wl_len = 6 + stage * 3
        for side, off in ((1, 0.0), (-1, 1.3)):
            prev = P(snout * 0.85, side * hr * 0.1)
            for k in range(1, wl_len + 1):
                a = snout * 0.85 - k * 1.0
                b = side * hr * 0.1 - k * 0.35 + 1.6 * math.sin(phase * 2 - k * 0.45 + off)
                cur = P(a, b - (0 if side > 0 else 1.5))
                dr.line([prev, cur], fill=GOLD if k < wl_len * 0.7 else GOLD_DARK)
                prev = cur
        # beard
        if stage >= 4:
            for k in range(3):
                dr.line([P(snout * 0.3 - k * 1.5, -hr * 0.85),
                         P(snout * 0.2 - k * 2 - 1, -hr * 1.7 - k % 2 + 0.6 * math.sin(phase * 2 + k))],
                        fill=IVORY)

        # --- fire breath when soaring
        if d["behaviour"] == "soaring":
            for k in range(14):
                dist = snout + 2 + rng.random() * 16
                spread = (dist - snout) * 0.3
                fx_, fy_ = P(dist, -hr * 0.45 + rng.uniform(-spread, spread))
                col = rng.choice([(255, 236, 120), (255, 170, 40), (240, 90, 30)])
                dr.rectangle([fx_, fy_, fx_ + (1 if dist < snout + 10 else 0), fy_], fill=col)

        # --- the pearl (current streak)
        bx = min(W - pearl_r - 3, hx + snout + 14 + (4 if d["behaviour"] == "soaring" else 0))
        by = hy - 3 + 2.5 * math.sin(phase * 2 + 1.3)
        if pearl_fire:
            for k in range(8):
                a = 2 * math.pi * k / 8 + phase
                fl = pearl_r + 1.5 + rng.random() * (1 + d["current"] / 10)
                dr.line([(bx, by), (bx + math.cos(a) * fl, by + math.sin(a) * fl)],
                        fill=(255, 150, 40) if k % 2 else (255, 214, 90))
        dr.ellipse([bx - pearl_r - 1, by - pearl_r - 1, bx + pearl_r + 1, by + pearl_r + 1], fill=OUTLINE)
        dr.ellipse([bx - pearl_r, by - pearl_r, bx + pearl_r, by + pearl_r],
                   fill=(255, 244, 214) if pearl_fire else (196, 196, 206))
        dr.point((bx - pearl_r * 0.4, by - pearl_r * 0.4), fill=(255, 255, 255))

        out.append(img)
    return out


def save_gif(frames, path):
    """Quantise to one shared palette with index 0 transparent, upscale, write."""
    palette = [(0, 0, 0)]
    index = {}
    paletted = []
    for fr in frames:
        data = []
        for r, g, b, a in fr.getdata():
            if a < 128:
                data.append(0)
                continue
            key = (r, g, b)
            if key not in index:
                if len(palette) >= 256:
                    key = min(index, key=lambda c: sum((c[i] - key[i]) ** 2 for i in range(3)))
                else:
                    index[key] = len(palette)
                    palette.append(key)
            data.append(index[key])
        p = Image.new("P", fr.size)
        p.putdata(data)
        paletted.append(p)
    flat = [v for c in palette for v in c] + [0] * (768 - 3 * len(palette))
    big = []
    for p in paletted:
        p.putpalette(flat)
        big.append(p.resize((W * SCALE, H * SCALE), Image.NEAREST))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".gif", dir=os.path.dirname(path) or ".")
    os.close(fd)
    big[0].save(tmp, save_all=True, append_images=big[1:], duration=FRAME_MS, loop=0,
                transparency=0, disposal=2, optimize=False)
    os.replace(tmp, path)   # keep the previous GIF if anything above failed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("login", nargs="?")
    ap.add_argument("--snapshot", help="render from a saved snapshot JSON instead of the API")
    ap.add_argument("--save-snapshot", help="also write the collected snapshot here")
    ap.add_argument("--output", default="dragon/dragon.gif")
    args = ap.parse_args()

    if args.snapshot:
        with open(args.snapshot) as fh:
            snap = json.load(fh)
    else:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not args.login or not token:
            ap.error("need a login and GH_TOKEN (or --snapshot)")
        snap = collect(args.login, token)
        if args.save_snapshot:
            with open(args.save_snapshot, "w") as fh:
                json.dump(snap, fh)

    d = derive(snap)
    print(json.dumps({k: v for k, v in d.items() if k != "colors"}), file=sys.stderr)
    save_gif(frames_for(d), args.output)


if __name__ == "__main__":
    main()
