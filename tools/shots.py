"""Dump the arena canvas for each round's event, for eyeballing the visuals.

Headless Edge won't composite this page into a --screenshot, so instead the
game is stepped with a fake clock and the canvas is exported with toDataURL(),
which is the only surface worth looking at anyway.

Run:  py tools/shots.py      ->  writes tools/shots/*.png
"""
import base64, os, re, sys, tempfile

from _edge import dump

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tools", "shots")
os.makedirs(OUT, exist_ok=True)

base = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()

CFG = "  const FORCE_ROUND = null;"


# Pinning the schedule to one event makes round 1 the one we want to photograph —
# and, for the box, makes it the boss round it is every fifth round anyway.
def only(name):
    return (CFG, "  const FORCE_ROUND = '%s';" % name)


# The barrage picks its pattern at random, so pin it.
def pattern(i):
    return (("pick(ROCKET_PATTERNS)", "ROCKET_PATTERNS[%d]" % i),)


# Walls pick a side at random. Pinning it makes the timing of a shot predictable —
# a wall from the left takes 4.5 s to cross — and, since two slabs never share an
# axis, it also puts them back in single file. Used where the walls are the backdrop
# rather than the subject.
WALL_L = (("let sides = ['l', 'r', 'u', 'd'].filter(d => d !== lastWallDir);",
            "let sides = ['l'];"),)

# Nine seconds of standing in a corner is nine seconds of walls going through you,
# and the rescue cube it earns takes over the whole frame. Held back where the shot
# is of something else.
NOHEART = (("const HEART_DELAY = 6.0;", "const HEART_DELAY = 1e9;"),)

# …and the same for the idle watch, whose loner would otherwise turn up in any frame
# where the marker is parked away from the prize cube. It has frames of its own.
NOIDLE = (("const IDLE_MIN  = 8;", "const IDLE_MIN  = 1e9;"),)

# The boss only ever plays rounds 10 and 20, so the volley a player actually meets is
# four shots, not the three its base numbers give it. These frames are pinned to round
# 1, so the count is pinned back up to what it is when the thing is met.
HUNT4 = (("shots:  3 + T,", "shots:  4,"),)

# The rescue's two clocks only mean anything against a round of a known length, so the
# frames that photograph it racing the round pin the round to 22 s — and the count with
# it, since the game creeps a neglected count back up to HEART_COUNT and no further.
R22 = (("const ROUND_MIN   = 20, ROUND_MAX = 30;", "const ROUND_MIN   = 22, ROUND_MAX = 22;"),)


def count(n):
    return R22 + (("const HEART_COUNT = 9;", "const HEART_COUNT = %d;" % n),)

HALF_CW = (("const dir = Math.random() < 0.5 ? 1 : -1;      // round clockwise, or back",
            "const dir = 1;      // round clockwise, or back"),
           ("const s0 = (Math.random() * 4) | 0;", "const s0 = 0;"))


HOOK = """
  // shots.py: park the prize cube so a frame can be composed around it
  window.__pin = (x, y) => { orb.step = () => true; orb.x = x; orb.y = y; };
  // …and jump the multiplier, to photograph what the cap looks like
  window.__mult = m => { mult = m; };
  // …and stage a rescue: a heart lost, its gold cube parked where the shot wants
  // it, already part way through its count. `heartUsed` stops the game offering a
  // second one on top of the staged one.
  window.__rescue = (x, y, left) => {
    takeHit();
    heartUsed = true;
    const h = spawnHeart();
    h.step = () => true;
    h.x = x - h.w / 2; h.y = y - h.h / 2;
    h.born = 0; h.ghost = false; h.left = left;
  };
  // …and end the round on the spot, so the breather between rounds — and the boss
  // announcement in it — can be photographed without playing a round out first.
  window.__endRound = () => { activeEvent = null; roundT = 1e3; roundLen = 0; };
"""

