"""One-off probe: does the boss track swap in and out on the right rounds?

Instruments the two <audio> elements, plays a rigged game with tiny rounds, and
reports which track was playing round by round plus anything play() refused.

Run:  py tools/_musicprobe.py
"""
import os, re, sys, tempfile

from _edge import dump

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
game = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()

for a, b in (("const ROUND_MIN   = 20, ROUND_MAX = 30;", "const ROUND_MIN   = 2, ROUND_MAX = 2;"),
             ("const ROUND_BREAK = 2.6;", "const ROUND_BREAK = 0.2;"),
             ("const BOSS_BREAK  = 4.4;", "const BOSS_BREAK  = 0.3;"),
             ("const MAX_LIVES = 3;", "const MAX_LIVES = 999;"),
             ("const USE_POINTER_LOCK = true;", "const USE_POINTER_LOCK = false;"),
             ("    activeEvent = EVENT_MAKERS[cfg.name]();",
              "    activeEvent = { t: 0, quiet: true, "
              "update(dt) { this.t += dt; return this.t < 0.4; } };")):
    assert a in game, a
    game = game.replace(a, b)

peep = """
  window.__music = () => ({
    round: roundNo, live,
    track: track === bgmBoss ? 'boss' : 'plain',
    playing: TRACKS.filter(el => !el.paused).map(el => el.id).join(','),
    vol: TRACKS.map(el => el.id + '=' + el.volume.toFixed(2)).join(' '),
    muted: TRACKS.map(el => el.id + '=' + el.muted).join(' ')
  });
"""
game = game.replace("  // ---------- boot ----------", peep + "\n  // ---------- boot ----------")

prelude = """
<script>
window.__errors = [];
window.onerror = (m, s, l) => window.__errors.push(m + ' @' + l);
window.__q = [];
window.requestAnimationFrame = cb => { window.__q.push(cb); return 1; };
window.__rej = [];
const realPlay = HTMLMediaElement.prototype.play;
HTMLMediaElement.prototype.play = function () {
  const p = realPlay.call(this);
  if (p && p.catch) p.catch(e => window.__rej.push((this.id || this.src) + ': ' + e.name));
  return p;
};
</script>
"""
game = game.replace("<script>\n(() => {", prelude + "<script>\n(() => {", 1)

driver = """
<div id="out" style="display:none"></div>
<script>
const LOG = [];
const TAG = 'M' + '>>';
let T = 0;
function step(n) {
  for (let i = 0; i < n; i++) { T += 1000 / 60; for (const cb of window.__q.splice(0)) cb(T); }
}
step(1);
document.getElementById('startBtn').click();
let seen = {};
for (let i = 0; i < 60 * 120; i++) {
  step(1);
  const m = window.__music();
  if (m.round >= 1 && m.live && !seen[m.round]) { seen[m.round] = m; }
  if (window.__peek && window.__peek().state !== 1) break;
  if (m.round >= 12) break;
}
for (const n of Object.keys(seen).sort((a, b) => a - b)) {
  const m = seen[n];
  LOG.push('round ' + String(n).padStart(2) + '  track=' + m.track.padEnd(5) +
           '  playing=' + (m.playing || 'none').padEnd(15) + '  ' + m.vol);
}
// the slider and mute still reach both tracks
document.getElementById('volRange').value = '70';
document.getElementById('volRange').dispatchEvent(new Event('input', { bubbles: true }));
LOG.push('after slider   ' + window.__music().vol);
document.getElementById('muteBtn').click();
LOG.push('after mute     ' + window.__music().muted);
document.getElementById('muteBtn').click();
LOG.push('after unmute   ' + window.__music().muted);
LOG.push('refused        ' + (window.__rej.length ? window.__rej.join(' | ') : 'nothing'));
LOG.push('errors         ' + (window.__errors.length ? window.__errors.join(' | ') : 'none'));
document.getElementById('out').textContent = TAG + LOG.join('\\n' + TAG);
</script>
"""
game = game.replace("</body>", driver + "</body>", 1)

tmp = os.path.join(tempfile.gettempdir(), "music_probe.html")
open(tmp, "w", encoding="utf-8").write(game)
out = dump(tmp, timeout=300)
lines = [l.strip() for l in re.findall(r"M&gt;&gt;(.*)|M>>(.*)", out) for l in l if l]
if not lines:
    print("no results — dump follows:\n", out[:3000])
    sys.exit(1)
print("\n".join(l[:-6].rstrip() if l.endswith("</div>") else l for l in lines))
