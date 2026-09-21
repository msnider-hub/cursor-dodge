"""Behaviour tests for index.html: the prize cube, the multiplier, and the rounds.

Each scenario runs the real game loop in headless Edge on a fake clock, with a
few debug hooks spliced in, and reports PASS/FAIL lines.

Run:  py tools/logictest.py
"""
import os, re, sys, tempfile

from _edge import dump

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
base = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()

# ---- source hooks the scenarios patch. Exact strings, so a reformat is caught.
LOCK  = ("const USE_POINTER_LOCK = true;", "const USE_POINTER_LOCK = false;")
LIVES = ("const MAX_LIVES = 3;", "const MAX_LIVES = 999;")
NOAMB = ("    return cfg.ambient || (tier() >= 2 ? 0.45 : 0);", "    return 0;")
# Keeps the rescue cube out of scenarios that count hearts for other reasons: a
# life handed back mid-run is exactly what they are not measuring.
NOHEART = ("const HEART_DELAY = 6.0;", "const HEART_DELAY = 1e9;")
# …and the same for the idle watch. Almost every scenario parks the marker in the
# middle of the arena and lets the prize cube wander, which is precisely the
# behaviour the watch answers — so a scenario counting the cubes of one event has to
# turn it off, or it ends up counting a hunter it never asked for. The idle-watch
# scenario is the one that leaves it on.
NOIDLE = ("const IDLE_MIN  = 8;", "const IDLE_MIN  = 1e9;")
CFG   = "  const FORCE_ROUND = null;"
MAKE  = "    activeEvent = EVENT_MAKERS[cfg.name]();"
# The schedule reserves every fifth round for the boss and draws the rest, so a
# scenario that wants twenty rounds of one event pins the whole schedule instead.
BREATHER = (("const ROUND_BREAK = 2.6;", "const ROUND_BREAK = %s;"),
            ("const BOSS_BREAK  = 4.4;", "const BOSS_BREAK  = %s;"))


# Every round runs the same event type, so one scenario can watch a series of them.
def only(name):
    return (CFG, "  const FORCE_ROUND = '%s';" % name)


def breather(secs):
    return tuple((a, b % secs) for a, b in BREATHER)

# A stand-in event that telegraphs nothing and spawns nothing: an empty arena to
# test the marker and the multiplier in, with the round machinery still running.
NOEVENT = (MAKE, "    activeEvent = { t: 0, quiet: true, "
                 "update(dt) { this.t += dt; return this.t < 0.4; } };")

# The rescue cube on a stopwatch a scenario can afford to sit through: the delay
# and the count are the two numbers that decide how long its story takes.
def heart(delay, count):
    return (("const HEART_DELAY = 6.0;", "const HEART_DELAY = %s;" % delay),
            ("const HEART_COUNT = 9;", "const HEART_COUNT = %s;" % count))


def rounds(lo, hi):
    return ("const ROUND_MIN   = 20, ROUND_MAX = 30;",
            "const ROUND_MIN   = %s, ROUND_MAX = %s;" % (lo, hi))

HOOKS = """
  window.__peek = () => ({
    state, score, elapsed, lives, mult, bestMult, prox, graceT,
    cubes: cubes.length,
    roundNo, roundT, roundLen, live, breakT, waveNo, cleared,
    sched: schedule.join(','), boss: isBoss(roundNo), nextBoss: isBoss(roundNo + 1),
    cfg: (roundCfg() || {}).name || null,
    ev: activeEvent && activeEvent.name, evT: (activeEvent && activeEvent.t) || 0,
    evDir: (activeEvent && activeEvent.dir) || null,
    evNext: (activeEvent && activeEvent.next && activeEvent.next.dir) || null,
    evPh: (activeEvent && activeEvent.ph) || null,
    focus: activeEvent && activeEvent.focus
      ? [activeEvent.focus.x, activeEvent.focus.y, activeEvent.focus.r,
         !!activeEvent.focus.keep] : null,
    // …with a seventh field saying what kind of thing it is, so a scenario measuring an
    // event's own cubes can leave out a hunter body (1), one of its shots (2) or a piece
    // of the ambient traffic layer (3)
    rects: cubes.filter(c => !c.orb && !c.heart)
      .map(c => [c.x, c.y, c.w, c.h, c.vx, c.vy,
                 c.hunter ? 1 : c.shot ? 2 : c.amb ? 3 : 0]),
    orb: cubes.indexOf(orb) !== -1 ? [orb.x, orb.y, orb.w, orb.h] : null,
    orbs: cubes.filter(c => c.orb).length,
    orbCaged: !!(orb && orb.caged),
    // the siege: the boss, its lives, the numbers this round of it was dealt, and
    // whichever decoy is currently out
    siege: siege ? {
      lives: siege.lives, freed: siege.freed, won: siege.won, late: siege.late,
      band: siege.band, unlock: siege.unlock, relock: siege.relock,
      boss: [siege.boss.pos.x, siege.boss.pos.y],
      decoy: siege.decoy ? [siege.decoy.pos.x, siege.decoy.pos.y,
                            siege.decoy.shield, siege.decoy.crack,
                            siege.decoy.flash, siege.decoy.fireT,
                            siege.decoy.salvo, siege.decoy.charge(),
                            siege.decoy.hold ? 1 : 0] : null,
      decoyPh: siege.decoy ? siege.decoy.ph : null
    } : null,
    heart: heartCube ? [heartCube.x, heartCube.y, heartCube.w, heartCube.h,
                        heartCube.left, !!heartCube.near, !!heartCube.ghost] : null,
    // the two clocks the rescue is caught between, and the prize cube's own mood
    heartSlack: heartCube ? heartCube.slack : null,
    heartDoom: !!(heartCube && heartCube.doom),
    orbFright: orb ? (orb.fright || 0) : 0,
    orbSpin: orb ? (orb.spin || 0) : 0,
    hearts: cubes.filter(c => c.heart).length,
    px: player.x, py: player.y, multShown: mult > 1.05,
    cx: cursor.x, cy: cursor.y,
    evLines: activeEvent && activeEvent.lines
      ? activeEvent.lines.map(l => [l.px, l.py, l.dx, l.dy]) : null,
    evFired: (activeEvent && activeEvent.fired) || 0,
    tier: tier(),
    evSpeed: (activeEvent && activeEvent.speed) || 0,
    evVary: (activeEvent && activeEvent.vary) || 0,
    evShots: (activeEvent && activeEvent.shots) || 0,
    evTotal: (activeEvent && activeEvent.total) || 0,
    evN: (activeEvent && activeEvent.n) || 0,
    evVolley: (activeEvent && activeEvent.volley) || 0,
    // the hunter bodies, and every shot any of them has in the air: centre,
    // velocity, how long it has been flying and how long it tracks for
    bodies: cubes.filter(c => c.hunter).length,
    body: (b => b ? [b.x + b.w / 2, b.y + b.h / 2, b.w, !!b.ghost] : null)
          (cubes.filter(c => c.hunter)[0]),
    shots: cubes.filter(c => c.shot)
      .map(c => [c.x + c.w / 2, c.y + c.h / 2, c.vx, c.vy, c.t, c.home]),
    // the units behind those bodies, whatever put them on the stage, plus the idle
    // watch's own books: how long the prize cube has been left alone this round
    prowl: prowlers.length,
    units: prowlers.map(u => [u.pos.x, u.pos.y, u.size, u.shots, u.mini ? 1 : 0, u.k]),
    phases: prowlers.map(u => u.ph).join(','),
    awayT, idler: !!idler, lonely, lonelyT,
    // what the prize cube is saying, if anything, and whether it is mid-breath
    quip: quip ? quip.key : null,
    quipText: quip ? quip.text : null,
    quipGap,
    evCard: !!(activeEvent && activeEvent.card)
  });
  // The table itself, and the state of the bags dealing out of it. Kept off __peek,
  // which some scenarios call every frame for a couple of hundred frames.
  window.__quips = () => ({
    keys: Object.keys(QUIPS),
    sets: Object.keys(QUIPS).reduce((o, k) => (o[k] = QUIPS[k].lines.slice(), o), {}),
    bag: Object.keys(QUIPS).reduce((o, k) => (o[k] = (quipBags[k] || []).length, o), {}),
    // read rather than written down here, so retuning how chatty the cube is re-aims
    // the assertion instead of leaving a stale number in its report
    cools: Object.keys(QUIPS).reduce((o, k) => (o[k] = QUIPS[k].cool || 0, o), {})
  });
  // Make a moment happen, or deal from its bag without making it happen, for the tests
  // that are about the dealing rather than about the triggers.
  window.__say = k => quipSay(k);
  window.__deal = k => quipDeal(k, QUIPS[k].lines);
  window.__spawn = c => pushCube(c);
  window.__clear = () => { cubes.length = 0; };
  // Park the prize cube: back in play if it was cleared, and stationary.
  window.__pin = (x, y) => {
    if (cubes.indexOf(orb) === -1) pushCube(orb);
    orb.step = () => true;
    orb.x = x; orb.y = y; orb.vx = 0; orb.vy = 0;
  };
  // Lose a heart on demand, without having to be hit by anything in particular.
  window.__hurt = () => takeHit();
  window.__k = { NEAR_BAND, MULT_GRACE, MULT_DECAY, MULT_GAIN, MULT_CURVE, MULT_MAX,
                 ORB_SIZE, MAX_ROUNDS, PLAYER_R, MAX_LIVES, INVULN,
                 ROUND_MIN, ROUND_MAX, ROUND_BREAK, BOSS_BREAK, BOSS_EVERY, BOX_OPEN,
                 WALL_CELL, WALL_WARN, WALL_SPEED, SPIRAL_EYE_R, SPIRAL_SPEED,
                 HEART_DELAY, HEART_COUNT, HEART_BAND, HEART_SLIP, HEART_FADE,
                 HEART_TIGHT, HEART_LEAVE, ORB_SCARE, ORB_HOP, ORB_CLING,
                 CLOCK_R, CLOCK_TICKS, CLOCK_DWELL, CLOCK_SNAP, CLOCK_ARM,
                 HALF_TURNS, HALF_HOLD, HALF_TURN,
                 SPIRAL_INSET, SPIRAL_WARN, SPIRAL_GAP, SPIRAL_ARMS, SPIRAL_ARMS_T,
                 HUNT_WARN, HUNT_SIZE, HUNT_SPEED, HUNT_EXIT, HUNT_TRACK,
                 HUNT_TURN, HUNT_HOME, HUNT_DASH, HUNT_SPACE, HUNT_VOLLEY,
                 SIEGE_LIVES, SIEGE_SHOTS, SIEGE_SHOTS_2, SIEGE_VOLLEY, SIEGE_VOLLEY_2,
                 SIEGE_FIRST, SIEGE_GAP, SIEGE_BAND, SIEGE_BAND_2,
                 SIEGE_UNLOCK, SIEGE_UNLOCK_2, SIEGE_RELOCK, SIEGE_RELOCK_2, SIEGE_WON,
                 MINI_SIZE, MINI_COUNT, MINI_MAX, MINI_SPEED, MINI_VOLLEY,
                 MINI_TRACK, MINI_HOME, MINI_STAGGER,
                 IDLE_MIN, IDLE_PART, IDLE_LEFT, LONELY_AT, LONELY_FLASH,
                 QUIP_TIME, QUIP_GAP, QUIP_RUDE, QUIP_SOON, QUIP_GRAZE,
                 QUIP_STREAK, QUIP_HUG, QUIP_FLAT, QUIP_STILL,
                 WALL_FOLLOW, CORNER_SIZE, CORNER_WARN,
                 MUSIC_VOL, VOL_STEP, VOL_KEY,
                 names: ROUNDS.map(r => r.name),
                 bosses: ROUNDS.filter(r => r.boss).map(r => r.name),
                 rota: BOSS_ROTA,
                 tour: ROUNDS.filter(r => !r.boss).map(r => r.name),
                 tails: ROUNDS.reduce((o, r) => (o[r.name] = r.tail, o), {}) };
"""

PRELUDE = """
<script>
window.__errors = [];
window.onerror = (m, s, l) => window.__errors.push(m + ' @' + l);
window.__q = [];
window.requestAnimationFrame = cb => { window.__q.push(cb); return 1; };
</script>
"""

HARNESS = """
<div id="out" style="display:none"></div>
<script>
const LOG = [];
const say = (...a) => LOG.push(a.join(' '));
const ok = (cond, label, extra) =>
  LOG.push((cond ? 'PASS ' : 'FAIL ') + label + (extra === undefined ? '' : '  [' + extra + ']'));
const K = window.__k;
let T = 0;
const canvas = document.getElementById('game');
const RECT = canvas.getBoundingClientRect();
function put(x, y) {
  canvas.dispatchEvent(new MouseEvent('mousemove', {
    clientX: RECT.left + x * (RECT.width / 900),
    clientY: RECT.top + y * (RECT.height / 600), bubbles: true }));
}
function step(n, mouse) {
  for (let i = 0; i < n; i++) {
    if (mouse) put(mouse[0], mouse[1]);
    T += 1000 / 60;
    for (const cb of window.__q.splice(0)) cb(T);
  }
}
function begin() { step(1); document.getElementById('startBtn').click(); }
// Built at runtime so the tag never appears in the page source the driver dumps.
const TAG = 'L' + '>>';
function finish() {
  ok(window.__errors.length === 0, 'no runtime errors', window.__errors.join(' | ') || 'clean');
  document.getElementById('out').textContent = TAG + LOG.join('\\n' + TAG);
}
// arena centre of a rect
const mid = r => [r[0] + r[2] / 2, r[1] + r[3] / 2];
const overlap = (a, b) => a[0] < b[0] + b[2] && b[0] < a[0] + a[2] &&
                          a[1] < b[1] + b[3] && b[1] < a[1] + a[3];
</script>
"""


def run(name, body, tweaks=()):
    # The harness drives the game with absolute synthetic mouse events, so the
    # captured-pointer path (which reads movementX/Y) is switched off throughout.
    g = base
    for a, b in (LOCK,) + tuple(tweaks):
        assert a in g, a
        g = g.replace(a, b)
    g = g.replace("  // ---------- boot ----------", HOOKS + "\n  // ---------- boot ----------")
    g = g.replace("<script>\n(() => {", PRELUDE + "<script>\n(() => {", 1)
    g = g.replace("</body>", HARNESS + "<script>\ntry {\n" + body +
                  "\n} catch (e) { window.__errors.push('driver: ' + ((e && e.stack) || e)); "
                  "try { finish(); } catch (_) {} }\n</script></body>", 1)
    p = os.path.join(tempfile.gettempdir(), "logic_%s.html" % name)
    open(p, "w", encoding="utf-8").write(g)
    dom = dump(p, timeout=300)
    lines = [m.group(1).strip() for m in re.finditer(r"L&gt;&gt;(.*)|L>>(.*)", dom) if m.group(1)]
    if not lines:
        lines = [l.strip() for m in re.finditer(r"L>>(.*)", dom) for l in [m.group(1)]]
    # the last line carries the closing tag of the div it was read out of
    return [l[:-6].rstrip() if l.endswith("</div>") else l for l in lines]


# ---------------------------------------------------------------- scenarios
TESTS = []

TESTS.append(("lives", """
begin();
// stand still in the middle and let round one's walls come
let hits = 0, last = 3, frames = 0;
while (window.__peek().state === 1 && frames < 60 * 150) {
  step(1, [450, 300]);
  frames++;
  const p = window.__peek();
  if (p.lives < last) { hits += last - p.lives; last = p.lives; }
}
const p = window.__peek();
ok(hits === 3, 'exactly three hits end the game', 'hits=' + hits + ' lives=' + p.lives);
ok(p.state === 2, 'state is OVER once lives run out', 'state=' + p.state);
ok(document.getElementById('lives').textContent.length === 3, 'HUD keeps three heart slots');
ok(/out of lives/.test(document.getElementById('breakdown').textContent),
   'game-over text says out of lives', document.getElementById('breakdown').textContent);
ok(/Game over/.test(document.getElementById('overTitle').textContent),
   'and the title is a loss, not a clear', document.getElementById('overTitle').textContent);
finish();
""", (NOHEART, NOIDLE)))