# name, round to force, seconds of play before the capture, mouse, setup, tweaks
SHOTS = [
    ("prize",         "wall",    0.40, (398, 300), "window.__pin(450, 283);", ()),
    ("prize-far",     "wall",    0.40, (300, 300), "window.__pin(450, 283);", ()),
    ("prize-maxed",   "wall",    0.40, (398, 300), "window.__pin(450, 283); window.__mult(12);", ()),
    ("rocket-warn",   "rocket",  1.20, (450, 300), "", pattern(0)),
    ("rocket-sweep",  "rocket",  2.60, (450, 300), "", pattern(0)),
    ("rocket-ladder", "rocket",  2.60, (450, 300), "", pattern(1)),
    ("rocket-star",   "rocket",  2.60, (450, 300), "", pattern(2)),
    ("wall-warn",     "wall",    0.70, (450, 300), "", ()),
    ("wall-live",     "wall",    1.80, (450, 300), "", ()),
    # Walls come in a train now, and two of them share the board as long as they are
    # crossing on different axes: by 5.6 s one slab is most of the way over and the
    # next has come in across it, which is the shape of the whole round.
    ("wall-cross",    "wall",    5.60, (450, 300), "", ()),
    # The first wave comes in off the four corners; the next one off the middles of
    # the four edges, which is the safe ground the first wave left. One wave takes
    # about 3.4 s end to end, so 5.3 s is the second one at full charge.
    ("corners",       "corners", 1.90, (450, 300), "", ()),
    ("corners-gate",  "corners", 4.00, (450, 300), "", ()),
    ("corners-card",  "corners", 5.30, (450, 300), "", ()),
    ("rescue",        "corners", 2.20, (473, 280), "window.__rescue(520, 280, 6.4);", ()),
    ("rescue-far",    "corners", 2.20, (170, 480), "window.__rescue(520, 280, 10);", ()),
    # …and the same cube losing its race with the round: a count of 19 s against the
    # 20 s left of a 22 s round is a second of daylight, which is amber and flashing;
    # a count of 30 s cannot be finished at all, so half a second later it is grey,
    # hollow, struck through and already half faded out.
    ("rescue-tight",  "corners", 2.00, (170, 480), "window.__rescue(520, 280, 19);", count(19)),
    ("rescue-lapsed", "corners", 0.40, (170, 480), "window.__rescue(520, 280, 30);", count(30)),
    # One vortex winds in, is spent, and the next opens somewhere else entirely:
    # 2.7 s is the middle of the first, 8.2 s the middle of the second.
    ("spiral-early",  "spiral",  2.70, (450, 300), "", ()),
    ("spiral-later",  "spiral",  8.20, (450, 300), "", ()),
    ("box-warn",      "box",     0.70, (700, 480), "", ()),
    ("box-closed",    "box",     2.40, (450, 300), "", ()),
    ("box-roam",      "box",     4.60, (450, 300), "", ()),
    ("clock-warn",    "clock",   0.60, (450, 300), "", ()),
    ("clock-hold",    "clock",   1.35, (450, 300), "", ()),
    ("clock-round",   "clock",   5.60, (450, 300), "", ()),
    # The round as it is actually played: the dial and the one small hunter it always
    # comes with, by now prowling and shooting. No loose traffic — the clock is dealt
    # none, so this frame is the whole round.
    ("clock-busy",    "clock",   9.60, (450, 420), "", NOHEART + NOIDLE),
    # The two small hunters outline 0.45 s apart, so at 1.3 s the first is on the
    # stage and the other is still only a promise. By 5.2 s both are prowling and
    # their single shots are in the air.
    ("minis-warn",    "minis",   1.30, (450, 420), "", ()),
    ("minis-live",    "minis",   2.60, (450, 420), "", ()),
    ("minis-shots",   "minis",   5.20, (450, 420), "", ()),
    # Abandoning the prize cube for long enough hands you a body you did not earn.
    # The cube is parked in one corner and the marker sits in the other, so the
    # neglect clock starts at once: by 7 s the cube is asking for company out loud,
    # and just after 8 s the loner outlines.
    ("lonely",        "wall",    7.00, (120, 120), "window.__pin(780, 470);", WALL_L + NOHEART),
    ("idle-mini",     "wall",    8.60, (120, 120), "window.__pin(780, 470);", WALL_L + NOHEART),
    # The boss outlines for 1.1 s, swells, then charges over the 0.6 s before its
    # first volley at ~2.7 s. Those shots track for 1.5 s and commit at ~4.2 s.
    ("hunter-warn",   "hunter",  0.80, (450, 420), "", ()),
    ("hunter-aim",    "hunter",  2.55, (450, 420), "", ()),
    ("hunter-shots",  "hunter",  3.40, (450, 420), "", HUNT4),
    ("hunter-commit", "hunter",  4.45, (450, 420), "", HUNT4),
    ("half-warn",     "half",    0.60, (700, 300), "", HALF_CW),
    ("half-hold",     "half",    2.20, (700, 300), "", HALF_CW),
    ("half-turn",     "half",    3.75, (700, 480), "", HALF_CW),
    ("boss-banner",   "box",     0.30, (450, 300), "", ()),
    ("boss-warn",     "box",     0.60, (450, 300), "window.__endRound();", ()),
]

