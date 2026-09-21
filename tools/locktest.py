"""Probe the pointer-capture path in a headless browser.

Unlike the other tools this one leaves USE_POINTER_LOCK on, because the point is
to see which branch runs. A headless browser will not grant a pointer lock, so it
runs twice:

  granted?  — as the browser leaves it. The lock is refused, so this proves the
              refusal is handled and the absolute fallback still plays.
  captured  — with the captured flag forced on, the only way to drive the
              relative-motion branch here: raw mouse deltas must move the marker
              1:1 and leave it exactly where they put it.

Run:  py tools/locktest.py
"""
import os, re, sys, tempfile

from _edge import dump

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

base = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
assert "const USE_POINTER_LOCK = true;" in base       # left ON for this probe

PEEP = """
  window.__peek = () => ({ state, locked, px: player.x, py: player.y,
                           cx: cursor.x, cy: cursor.y });
  window.__clear = () => { cubes.length = 0; };
"""

PRELUDE = """
<script>
window.__q = [];
window.requestAnimationFrame = cb => { window.__q.push(cb); return 1; };
window.onerror = (m, s, l) => { window.__err = m + ' @' + l; };
</script>
"""

DRIVER = """
<div id="out" style="display:none"></div>
<script>
(async () => {
  const log = [], say = s => log.push(s);
  let bad = 0;
  const ok = (c, what, note) => { if (!c) bad++;
    say((c ? 'PASS ' : 'FAIL ') + what + (note ? '  [' + note + ']' : '')); };
  let t = 0;
  const canvas = document.getElementById('game');
  const step = ms => { t += ms; for (const cb of window.__q.splice(0)) cb(t); };
  const frames = n => { for (let i = 0; i < n; i++) step(1000 / 60); };

  step(0);
  document.getElementById('startBtn').click();
  await new Promise(r => setTimeout(r, 300));     // give the lock request time
  frames(6);
  window.__clear();
  let p = window.__peek();
  const cap = p.locked;
  say('mode         ' + (cap ? 'captured (relative motion)' : 'absolute fallback'));
  ok(p.state === 1, 'the round is running', 'state=' + p.state);
  ok(!cap || (Math.abs(p.cx - 450) < 1 && Math.abs(p.cy - 300) < 1),
     'capture starts the marker in the middle', p.cx.toFixed(0) + ',' + p.cy.toFixed(0));

  const start = { x: p.px, y: p.py };
  const rel = (dx, dy) => document.dispatchEvent(new MouseEvent('mousemove',
    { movementX: dx, movementY: dy, bubbles: true }));
  for (let i = 0; i < 5; i++) { rel(12, 0); frames(1); }
  p = window.__peek();
  const slid = p.px - start.x;
  say('relative x5  marker=' + p.px.toFixed(1) + ',' + p.py.toFixed(1));
  if (cap) {
    ok(slid > 40, 'raw movement drives the marker', 'moved ' + slid.toFixed(1) + 'px right');
    // 1:1 up to the canvas's own CSS scale, which a narrow window can shrink.
    ok(Math.abs(slid - 60) < 6, 'and 1:1 with the mouse', slid.toFixed(2) + 'px for 60px of mouse');
  } else {
    ok(Math.abs(slid) < 0.001, 'raw movement is ignored without the lock', 'moved ' + slid.toFixed(3) + 'px');
  }

  // Space is a dead key now. On the captured path especially, anything it still
  // did to the marker would be invisible to the player until it cost them a life.
  const a = window.__peek();
  window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space', bubbles: true }));
  frames(1);
  const sp = window.__peek();
  ok(Math.hypot(sp.px - a.px, sp.py - a.py) < 1e-9, 'Space does nothing to the marker',
     'moved ' + Math.hypot(sp.px - a.px, sp.py - a.py).toFixed(3) + 'px');

  frames(90);                                     // 1.5 s: any drift would show
  const s = window.__peek();
  const off = Math.hypot(s.px - s.cx, s.py - s.cy);
  const since = Math.hypot(s.px - a.px, s.py - a.py);
  say('1.5s later   marker=' + s.px.toFixed(1) + ',' + s.py.toFixed(1) +
      ' cursor=' + s.cx.toFixed(1) + ',' + s.cy.toFixed(1));
  ok(off < 1e-6, 'the marker ends up exactly on the pointer', 'off by ' + off.toExponential(1) + 'px');
  ok(since < 1e-6, 'and stays where the mouse left it, on either path',
     'moved ' + since.toFixed(3) + 'px with the mouse still');
  ok(!window.__err, 'no runtime errors', window.__err || 'clean');
  say('BAD ' + bad);
  document.getElementById('out').textContent = 'R>>' + log.join('\\nR>>');
})();
</script>
"""


def run(name, tweaks):
    g = base.replace("const MAX_LIVES = 3;", "const MAX_LIVES = 999;")
    for a, b in tweaks:
        assert a in g, a
        g = g.replace(a, b)
    g = g.replace("  // ---------- boot ----------", PEEP + "\n  // ---------- boot ----------")
    g = g.replace("<script>\n(() => {", PRELUDE + "<script>\n(() => {", 1)
    g = g.replace("</body>", DRIVER + "</body>", 1)
    tmp = os.path.join(tempfile.gettempdir(), "cd_lock_%s.html" % name)
    open(tmp, "w", encoding="utf-8").write(g)
    out = dump(tmp, timeout=180, extra=["--virtual-time-budget=4000"])
    lines = [x for m in re.findall(r"R&gt;&gt;(.*)|R>>(.*)", out) for x in m if x]
    print("== " + name)
    tail = [re.match(r"BAD (\d+)", l) for l in lines]     # the script's own source
    cut = next((i for i, m in enumerate(tail) if m), None)  # echoes after this line
    if cut is None:
        print("   no results — dump follows:\n", out[:1500])
        return 1
    print("\n".join("   " + l for l in lines[:cut]))
    return int(tail[cut].group(1))


# Forcing the captured flag on is the only way to reach the relative branch here:
# the flag starts true and the refusal handler is stubbed out so it stays true.
bad = run("as-served", ())
bad += run("captured", (
    ("  let locked  = false;", "  let locked  = true;"),
    ("document.addEventListener('pointerlockerror', () => { locked = false; });",
     "document.addEventListener('pointerlockerror', () => {});"),
))
print("\n" + ("all green" if not bad else "%d failing check(s)" % bad))
sys.exit(1 if bad else 0)