TESTS.append(("prize-only", """
begin();
step(30, [450, 300]);
window.__clear();
// a decoy: an ordinary cube, hugged at 25 px. It used to pay; now it must not.
window.__spawn({ x: 500, y: 270, w: 60, h: 60, vx: 0, vy: 0 });
const hug = 500 - 25 - 9;
step(60, [hug, 300]);
const d0 = window.__peek();
ok(d0.prox === 0, 'hugging an ordinary cube reads as no proximity at all', 'prox=' + d0.prox);
ok(d0.mult === 1, 'and builds no multiplier', 'mult=' + d0.mult.toFixed(3));
const s0 = d0.score;
step(180, [hug, 300]);
const decoyRate = (window.__peek().score - s0) / 3;

// now the prize cube in the same place, hugged the same way
window.__clear();
window.__pin(500, 270);
const oy = 270 + K.ORB_SIZE / 2;
step(1, [hug, oy]);
const s1 = window.__peek().score;
step(300, [hug, oy]);                          // 5 s of hugging the prize
const on = window.__peek();
const prizeRate = (on.score - s1) / 5;
ok(on.prox > 0.7, 'hugging the prize cube registers as near', 'prox=' + on.prox.toFixed(2));
ok(on.mult > 5, 'the multiplier climbs while you stay on it', 'mult=' + on.mult.toFixed(2));
ok(on.multShown, 'the multiplier label is visible');
ok(prizeRate > decoyRate * 8, 'only the prize cube pays',
   'prize=' + prizeRate.toFixed(0) + '/s decoy=' + decoyRate.toFixed(0) + '/s');

// step off it: grace holds, then it burns down fast
const FAR = [100, 550];
step(Math.round(60 * 0.5), FAR);
const grace = window.__peek();
ok(Math.abs(grace.mult - on.mult) < 0.05, 'a short slip is covered by the grace window',
   'mult=' + grace.mult.toFixed(2) + ' grace=' + K.MULT_GRACE + 's');
// …and now out to 0.2 s past the end of grace, measured off `MULT_GRACE` rather than
// assumed: at a hardcoded 0.2 s this stopped 0.05 s *inside* a grace window that had
// been retuned to 0.75 s, so three of the thirty frames below were still covered and
// the rate came out a clean tenth short of the constant it was being checked against.
step(Math.round(60 * (K.MULT_GRACE - 0.5 + 0.2)), FAR);
const m0 = window.__peek().mult;
step(30, FAR);                                 // 0.5 s of pure decay
const m1 = window.__peek().mult;
const rate = (m0 - m1) / 0.5;
ok(Math.abs(rate - K.MULT_DECAY) < 0.35, 'then it burns down at the full decay rate',
   'measured ' + rate.toFixed(2) + '/s, expected ' + K.MULT_DECAY.toFixed(2) + '/s');
step(180, FAR);
const gone = window.__peek();
ok(gone.mult === 1, 'and bottoms out at x1', 'mult=' + gone.mult.toFixed(3));
ok(!gone.multShown, 'the label disappears with it');
ok(gone.bestMult >= on.mult - 0.01, 'peak multiplier is remembered',
   'best=' + gone.bestMult.toFixed(2));
finish();
""", (LIVES, NOAMB, NOEVENT, NOIDLE)))

TESTS.append(("volume", """
// The music controls work from the menu, so this one never starts a game. The
// browser profile is reused between runs, which is the whole point of the saved
// setting — so the checks are about how the controls move, not about one number.
const vol = document.getElementById('volRange');
const bgm = document.getElementById('bgm');
const stat = document.getElementById('musicStat');
const at = () => Math.round(vol.value) / 100;
const drag = v => { vol.value = String(v); vol.dispatchEvent(new Event('input', { bubbles: true })); };
const key = k => window.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true }));

ok(!!vol && vol.type === 'range', 'the HUD carries a volume slider');
ok(K.MUSIC_VOL <= 0.35, 'and the out-of-the-box level is modest, not full blast',
   'default ' + (K.MUSIC_VOL * 100).toFixed(0) + '%');
ok(Math.abs(bgm.volume - at()) < 0.006, 'the slider and the audio element agree on boot',
   'slider ' + vol.value + ' audio ' + bgm.volume.toFixed(2));

drag(70);
ok(Math.abs(bgm.volume - 0.70) < 0.001, 'dragging it sets the volume straight away',
   'audio ' + bgm.volume.toFixed(2));
ok(parseFloat(localStorage.getItem(K.VOL_KEY)) === 0.7, 'and the setting is remembered',
   localStorage.getItem(K.VOL_KEY));

key('-');
const down = bgm.volume;
ok(Math.abs(down - (0.70 - K.VOL_STEP)) < 0.001, 'the - key turns it down a step',
   'audio ' + down.toFixed(2));
ok(Math.abs(at() - down) < 0.006, 'and the slider follows the keys', 'slider ' + vol.value);
key('+');
ok(Math.abs(bgm.volume - 0.70) < 0.001, 'the + key turns it back up', 'audio ' + bgm.volume.toFixed(2));

for (let i = 0; i < 30; i++) key('-');
ok(bgm.volume === 0, 'it bottoms out at silence rather than going negative',
   'audio ' + bgm.volume.toFixed(2));
for (let i = 0; i < 40; i++) key('+');
ok(bgm.volume === 1, 'and tops out at full', 'audio ' + bgm.volume.toFixed(2));

window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyM', bubbles: true }));
ok(bgm.muted, 'M still mutes');
ok(stat.classList.contains('off'), 'and the whole music panel dims with it');
key('-');
ok(bgm.muted, 'turning it down while muted leaves it muted', 'audio ' + bgm.volume.toFixed(2));
key('+');
ok(!bgm.muted, 'but turning it up means you want to hear it again');
drag(30);
finish();
""", (NOIDLE,)))

TESTS.append(("mult-curve", """
begin();
step(30, [450, 300]);
window.__clear();
window.__pin(500, 270);
const O = window.__peek().orb;
const face = O[0] + O[2], oy = O[1] + O[3] / 2;   // right face, vertical centre
// Measure the growth rate at a standoff, in px from the cube's surface. Far first,
// so the multiplier is nowhere near its cap while the fast rates are read.
function rateAt(gap) {
  const at = [face + K.PLAYER_R + gap, oy];
  step(8, at);                                    // settle: prox is per-frame anyway
  const a = window.__peek();
  step(30, at);                                   // half a second of growth
  const b = window.__peek();
  return [(b.mult - a.mult) / 0.5, a.prox];
}
const GAPS = [K.NEAR_BAND + 20, K.NEAR_BAND - 6, K.NEAR_BAND * 0.75,
              K.NEAR_BAND * 0.5, K.NEAR_BAND * 0.25, 1];
step(120, [100, 550]);                            // bottom the multiplier out first
const got = GAPS.map(rateAt);
say('gain by gap  ' + GAPS.map((g, i) =>
    g.toFixed(0) + 'px:' + got[i][0].toFixed(3) + '/s').join('  '));

const out = got[0][0], edge = got[1][0], mid = got[3][0], close = got[5][0];
ok(got[0][1] === 0 && out <= 0, 'outside the band nothing is earned — it only falls',
   'prox=' + got[0][1] + ' gain=' + out.toFixed(3) + '/s');
ok(Math.abs(close - K.MULT_GAIN) < K.MULT_GAIN * 0.06,
   'point-blank pays the full rate',
   close.toFixed(2) + '/s vs ' + K.MULT_GAIN.toFixed(2) + '/s');
ok(edge > 0 && edge < 0.02, 'just inside the edge it is nearly nothing',
   edge.toFixed(4) + '/s at ' + (K.NEAR_BAND - 6) + 'px');
// ^1.9 over 155px puts halfway out at 27% of the rate. Softer than the ^2.4/120px
// it started at — the far half of the band is meant to be worth standing in — but
// still nowhere near the 50% a linear falloff would pay.
ok(mid < close * 0.35, 'halfway out it is well below half the rate — the curve is not linear',
   (mid / close * 100).toFixed(1) + '% of point-blank');
const rates = got.slice(1).map(r => r[0]);        // the in-band samples
ok(rates.every((r, i) => i === 0 || r >= rates[i - 1] - 1e-9),
   'and it rises the whole way in, with no plateau to sit on', rates.map(r => r.toFixed(3)).join(' '));
// each step inward must be a bigger jump than the last: that is the exponential
const jumps = rates.slice(1).map((r, i) => r - rates[i]);
ok(jumps.every((j, i) => i === 0 || j > jumps[i - 1]),
   'each step closer is worth more than the one before',
   jumps.map(j => j.toFixed(3)).join(' '));
finish();
""", (LIVES, NOAMB, NOEVENT, NOIDLE)))

TESTS.append(("orb-always", """
begin();
let frames = 0, bad = 0, seen = 0, out = 0, moved = 0;
let prev = null, roundsSeen = {};
while (frames < 60 * 90) {
  step(1, [450 + Math.cos(frames * 0.02) * 260, 300 + Math.sin(frames * 0.03) * 180]);
  frames++;
  const p = window.__peek();
  roundsSeen[p.roundNo] = 1;
  if (p.orbs !== 1) bad++;
  if (!p.orb) continue;
  seen++;
  const [x, y, w, h] = p.orb;
  if (x < -0.5 || y < -0.5 || x + w > 900.5 || y + h > 600.5) out++;
  if (prev) moved += Math.hypot(x - prev[0], y - prev[1]);
  prev = [x, y];
  if (p.state !== 1) break;
}
ok(bad === 0, 'exactly one prize cube exists on every frame of play', bad + ' bad frames');
ok(seen === frames, 'it is never absent', seen + '/' + frames + ' frames');
ok(out === 0, 'it always stays inside the arena', out + ' frames outside');
ok(moved / (frames / 60) > 60, 'and it is always on the move',
   (moved / (frames / 60)).toFixed(0) + ' px/s average');
ok(Object.keys(roundsSeen).length >= 3, 'across several rounds',
   'rounds ' + Object.keys(roundsSeen).join(','));
finish();
""", (LIVES, NOIDLE)))

# The prize cube has a mood, and two parts of it are behaviour rather than paint: it
# gets out of the way of a near miss, and its bracket spins faster in good company.
TESTS.append(("orb-mood", """
begin();
step(60, [40, 560]);                           // out of the way, in a far corner
const hugRate = (() => {
  const p0 = window.__peek();
  const [x, y] = mid(p0.orb);
  step(1, [x + p0.orb[2] / 2 + 12, y]);
  const a = window.__peek();
  // hug it for a second, following it as it drifts
  for (let i = 0; i < 60; i++) {
    const p = window.__peek();
    const [hx, hy] = mid(p.orb);
    step(1, [hx + p.orb[2] / 2 + 12, hy]);
  }
  const b = window.__peek();
  return [b.prox, (b.orbSpin - a.orbSpin) / 1];
})();
ok(hugRate[0] > 0.7, 'the cube is being hugged', 'prox=' + hugRate[0].toFixed(2));
ok(hugRate[1] > 2.4, 'and it spins its bracket faster for the company',
   hugRate[1].toFixed(2) + ' rad/s against 1.6 idle');

// Now a cube parked off its shoulder, inside the clearance it minds, and nowhere near
// the marker. It should notice, and it should not still be sitting there.
step(60, [40, 560]);
const before = window.__peek();
const S = 30;
const hx = before.orb[0] + before.orb[2] + K.ORB_SCARE * 0.4;
const hy = before.orb[1] + before.orb[3] / 2 - S / 2;
window.__spawn({ x: hx, y: hy, w: S, h: S, vx: 0, vy: 0 });
const gapTo = o => {
  const gx = Math.max(hx - (o[0] + o[2]), o[0] - (hx + S), 0);
  const gy = Math.max(hy - (o[1] + o[3]), o[1] - (hy + S), 0);
  return Math.hypot(gx, gy);
};
const gap0 = gapTo(before.orb);
step(1, [40, 560]);
const hit = window.__peek();
ok(gap0 < K.ORB_SCARE, 'a cube is parked inside the clearance the prize cube minds',
   gap0.toFixed(0) + 'px of ' + K.ORB_SCARE);
ok(hit.orbFright > 0.9, 'which startles it', 'fright=' + hit.orbFright.toFixed(2));
let worstGap = gap0;
for (let i = 0; i < 24; i++) { step(1, [40, 560]); worstGap = Math.max(worstGap, gapTo(window.__peek().orb)); }
ok(worstGap > gap0 + 12, 'and it flinches away rather than drifting through it',
   gap0.toFixed(0) + 'px to ' + worstGap.toFixed(0) + 'px in 0.4s');
step(90, [40, 560]);
ok(window.__peek().orbFright < 0.05, 'a fright wears off on its own',
   'fright=' + window.__peek().orbFright.toFixed(3));
finish();
""", (LIVES, NOAMB, NOEVENT, NOIDLE, rounds(30, 30))))

# The other half of the mood: a cube nobody is coming for sulks, and edges your way.
TESTS.append(("orb-sulk", """
begin();
// Parked in a corner from the first frame of every round, so the cube is ignored for
// all of each of them. Two windows either side of the point where it starts
// complaining: the marker never moves, so whatever differs between them is the cube's
// own doing. Several rounds of each, because one window of one drifting cube is one
// random walk and a random walk proves nothing.
const CORNER = [40, 560];
const win = (t, a, b) => t > a && t < b;
const EARLY = [1, 8], LATE = [10, 18];
const seen = { early: [], late: [] }, spin = { early: [], late: [] };
let sawLonely = 0, rounds = {}, prevSpin = null;
for (let f = 0; f < 60 * 105; f++) {
  step(1, CORNER);
  const p = window.__peek();
  if (p.state !== 1) break;
  if (!p.orb || !p.live) { prevSpin = null; continue; }
  rounds[p.roundNo] = 1;
  const [ox, oy] = mid(p.orb);
  const d = Math.hypot(ox - p.px, oy - p.py);
  const t = p.roundT;
  const w = win(t, EARLY[0], EARLY[1]) ? 'early' : win(t, LATE[0], LATE[1]) ? 'late' : null;
  // The bracket's spin is cumulative, so what is sampled is the rate it turns at.
  if (w && prevSpin !== null) { seen[w].push(d); spin[w].push((p.orbSpin - prevSpin) * 60); }
  prevSpin = p.orbSpin;
  if (win(t, 9.5, 9.6)) sawLonely = p.lonely;
}
const mean = a => a.reduce((s, v) => s + v, 0) / Math.max(1, a.length);
const dEarly = mean(seen.early), dLate = mean(seen.late);
const sEarly = mean(spin.early), sLate = mean(spin.late);
say('sulk  ' + Object.keys(rounds).length + ' rounds, lonely=' + sawLonely.toFixed(2) +
    ', distance ' + dEarly.toFixed(0) + 'px -> ' + dLate.toFixed(0) + 'px, spin ' +
    sEarly.toFixed(2) + ' -> ' + sLate.toFixed(2) + ' rad/s');
ok(Object.keys(rounds).length >= 3, 'three rounds of being ignored',
   Object.keys(rounds).join(','));
ok(sawLonely > K.LONELY_AT, 'left alone for long enough, the cube starts saying so',
   'lonely=' + sawLonely.toFixed(2));
ok(sLate < sEarly - 0.3 && sLate < 1.6,
   'and it sulks — the bracket slows to a crawl', sLate.toFixed(2) + ' rad/s');
ok(dLate < dEarly * 0.9, 'and it leans your way, asking before the arena does',
   dLate.toFixed(0) + 'px against ' + dEarly.toFixed(0) + 'px while it was still patient');
finish();
""", (LIVES, NOAMB, NOEVENT, rounds(30, 30),
      ("const IDLE_MIN  = 8;", "const IDLE_MIN  = 20;"))))