PRELUDE = """
<script>
window.__q = [];
window.requestAnimationFrame = cb => { window.__q.push(cb); return 1; };
window.onerror = (m, s, l) => { window.__err = m + ' @' + l; };
</script>
"""


def build(name, event, secs, mouse, setup, extra=()):
    g = base
    for a, b in (("const MAX_LIVES = 3;", "const MAX_LIVES = 999;"),
                 ("const USE_POINTER_LOCK = true;", "const USE_POINTER_LOCK = false;"),
                 only(event)) + tuple(extra):
        assert a in g, a
        g = g.replace(a, b)
    g = g.replace("  // ---------- boot ----------", HOOK + "\n  // ---------- boot ----------")
    g = g.replace("<script>\n(() => {", PRELUDE + "<script>\n(() => {", 1)

    driver = """
<div id="png" style="display:none"></div>
<script>
(() => {
  let t = 0;
  const canvas = document.getElementById('game');
  const step = ms => { t += ms; for (const cb of window.__q.splice(0)) cb(t); };
  const r = canvas.getBoundingClientRect();
  const put = () => canvas.dispatchEvent(new MouseEvent('mousemove', {
    clientX: r.left + %d * (r.width / 900),
    clientY: r.top  + %d * (r.height / 600), bubbles: true }));
  step(0);
  document.getElementById('startBtn').click();
  put(); step(1000 / 60);
  %s
  const n = Math.round(%f * 60);
  for (let i = 0; i < n; i++) { put(); step(1000 / 60); }
  document.getElementById('png').textContent =
    (window.__err ? 'ERR:' + window.__err : canvas.toDataURL('image/png'));
})();
</script>
""" % (mouse[0], mouse[1], setup, secs)
    g = g.replace("</body>", driver + "</body>", 1)
    p = os.path.join(tempfile.gettempdir(), "shot_%s.html" % name)
    open(p, "w", encoding="utf-8").write(g)
    return p


bad = 0
want = sys.argv[1:]
for name, event, secs, mouse, setup, extra in SHOTS:
    if want and not any(w in name for w in want):
        continue
    src = build(name, event, secs, mouse, setup, extra)
    dom = dump(src, timeout=180)
    m = re.search(r'id="png"[^>]*>data:image/png;base64,([A-Za-z0-9+/=]+)<', dom)
    if not m:
        err = re.search(r'id="png"[^>]*>(ERR:[^<]*)<', dom)
        print("FAIL %-13s %s" % (name, err.group(1) if err else "no canvas data"))
        bad += 1
        continue
    png = os.path.join(OUT, name + ".png")
    open(png, "wb").write(base64.b64decode(m.group(1)))
    print("ok   %-13s %6d bytes  %s" % (name, os.path.getsize(png), png))

sys.exit(1 if bad else 0)
