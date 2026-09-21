"""Headless smoke test for index.html: play a whole twenty-round game.

Builds an instrumented copy of the game, drives it with a fake clock and
synthetic mouse input inside headless Edge, and prints what happened round by
round. Rounds are shortened so the run stays a couple of minutes; every round
type and every difficulty cycle is still played.

Run:  py tools/smoketest.py
"""
import os, re, sys, tempfile

from _edge import dump

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()

# --- instrument: shorter rounds, endless lives, and a debug peephole ---
# The rescue cube is put on a shorter fuse to match the shortened rounds — at its
# shipped 6 s + 9 s it would never fit in one and the path would go unplayed.
game = html
for a, b in (("const ROUND_MIN   = 20, ROUND_MAX = 30;", "const ROUND_MIN   = 11, ROUND_MAX = 14;"),
             ("const MAX_LIVES = 3;", "const MAX_LIVES = 999;"),
             ("const HEART_DELAY = 6.0;", "const HEART_DELAY = 2.0;"),
             ("const HEART_COUNT = 9;", "const HEART_COUNT = 4;"),
             ("const USE_POINTER_LOCK = true;", "const USE_POINTER_LOCK = false;")):
    assert a in game, a
    game = game.replace(a, b)

peep = """
  window.__peek = () => ({
    state, score, elapsed, lives, mult, bestMult, prox, graceT,
    cubes: cubes.length, orbs: cubes.filter(c => c.orb).length,
    hearts: cubes.filter(c => c.heart).length,
    heart: heartCube ? [heartCube.x + heartCube.w / 2, heartCube.y + heartCube.h / 2] : null,
    roundNo, roundT, roundLen, live, waveNo, cleared,
    siege: siege ? {
      lives: siege.lives, freed: siege.freed, caged: !!(orb && orb.caged),
      decoy: siege.decoy && siege.decoy.ph === 'live'
             ? [siege.decoy.pos.x, siege.decoy.pos.y, siege.decoy.shield, siege.decoy.crack] : null,
      boss: [siege.boss.pos.x, siege.boss.pos.y]
    } : null,
    ev: activeEvent && activeEvent.name, tier: tier(),
    boss: isBoss(roundNo), sched: schedule.join(','),
    names: ROUNDS.map(r => r.name).join(','), bossEvery: BOSS_EVERY, maxRounds: MAX_ROUNDS,
    tour: ROUNDS.filter(r => !r.boss).map(r => r.name).join(','), rota: BOSS_ROTA.join(',')
  });
"""
game = game.replace("  // ---------- boot ----------", peep + "\n  // ---------- boot ----------")
assert "__peek" in game

prelude = """
<script>
window.__errors = [];
window.onerror = (m, s, l, c) => { window.__errors.push(m + ' @' + l + ':' + c); };
window.__q = [];
window.requestAnimationFrame = cb => { window.__q.push(cb); return window.__q.length; };
</script>
"""
game = game.replace("<script>\n(() => {", prelude + "<script>\n(() => {", 1)