TESTS.append(("rounds", """
begin();
const waves = [], starts = [];
const by = {};
let prevNo = 0, prevLive = false, frames = 0;
while (frames < 60 * 215) {
  step(1, [450 + Math.cos(frames * 0.02) * 250, 300 + Math.sin(frames * 0.031) * 170]);
  frames++;
  const p = window.__peek();
  if (p.roundNo !== prevNo) { starts.push([p.roundNo, p.elapsed]); prevNo = p.roundNo; }
  if (p.ev) by[p.roundNo] = p.ev;
  if (prevLive && !p.live) waves.push([p.roundNo, p.roundT, p.roundLen, by[p.roundNo]]);
  prevLive = p.live;
  if (p.state !== 1) break;
}
say('rounds       ' + starts.map(s => s[0] + '@' + s[1].toFixed(0) + 's ' + by[s[0]]).join('  '));
say('wave phases  ' + waves.map(w => w[1].toFixed(1) + '/' + w[2].toFixed(1) + 's').join('  '));
ok(waves.length >= 5, 'at least five rounds finish inside 215 s', waves.length + ' finished');
// The first pass through the roster is fixed — the non-boss events in order, into
// the slots the boss has not reserved — so whatever we got to see of it has to
// match, in order.
const seen = starts.map(s => s[0]).filter(n => by[n] && n % K.BOSS_EVERY);
const tour = seen.map(n => by[n]).slice(0, K.tour.length);
ok(tour.join(',') === K.tour.slice(0, tour.length).join(','),
   'the opening rounds are a tour of the roster, in order', tour.join(','));
ok(by[K.BOSS_EVERY] === K.rota[0], 'and every fifth one is a boss',
   'round ' + K.BOSS_EVERY + ' = ' + by[K.BOSS_EVERY]);
ok(waves.every(w => w[2] >= K.ROUND_MIN - 0.01 && w[2] <= K.ROUND_MAX + 0.01),
   'every round targets ' + K.ROUND_MIN + '-' + K.ROUND_MAX + ' s of waves',
   waves.map(w => w[2].toFixed(1)).join(' '));
// A round ends on a wave boundary, so it lands near its target rather than on it:
// never under the floor, and only over by the tail of the wave already in the air.
ok(waves.every(w => w[1] >= K.ROUND_MIN - 0.05), 'none is shorter than the ' + K.ROUND_MIN + ' s floor',
   'shortest ' + Math.min.apply(null, waves.map(w => w[1])).toFixed(1) + 's');
// How far a round may miss its target is set by how long one of its waves runs —
// a clock or a half wave is ten seconds, a wall four. Over the target it can only
// be the wave already in the air; under it, only the room that wave asked for.
const over  = w => K.tails[w[3]] + 0.5;
const under = w => K.tails[w[3]] * 0.55 + 1;
ok(waves.every(w => w[1] - w[2] < over(w)), 'nor over by more than the wave already in the air',
   waves.map(w => (w[1] - w[2]).toFixed(1) + '/+' + over(w).toFixed(1)).join(' '));
ok(waves.every(w => w[2] - w[1] < under(w)), 'and never cut shorter than the room that wave asked for',
   waves.map(w => (w[1] - w[2]).toFixed(1) + '/-' + under(w).toFixed(1)).join(' '));
const sched = window.__peek().sched.split(',');
const brkFor = n => K.bosses.indexOf(sched[n - 1]) !== -1 ? K.BOSS_BREAK : K.ROUND_BREAK;
const periods = starts.slice(1).map((s, i) => s[1] - starts[i][1]);
ok(periods.every((g, i) => Math.abs(g - waves[i][1] - brkFor(starts[i + 1][0])) < 0.1),
   'each round is its waves plus a breather, ' + K.BOSS_BREAK + ' s of it before a boss',
   periods.map((g, i) => g.toFixed(1) + '/' + brkFor(starts[i + 1][0])).join(' '));
ok(window.__peek().cubes < 120, 'no cube build-up after 215 s', window.__peek().cubes);
finish();
""", (LIVES, NOIDLE)))

TESTS.append(("twenty-rounds", """
begin();
let frames = 0, top = 0;
while (frames < 60 * 200) {
  step(1, [450, 300]);
  frames++;
  const p = window.__peek();
  top = Math.max(top, p.roundNo);
  if (p.state !== 1) break;
}
const p = window.__peek();
ok(p.state === 2, 'the game ends on its own', 'state=' + p.state + ' after ' + (frames / 60).toFixed(1) + 's');
ok(top === K.MAX_ROUNDS, 'after exactly ' + K.MAX_ROUNDS + ' rounds', 'highest round ' + top);
ok(p.cleared, 'and it counts as cleared, not survived');
ok(/cleared it/.test(document.getElementById('overTitle').textContent),
   'the title says so', document.getElementById('overTitle').textContent);
const bd = document.getElementById('breakdown').textContent;
ok(bd.indexOf(K.MAX_ROUNDS + '/' + K.MAX_ROUNDS + ' rounds') === 0 &&
   /all rounds cleared/.test(bd), 'and the breakdown reads 20/20', bd);
ok(p.lives > 0, 'with hearts to spare in this rigged run', 'lives=' + p.lives);
finish();
""", (LIVES, rounds(1.2, 1.6), NOEVENT, NOIDLE) + breather(0.15)))

TESTS.append(("schedule", """
begin();
const p = window.__peek();
const s = p.sched.split(',');
say('schedule     ' + s.map((n, i) => (i + 1) + ':' + n).join(' '));
ok(s.length === K.MAX_ROUNDS, 'a schedule is drawn for the whole game', s.length + ' rounds');
// The tour fills the slots the boss has not reserved, so it is the non-boss rounds
// in order that have to read as the roster.
const tour = s.filter(n => K.bosses.indexOf(n) === -1).slice(0, K.tour.length);
ok(tour.join(',') === K.tour.join(','),
   'the first ' + K.tour.length + ' non-boss rounds are the roster in order', tour.join(','));
const boss = s.map((n, i) => K.bosses.indexOf(n) !== -1 ? i + 1 : 0).filter(n => n);
const want = [];
for (let n = K.BOSS_EVERY; n <= K.MAX_ROUNDS; n += K.BOSS_EVERY) want.push(n);
ok(boss.join(',') === want.join(','), 'a boss owns every fifth round and no other',
   'boss on ' + boss.join(','));
const rota = want.map((n, i) => K.rota[i % K.rota.length]);
ok(want.map(n => s[n - 1]).join(',') === rota.join(','),
   'and the two of them alternate: ' + rota.join(', '),
   want.map(n => n + ':' + s[n - 1]).join(' '));
ok(s.every((n, i) => i === 0 || n !== s[i - 1]), 'and no event runs twice in a row',
   s.join(','));
// The tail of the schedule is a draw, so two games should not line up. (Two
// identical draws of twelve slots is a 1-in-7^12 accident, not a flaky test.)
const first = p.sched;
document.getElementById('startBtn').click();      // deal again
step(1);
const again = window.__peek().sched.split(',');
say('and again    ' + again.map((n, i) => (i + 1) + ':' + n).join(' '));
ok(again.filter(n => K.bosses.indexOf(n) === -1).slice(0, K.tour.length).join(',') ===
   K.tour.join(','), 'a second game opens on the same fixed tour');
ok(again.join(',') !== first, 'but deals the back half of the game differently');
finish();
""", (LIVES, NOIDLE)))

TESTS.append(("bail-out", """
begin();
// play into the breather after round one, then hand the mouse back
let frames = 0;
while (frames < 60 * 120) {
  step(1, [450 + Math.cos(frames * 0.02) * 250, 300]);
  frames++;
  const p = window.__peek();
  if (p.roundNo === 1 && !p.live) break;
}
const brk = window.__peek();
ok(brk.roundNo === 1 && !brk.live, 'round one finished and the breather started',
   'round=' + brk.roundNo + ' live=' + brk.live);
ok(document.getElementById('round').textContent === '1/' + K.MAX_ROUNDS,
   'the HUD counts the round that just finished', document.getElementById('round').textContent);
window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Escape', bubbles: true }));
step(1);
const bd = document.getElementById('breakdown').textContent;
ok(window.__peek().state === 2, 'Esc ends the game');
ok(bd.indexOf('1/' + K.MAX_ROUNDS + ' rounds') === 0,
   'and the round you got through is counted, not the one you were about to start', bd);
ok(/bailed out/.test(bd), 'with hearts to spare it reads as bailing out', bd);
finish();
""", (LIVES, NOIDLE)))

TESTS.append(("wall-series", """
begin();
// WALL_CELL-2 on one axis identifies a wall piece
const isWall = r => Math.abs(r[2] - (K.WALL_CELL - 2)) < 0.01 || Math.abs(r[3] - (K.WALL_CELL - 2)) < 0.01;
let frames = 0, dirs = [], prevDir = null, peak = 0, most = 0, pairs = 0, headOn = 0;
let seen = false, warned = 0, len = 0, run = 0, runs = [];
while (frames < 60 * 60) {
  step(1, [450, 300]);
  frames++;
  const p = window.__peek();
  if (p.roundNo > 1) break;
  len = p.roundT;
  if (p.evDir && p.evDir !== prevDir) { dirs.push(p.evDir); prevDir = p.evDir; }
  const w = p.rects.filter(isWall);
  peak = Math.max(peak, w.length);
  // Every slab in the air, by the way it travels. Two headings is the point of the
  // train; two headings on one axis would be a head-on pair whose gaps need not line
  // up, which is a wall with no gap in it.
  const head = new Set(w.map(r => Math.abs(r[4]) > 0.01 ? 'x' + Math.sign(r[4]) : 'y' + Math.sign(r[5])));
  const axes = new Set([...head].map(h => h[0]));
  most = Math.max(most, head.size);
  if (head.size > 1) pairs++;
  if (axes.size < head.size) headOn++;
  if (w.length) { seen = true; if (p.evNext) warned++; if (run) { runs.push(run); run = 0; } }
  else if (seen) run++;              // arena empty between slabs: dead air
}
// `run` is left open at the end: that is the calm after the train stops, not a gap
// inside it. Only the runs that closed — because another wall turned up — count.
const dead = runs.reduce((a, b) => a + b, 0) / 60;
const each = (len - K.WALL_WARN) / dirs.length;
say('directions   ' + dirs.join(' ') + '   (' + dirs.length + ' walls in ' + len.toFixed(1) +
    's, one every ' + each.toFixed(2) + 's, ' + (pairs / 60).toFixed(1) + 's of two at once)');
ok(dirs.length >= 8, 'a round of walls is a train of them', dirs.length + ' walls');
// A wall used to cost its whole crossing — 4.5s down the long axis — because the
// board had to be clear before the next one was loosed. Letting a perpendicular
// slab in early is what buys the extra walls.
ok(each < 3.2, 'and they cost rather less than a crossing each now', each.toFixed(2) + 's per wall');
ok(dirs.every((d, i) => i === 0 || d !== dirs[i - 1]), 'never twice from the same side in a row',
   dirs.join(''));
ok(most <= 2, 'never more than two slabs in the air', most + ' headings at once');
ok(pairs > 60, 'and two at once is the ordinary case, not a fluke',
   (pairs / 60).toFixed(1) + 's of overlap');
ok(headOn === 0, 'but never two on the same axis, which would leave no gap at all',
   headOn + ' frames head-on');
// The whole point of the train: the next wall's warning runs while this one is
// still crossing, so the arena is never sitting empty waiting to be told.
ok(warned > 60, 'the next wall is telegraphed over the one still crossing',
   (warned / 60).toFixed(1) + 's of overlapped warning');
ok(dead < 0.2, 'so the walls come back to back with no dead air between them',
   dead.toFixed(2) + 's empty across ' + dirs.length + ' walls: ' + runs.join('+') + ' frames');
ok(peak > 8, 'and each one is a proper slab of cubes', peak + ' pieces at its widest');
finish();
""", (LIVES, only('wall'), NOAMB, NOIDLE)))

# Two perpendicular slabs always leave somewhere legal: one gap is a band across the
# arena, the other a band down it, and bands that cross meet in a rectangle. Worth
# measuring rather than arguing, because it is the licence for the overlap.
TESTS.append(("corner-sides", """
begin();
// A round of corner charges alternates where it comes from: the four corners, then
// the four edge middles, then the corners again. Each set's safe ground is the other
// set's line of fire, so the round keeps turning you 45 degrees round the arena.
const out  = (v, hi) => v < -1 || v > hi + 1;
const half = (v, hi) => Math.abs(v - hi / 2) < 1.5;
let frames = 0, waves = [], prevT = 1e9, cur = null;
while (frames < 60 * 60) {
  step(1, [450, 300]);
  frames++;
  const p = window.__peek();
  if (p.state !== 1 || p.roundNo > 1) break;
  const fresh = p.ev && p.evT < prevT;
  prevT = p.ev ? p.evT : 1e9;
  if (fresh) { cur = { card: p.evCard, spots: null }; waves.push(cur); }
  if (cur && !cur.spots && p.rects.length >= 4)
    cur.spots = p.rects.map(r => [r[0] + r[2] / 2, r[1] + r[3] / 2]);
}
const full = waves.filter(w => w.spots);
const kind = w => {
  const corner = w.spots.filter(([x, y]) => out(x, 900) && out(y, 600)).length;
  const card = w.spots.filter(([x, y]) => (half(x, 900) && out(y, 600)) ||
                                          (half(y, 600) && out(x, 900))).length;
  return corner === 4 ? 'corner' : card === 4 ? 'card' : 'mixed';
};
const kinds = full.map(kind);
say('corners  ' + full.length + ' waves: ' + kinds.join(' ') +
    '   (flags ' + full.map(w => w.card ? 'E' : 'C').join('') + ')');
ok(full.length >= 3, 'a round of them is several waves', full.length + ' waves');
ok(kinds[0] === 'corner', 'it starts at the corners, as the name promises', kinds[0]);
ok(kinds.every((k, i) => k === (i % 2 ? 'card' : 'corner')),
   'and then alternates corners, edges, corners', kinds.join(' '));
ok(kinds.indexOf('mixed') === -1, 'every charge of a wave comes from the same set',
   kinds.join(' '));
ok(full.every(w => w.card === (kind(w) === 'card')),
   'and the event says which it is, so its telegraph can too',
   full.map(w => (w.card ? 'E' : 'C') + kind(w)[0]).join(' '));
// Perpendicular to each other by construction: four charges meeting in the middle
// from the corners leave the cardinals open, and the other way about.
const set = w => w.spots.map(([x, y]) => (out(x, 900) ? (x < 0 ? 'l' : 'r') : '') +
                                         (out(y, 600) ? (y < 0 ? 'u' : 'd') : '')).sort().join(',');
ok(full.every(w => new Set(set(w).split(',')).size === 4),
   'four of them, one from each side of the arena', set(full[0]));
finish();
""", (LIVES, only('corners'), NOAMB, NOIDLE)))

TESTS.append(("wall-cross", """
begin();
const isWall = r => Math.abs(r[2] - (K.WALL_CELL - 2)) < 0.01 || Math.abs(r[3] - (K.WALL_CELL - 2)) < 0.01;
// The widest clear run along one axis, given the spans the slabs block on it.
function widest(segs, span) {
  segs.sort((a, b) => a[0] - b[0]);
  let cursor = 0, best = [0, 0];
  for (const [a, b] of segs) { if (a - cursor > best[1] - best[0]) best = [cursor, a]; cursor = Math.max(cursor, b); }
  if (span - cursor > best[1] - best[0]) best = [cursor, span];
  return best;
}
let frames = 0, both = 0, worst = 1e9, stuck = 0;
while (frames < 60 * 60) {
  step(1, [450, 300]);
  frames++;
  const p = window.__peek();
  if (p.roundNo > 1) break;
  const w = p.rects.filter(isWall);
  const xs = w.filter(r => Math.abs(r[4]) > 0.01), ys = w.filter(r => Math.abs(r[5]) > 0.01);
  if (!xs.length || !ys.length) continue;        // only the overlapping frames
  both++;
  // an x-moving slab blocks a span of y, and the other way about
  const gy = widest(xs.map(r => [r[1], r[1] + r[3]]), 600);
  const gx = widest(ys.map(r => [r[0], r[0] + r[2]]), 900);
  const box = Math.min(gx[1] - gx[0], gy[1] - gy[0]);
  worst = Math.min(worst, box);
  if (box < 2 * K.PLAYER_R) stuck++;
}
say('crossing  ' + (both / 60).toFixed(1) + 's with both axes in the air, tightest box ' +
    (worst === 1e9 ? 'n/a' : worst.toFixed(0) + 'px'));
ok(both > 60, 'two perpendicular walls do share the board', (both / 60).toFixed(1) + 's');
ok(stuck === 0, 'and where the two gaps cross is always somewhere a player fits',
   stuck + ' frames with nowhere to stand');
ok(worst > 70, 'with room to spare, not a pixel of it',
   (worst === 1e9 ? 'n/a' : worst.toFixed(0) + 'px at the tightest'));
finish();
""", (LIVES, only('wall'), NOAMB, NOIDLE)))

TESTS.append(("wall-gap", """
begin();
let frames = 0, rs = null;
while (frames < 60 * 6) {
  step(1, [450, 300]);
  frames++;
  const p = window.__peek();
  if (p.rects.length > 8) { rs = p.rects; break; }
}
// The wall spans one axis. Measure the widest clear run on both axes; the one
// the wall actually blocks is whichever comes out smaller.
function widestGap(segs, span) {
  segs.sort((a, b) => a[0] - b[0]);
  let cursor = 0, best = 0;
  for (const [a, b] of segs) { if (a - cursor > best) best = a - cursor; cursor = Math.max(cursor, b); }
  return Math.max(best, span - cursor);
}
ok(rs, 'the wall arrived', rs && rs.length + ' pieces');
const best = Math.min(
  widestGap(rs.map(r => [r[0], r[0] + r[2]]), 900),
  widestGap(rs.map(r => [r[1], r[1] + r[3]]), 600));
ok(rs.length > 8, 'the wall is a slab of many cubes', rs.length + ' pieces');
ok(best > 70 && best < 140, 'exactly one player-sized gap', 'widest clear run ' + best.toFixed(0) + 'px');
finish();
""", (LIVES, only('wall'), NOAMB, NOIDLE)))

