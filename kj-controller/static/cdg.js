// cdg.js — minimal CD+G decoder/renderer for in-browser preview.
// CD+G is a 300-packet/sec stream of 24-byte packets. Only a handful of
// instruction types matter. We keep a 300x216 indexed-colour buffer and a
// 16-entry 12-bit CLUT, and paint to a <canvas>. Seeking is free: a backward
// jump resets the buffer and replays packets from the start.
// UMD: exposes window.CDGPlayer in the browser and module.exports in node.
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.CDGPlayer = factory().CDGPlayer;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  var W = 300, H = 216, TW = 6, TH = 12;
  var CDG_COMMAND = 0x09, MASK = 0x3F;

  function CDGPlayer(canvas) {
    this.canvas = canvas || null;
    this.ctx = canvas ? canvas.getContext('2d') : null;
    this.width = W;
    this.height = H;
    this.packets = [];
    this._initState();
  }

  CDGPlayer.prototype._initState = function () {
    this.pixels = new Uint8Array(W * H);                 // colour indices
    this.clut = [];
    for (var i = 0; i < 16; i++) this.clut.push([0, 0, 0]);
    this.lastIdx = -1;
    this.borderIndex = 0;
    if (this.ctx) this.image = this.ctx.createImageData(W, H);
  };

  CDGPlayer.prototype.reset = function () {
    this._initState();
  };

  CDGPlayer.prototype.load = function (bytes) {
    var n = Math.floor(bytes.length / 24);
    this.packets = new Array(n);
    for (var i = 0; i < n; i++) {
      this.packets[i] = bytes.subarray ? bytes.subarray(i * 24, i * 24 + 24)
                                       : bytes.slice(i * 24, i * 24 + 24);
    }
    this.reset();
  };

  CDGPlayer.prototype.renderAt = function (timeSeconds) {
    var target = Math.floor(timeSeconds * 300);
    if (target >= this.packets.length) target = this.packets.length - 1;
    if (target < this.lastIdx) this.reset();              // backward seek → replay
    for (var i = this.lastIdx + 1; i <= target; i++) this._apply(this.packets[i]);
    this.lastIdx = target;
    this._paint();
  };

  CDGPlayer.prototype._apply = function (p) {
    if (!p || (p[0] & MASK) !== CDG_COMMAND) return;
    var inst = p[1] & MASK;
    switch (inst) {
      case 1:  this._memoryPreset(p); break;   // Memory Preset
      case 2:  this.borderIndex = p[4] & 0x0F; break;  // Border Preset
      case 6:  this._tile(p, false); break;    // Tile Block (normal)
      case 38: this._tile(p, true); break;     // Tile Block (XOR)
      case 20: this._scroll(p, false); break;  // Scroll Preset
      case 24: this._scroll(p, true); break;   // Scroll Copy
      case 30: this._clut(p, 0); break;        // Load CLUT lo (0-7)
      case 31: this._clut(p, 8); break;        // Load CLUT hi (8-15)
      default: break;                          // 28 (transparency) ignored for preview
    }
  };

  CDGPlayer.prototype._memoryPreset = function (p) {
    var c = p[4] & 0x0F;
    this.pixels.fill(c);
    this.borderIndex = c;
  };

  CDGPlayer.prototype._clut = function (p, base) {
    for (var i = 0; i < 8; i++) {
      var hi = p[4 + i * 2] & MASK;
      var lo = p[4 + i * 2 + 1] & MASK;
      var r = (hi >> 2) & 0x0F;
      var g = ((hi & 0x03) << 2) | ((lo >> 4) & 0x03);
      var b = lo & 0x0F;
      this.clut[base + i] = [r * 17, g * 17, b * 17];     // 4-bit → 0..255
    }
  };

  CDGPlayer.prototype._tile = function (p, xor) {
    var c0 = p[4] & 0x0F, c1 = p[5] & 0x0F;
    var row = (p[6] & MASK) * TH, col = (p[7] & MASK) * TW;
    for (var y = 0; y < TH; y++) {
      var bits = p[8 + y] & MASK;
      for (var x = 0; x < TW; x++) {
        var on = (bits >> (5 - x)) & 1;
        var px = col + x, py = row + y;
        if (px < 0 || px >= W || py < 0 || py >= H) continue;
        var idx = py * W + px;
        if (xor) this.pixels[idx] = this.pixels[idx] ^ (on ? c1 : c0);
        else this.pixels[idx] = on ? c1 : c0;
      }
    }
  };

  CDGPlayer.prototype._scroll = function (p, copy) {
    var fill = p[4] & 0x0F;
    var hCmd = (p[5] & 0x30) >> 4, vCmd = (p[6] & 0x30) >> 4;
    var dx = hCmd === 1 ? TW : hCmd === 2 ? -TW : 0;
    var dy = vCmd === 1 ? TH : vCmd === 2 ? -TH : 0;
    if (dx === 0 && dy === 0) return;
    var src = this.pixels.slice();
    for (var y = 0; y < H; y++) {
      for (var x = 0; x < W; x++) {
        var sx = x - dx, sy = y - dy;
        var val;
        if (copy) {
          sx = (sx + W) % W; sy = (sy + H) % H;
          val = src[sy * W + sx];
        } else {
          val = (sx >= 0 && sx < W && sy >= 0 && sy < H) ? src[sy * W + sx] : fill;
        }
        this.pixels[y * W + x] = val;
      }
    }
  };

  CDGPlayer.prototype.getPixelRGBA = function (x, y) {
    var c = this.clut[this.pixels[y * W + x]] || [0, 0, 0];
    return [c[0], c[1], c[2], 255];
  };

  CDGPlayer.prototype._paint = function () {
    if (!this.ctx) return;
    var img = this.image, px = this.pixels, clut = this.clut;
    for (var i = 0; i < W * H; i++) {
      var c = clut[px[i]] || [0, 0, 0], o = i * 4;
      img.data[o] = c[0];
      img.data[o + 1] = c[1];
      img.data[o + 2] = c[2];
      img.data[o + 3] = 255;
    }
    this.ctx.putImageData(img, 0, 0);
  };

  return { CDGPlayer: CDGPlayer };
}));
