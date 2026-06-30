// Standalone node harness for cdg.js — built synthetic packets, assert pixels.
// Exits 0 on success, 1 on failure (with a diagnostic on stderr).
const path = require('path');
const { CDGPlayer } = require(path.join(__dirname, '..', '..', 'static', 'cdg.js'));

function pkt(inst, data) {
  const b = new Uint8Array(24);
  b[0] = 0x09;
  b[1] = inst;
  for (let i = 0; i < 16; i++) b[4 + i] = data[i] || 0;
  return b;
}

function fail(msg) { console.error('FAIL: ' + msg); process.exit(1); }

// Load CLUT lo: color0 = black (0,0,0), color1 = white (15,15,15 -> 0x3F,0x3F).
const clutLo = new Array(16).fill(0);
clutLo[0] = 0; clutLo[1] = 0;        // color0 black
clutLo[2] = 0x3F; clutLo[3] = 0x3F;  // color1 white

const p1 = pkt(30, clutLo);          // load clut lo
const mp = pkt(1, [0, 0]);           // memory preset to color0 (black)
// Tile block at row0/col0: c0=0, c1=1, top pixel-row all set (0x3F).
const tb = pkt(6, [0, 1, 0, 0, 0x3F, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]);

const all = [];
[p1, mp, tb].forEach((p) => { for (let i = 0; i < 24; i++) all.push(p[i]); });
const buf = Uint8Array.from(all);

const pl = new CDGPlayer(null);
pl.load(buf);
pl.renderAt(1.0);  // applies all 3 packets (indices 0,1,2 all <= floor(1.0*300))

const [r, g, b] = pl.getPixelRGBA(0, 0);     // top-left: color1 white
if (!(r === 255 && g === 255 && b === 255)) fail('pixel(0,0) expected white, got ' + [r, g, b]);

const bg = pl.getPixelRGBA(0, 11);            // below the set row: color0 black
if (bg[0] !== 0 || bg[1] !== 0 || bg[2] !== 0) fail('pixel(0,11) expected black, got ' + bg);

// CLUT decode sanity for white.
if (pl.clut[1][0] !== 255 || pl.clut[1][1] !== 255 || pl.clut[1][2] !== 255) {
  fail('clut[1] expected white, got ' + pl.clut[1]);
}

// Backward seek replays from scratch (no crash, buffer reset).
pl.renderAt(0.0);
if (pl.lastIdx !== 0) fail('after seek to 0, lastIdx expected 0, got ' + pl.lastIdx);

console.log('OK');