TESTS.append(("corners-traffic", """
begin();
// The cycle-1 size, read out of the game rather than written down here: at 70 px this
// matched nothing once CORNER_SIZE was retuned to 68, and a filter that matches nothing
// reports no corner charges at all rather than the wrong ones.
const CS = K.CORNER_SIZE;
const isCorner = r => Math.abs(r[2] - CS) < 0.01 && Math.abs(r[3] - CS) < 0.01;
let frames = 0, waves = 0, prevEvT = 1e9, ambPeak = 0, ambDuring = 0, samples = 0, reach = 1e9;
while (frames < 60 * 40) {
  step(1, [450, 300]);
  frames++;
  const p = window.__peek();
  if (p.ev && p.evT < prevEvT) waves++;              // the event object was replaced
  prevEvT = p.ev ? p.evT : 1e9;
  const corner = p.rects.filter(isCorner);
  const amb = p.rects.length - corner.length;
  ambPeak = Math.max(ambPeak, amb);
  if (corner.length === 4) {
    samples++;
    if (amb > 0) ambDuring++;
    for (const r of corner) reach = Math.min(reach, Math.hypot(mid(r)[0] - 450, mid(r)[1] - 300));
  }
  if (p.roundNo > 1) break;
}
say('round lasted ' + (frames / 60).toFixed(1) + 's, ' + waves + ' waves');
ok(waves >= 3, 'the corners come round after round', waves + ' waves');
ok(ambPeak >= 3, 'ordinary cubes spawn during this round too', 'peak ' + ambPeak + ' at once');
ok(ambDuring > samples * 0.8, 'and they keep coming while the corners close in',
   ambDuring + '/' + samples + ' frames with traffic');
ok(reach < 90, 'the corners still reach the middle', reach.toFixed(0) + 'px from centre');
finish();
""", (LIVES, only('corners'), NOIDLE)))

BARRAGE = """
begin();
// several rockets, each riding one of the lines that was telegraphed
const isRocket = r => Math.abs(r[2] - 42) < 0.01 && Math.abs(r[3] - 42) < 0.01;
let frames = 0, lines = null, fired = 0, live = 0, off = 0, samples = 0;
let waves = 0, prevEvT = 1e9;
while (frames < 60 * 40) {
  step(1, [450, 300]);
  frames++;
  const p = window.__peek();
  if (p.ev && p.evT < prevEvT) waves++;
  prevEvT = p.ev ? p.evT : 1e9;
  if (p.evLines) { lines = p.evLines; fired = Math.max(fired, p.evFired); }
  const sp = p.rects.filter(isRocket);
  live = Math.max(live, sp.length);
  if (lines) for (const r of sp) {
    const [cx, cy] = mid(r);
    let best = 1e9;
    for (const [px, py, dx, dy] of lines) {
      best = Math.min(best, Math.abs((cx - px) * dy - (cy - py) * dx));
    }
    off = Math.max(off, best);
    samples++;
  }
  if (p.roundNo > 1) break;
}
ok(fired >= 4, 'the barrage fires a series of rockets', 'fired=' + fired);
ok(waves >= 2, 'and another barrage queues up behind it', waves + ' barrages in one round');
ok(live >= 2, 'rockets overlap in flight', 'peak ' + live + ' at once');
ok(samples > 30, 'they cross the arena', samples + ' samples');
ok(off < 0.5, 'every rocket rides one of the warned lines', 'max offset ' + off.toFixed(3) + 'px');

// the lines must form a pattern: parallel lanes, or a star through the middle
const d0 = [lines[0][2], lines[0][3]];
const par = lines.every(l => Math.abs(Math.abs(l[2] * d0[0] + l[3] * d0[1]) - 1) < 1e-9);
const star = lines.every(l => Math.abs((450 - l[0]) * l[3] - (300 - l[1]) * l[2]) < 1e-6);
ok(par || star, 'the lines are a pattern, not random',
   par ? 'parallel lanes' : star ? 'star through the middle' : 'neither');
if (par) {
  const offs = lines.map(l => l[0] * -d0[1] + l[1] * d0[0]).sort((a, b) => a - b);
  const gaps = offs.slice(1).map((o, i) => o - offs[i]);
  ok(Math.max.apply(null, gaps) - Math.min.apply(null, gaps) < 0.01,
     'the lanes are evenly spaced', gaps.map(g => g.toFixed(0)).join(' '));
}
finish();
"""

# Same checks against each fixed pattern, so every shape is covered every run.
for _i, _pat in enumerate(("sweep", "ladder", "star")):
    TESTS.append(("rocket-" + _pat, BARRAGE,
                  (LIVES, only('rocket'), NOAMB, NOIDLE,
                   ("pick(ROCKET_PATTERNS)", "ROCKET_PATTERNS[%d]" % _i))))

TESTS.append(("spiral-eye", """
begin();
// Each vortex opens somewhere new and then stays put, so what is measured is both
// halves of that: no drift inside a wave, and no two waves in the same place.
let frames = 0, waves = [], cur = null, near = 0, samples = 0, prevEvT = 1e9;
let arms = [], plan = [], tiers = {};
while (frames < 60 * 90) {
  step(1, [450, 300]);
  frames++;
  const p = window.__peek();
  if (p.state !== 1) break;
  const fresh = p.ev && p.evT < prevEvT;
  prevEvT = p.ev ? p.evT : 1e9;
  if (fresh) { cur = []; waves.push(cur); arms.push(0); plan.push(p.evTotal); }
  if (arms.length) {
    arms[arms.length - 1] = Math.max(arms[arms.length - 1], p.evN);
    tiers[p.tier] = 1;
  }
  if (p.focus && cur) {
    cur.push([p.focus[0], p.focus[1]]);
    if (p.orb && p.evT > 1.2) {
      samples++;
      const [ox, oy] = mid(p.orb);
      if (Math.hypot(ox - p.focus[0], oy - p.focus[1]) < K.SPIRAL_EYE_R * 1.6) near++;
    }
  }
  if (p.roundNo > 2) break;
}
const eyes = waves.filter(w => w.length).map(w => w[0]);
// how far an eye moves while its own vortex is on the stage: it should not, at all
let drift = 0;
for (const w of waves)
  for (const q of w) drift = Math.max(drift, Math.hypot(q[0] - w[0][0], q[1] - w[0][1]));
const xs = eyes.map(e => e[0]), ys = eyes.map(e => e[1]);
const spanX = Math.max.apply(null, xs) - Math.min.apply(null, xs);
const spanY = Math.max.apply(null, ys) - Math.min.apply(null, ys);
let hop = 1e9;
for (let i = 1; i < eyes.length; i++)
  hop = Math.min(hop, Math.hypot(eyes[i][0] - eyes[i - 1][0], eyes[i][1] - eyes[i - 1][1]));
const inset = eyes.filter(e => e[0] < K.SPIRAL_INSET - 0.01 || e[0] > 900 - K.SPIRAL_INSET + 0.01 ||
                               e[1] < K.SPIRAL_INSET - 0.01 || e[1] > 600 - K.SPIRAL_INSET + 0.01);
say('spiral  ' + eyes.length + ' eyes over ' + spanX.toFixed(0) + 'x' + spanY.toFixed(0) +
    'px, nearest pair ' + hop.toFixed(0) + 'px, ' + drift.toFixed(2) + 'px of drift');
ok(eyes.length >= 4, 'the spiral comes round again, and again', eyes.length + ' vortices');
ok(drift === 0, 'an eye holds still for as long as its own vortex lasts',
   drift.toFixed(2) + 'px of drift');
// The inset leaves 270px of height to play with against 570px of width, so the
// vertical spread a handful of eyes covers is bound to be the smaller number.
ok(spanX > 250 && spanY > 110, 'but over a round they open all over the map',
   spanX.toFixed(0) + 'x' + spanY.toFixed(0) + 'px');
ok(hop > 80, 'never twice in nearly the same place', 'nearest pair ' + hop.toFixed(0) + 'px');
ok(inset.length === 0, 'and never so near an edge that the calm middle is off the stage',
   inset.length + ' too close');
ok(near > samples * 0.7, 'the prize cube settles into the eye, so it can still be hugged',
   near + '/' + samples + ' frames within reach');
// Arms per vortex, spawned against planned: a vortex is a shape with gaps in it, and
// the gaps are the difference between a spiral and a wall.
const done = arms.slice(0, -1), want = plan.slice(0, -1);   // the last one is mid-flight
const cycles = Object.keys(tiers).map(Number);
say('spiral  arms ' + done.join(',') + ' of ' + want.join(','));
// The last arm and the end of the wave land on the same frame — the spawn happens and
// then update() reports itself finished — so the highest count a peek can ever see is
// one short of the roster. Anything less than that is arms going missing.
ok(done.length > 0 && done.every((a, i) => a === want[i] - 1),
   'every vortex lays out the arms it planned and no more',
   done.map(a => a + 1).join(',') + ' of ' + want.join(','));
ok(cycles.length === 1 && want.every(w => w === K.SPIRAL_ARMS + K.SPIRAL_ARMS_T * cycles[0]),
   'which at this cycle is ' + (K.SPIRAL_ARMS + K.SPIRAL_ARMS_T * cycles[0]) + ' of them',
   'cycles seen ' + cycles.join(','));
finish();
""", (LIVES, only('spiral'), NOAMB, NOIDLE)))

TESTS.append(("box-roam", """
begin();
let frames = 0, pocket = null, spots = [], kept = 0, roamF = 0, buried = 0;
while (frames < 60 * 40) {
  step(1, [450, 300]);
  frames++;
  const p = window.__peek();
  const slabs = p.rects.filter(r => r[2] >= 450 || r[3] >= 300);
  if (p.evPh === 'roam') {
    roamF++;
    if (p.focus && p.focus[3]) kept++;
    if (p.focus) spots.push([p.focus[0], p.focus[1]]);
    if (p.orb && slabs.some(s => overlap(p.orb, s))) buried++;
  }
  if (slabs.length === 4 && p.focus) {
    // Free space around the pocket centre. Every slab reaches 900px outwards, so
    // which pair it belongs to is decided by whether it spans the pocket's row.
    const [fx, fy] = p.focus;
    let x0 = 0, x1 = 900, y0 = 0, y1 = 600;
    for (const [x, y, w, h] of slabs) {
      if (fy >= y && fy <= y + h) { if (x + w <= fx) x0 = Math.max(x0, x + w); else x1 = Math.min(x1, x); }
      else                        { if (y + h <= fy) y0 = Math.max(y0, y + h); else y1 = Math.min(y1, y); }
    }
    const box = [x1 - x0, y1 - y0];
    if (!pocket || box[0] * box[1] < pocket[0] * pocket[1]) pocket = box;
  }
  if (spots.length && p.evPh === null && p.ev === null) break;
}
let travel = 0;
for (let i = 1; i < spots.length; i++) travel += Math.hypot(spots[i][0] - spots[i - 1][0], spots[i][1] - spots[i - 1][1]);
const xs = spots.map(s => s[0]), ys = spots.map(s => s[1]);
const spanX = Math.max.apply(null, xs) - Math.min.apply(null, xs);
const spanY = Math.max.apply(null, ys) - Math.min.apply(null, ys);
// How much the pocket's velocity can change in a single frame. This is the whole
// measure of smooth: the pocket is steered, so its velocity only ever bends by an
// acceleration; the lunge-and-stop version it replaced could reverse outright, or
// stop dead from full speed, between one frame and the next.
let jolt = 0;
for (let i = 2; i < spots.length; i++) {
  const ax = (spots[i - 1][0] - spots[i - 2][0]) * 60, ay = (spots[i - 1][1] - spots[i - 2][1]) * 60;
  const bx = (spots[i][0] - spots[i - 1][0]) * 60, by = (spots[i][1] - spots[i - 1][1]) * 60;
  jolt = Math.max(jolt, Math.hypot(bx - ax, by - ay));
}
say('pocket roam  ' + travel.toFixed(0) + 'px over ' + (roamF / 60).toFixed(1) + 's, ' +
    'area ' + spanX.toFixed(0) + 'x' + spanY.toFixed(0) + 'px, worst jolt ' + jolt.toFixed(0) + 'px/s');
ok(pocket && Math.abs(pocket[0] - K.BOX_OPEN) < 1 && Math.abs(pocket[1] - K.BOX_OPEN) < 1,
   'the walls squeeze down to a ' + K.BOX_OPEN + 'px pocket', pocket && pocket.map(Math.round).join('x'));
ok(roamF > 60, 'and then it goes hunting', (roamF / 60).toFixed(1) + 's of roaming');
ok(travel > 300 && Math.hypot(spanX, spanY) > 150, 'covering ground around the map, not drifting in place',
   travel.toFixed(0) + 'px travelled across ' + Math.hypot(spanX, spanY).toFixed(0) + 'px of ground');
// Stopping dead from the roam speed is a 150px/s jolt and a reversal is twice that,
// so a limit of 75 still tells the two kinds of motion apart with room to spare.
ok(jolt > 0 && jolt < 75, 'and arcing into each new heading rather than snapping to it',
   'velocity never changes by more than ' + jolt.toFixed(0) + 'px/s in a frame');
ok(kept === roamF, 'the prize is penned inside the pocket while it roams',
   kept + '/' + roamF + ' frames');
ok(buried === 0, 'so it is never stranded inside a wall', buried + ' frames buried');
ok(window.__peek().rects.filter(r => r[2] >= 450 || r[3] >= 300).length === 0,
   'the walls retreat and clear afterwards');
finish();
""", (LIVES, only('box'), NOAMB, NOIDLE)))

TESTS.append(("ghosting", """
begin();
// sit in a corner and let the closing box swallow the marker whole
let frames = 0, lost = 0, last = 999, sawSlab = false;
while (frames < 60 * 20) {
  step(1, [16, 16]);
  frames++;
  const p = window.__peek();
  if (p.lives < last) lost += last - p.lives;
  last = p.lives;
  const slabs = p.rects.filter(r => r[2] >= 450 || r[3] >= 300).length;
  if (slabs) sawSlab = true;
  if (sawSlab && !slabs) break;       // stop once the walls are gone
  if (p.state !== 1) break;
}
ok(sawSlab, 'the box closed');
ok(lost === 1, 'a slab parked on the marker costs one life, not many', 'lost=' + lost);
finish();
""", (LIVES, only('box'), NOAMB, NOHEART, NOIDLE)))