driver = """
<div id="testlog" style="display:none"></div>
<script>
(() => {
  const log = [];
  const say = s => log.push(s);
  const TAG = 'RESULT' + '>>';
  const rounds = [];          // one row per round played
  let t = 0, deaths = 0, lastLives = null, peakCubes = 0, orbBad = 0;
  let saved = 0, offered = 0, heartBad = 0, sawHeart = false;
  let bait0 = false;            // a decoy was on the stage last frame
  const sieges = [];            // one row per hunter boss round, as it was fought
  let sg = null;

  function step(ms) {
    t += ms;
    const q = window.__q.splice(0, window.__q.length);
    for (const cb of q) { try { cb(t); } catch (e) { window.__errors.push('frame: ' + e.message); } }
  }

  const canvas = document.getElementById('game');
  const r = canvas.getBoundingClientRect();
  function mouse(x, y) {
    canvas.dispatchEvent(new MouseEvent('mousemove', {
      clientX: r.left + x * (r.width / 900), clientY: r.top + y * (r.height / 600), bubbles: true
    }));
  }

  step(0);                                   // let the boot frame land
  document.getElementById('startBtn').click();

  const FRAMES = 60 * 700;                   // ample for twenty shortened rounds, two
                                             // of which run until their boss is beaten
  let row = null, i = 0, aim = null, bait = null;
  for (; i < FRAMES; i++) {
    const a = i * 0.031;
    // A siege has to be played, not survived: it has no clock, so a sweep that
    // never wears a decoy's shield down never ends the round. One target does both
    // halves of the job — a point just past the decoy on the far side from the boss,
    // which is inside the band that opens the shield *and* lines the decoy up to
    // catch the volley the boss aims at the marker.
    // A rescue cube on offer is likewise worth breaking the sweep for: parking beside
    // it is the only way this test ever exercises winning a heart back. The marker
    // goes to whichever face points at the middle of the arena, so the edge can never
    // shove it into the cube.
    if (bait) mouse(bait[0], bait[1]);
    else if (aim) mouse(aim[0] + (aim[0] > 450 ? -47 : 47), aim[1]);
    else     mouse(450 + Math.cos(a) * 300, 300 + Math.sin(a * 1.3) * 200);
    step(1000 / 60);
    const p = window.__peek();
    aim = p.heart;
    bait = null;
    if (p.siege) {
      if (!sg || sg.n !== p.roundNo) {
        sg = { n: p.roundNo, lives: p.siege.lives, decoys: 0, freed: false, held: 0 };
        sieges.push(sg);
      }
      sg.lives = Math.min(sg.lives, p.siege.lives);
      if (p.siege.caged) sg.held++;
      if (p.siege.freed) sg.freed = true;
      if (p.siege.decoy) {
        if (!bait0) { bait0 = true; sg.decoys++; }
        const d = p.siege.decoy, b = p.siege.boss;
        const m = Math.hypot(d[0] - b[0], d[1] - b[1]) || 1;
        bait = [Math.min(880, Math.max(20, d[0] + (d[0] - b[0]) / m * 95)),
                Math.min(580, Math.max(20, d[1] + (d[1] - b[1]) / m * 95))];
      } else bait0 = false;
    }
    if (p.state !== 1) break;
    if (!row || row.n !== p.roundNo) {
      row = { n: p.roundNo, tier: p.tier, boss: p.boss, ev: p.ev, t0: p.elapsed,
              dur: 0, waves: 0, cubes: 0, hits: 0, saved: 0 };
      rounds.push(row);
    }
    if (p.ev) row.ev = p.ev;
    row.dur = p.elapsed - row.t0;
    row.waves = p.waveNo;
    row.cubes = Math.max(row.cubes, p.cubes);
    peakCubes = Math.max(peakCubes, p.cubes);
    if (p.orbs !== 1) orbBad++;
    if (p.hearts > 1) heartBad++;                    // never more than one on offer
    if (p.hearts && !sawHeart) offered++;
    sawHeart = p.hearts > 0;
    if (lastLives !== null && p.lives < lastLives) { deaths += lastLives - p.lives; row.hits += lastLives - p.lives; }
    if (lastLives !== null && p.lives > lastLives) { saved += p.lives - lastLives; row.saved += p.lives - lastLives; }
    lastLives = p.lives;
    if (!isFinite(p.score) || p.score < 0) { window.__errors.push('bad score ' + p.score); break; }
    // …and zero is in range in a siege round, where the ledger is shut on purpose.
    const multLo = p.siege && !p.siege.freed ? 0 : 1;
    if (p.mult < multLo || p.mult > 12.001) { window.__errors.push('mult out of range ' + p.mult); break; }
    if (p.cubes > 400) { window.__errors.push('cube leak: ' + p.cubes); break; }
  }

  const p = window.__peek();
  for (const q of rounds) {
    say('round ' + String(q.n).padStart(2) + '  c' + (q.tier + 1) + (q.boss ? '  BOSS ' : '       ') + q.ev.padEnd(8) +
        q.dur.toFixed(1).padStart(6) + 's  ' + String(q.waves).padStart(2) + ' waves  ' +
        String(q.cubes).padStart(3) + ' cubes  ' + q.hits + ' hits  ' +
        (q.saved ? '+' + q.saved + ' saved' : ''));
  }
  say('schedule     ' + p.sched.split(',').map(
    (n, k) => ((k + 1) % p.bossEvery ? '' : '*') + n).join(' '));
  say('played       ' + rounds.length + ' rounds in ' + p.elapsed.toFixed(1) + 's  (' + i + ' frames)');
  say('score        ' + Math.round(p.score) + '   peak mult x' + p.bestMult.toFixed(2));
  say('hits taken   ' + deaths + '   peak cubes ' + peakCubes);
  say('prize cube   ' + (orbBad ? 'MISSING on ' + orbBad + ' frames' : 'present on every frame'));
  say('rescues      ' + offered + ' offered, ' + saved + ' banked' +
      (heartBad ? '   DOUBLED UP on ' + heartBad + ' frames' : ''));
  for (const g of sieges)
    say('siege        round ' + String(g.n).padStart(2) + '  ' + g.decoys +
        ' decoys, boss down to ' + g.lives + ', cube ' +
        (g.freed ? 'saved' : 'still in there') +
        ', held for ' + (g.held / 60).toFixed(1) + 's');
  say('cleared      ' + p.cleared + '   final state ' + p.state);
  say('HUD          score=' + document.getElementById('score').textContent +
      ' time=' + document.getElementById('timer').textContent +
      ' round=' + document.getElementById('round').textContent +
      ' lives=' + document.getElementById('lives').textContent.length +
      ' best=' + document.getElementById('best').textContent);
  say('over screen  ' + document.getElementById('overTitle').textContent +
      ' | ' + document.getElementById('breakdown').textContent);
  say('board rows   ' + document.querySelectorAll('#boardList li').length);

  // restart path
  document.getElementById('againBtn').click();
  for (let k = 0; k < 120; k++) { mouse(450, 300); step(1000 / 60); }
  const q = window.__peek();
  say('after replay state=' + q.state + ' round=' + q.roundNo + ' elapsed=' + q.elapsed.toFixed(2) +
      ' lives=' + q.lives + ' mult=' + q.mult.toFixed(2) + ' orbs=' + q.orbs);

  // The schedule is a fixed tour of the ordinary rounds first, then a draw, with
  // every BOSS_EVERY-th round reserved for a boss — and the bosses themselves take
  // turns. All of it is checkable from what actually got played.
  const tour = p.tour.split(',');
  const plain = rounds.filter(q => !q.boss).map(q => q.ev);
  if (plain.slice(0, tour.length).join(',') !== tour.join(','))
    window.__errors.push('opened with ' + plain.slice(0, tour.length).join(',') +
                         ', not the ordinary rounds in order');
  const want = [];
  for (let n = p.bossEvery; n <= p.maxRounds; n += p.bossEvery) want.push(n);
  const rota = p.rota.split(',');
  const bossEv = rounds.filter(q => q.boss).map(q => q.ev);
  const wantEv = want.map((n, k) => rota[k % rota.length]);
  if (bossEv.join(',') !== wantEv.join(','))
    window.__errors.push('the bosses came as ' + bossEv.join(',') +
                         ', expected ' + wantEv.join(','));
  // A boss event never turns up outside its slot, which is what keeps the rota
  // honest: the box owning round 5 means nothing if it also filled round 3.
  const stray = rounds.filter(q => !q.boss && rota.includes(q.ev)).map(q => q.n);
  if (stray.length)
    window.__errors.push('a boss event played ordinary round ' + stray.join(','));
  const bossAt = rounds.filter(q => q.boss).map(q => q.n).join(',');
  if (bossAt !== want.join(','))
    window.__errors.push('boss rounds were ' + bossAt + ', expected ' + want.join(','));
  if (rounds.length !== p.maxRounds)
    window.__errors.push('played ' + rounds.length + ' rounds, expected ' + p.maxRounds);
  if (!p.cleared) window.__errors.push('the game did not finish as cleared');
  // A siege is the one round that cannot be waited out, so a run that cleared the game
  // must have actually taken both of them apart, a decoy at a time.
  const hunterAt = rounds.filter(q => q.ev === 'hunter').map(q => q.n).join(',');
  if (sieges.map(g => g.n).join(',') !== hunterAt)
    window.__errors.push('the hunter played ' + hunterAt + ' but laid siege on ' +
                         (sieges.map(g => g.n).join(',') || 'none'));
  for (const g of sieges) {
    if (g.lives !== 0 || !g.freed)
      window.__errors.push('round ' + g.n + ' ended with the boss on ' + g.lives +
                           ' lives and the cube ' + (g.freed ? 'out' : 'still held'));
    if (!g.decoys) window.__errors.push('round ' + g.n + ' sent out no decoys');
    if (g.held < 60) window.__errors.push('round ' + g.n + ' never held the prize cube for long');
  }
  if (orbBad) window.__errors.push('prize cube missing on ' + orbBad + ' frames');
  if (heartBad) window.__errors.push('two rescue cubes at once on ' + heartBad + ' frames');
  if (!offered) window.__errors.push('no rescue cube was ever offered');
  if (q.state !== 1 || q.roundNo !== 1) window.__errors.push('replay did not restart at round 1');
  say('ERRORS       ' + (window.__errors.length ? window.__errors.join(' | ') : 'none'));
  document.getElementById('testlog').textContent = TAG + log.join('\\n' + TAG);
})();
</script>
"""
game = game.replace("</body>", driver + "</body>", 1)

tmp = os.path.join(tempfile.gettempdir(), "cursor_dodge_test.html")
open(tmp, "w", encoding="utf-8").write(game)

out = dump(tmp, timeout=600)

lines = [l.strip() for l in re.findall(r"RESULT&gt;&gt;(.*)|RESULT>>(.*)", out) for l in l if l]
if not lines:
    print("no results — dump follows:\n", out[:3000])
    sys.exit(1)
print("\n".join(l[:-6].rstrip() if l.endswith("</div>") else l for l in lines))
sys.exit(1 if any("ERRORS" in l and "none" not in l for l in lines) else 0)