TESTS.append(("clock-tick", """
begin();
step(20, [450, 300]);
const R = K.CLOCK_R;
// hour 0 is 12 o'clock and the hours run clockwise, so a tick is +1
const hourOf = r => {
  const m = mid(r);
  const a = Math.atan2(m[1] - 300, m[0] - 450) + Math.PI / 2;
  return ((a / (Math.PI * 2 / 12)) % 12 + 12) % 12;
};
const radOf = r => { const m = mid(r); return Math.hypot(m[0] - 450, m[1] - 300); };
const near = h => Math.abs(h - Math.round(h)) < 0.03;

let sawDial = false, dialFrames = 0, dialWrong = 0, dialHours = null;
let onHour = 0, seq = [], gapWorst = 0, reach = 0, left = -1;
for (let i = 0; i < 60 * 40; i++) {
  step(1, [450, 300]);
  const p = window.__peek();
  if (p.state !== 1 || p.roundNo > 1) break;
  // The clockwork round comes with an escort and its shots; they are not the clock,
  // so the dial and the hands are read out of the event's own cubes alone.
  const own = p.rects.filter(r => r[6] === 0);
  const dial = own.filter(r => Math.abs(radOf(r) - R) < 1);
  const arm  = own.filter(r => radOf(r) < R - 20);
  if (!dial.length) {
    if (sawDial) { left = own.length; break; }       // the wave is over: what is left?
    continue;
  }
  sawDial = true;
  dialFrames++;
  if (dial.length !== 12) dialWrong++;
  if (!dialHours) dialHours = dial.map(hourOf).sort((a, b) => a - b);
  if (!arm.length) continue;
  // the long hand is the outermost link; the hand itself is the run along its ray
  const tip = arm.reduce((a, b) => (radOf(a) > radOf(b) ? a : b));
  const ta = Math.atan2(mid(tip)[1] - 300, mid(tip)[0] - 450);
  const links = arm.filter(r => {
    if (radOf(r) < 4) return true;                   // the link over the centre
    const m = mid(r), d = Math.atan2(m[1] - 300, m[0] - 450) - ta;
    return Math.abs(Math.atan2(Math.sin(d), Math.cos(d))) < 0.05;
  }).map(radOf).sort((a, b) => a - b);
  for (let j = 1; j < links.length; j++) gapWorst = Math.max(gapWorst, links[j] - links[j - 1]);
  reach = Math.max(reach, links[links.length - 1]);
  const h = hourOf(tip);
  if (near(h)) {
    onHour++;
    const r = Math.round(h) % 12;
    if (seq[seq.length - 1] !== r) seq.push(r);
  }
}
let stepped = seq.length > 1, hours = new Set(seq);
for (let i = 1; i < seq.length; i++) if ((seq[i - 1] + 1) % 12 !== seq[i]) stepped = false;
say('clock  dial ' + (dialHours ? dialHours.length : 0) + ' hours at r=' + R +
    ', hand reaches ' + reach.toFixed(0) + 'px, ' + seq.length + ' hours visited, ' +
    (100 * onHour / Math.max(1, dialFrames)).toFixed(0) + '% of frames on the hour');
ok(sawDial && dialWrong === 0, 'twelve cubes stand in for the hours, all wave long',
   dialFrames + ' frames, ' + dialWrong + ' off-count');
ok(dialHours && dialHours.every((h, i) => Math.abs(h - i) < 0.01),
   'and they sit on the twelve hour marks of a ' + R + 'px dial',
   dialHours && dialHours.map(h => h.toFixed(2)).join(' '));
ok(reach > R - 34 - K.CLOCK_ARM, 'the long hand reaches the dial', reach.toFixed(0) + 'px');
ok(gapWorst > 0 && gapWorst < K.CLOCK_ARM, 'and is a solid run of links, with no hole to slip through',
   'worst gap ' + gapWorst.toFixed(0) + 'px between ' + K.CLOCK_ARM + 'px links');
ok(stepped, 'it ticks one hour clockwise at a time', seq.join(' '));
ok(hours.size === 12 && seq.length >= 12, 'twelve of them: one whole revolution per wave',
   seq.length + ' ticks over ' + hours.size + ' hours');
ok(onHour / Math.max(1, dialFrames) > 0.55,
   'and it is parked on an hour most of the time, rather than sweeping',
   (100 * onHour / Math.max(1, dialFrames)).toFixed(0) + '% of frames');
ok(left === 0, 'dial and hands clear together when the wave ends', 'left=' + left);
finish();
""", (LIVES, only('clock'), NOAMB, NOHEART, NOIDLE)))

# The dial is a puzzle rather than a threat, so the round it runs is also dealt one
# small hunter — and the hunter is the round's, not the idle watch's. Loose traffic is
# the other half of this scenario and it is a negative now: the round used to be dealt
# busy traffic across the face as well, and the two reading jobs at once made it a
# matter of luck. The dial and the hunter are the whole round, in every cycle.
TESTS.append(("clock-escort", """
begin();
let frames = 0, alone = 0, folded = 0, wrongSize = 0, notMini = 0, fromWatch = 0;
let horiz = 0, vert = 0, diag = 0, rounds = {}, bodies = {};
let dialF = 0, dialBody = 0;
for (let i = 0; i < 60 * 75; i++) {
  step(1, [450 + Math.cos(i * 0.03) * 230, 300 + Math.sin(i * 0.04) * 150]);
  frames++;
  const p = window.__peek();
  if (p.state !== 1) break;
  if (!p.live) continue;                       // the breather is nobody's round
  rounds[p.roundNo] = 1;
  bodies[p.roundNo] = Math.max(bodies[p.roundNo] || 0, p.prowl);
  // It folds away on the round's own clock, like every other hunter body, so the last
  // second or two of an overrunning round is not its problem.
  const staying = p.roundT < K.ROUND_MIN || p.roundT + K.HUNT_EXIT <= p.roundLen;
  if (staying && p.prowl !== 1) alone++;
  if (!staying && p.prowl === 0) folded++;
  for (const u of p.units) {
    if (Math.abs(u[2] - K.MINI_SIZE) > 0.5) wrongSize++;
    if (!u[4]) notMini++;
  }
  if (p.idler) fromWatch++;
  // The traffic layer is counted by its own tag rather than by how it moves, so a
  // stray one is caught whether it crosses the dial or merely sits on it.
  for (const r of p.rects) {
    if (r[6] !== 3) continue;
    const mx = Math.abs(r[4]) > 1, my = Math.abs(r[5]) > 1;
    if (mx && !my) horiz++;
    else if (my && !mx) vert++;
    else diag++;
  }
  // The escort has to be on the board while the dial is, which is the point of it: a
  // dial with the hunter still outlining or already folded away is the quiet round the
  // escort was added to answer.
  const dial = p.rects.filter(r => r[6] === 0).length;
  if (dial >= 12) {
    dialF++;
    if (p.rects.some(r => r[6] === 1)) dialBody++;
  }
}
const rs = Object.keys(rounds);
say('clock  ' + rs.length + ' rounds, bodies ' + rs.map(r => bodies[r]).join(',') +
    ', traffic ' + (horiz + vert + diag) + ' cubes');
ok(rs.length >= 2, 'several clockwork rounds', rs.join(','));
ok(alone === 0, 'every clockwork round has exactly one small hunter in it, all round long',
   alone + '/' + frames + ' frames with any other count');
ok(folded > 0, 'and it folds away on the round clock rather than outstaying it',
   folded + ' frames already gone by the bell');
ok(wrongSize === 0 && notMini === 0, 'and it is a mini, not a boss',
   wrongSize + ' odd sizes, ' + notMini + ' non-minis');
ok(fromWatch === 0, 'dealt by the round rather than by the idle watch',
   fromWatch + ' frames blaming the watch');
ok(horiz + vert + diag === 0, 'with no loose traffic over the dial, in any cycle',
   (horiz + vert + diag) + ' traffic cubes across ' + rs.length + ' rounds');
ok(dialF > 0 && dialBody / Math.max(1, dialF) > 0.8,
   'and the hunter shares the board with the dial rather than waiting for it to finish',
   (100 * dialBody / Math.max(1, dialF)).toFixed(0) + '% of ' + dialF + ' dial frames');
finish();
""", (LIVES, only('clock'), NOHEART, NOIDLE, rounds(20, 20))))

TESTS.append(("minis", """
begin();
// The marker keeps moving: two sets of homing shots land on anything that stands
// still, and what is being measured is whether a moving one can still read them.
const path = i => [450 + Math.cos(i * 0.035) * 250, 300 + Math.sin(i * 0.05) * 165];
let frames = 0, warnF = 0, fullF = 0, most = 0, sizeTop = 0, sizeLow = 1e9;
let shotsPer = 0, arrivals = [], sep = 1e9, spots = [], trackTop = 0, trackLow = 1e9;
let after = null, big = 0, bodyF = 0, stacked = 0;
const births = [], seen = new Set();
while (frames < 60 * 45) {
  step(1, path(frames));
  frames++;
  const p = window.__peek();
  if (p.state !== 1) break;
  if (p.roundNo > 1 || !p.live) { after = p; break; }   // the breather: are they gone?
  if (p.prowl && !p.bodies) warnF++;
  most = Math.max(most, p.bodies);
  if (p.bodies === K.MINI_COUNT) fullF++;
  if (p.bodies) bodyF++;
  // Each body noted the first frame it is solid, so the stagger can be read off.
  if (p.bodies > arrivals.length) arrivals.push(p.roundT);
  for (const u of p.units) {
    if (u[3] !== 1) shotsPer++;                        // a mini fires one at a time
    if (u[4] !== 1) big++;
    if (u[2] > 1) { sizeTop = Math.max(sizeTop, u[2]); sizeLow = Math.min(sizeLow, u[2]); }
  }
  // ...and how close two of them ever get, since two bodies in a heap is one body
  // with two rates of fire. Only the ones grown enough to bite are worth counting.
  for (let i = 0; i < p.units.length; i++)
    for (let j = i + 1; j < p.units.length; j++) {
      const a = p.units[i], b = p.units[j];
      if (a[5] < 0.4 || b[5] < 0.4) continue;
      const dx = Math.abs(a[0] - b[0]), dy = Math.abs(a[1] - b[1]);
      sep = Math.min(sep, Math.hypot(dx, dy));
      const room = (a[2] * a[5] + b[2] * b[5]) / 2;
      if (dx < room && dy < room) stacked++;
    }
  spots.push(p.units.map(u => [u[0], u[1]]));
  // A shot is known by the frame it left the muzzle, which is the one thing about it
  // that never changes. Two leaving on the same frame count once — a coincidence
  // between two hunters on their own clocks, and rare enough not to matter here.
  for (const s of p.shots) {
    const k = Math.round((p.elapsed - s[4]) * 60);
    if (!seen.has(k)) { seen.add(k); births.push(p.elapsed); }
    const sp = Math.hypot(s[2], s[3]);
    if (s[4] < s[5]) { trackTop = Math.max(trackTop, sp); trackLow = Math.min(trackLow, sp); }
  }
}
let travel = 0;
for (let i = 1; i < spots.length; i++) {
  const a = spots[i - 1], b = spots[i];
  for (let j = 0; j < Math.min(a.length, b.length); j++)
    travel += Math.hypot(b[j][0] - a[j][0], b[j][1] - a[j][1]);
}
const stagger = arrivals.slice(1).map((t, i) => t - arrivals[i]);
say('minis  ' + most + ' bodies of ' + sizeLow.toFixed(0) + '-' + sizeTop.toFixed(0) +
    'px, in at ' + arrivals.map(t => t.toFixed(1)).join('/') + 's, ' + births.length +
    ' shots, closest pair ' + sep.toFixed(0) + 'px, ' + travel.toFixed(0) + 'px of prowling');
ok(warnF > 0, 'they are outlined before any of them can bite', (warnF / 60).toFixed(1) + 's of warning');
ok(most === K.MINI_COUNT, K.MINI_COUNT + ' of them on the stage, no more', most + ' at once');
ok(fullF > 60 * K.ROUND_MIN * 0.6, 'and all ' + K.MINI_COUNT + ' of them for most of the round',
   (fullF / 60).toFixed(1) + 's of a ' + K.ROUND_MIN + 's floor');
ok(arrivals.length === K.MINI_COUNT && stagger.every(g => g > K.MINI_STAGGER * 0.5),
   'arriving one after another rather than all at once',
   stagger.map(g => g.toFixed(2)).join('/') + 's apart');
ok(sizeLow > K.MINI_SIZE - 0.01 && sizeTop < K.MINI_SIZE + 0.01 && big === 0,
   'each is a small hunter, ' + K.MINI_SIZE + 'px of it',
   sizeLow.toFixed(0) + '-' + sizeTop.toFixed(0) + 'px');
ok(shotsPer === 0, 'and fires one cube at a time, not a fan', shotsPer + ' frames of more');
// Each hunter on its own clock, firing once a volley: between them the round should
// see about MINI_COUNT times one hunter's rate of fire.
const rate = births.length / Math.max(1, bodyF / 60) * K.MINI_VOLLEY;
ok(rate > K.MINI_COUNT - 0.5 && rate < K.MINI_COUNT + 0.5,
   'so between them they keep up ' + K.MINI_COUNT + ' shots a volley, from ' +
   K.MINI_COUNT + ' places at once',
   births.length + ' shots in ' + (bodyF / 60).toFixed(0) + 's, a rate of ' + rate.toFixed(2));
ok(stacked === 0, 'no two of them ever stand in the same place',
   stacked + ' frames overlapping, closest pair ' + sep.toFixed(0) + 'px');
ok(travel > 300, 'both of them prowl rather than sitting still', travel.toFixed(0) + 'px');
ok(Math.abs(trackTop - K.MINI_TRACK) < 0.01 && Math.abs(trackLow - K.MINI_TRACK) < 0.01,
   'a mini shot tracks at a steady ' + K.MINI_TRACK + 'px/s, under the ' + K.HUNT_TRACK + ' of the boss',
   trackLow.toFixed(0) + '-' + trackTop.toFixed(0) + 'px/s');
ok(after && after.bodies === 0 && after.prowl === 0, 'and they both leave with the round',
   after ? 'live=' + after.live + ' bodies=' + after.bodies + ' units=' + after.prowl : 'never ended');
finish();
""", (LIVES, only('minis'), NOAMB, NOHEART, NOIDLE)))

# The idle watch, which is the one thing in the arena that answers a player refusing
# the bargain. Every event is stubbed out, so the only body that can appear is one
# the watch sent for -- and the real schedule is left alone so that the fifth round is
# a boss and can be checked to be left out of it.
TESTS.append(("idle-watch", """
begin();
step(30, [60, 540]);
window.__pin(820, 60);                  // the prize cube in one corner...
const FAR = [60, 540];                  // ...and the marker in the other, all round long
const byRound = {}, at = {}, sizes = [], shots = [];
let frames = 0, early = 0, twice = 0, boss = 0, carried = 0, prevRound = 1;
let cueBefore = 0, cueMax = 0, flash = 0, cueBoss = 0;
while (frames < 60 * 150) {
  step(1, FAR);
  frames++;
  const p = window.__peek();
  if (p.state !== 1) break;
  if (p.roundNo > prevRound) {
    // A body may not be carried over into the next round, watch or no watch. The
    // clockwork round is the exception and not an interesting one: it deals itself an
    // escort in beginRound, so a body there is the new round's, not the old one's.
    if (p.prowl && p.cfg !== 'clock') carried++;
    prevRound = p.roundNo;
  }
  if (p.roundNo > 5) break;
  byRound[p.roundNo] = Math.max(byRound[p.roundNo] || 0, p.bodies);
  if (p.bodies > 1) twice++;
  if (p.bodies && p.boss) boss++;
  if (p.bodies && !(p.roundNo in at)) at[p.roundNo] = p.roundT;
  // ...and nothing may be sent for before the floor, however long the round is
  if (p.prowl && p.awayT < K.IDLE_MIN) early++;
  // the cube's own cue: it should be asking for company before any turns up, and
  // saying so out loud the moment one does
  if (!p.prowl) { cueMax = Math.max(cueMax, p.lonely); if (p.lonely > K.LONELY_AT) cueBefore++; }
  if (p.lonelyT > 0) flash++;
  if (p.boss && p.lonely > 0) cueBoss++;
  for (const u of p.units) if (u[2] > 1) { sizes.push(u[2]); shots.push(u[3]); }
}
const ord = [1, 2, 3, 4];
say('idle  bodies by round ' + [1, 2, 3, 4, 5].map(r => r + ':' + (byRound[r] || 0)).join(' ') +
    ', sent for at ' + ord.map(r => (at[r] || 0).toFixed(1)).join('/') + 's');
ok(ord.every(r => byRound[r] === 1), 'leave the prize cube alone and one hunter comes looking',
   ord.map(r => byRound[r] || 0).join('/'));
ok(twice === 0, 'one of them, never a second', twice + ' frames of more');
ok(ord.every(r => at[r] >= K.IDLE_MIN), 'never before ' + K.IDLE_MIN + 's of neglect',
   ord.map(r => (at[r] || 0).toFixed(1)).join('/') + 's');
ok(ord.every(r => at[r] < K.ROUND_MIN), 'and in time to matter, not on the last second',
   ord.map(r => (at[r] || 0).toFixed(1)).join('/') + 's of a ' + K.ROUND_MIN + 's round');
ok(early === 0, 'the floor is on neglect, not on the clock', early + ' frames too early');
ok(sizes.length > 0 && sizes.every(s => Math.abs(s - K.MINI_SIZE) < 0.01) && shots.every(n => n === 1),
   'what it sends is one mini hunter, one shot at a time',
   sizes.length ? sizes[0].toFixed(0) + 'px x' + shots[0] : 'none seen');
ok(carried === 0, 'it lasts until that round is over, and no longer', carried + ' carried over');
ok(cueBefore > 60, 'the cube gets LONELY out loud before anything is sent for',
   (cueBefore / 60).toFixed(1) + 's of it');
ok(cueMax > 0.97, 'and the cue runs all the way up to the moment one is',
   cueMax.toFixed(3) + ' of 1');
ok(flash > 60 * K.LONELY_FLASH * 0.5, 'then says so outright when company arrives',
   (flash / 60).toFixed(1) + 's of flash over ' + ord.length + ' rounds');
ok(cueBoss === 0, 'a boss round has no cue, because it has no watch', cueBoss + ' frames');
ok((byRound[5] || 0) === 0 && boss === 0, 'and a boss round is left to its boss',
   'round 5 saw ' + (byRound[5] || 0));
finish();
""", (LIVES, NOEVENT, NOAMB, NOHEART, rounds(14, 16)) + breather(0.3)))

# The other half of the rule: hug the prize cube and nothing comes looking.
TESTS.append(("idle-hugged", """
begin();
step(30, [450, 300]);
window.__pin(450, 300);
const p0 = window.__peek();
const [ox, oy] = mid(p0.orb);
let frames = 0, bodies = 0, away = 0, held = 0, cue = 0;
while (frames < 60 * 40) {
  step(1, [ox + p0.orb[2] / 2 + 24, oy]);        // just off one face of it, all round
  frames++;
  const p = window.__peek();
  if (p.state !== 1 || p.roundNo > 1) break;
  held++;
  bodies = Math.max(bodies, p.prowl);
  away = Math.max(away, p.awayT);
  cue = Math.max(cue, p.lonely);
}
say('hugged  ' + (held / 60).toFixed(1) + 's in the band, ' + away.toFixed(1) + 's of it counted away');
ok(held > 60 * K.ROUND_MIN * 0.9, 'a whole round spent on the prize cube',
   (held / 60).toFixed(1) + 's of a ' + K.ROUND_MIN + 's floor');
ok(away < 1, 'which the watch does not count as neglect', away.toFixed(2) + 's counted');
ok(bodies === 0, 'so nothing comes looking', bodies + ' sent for');
ok(cue < K.LONELY_AT, 'and it never gets lonely enough to say so', cue.toFixed(3));
finish();
""", (LIVES, NOEVENT, NOAMB, NOHEART)))

# The ceiling, on the one round that arrives with bodies of its own: the watch may
# fill the last slot and not one more.
TESTS.append(("idle-ceiling", """
begin();
step(30, [60, 540]);
window.__pin(820, 60);
let frames = 0, most = 0, over = 0, full = 0, after = null;
while (frames < 60 * 45) {
  step(1, [60, 540]);
  frames++;
  const p = window.__peek();
  if (p.state !== 1) break;
  if (p.roundNo > 1 || !p.live) { after = p; break; }
  most = Math.max(most, p.prowl);
  if (p.prowl > K.MINI_MAX) over++;
  if (p.prowl === K.MINI_MAX) full++;
}
say('ceiling  up to ' + most + ' hunters, ' + (full / 60).toFixed(1) + 's of a full stage');
ok(most === K.MINI_MAX, 'a neglected mini-hunter round runs to ' + K.MINI_MAX + ' hunters',
   most + ' at once');
ok(over === 0, 'and no further', over + ' frames over');
ok(full > 60, 'the one it sent is there long enough to be the point of it',
   (full / 60).toFixed(1) + 's');
ok(after && after.prowl === 0, 'and all ' + K.MINI_MAX + ' of them leave with the round',
   after ? 'units=' + after.prowl : 'never ended');
finish();
""", (LIVES, only('minis'), NOAMB, NOHEART)))

TESTS.append(("half-turn", """
begin();
step(20, [450, 300]);
const W = 900, H = 600;
// which half-plane a slab covers: 0 is the left, then clockwise
const sideOf = r => {
  if (r[3] > H - 1 && r[0] < 1) return 0;
  if (r[2] > W - 1 && r[1] < 1) return 1;
  if (r[3] > H - 1 && r[0] + r[2] > W - 1) return 2;
  if (r[2] > W - 1 && r[1] + r[3] > H - 1) return 3;
  return -1;
};
const area = r => Math.max(0, r[2]) * Math.max(0, r[3]);
const inter = (a, b) => [0, 0,
  Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0]),
  Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1])];

let sawSlab = false, frames = 0, badSide = 0, overHalf = 0, worst = 0, onBoard = 0;
let holds = [], sides = [], notRect = 0, kept = 0, penned = 0, buried = 0, left = -1;
for (let i = 0; i < 60 * 40; i++) {
  step(1, [450, 300]);
  const p = window.__peek();
  if (p.state !== 1 || p.roundNo > 1) break;
  if (sawSlab && !p.rects.length) { left = 0; break; }   // slabs gone, not just flat
  // A strip of no depth is parked off the board, never left as a line on an edge
  // the marker can be clamped against.
  if (p.rects.some(r => (r[2] < 0.5 || r[3] < 0.5) && r[0] > -1000)) onBoard++;
  const slabs = p.rects.filter(r => r[2] > 0.5 && r[3] > 0.5);
  if (!slabs.length) continue;
  sawSlab = true;
  frames++;
  if (slabs.some(r => sideOf(r) < 0)) badSide++;
  const cover = slabs.reduce((a, r) => a + area(r), 0) -
                (slabs.length > 1 ? area(inter(slabs[0], slabs[1])) : 0);
  worst = Math.max(worst, cover);
  if (cover > W * H / 2 + 1) overHalf++;
  // what is left over, and whether it is one rectangle at all
  let x0 = 0, y0 = 0, x1 = W, y1 = H;
  for (const r of slabs) {
    const s = sideOf(r);
    if (s === 0)      x0 = Math.max(x0, r[0] + r[2]);
    else if (s === 1) y0 = Math.max(y0, r[1] + r[3]);
    else if (s === 2) x1 = Math.min(x1, r[0]);
    else if (s === 3) y1 = Math.min(y1, r[1]);
  }
  const safe = [x0, y0, x1 - x0, y1 - y0];
  if (Math.abs(area(safe) - (W * H - cover)) > 2) notRect++;
  if (p.evPh === 'hold') {
    holds.push(cover);
    const s = sideOf(slabs.find(r => area(r) > 1));
    if (sides[sides.length - 1] !== s) sides.push(s);
  }
  if (p.focus && p.focus[3]) {
    kept++;
    const o = p.orb;
    if (o && o[0] >= x0 - 1 && o[1] >= y0 - 1 && o[0] + o[2] <= x1 + 1 && o[1] + o[3] <= y1 + 1) penned++;
    if (o && slabs.some(r => overlap(o, r))) buried++;
  }
}
let walked = sides.length > 1, dir = sides.length > 1 ? (sides[1] - sides[0] + 4) % 4 : 0;
for (let i = 1; i < sides.length; i++) if ((sides[i - 1] + dir) % 4 !== sides[i]) walked = false;
say('half  ' + frames + ' frames of cover, worst ' + (100 * worst / (W * H)).toFixed(1) +
    '% of the stage, sides ' + sides.join('>') + ' (' + (dir === 1 ? 'clockwise' : 'widdershins') + ')');
ok(sawSlab && badSide === 0, 'the cover is always a half-plane of the stage, squared to an edge',
   frames + ' frames, ' + badSide + ' odd ones');
ok(overHalf === 0, 'and never more than half of it', 'worst ' +
   (100 * worst / (W * H)).toFixed(1) + '%');
ok(holds.length > 0 && holds.every(c => Math.abs(c - W * H / 2) < 1),
   'at rest it is exactly half', holds.length + ' frames held');
ok(notRect === 0, 'what is left is a single rectangle, not an L', notRect + ' frames otherwise');
ok(onBoard === 0, 'a strip with no depth is parked off the board, not left as a line on an edge',
   onBoard + ' frames of one');
ok(sides.length === K.HALF_TURNS, 'it moves on a quarter turn at a time',
   sides.length + ' sides held of ' + K.HALF_TURNS);
ok(walked && (dir === 1 || dir === 3), 'all the way round one way, never doubling back',
   sides.join('>'));
ok(kept > 0 && penned === kept, 'the prize is penned into the safe rectangle while the cover is up',
   penned + '/' + kept + ' frames');
ok(buried === 0, 'so it is never stranded under a slab', buried + ' frames buried');
ok(left === 0, 'and the cover pulls back off the stage at the end', 'left=' + left);
finish();
""", (LIVES, only('half'), NOAMB, NOHEART, NOIDLE)))

TESTS.append(("hunter", """
begin();
// The marker keeps moving, because a homing shot lands on anything that stands
// still — and what this scenario is about is whether a moving one can out-turn it.
const path = i => [450 + Math.cos(i * 0.035) * 250, 300 + Math.sin(i * 0.05) * 165];
let frames = 0, warnF = 0, bodyF = 0, twoBodies = 0, solidEarly = 0, sizeTop = 0;
let siegeF = 0, cagedF = 0;      // neither of which a hunter round has any business doing
let spots = [], jolt = 0, births = [], after = null, per = 0;
let trackTop = 0, trackLow = 1e9, dashTop = 0, bendTop = 0, lockWorst = 0, aimed = 0;
const life = new Map();          // one entry per shot, keyed by when it was fired
const shot = new Map();          // and one about how it flew, same key
const mkPath = [];               // where the marker was, frame by frame
const DT = 1 / 60;               // the harness clock, and so the game's
const wrap = d => Math.atan2(Math.sin(d), Math.cos(d));
let lawF = 0, lawWorst = 0, turns = 0, toward = 0;
let was = new Map();
while (frames < 60 * 45) {
  step(1, path(frames));
  frames++;
  const p = window.__peek();
  if (p.state !== 1) break;
  mkPath.push([p.px, p.py]);                                 // index: frames - 1
  if (p.roundNo > 1) { after = p; break; }
  if (!p.live && p.roundNo === 1) { after = p; break; }      // the breather: is it gone?
  if (p.ev === 'hunter' && !p.body) warnF++;
  per = p.evShots || per;
  if (p.body) {
    bodyF++;
    sizeTop = Math.max(sizeTop, p.body[2]);
    if (!p.body[3] && p.body[2] < K.HUNT_SIZE * 0.39) solidEarly++;   // biting before it is there
    spots.push([p.body[0], p.body[1]]);
    const n = spots.length;
    if (n > 2) {
      const ax = (spots[n - 2][0] - spots[n - 3][0]) * 60, ay = (spots[n - 2][1] - spots[n - 3][1]) * 60;
      const bx = (spots[n - 1][0] - spots[n - 2][0]) * 60, by = (spots[n - 1][1] - spots[n - 2][1]) * 60;
      jolt = Math.max(jolt, Math.hypot(bx - ax, by - ay));
    }
  }
  twoBodies += p.bodies > 1 ? 1 : 0;
  siegeF += p.siege ? 1 : 0;
  cagedF += p.orbCaged ? 1 : 0;
  // Follow each shot by its birth time — the one thing about it that never changes.
  const now = new Map();
  for (const s of p.shots) {
    const k = (p.elapsed - s[4]).toFixed(2);
    const a = Math.atan2(s[3], s[2]), sp = Math.hypot(s[2], s[3]);
    const to = Math.atan2(p.py - s[1], p.px - s[0]);
    const off = Math.abs(Math.atan2(Math.sin(to - a), Math.cos(to - a)));
    const cmt = s[4] >= s[5];
    now.set(k, { a, cmt, x: s[0], y: s[1] });
    if (!life.has(k)) {
      life.set(k, off);
      births.push([p.elapsed, k]);
      // First sight is one step past the muzzle, which is near enough: what the
      // straight-line comparison below needs is a place and a heading to fly from.
      shot.set(k, { f: frames - 1, x: s[0], y: s[1], a, sp, n: 0, near: 1e9 });
    }
    const rec = shot.get(k);
    if (!cmt) {
      life.set(k, Math.min(life.get(k), off));
      trackTop = Math.max(trackTop, sp);
      trackLow = Math.min(trackLow, sp);
      rec.n++;
      rec.near = Math.min(rec.near, Math.hypot(p.px - s[0], p.py - s[1]));
    } else dashTop = Math.max(dashTop, sp);
    const b = was.get(k);
    if (b) {
      const bend = Math.abs(wrap(a - b.a));
      if (b.cmt && cmt) lockWorst = Math.max(lockWorst, bend);
      else if (!cmt) {
        bendTop = Math.max(bendTop, bend);
        // Pure pursuit, held to the law rather than eyeballed: from where it was, the
        // heading turns onto where the marker is now, by every radian of the error the
        // turn rate can pay for and not one more.
        const err = wrap(Math.atan2(p.py - b.y, p.px - b.x) - b.a);
        const cap = K.HUNT_TURN * DT;
        lawWorst = Math.max(lawWorst, Math.abs(wrap(a - b.a) - Math.max(-cap, Math.min(cap, err))));
        lawF++;
        // Frames where the error was well past one frame of turning, so which way it
        // went is a decision and not a rounding.
        if (Math.abs(err) > cap * 1.5) { turns++; if (wrap(a - b.a) * err > 0) toward++; }
      }
    }
  }
  was = now;
}
for (const off of life.values()) if (off < 0.2) aimed++;
// What the homing is actually worth: fly each shot again as a straight line on the
// heading it left on, over the same frames, and see how close that would have come
// to the marker instead. The shots still tracking when the round ended are left out,
// since half a life is not a fair comparison.
let better = 0, sumH = 0, sumS = 0, tried = 0;
for (const r of shot.values()) {
  if (r.n < 60 * K.HUNT_HOME * 0.9) continue;
  let near = 1e9;
  for (let i = 0; i < r.n && mkPath[r.f + i]; i++) {
    const m = mkPath[r.f + i];
    const x = r.x + Math.cos(r.a) * r.sp * i * DT, y = r.y + Math.sin(r.a) * r.sp * i * DT;
    near = Math.min(near, Math.hypot(m[0] - x, m[1] - y));
  }
  tried++;
  sumH += r.near;
  sumS += near;
  if (r.near < near) better++;
}
// Births inside a tenth of a second of each other are one volley: [start, last, count].
const volleys = [];
for (const [t] of births) {
  if (!volleys.length || t - volleys[volleys.length - 1][1] > K.HUNT_SPACE * 1.6) volleys.push([t, t, 0]);
  volleys[volleys.length - 1][1] = t;
  volleys[volleys.length - 1][2]++;
}
let travel = 0;
for (let i = 1; i < spots.length; i++)
  travel += Math.hypot(spots[i][0] - spots[i - 1][0], spots[i][1] - spots[i - 1][1]);
// Measured muzzle to muzzle, so the last volley being cut off by the end of the
// round shortens it rather than moving everything along by the missing shots.
const gaps = volleys.slice(1).map((v, i) => v[0] - volleys[i][0]);
// …and for the same reason only the last one is allowed to be short.
const whole = volleys.filter((v, i) => i < volleys.length - 1);
say('hunter  body up ' + (bodyF / 60).toFixed(1) + 's over ' + travel.toFixed(0) + 'px, ' +
    volleys.length + ' volleys of ' + volleys.map(v => v[2]).join('/') + ', ' +
    life.size + ' shots, ' + (100 * aimed / Math.max(1, life.size)).toFixed(0) + '% got on target');
ok(siegeF === 0 && cagedF === 0,
   'a hunter outside a boss slot is just a hunter: it comes for you, not for the cube',
   siegeF + ' siege frames, ' + cagedF + ' with the cube held');
ok(warnF > 0 && bodyF > 0, 'the body is outlined before it can bite',
   (warnF / 60).toFixed(1) + 's of warning, then ' + (bodyF / 60).toFixed(1) + 's of boss');
ok(twoBodies === 0 && Math.abs(sizeTop - K.HUNT_SIZE) < 0.01,
   'one body, ' + K.HUNT_SIZE + 'px of it', sizeTop.toFixed(0) + 'px, ' + twoBodies + ' double frames');
ok(solidEarly === 0, 'and it is harmless while it swells', solidEarly + ' frames biting early');
ok(bodyF > 60 * K.ROUND_MIN * 0.8, 'it stays for the whole round rather than one wave',
   (bodyF / 60).toFixed(1) + 's of a ' + K.ROUND_MIN + 's floor');
ok(travel > 300 && jolt > 0 && jolt < 60, 'prowling on the same steering the box pocket uses',
   travel.toFixed(0) + 'px, worst jolt ' + jolt.toFixed(0) + 'px/s');
ok(volleys.length >= 4, 'it fires volley after volley', volleys.length + ' of them');
ok(per > 1 && whole.length > 0 && whole.every(v => v[2] === per),
   'every volley is the same handful of shots — ' + per + ' of them',
   volleys.map(v => v[2]).join('/'));
// One volley to the next is the gap it waits out plus the time the volley itself
// takes to leave the muzzle, which is what the shot spacing buys.
const period = K.HUNT_VOLLEY + (per - 1) * K.HUNT_SPACE;
ok(gaps.length > 0 && gaps.every(g => Math.abs(g - period) < 0.1),
   'one volley every ' + period.toFixed(2) + 's',
   gaps.map(g => g.toFixed(2)).join(' ') + 's');
ok(Math.abs(trackTop - K.HUNT_TRACK) < 0.01 && Math.abs(trackLow - K.HUNT_TRACK) < 0.01,
   'a shot tracks at a steady ' + K.HUNT_TRACK + 'px/s, so it can be out-run',
   trackLow.toFixed(0) + '-' + trackTop.toFixed(0) + 'px/s');
ok(bendTop > 0 && bendTop < K.HUNT_TURN / 60 + 1e-6,
   'bending onto the marker at no more than ' + K.HUNT_TURN + ' rad/s, never snapping to it',
   (bendTop * 60).toFixed(2) + ' rad/s');
ok(lawF > 300 && lawWorst < 1e-6,
   'and spending the whole of that allowance on the error it has, every frame',
   lawF + ' frames, worst ' + lawWorst.toExponential(1) + ' rad off the law');
ok(turns > 100 && toward === turns, 'always turning the short way towards the marker, never off it',
   toward + '/' + turns + ' frames with a real decision to make');
// Most of them rather than all of them, and per shot rather than in aggregate: a shot
// loosed point-blank at a marker doing 700px/s can be left behind whichever way it is
// pointed, and whether that happens twice in a run or six times is luck.
ok(tried >= 6 && better >= tried * 0.7,
   'so a shot ends up nearer you than the same shot fired straight would have',
   better + '/' + tried + ' shots, ' + sumH.toFixed(0) + 'px of near misses against ' +
   sumS.toFixed(0) + 'px');
// Run to run this lands anywhere between half and three quarters, since it depends on
// where the marker happened to be when each volley went out — so the bar is only that
// homing is worth a large fraction of the distance, not a particular fraction of it.
ok(sumH < sumS * 0.85, 'and nearer by a wide margin, not a hair',
   (100 * sumH / Math.max(1, sumS)).toFixed(0) + '% of the distance a straight line kept');
// Not all of them, and it is not meant to be: the marker in this scenario is doing
// up to 700px/s, and inside about 270px of it that bearing sweeps faster than 2.6
// rad/s, so a shot loosed point-blank at a sprinting marker cannot ever point at it.
// How many are in that position is down to where the boss happened to be prowling.
ok(aimed > life.size * 0.6, 'and most of them come to bear outright — this is homing, not a scatter',
   aimed + '/' + life.size + ' within 0.2 rad');
ok(lockWorst < 1e-9, 'once committed the heading never changes again',
   'worst ' + lockWorst.toExponential(1) + ' rad');
ok(dashTop > K.HUNT_DASH * 0.98 && dashTop < K.HUNT_DASH + 1,
   'and it leaves at ' + K.HUNT_DASH + 'px/s', dashTop.toFixed(0) + 'px/s');
ok(after && after.bodies === 0, 'the boss leaves with the round',
   after ? 'round ' + after.roundNo + ' live=' + after.live + ' bodies=' + after.bodies : 'never ended');
finish();
""", (LIVES, only('hunter'), NOAMB, NOHEART, NOIDLE)))

TESTS.append(("rescue", """
begin();
step(30, [450, 300]);
window.__clear();                          // an empty arena: nothing else to be hit by
const secs = s => Math.round(60 * s);
// Park the marker just outside one face of the rescue cube, always on the side
// facing the middle of the arena so the arena edge can never push it into the cube.
function chase(n) {
  for (let i = 0; i < n; i++) {
    const p = window.__peek();
    if (!p.heart) break;                   // banked and gone
    const [x, y] = mid(p.heart);
    step(1, [x + (x > 450 ? -1 : 1) * (p.heart[2] / 2 + 30), y]);
  }
}
// ...and the opposite: the far corner from wherever it has got to.
function drift(n) {
  for (let i = 0; i < n; i++) {
    const p = window.__peek();
    const [x, y] = p.heart ? mid(p.heart) : [450, 300];
    step(1, [x > 450 ? 20 : 880, y > 300 ? 20 : 580]);
  }
}

const full = window.__peek();
ok(full.heart === null && full.lives === K.MAX_LIVES,
   'at full hearts there is no rescue on offer', 'lives=' + full.lives);

window.__hurt();
ok(window.__peek().lives === K.MAX_LIVES - 1, 'a heart goes',
   'lives=' + window.__peek().lives);
// Time the wait out frame by frame, so the cube is read the frame it appears.
let wait = 0, early = 'never looked';
while (wait < secs(K.HEART_DELAY + 3) && window.__peek().heart === null) {
  step(1, [450, 300]);
  wait++;
  if (wait === secs(K.HEART_DELAY - 0.5)) early = window.__peek().heart;
}
const delay = wait / 60;
const born = window.__peek();
ok(early === null, 'the rescue is held back for a few seconds after the hit',
   'nothing yet half a second short of the ' + K.HEART_DELAY + 's delay');
ok(!!born.heart && born.hearts === 1, 'then exactly one rescue cube turns up',
   'after ' + delay.toFixed(2) + 's, hearts=' + born.hearts);
ok(Math.abs(delay - K.HEART_DELAY) < 0.1, 'on the delay it promises',
   delay.toFixed(2) + 's vs ' + K.HEART_DELAY + 's');
const [bx, by] = mid(born.heart);
ok(Math.hypot(bx - born.px, by - born.py) > 120, 'clear of the marker, not dropped on it',
   Math.hypot(bx - born.px, by - born.py).toFixed(0) + 'px away');
ok(Math.abs(born.heart[4] - K.HEART_COUNT) < 0.1, 'carrying a full count',
   'count=' + born.heart[4].toFixed(2) + ' of ' + K.HEART_COUNT);
ok(born.heart[6], 'and it fades in before it can bite');

chase(secs(K.HEART_FADE + 0.25));
const solid = window.__peek();
ok(!solid.heart[6], 'once it has materialised it is a cube like any other');
ok(solid.heart[5], 'and it knows when the marker is beside it');
ok(Math.abs(solid.heart[4] - (K.HEART_COUNT - 0.25)) < 0.25,
   'nothing is banked while it is still fading in', 'count=' + solid.heart[4].toFixed(2));

const c0 = window.__peek().heart[4];
chase(secs(2));
const c1 = window.__peek();
ok(Math.abs((c0 - c1.heart[4]) - 2) < 0.15, 'the count runs down a second per second beside it',
   (c0 - c1.heart[4]).toFixed(2) + 's off it in 2s');
ok(c1.prox === 0 && c1.mult === 1, 'but it pays nothing and feeds no multiplier',
   'prox=' + c1.prox + ' mult=' + c1.mult.toFixed(2));

const b0 = window.__peek().heart[4];
drift(secs(2));
const b1 = window.__peek();
ok(!b1.heart[5], 'being driven off it stops the count');
ok(Math.abs((b1.heart[4] - b0) - 2 * K.HEART_SLIP) < 0.15,
   'and it creeps back at ' + K.HEART_SLIP + '/s rather than wiping',
   (b1.heart[4] - b0).toFixed(2) + 's back in 2s');

const worked = window.__peek();
ok(!worked.heartDoom && worked.heartSlack > 0,
   'a rescue that is being worked at is never written off',
   'slack=' + worked.heartSlack.toFixed(1) + 's of round to spare');

chase(secs(K.HEART_COUNT + 2));
const won = window.__peek();
ok(won.lives === K.MAX_LIVES, 'holding the count to zero hands the heart back', 'lives=' + won.lives);
ok(won.heart === null && won.hearts === 0, 'and the cube leaves with it', 'hearts=' + won.hearts);
ok(document.getElementById('lives').textContent.length === K.MAX_LIVES, 'the HUD gets it back too');

window.__hurt();                           // lose another one in the same round
step(secs(K.HEART_DELAY + 1.5), [450, 300]);
const again = window.__peek();
ok(again.heart === null, 'one rescue per round, and this round has had its one',
   'lives=' + again.lives + ' heart=' + again.heart);
ok(again.live && again.roundNo === 1, 'and it is still the same round',
   'round ' + again.roundNo + ' at ' + again.roundT.toFixed(1) + 's');
finish();
""", (NOAMB, NOEVENT, rounds(30, 30), NOIDLE) + heart(1.5, 4)))

TESTS.append(("rescue-round", """
begin();
step(20, [450, 300]);
window.__clear();
const secs = s => Math.round(60 * s);
function drift(n) {
  for (let i = 0; i < n; i++) {
    const p = window.__peek();
    const [x, y] = p.heart ? mid(p.heart) : [450, 300];
    step(1, [x > 450 ? 20 : 880, y > 300 ? 20 : 580]);
  }
}

window.__hurt();
drift(secs(K.HEART_DELAY + 0.8));
const out = window.__peek();
ok(!!out.heart, 'a rescue is offered in the round the heart was lost in',
   'round ' + out.roundNo + ' at ' + out.roundT.toFixed(1) + 's');
ok(Math.abs(out.heart[4] - K.HEART_COUNT) < 0.1, 'and sits on a full count while it is ignored',
   'count=' + out.heart[4].toFixed(2));

// Let the round run out from under it. Two clocks are running against each other: the
// count the cube still wants, and the round it has to be finished inside. Ignored, the
// count sits full while the round burns down, so the daylight between them runs out
// before the bell does — and a heart that can no longer be won is not left dangling.
const was = out.roundNo, rlen = out.roundLen;
let frames = 0, gone = 'the round never ended';
let slacks = [], doomAt = -1, doomLeft = 0, doomRem = 0, doomGhost = null, goneAt = -1;
while (frames < 60 * 30) {
  drift(1);
  frames++;
  const p = window.__peek();
  if (p.heart) slacks.push(p.heartSlack);
  if (p.heartDoom && doomAt < 0) {
    doomAt = p.roundT; doomLeft = p.heart[4];
    doomRem = p.roundLen - p.roundT; doomGhost = p.heart[6];
  }
  if (doomAt >= 0 && goneAt < 0 && !p.heart) goneAt = p.roundT;
  if (!p.live && p.roundNo === was) { gone = p.heart; break; }
}
const narrowed = slacks.every((s, i) => i === 0 || s <= slacks[i - 1] + 1e-9);
say('rescue  slack ' + slacks[0].toFixed(1) + 's -> ' + slacks[slacks.length - 1].toFixed(1) +
    's, written off at ' + doomAt.toFixed(1) + 's, gone at ' + goneAt.toFixed(1) +
    's of a ' + rlen.toFixed(1) + 's round');
ok(narrowed, 'the daylight between those two clocks only ever narrows',
   slacks[0].toFixed(1) + 's down to ' + slacks[slacks.length - 1].toFixed(1) + 's');
ok(doomAt > 0, 'a rescue nobody comes for runs out of round to be won in',
   'written off at ' + doomAt.toFixed(1) + 's');
ok(doomLeft >= doomRem - 0.05,
   'which is exactly when the count it wants no longer fits in the round',
   'wants ' + doomLeft.toFixed(1) + 's, round has ' + doomRem.toFixed(1) + 's');
ok(doomGhost === true, 'it stops being able to bite on the way out');
ok(goneAt > 0 && goneAt - doomAt <= K.HEART_LEAVE + 0.05,
   'and it is gone within ' + K.HEART_LEAVE + 's of saying so',
   (goneAt - doomAt).toFixed(2) + 's');
ok(goneAt < rlen - 0.05, 'before the bell rather than at it',
   goneAt.toFixed(1) + 's of a ' + rlen.toFixed(1) + 's round');
ok(gone === null, 'and it leaves with the round, taken or not', 'heart=' + gone);

// the heart is still missing, so the next round is a fresh chance at it
let waited = 0;
while (waited < 60 * 25 && window.__peek().heart === null) { drift(1); waited++; }
const fresh = window.__peek();
ok(!!fresh.heart && fresh.roundNo === was + 1, 'the next round offers a new one',
   'round ' + fresh.roundNo + ' at ' + fresh.roundT.toFixed(1) + 's');
ok(fresh.lives === K.MAX_LIVES - 1, 'which it only does because a heart is still missing',
   'lives=' + fresh.lives);
finish();
""", (NOAMB, NOEVENT, rounds(10, 10), NOIDLE) + heart(1.0, 3)))

TESTS.append(("tracking", """
begin();
step(30, [450, 300]);
window.__clear();
// Nothing sits between the pointer and the marker any more: no offset to hold, no
// offset to close, nothing to drift by. It should be exact on every single frame.
let x = 300, worst = 0;
for (let i = 0; i < 90; i++) {
  x += 3;
  step(1, [x, 300]);
  const p = window.__peek();
  worst = Math.max(worst, Math.hypot(p.px - p.cx, p.py - p.cy));
}
ok(worst < 1e-9, 'the marker sits exactly on the pointer, every frame',
   'worst offset ' + worst.toExponential(1) + 'px');
const end = window.__peek();
ok(Math.abs(end.cx - x) < 0.01, 'and the pointer maps 1:1 to arena space',
   'cursor=' + end.cx.toFixed(3) + ' expected=' + x);
// Space used to warp. It must now do nothing at all — including nothing to lives.
const b = window.__peek();
window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space', bubbles: true }));
step(2, [x, 300]);
const a = window.__peek();
ok(Math.abs(a.px - b.px) < 1e-9 && Math.abs(a.py - b.py) < 1e-9 && a.lives === b.lives,
   'Space no longer moves the marker', a.px.toFixed(1) + ',' + a.py.toFixed(1));
// The arena edge pins the marker rather than losing it off the side.
put(1400, -200); step(1);
const oob = window.__peek();
ok(oob.px === 900 && oob.py === 0, 'the marker is clamped to the arena',
   oob.px + ',' + oob.py);
finish();
""", (LIVES, NOAMB, NOEVENT, NOIDLE)))

# The prize cube's commentary, in two halves: the writing and the machinery here, the
# moments that set it off in the scenario below.
TESTS.append(("quips", """
begin();
// Round one opens with a line of its own, so wait it out before testing the manners.
const opener = window.__peek();
ok(opener.quip === 'firstround', 'the cube opens the game with a remark',
   opener.quip + ': ' + opener.quipText);
// This scenario measures the machinery, not the moments, so the payout is parked in a
// corner well outside its band for the whole of it. Left to wander, the prize cube
// drifts through the marker and away again while the manners below are being timed,
// which builds a multiplier and then mourns it — a moment of its own arriving in the
// middle of the measurement, and the reason this section used to pass or fail on where
// the cube happened to go.
window.__pin(60, 60);
for (let i = 0; i < 60 * 8; i++) {
  step(1, [450 + (i % 2) * 5, 300]);
  const p = window.__peek();
  if (p.quip === null && p.quipGap <= 0) break;
}
ok(window.__peek().quip === null, 'then falls quiet', String(window.__peek().quip));
const Q = window.__quips();
const sets = Q.sets, keys = Q.keys;

// --- the writing ---
// The whole point of the feature is that you cannot hear it all in one sitting, so a
// thin set is a bug: fewer than eight lines and a moment starts repeating inside a
// single game.
const thin = keys.filter(k => sets[k].length < 8);
ok(thin.length === 0, 'every moment has at least eight lines to draw from',
   thin.length ? thin.join(',') : keys.length + ' moments, ' +
   keys.reduce((n, k) => n + sets[k].length, 0) + ' lines');
const all = [].concat(...keys.map(k => sets[k]));
const dupes = all.filter((l, i) => all.indexOf(l) !== i);
ok(dupes.length === 0, 'and no line is written twice anywhere in the table',
   dupes.length ? dupes[0] : all.length + ' distinct lines');
const blank = all.filter(l => !l || !l.trim());
ok(blank.length === 0, 'no blank lines', blank.length + ' blank');
// A bubble is drawn on one line and clamped to the arena, so a very long remark would
// be squeezed unreadable rather than wrapped.
const longest = all.reduce((a, b) => b.length > a.length ? b : a);
ok(longest.length <= 56, 'and every line is short enough for a one-line bubble',
   longest.length + ' chars: ' + longest);

// --- the bag ---
// Dealing is a shuffled bag, not a random pick: three full passes must each be a
// permutation of the set, so every line is heard before any of them comes round again.
const key = 'flinch', n = sets[key].length;
const drawn = [];
for (let i = 0; i < n * 3; i++) drawn.push(window.__deal(key));
let good = 0;
for (let pass = 0; pass < 3; pass++) {
  const slice = drawn.slice(pass * n, pass * n + n);
  if (new Set(slice).size === n) good++;
}
ok(good === 3, 'a moment deals every line it has before repeating any of them',
   good + '/3 clean passes of ' + n);
const back = drawn.filter((l, i) => i && l === drawn[i - 1]);
ok(back.length === 0, 'and never says the same line twice in a row across a reshuffle',
   back.length ? back[0] : 'clean over ' + drawn.length + ' deals');
ok(window.__quips().bag[key] === 0, 'the bag empties as it deals',
   'left=' + window.__quips().bag[key]);

// --- the manners ---
// One line on screen at a time, and a less important moment waits rather than talking
// over a more important one. `flinch` is the quietest moment there is; `crash` the loudest.
window.__say('flinch');
const q1 = window.__peek();
ok(q1.quip === 'flinch', 'a moment puts its line on screen', q1.quipText);
window.__say('nearmiss');
ok(window.__peek().quip === 'flinch', 'another quiet moment does not talk over it',
   window.__peek().quip);
window.__say('crash');
const q2 = window.__peek();
ok(q2.quip === 'crash', 'but a loud one does', q2.quipText);
// …and a moment that has just spoken holds its tongue for its own cooldown, which is
// what stops a wall round being narrated wall by wall.
step(Math.round(60 * (K.QUIP_TIME + K.QUIP_GAP + 0.2)), [452, 307]);
ok(window.__peek().quip === null, 'a line clears itself after its time is up',
   'after ' + (K.QUIP_TIME + K.QUIP_GAP + 0.2).toFixed(1) + 's');
window.__say('flinch');
ok(window.__peek().quip === null, 'and its moment stays quiet for the whole cooldown',
   'cool=' + Q.cools[key] + 's');
// The quiet after a line is short, and only a loud moment may break it.
window.__say('crash');
const q3 = window.__peek();
ok(q3.quip === 'crash' && q3.quipText !== q2.quipText,
   'a loud moment speaks again with a different line', q3.quipText);
finish();
""", (LIVES, NOAMB, NOEVENT, NOIDLE, NOHEART)))

TESTS.append(("quip-moments", """
begin();
// Crashing into the prize cube: the one thing in the arena that pays you, and the cube
// has a word to say about being flown into. Loud enough to interrupt round one's opener.
window.__pin(450, 270);
step(4, [450, 270 + K.ORB_SIZE / 2]);
const crash = window.__peek();
ok(crash.quip === 'crash', 'flying into the prize cube gets its own remark',
   crash.quip + ': ' + crash.quipText);
ok(window.__peek().lives === K.MAX_LIVES - 1, 'and it really did cost a heart',
   'lives=' + window.__peek().lives);

// Now the multiplier: hug the cube up to the cap, which is remarked on once, then walk
// away and let the streak die, which is remarked on again. Five pixels of jiggle every
// other frame, so none of this also reads as a motionless marker.
window.__pin(200, 200);
const hug = [200 + K.ORB_SIZE / 2, 200 + K.ORB_SIZE + K.PLAYER_R + 1];
step(Math.round(60 * (K.INVULN + 0.2)), hug);
let maxed = null, maxT = 0;
for (let i = 0; i < 60 * 30 && !maxed; i++) {
  step(1, [hug[0] + (i % 2) * 5, hug[1]]);
  maxT += 1 / 60;
  const p = window.__peek();
  if (p.quip === 'maxed') maxed = p;
}
ok(maxed, 'reaching the top multiplier is remarked on',
   maxed ? maxed.quipText : 'never said in 30s');
ok(window.__peek().mult >= K.MULT_MAX - 1e-6, 'and the cap really was reached',
   'mult=' + window.__peek().mult.toFixed(2) + ' after ' + maxT.toFixed(1) + 's');
// …and it is said once per visit to the cap, not every frame it is held there.
let again = 0;
for (let i = 0; i < 120; i++) {
  step(1, [hug[0] + (i % 2) * 5, hug[1]]);
  if (window.__peek().quip === 'maxed' && window.__peek().quipText !== maxed.quipText) again++;
}
ok(again === 0, 'once per visit, not every frame it is held there', again + ' repeats in 2s');

let lost = null;
for (let i = 0; i < 60 * 12 && !lost; i++) {
  step(1, [820 + (i % 2) * 5, 520]);            // walk away and let it burn down
  const p = window.__peek();
  if (p.quip === 'multlost') lost = p;
}
ok(lost, 'losing a multiplier worth having is mourned',
   lost ? lost.quipText : 'never said in 12s');

// Standing perfectly still long enough to look asleep. No jiggle at all, this time.
let still = null;
for (let i = 0; i < 60 * (K.QUIP_STILL * 2 + 4) && !still; i++) {
  step(1, [300, 500]);
  if (window.__peek().quip === 'stalled') still = window.__peek();
}
ok(still, 'a motionless marker is noticed', still ? still.quipText : 'never said');

// Muting the music, which the cube takes personally. Said quietly, so it has to wait
// for a properly clear mouth: an unfinished line or even the breath after one blocks it.
for (let i = 0; i < 60 * 30; i++) {
  step(1, [300 + (i % 2) * 5, 500]);
  const p = window.__peek();
  if (p.quip === null && p.quipGap <= 0) break;
}
document.getElementById('muteBtn').click();
const mute = window.__peek();
ok(mute.quip === 'muted', 'so is muting the music', mute.quip + ': ' + mute.quipText);
document.getElementById('muteBtn').click();     // put it back for the next run
finish();
""", (LIVES, NOAMB, NOEVENT, NOIDLE, NOHEART)))

# The moments that need a round of a known shape around them: the finish line, the boss
# either side of it, and a rescue cube that does and does not get banked.
TESTS.append(("quip-rounds", """
begin();
let soon = null, down = null, next = null;
for (let i = 0; i < 60 * 300; i++) {
  step(1, [450 + (i % 2), 300]);
  const p = window.__peek();
  if (p.quip === 'roundend' && !soon) soon = p;
  if (p.quip === 'bossnext' && !next) next = p;
  if (p.quip === 'bossdown' && !down) down = p;
  if (p.state !== 1 || (soon && next && down)) break;
}
ok(soon, 'the last seconds of a round are remarked on', soon ? soon.quipText : 'never said');
ok(next, 'a boss coming up is announced by the cube too', next ? next.quipText : 'never said');
ok(down, 'and so is surviving one', down ? down.quipText : 'never said');
finish();
""", (LIVES, NOAMB, NOEVENT, NOIDLE, NOHEART, rounds(8, 8)) + breather(0.3)))

TESTS.append(("quip-rescue", """
begin();
step(30, [450, 300]);
window.__pin(120, 120);                          // the prize cube parked out of the way
window.__hurt();                                 // …so a rescue is on offer
step(Math.round(60 * (K.HEART_DELAY + 0.5)), [700, 480]);
const h = window.__peek().heart;
ok(h, 'a rescue cube is out', h ? 'left=' + h[4].toFixed(1) : 'none');
// Stand beside it until the count runs out. The cube wanders, so the marker chases it.
let banked = null;
for (let i = 0; i < 60 * 30 && !banked; i++) {
  const p = window.__peek();
  if (p.heart) put(p.heart[0] + p.heart[2] / 2, p.heart[1] + p.heart[3] + K.PLAYER_R + 2);
  step(1);
  const q = window.__peek();
  if (q.quip === 'rescued') banked = q;
}
ok(banked, 'banking a rescue is remarked on', banked ? banked.quipText : 'never said');
finish();
""", (LIVES, NOAMB, NOEVENT, NOIDLE) + heart(1.0, 3)))

# --- the hunter's boss rounds ---
# The siege is not scored and does not end on a clock, so it is also the one round a
# test can get stuck in: everything below drives it the way it is meant to be played,
# and the checks are what the round promises. Every fifth round is a boss slot and the
# siege is what a hunter does in one, so the way to put one at round 1 is to make every
# round a boss slot — which also leaves it at cycle 1, the gentler of its two settings.
SLOT = ("const BOSS_EVERY  = 5;", "const BOSS_EVERY  = 1;")
ONLY1 = ("const MAX_ROUNDS  = 20;", "const MAX_ROUNDS  = 1;")

TESTS.append(("siege", """
begin();
// Just beyond the decoy, on the far side from the boss: inside the band that wears the
// shield down, and with the decoy lined up to catch whatever the boss aims at the
// marker. One position does both halves of the round, which is the point of it.
function bait(p, at) {
  const d = p.siege.decoy, b = p.siege.boss;
  const m = Math.hypot(d[0] - b[0], d[1] - b[1]) || 1;
  return [Math.min(880, Math.max(20, d[0] + (d[0] - b[0]) / m * at)),
          Math.min(580, Math.max(20, d[1] + (d[1] - b[1]) / m * at))];
}
const FAR    = p => [26, 574];                    // a corner, out of every band there is
const NEAR   = p => p.siege && p.siege.decoy ? bait(p, 95) : [450, 300];
const BESIDE = p => p.siege ? [Math.min(880, p.siege.boss[0] + 100), p.siege.boss[1]] : [450, 300];

// What the run is watched for along the way: the order the cube said things in, every
// life the boss lost and whether the decoy's shield was open when it went, the first
// shield ever seen to open, and whether one was ever seen eating a shot.
const said = [];
let decoys = 0, out = false, lives = null, drops = [], shieldWas = null;
let ate = 0, opened = null;
function watch(p) {
  if (p.quip && said[said.length - 1] !== p.quip) said.push(p.quip);
  if (!p.siege) return;
  const d = p.siege.decoy, live = d && p.siege.decoyPh === 'live';
  if (lives === null) lives = p.siege.lives;
  // The life is taken in the same frame the decoy is folded away, so what matters is
  // the shield it had the frame before.
  if (p.siege.lives < lives) { drops.push(shieldWas); lives = p.siege.lives; }
  if (live) {
    if (!out) { out = true; decoys++; }
    if (d[4] > 0 && d[2] > 0) ate++;               // flashing with the shield still up
    if (d[2] === 0 && !opened) opened = { t: p.roundT, crack: d[3] };
  } else out = false;
  shieldWas = live ? d[2] : shieldWas;
}
// Frames, with the marker steered somewhere each one, until a condition holds.
function drive(n, where, until) {
  for (let i = 0; i < n; i++) {
    const p = window.__peek();
    watch(p);
    if (p.state !== 1 || (until && until(p))) return p;
    step(1, where(p));
  }
  return window.__peek();
}

// --- it opens by taking the cube ---
let p = drive(60 * 8, BESIDE, p => p.siege && p.orbCaged);
ok(p.siege, 'a hunter boss round deals a body with lives instead of a clock',
   p.ev + ', ' + (p.siege ? p.siege.lives + ' lives' : 'no finale'));
ok(p.orbCaged, 'and it swallows the prize cube', 'caged=' + p.orbCaged);
ok(p.roundLen > 1e6, 'in a round given a length it cannot reach', p.roundLen.toExponential(1));
ok(p.quip === 'caught', 'and the cube says so on the frame it happens',
   said.join(' '));
p = drive(60, BESIDE);                             // the grab pulls it in
const oc = [p.orb[0] + p.orb[2] / 2, p.orb[1] + p.orb[3] / 2];
const off = Math.hypot(oc[0] - p.siege.boss[0], oc[1] - p.siege.boss[1]);
ok(off < K.HUNT_SIZE / 2 - K.ORB_SIZE / 2,
   'which is then where it stays, inside the body', off.toFixed(1) + 'px off centre');

// --- and nothing is worth anything until it is out ---
// The marker parks 100px off the body, well inside the payout band the cube would be
// publishing if it were loose.
const s0 = p.score;
p = drive(60 * 3, BESIDE);
ok(p.score === s0, 'no points are scored while it is in there',
   'score ' + s0.toFixed(2) + ' -> ' + p.score.toFixed(2));
ok(p.mult === 0, 'the multiplier is pinned at zero', 'x' + p.mult);
ok(p.prox === 0, 'and standing on the thing holding it counts for nothing',
   'prox=' + p.prox.toFixed(2));
window.__hurt();
p = drive(30, BESIDE);
ok(p.mult === 0, 'a hit cannot reset it to one either, there being no one to reset to',
   'x' + p.mult + ' on ' + p.lives + ' lives');
p = drive(60 * 4, BESIDE);
ok(p.hearts === 0, 'and no rescue cube is offered in a round with no clock to race it',
   p.hearts + ' out');

// --- the decoys ---
p = drive(60 * 16, FAR, p => !p.siege || (p.siege.decoy && p.siege.decoyPh === 'live'));
ok(p.siege && p.siege.decoy, 'a small one is sent out', 'at ' + p.roundT.toFixed(1) + 's');
ok(p.siege.decoy[8] === 1, 'held: a hunter body with its volley switched off', 'hold=1');
const fire0 = p.siege.decoy[5];
p = drive(60 * 3, FAR);
ok(p.siege.decoy && p.siege.decoy[5] === fire0 && p.siege.decoy[6] === 0 && p.siege.decoy[7] === 0,
   'it never fires, never charges, and never even runs its volley clock',
   'fireT ' + fire0.toFixed(2) + ' salvo ' + (p.siege.decoy ? p.siege.decoy[6] : '-') +
   ' charge ' + (p.siege.decoy ? p.siege.decoy[7] : '-'));
ok(p.siege.decoy && p.siege.decoy[3] === 0, 'and its shield holds while you keep away from it',
   'crack=' + (p.siege.decoy ? p.siege.decoy[3].toFixed(2) : 'gone'));
const t0 = p.roundT, unlock = p.siege.unlock;
ok(!p.siege.late && unlock === K.SIEGE_UNLOCK && p.siege.band === K.SIEGE_BAND,
   'the first of its two rounds is dealt the gentler pair of numbers',
   unlock + 's to open a shield, held inside ' + p.siege.band + 'px');
p = drive(60 * (unlock * 4 + 8), NEAR, p => !p.siege || opened);
ok(opened, 'standing with it wears the shield through',
   opened ? 'crack=' + opened.crack.toFixed(2) : 'still shut');
ok(opened && opened.t - t0 >= unlock - 0.2,
   'and no faster than the ' + unlock + 's that should take',
   opened ? (opened.t - t0).toFixed(1) + 's' : '-');

// --- three lives, one decoy each ---
p = drive(60 * 100, NEAR, p => !p.siege || p.siege.freed);
ok(p.siege && p.siege.freed, 'baiting the boss into its own decoys takes it apart',
   (p.siege ? p.siege.lives + ' lives left' : 'no finale') + ' after ' + decoys + ' decoys');
ok(drops.length === K.SIEGE_LIVES, 'one life per decoy, ' + K.SIEGE_LIVES + ' of them',
   drops.length + ' lives over ' + decoys + ' decoys');
ok(drops.every(sh => sh === 0), 'and never one with its shield still up',
   'shield at each: ' + drops.join(','));
ok(ate > 0, 'a shot landing on a shield that is still up is simply eaten',
   ate + ' frames of a shield flashing');
ok(!p.orbCaged, 'the third life lets the cube out', 'caged=' + p.orbCaged);
const hints = ['hintwait', 'hintfollow', 'hintclose', 'hintbait'];
ok(hints.every(h => said.indexOf(h) !== -1), 'the cube talked the player through all of it',
   hints.filter(h => said.indexOf(h) === -1).join(',') || said.length + ' things said');
ok(said.indexOf('bosshurt') !== -1 && said[said.length - 1] === 'freed',
   'and has the last word once it is out', said.slice(-3).join(' '));

// --- and the game goes on ---
// Parked in whichever corner the boss is furthest from, because it is still on the
// stage for the victory beat and a hit would zero the payout band on its own.
const cor = [[90, 90], [810, 90], [90, 510], [810, 510]].sort(
  (u, v) => Math.hypot(v[0] - p.siege.boss[0], v[1] - p.siege.boss[1]) -
            Math.hypot(u[0] - p.siege.boss[0], u[1] - p.siege.boss[1]))[0];
window.__pin(cor[0], cor[1]);
const s1 = p.score;
let topProx = 0, topMult = 0;
p = drive(60 * 3, q => {
  topProx = Math.max(topProx, q.prox);
  topMult = Math.max(topMult, q.mult);
  return [cor[0] + 17 + (cor[0] < 450 ? 30 : -30), cor[1] + 17];
});
ok(p.score > s1 && topMult >= 1, 'the ledger opens again the moment it is loose',
   'x' + topMult.toFixed(2) + ', +' + (p.score - s1).toFixed(0) + ' points');
ok(topProx > 0, 'and the cube is worth standing next to again', 'prox up to ' + topProx.toFixed(2));
p = drive(60 * 12, p => [450, 300], p => p.state !== 1);
ok(p.state === 2 && p.cleared, 'then the round ends of its own accord, cleared',
   'state=' + p.state + ' cleared=' + p.cleared);
finish();
""", (LIVES, SLOT, ONLY1, only('hunter')) + heart(1.0, 3)))


fails = 0
want = sys.argv[1:]        # optional: run only the scenarios whose name matches
for name, body, tweaks in TESTS:
    if want and not any(w in name for w in want):
        continue
    print("== " + name)
    lines = run(name, body, tweaks)
    if not lines:
        print("   FAIL no output from the page")
        fails += 1
        continue
    for l in lines:
        print("   " + l)
        if l.startswith("FAIL"):
            fails += 1
print("\n%s" % ("all green" if not fails else "%d failing check(s)" % fails))
sys.exit(1 if fails else 0)
