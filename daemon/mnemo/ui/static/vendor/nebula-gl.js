/* nebula-gl.js -- mnemo v4.6 custom WebGL graph renderer (regl).

   Purpose-built for mnemo's single Nebula view (NOT a general graph
   library; the v4.5 third-party renderer stack is fully removed).
   Design goals, all realised here:
     - crisp SDF point-sprite nodes (sharp at ANY zoom) + a restrained
       additive glow (the only flourish; the C1 dark+teal identity);
     - low-alpha straight edges so overlap DENSITY reveals structure;
     - a true opaque dark gl.clearColor theme (no CSS atmosphere -- the
       v4.5.x failure source is gone);
     - renders ONLY when dirty (camera / selection / hover / drag) ->
       an idle graph costs ZERO (the definitive lag fix, in our code);
     - O(1)-ish CPU grid picking (no per-frame cost);
     - a LabelProvider seam (2D-canvas overlay now; swappable to SDF
       text later with no call-site change).

   API:
     var h = NebulaGL.create(canvas, { nodes, edges, theme, labels });
       nodes: [{x,y,size,color:[r,g,b]}]   edges: [{s,t}] indices
       theme: { bg:[r,g,b,a], edge:[r,g,b,a], glow:Number }
       labels: optional NebulaGL.LabelProvider instance
     h.on('clickNode'|'clickStage'|'hoverNode', cb)
     h.setHighlight(SetOfIndices|null)  h.select(i|null)  h.hover(i|null)
     h.fit()  h.camera  h.destroy()
   NebulaGL.LabelProvider(canvasEl) -> { setLabels(items), clear() }
*/
(function (global) {
  'use strict';

  function flat(arr, f) {
    var o = [];
    for (var i = 0; i < arr.length; i++) {
      var v = f(arr[i]);
      o.push(v[0], v[1]);
    }
    return o;
  }

  // --- CPU uniform-grid spatial index for picking (world space) ----
  function buildPickIndex(nodes) {
    var minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.x < minx) minx = n.x;
      if (n.y < miny) miny = n.y;
      if (n.x > maxx) maxx = n.x;
      if (n.y > maxy) maxy = n.y;
    }
    if (!isFinite(minx)) { minx = miny = 0; maxx = maxy = 1; }
    var cells = Math.max(1, Math.floor(Math.sqrt(nodes.length)));
    var spanx = (maxx - minx) || 1, spany = (maxy - miny) || 1;
    var cw = spanx / cells, ch = spany / cells;
    var grid = {};
    function key(cx, cy) { return cx + ':' + cy; }
    for (var j = 0; j < nodes.length; j++) {
      var cx = Math.floor((nodes[j].x - minx) / cw);
      var cy = Math.floor((nodes[j].y - miny) / ch);
      var k = key(cx, cy);
      (grid[k] || (grid[k] = [])).push(j);
    }
    return {
      nearest: function (wx, wy, maxDist) {
        var cx = Math.floor((wx - minx) / cw);
        var cy = Math.floor((wy - miny) / ch);
        var best = -1, bd = maxDist * maxDist;
        for (var dx = -1; dx <= 1; dx++) {
          for (var dy = -1; dy <= 1; dy++) {
            var bucket = grid[key(cx + dx, cy + dy)];
            if (!bucket) continue;
            for (var b = 0; b < bucket.length; b++) {
              var id = bucket[b];
              var ex = nodes[id].x - wx, ey = nodes[id].y - wy;
              var d2 = ex * ex + ey * ey;
              if (d2 < bd) { bd = d2; best = id; }
            }
          }
        }
        return best;
      },
    };
  }

  function fitCamera(cam, nodes, vw, vh) {
    var minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.x < minx) minx = n.x;
      if (n.y < miny) miny = n.y;
      if (n.x > maxx) maxx = n.x;
      if (n.y > maxy) maxy = n.y;
    }
    if (!isFinite(minx)) { cam.x = 0; cam.y = 0; cam.zoom = 1; return; }
    cam.x = (minx + maxx) / 2;
    cam.y = (miny + maxy) / 2;
    var sx = (maxx - minx) || 1, sy = (maxy - miny) || 1;
    cam.zoom = 0.9 * Math.min(vw / sx, vh / sy);
    if (!(cam.zoom > 0) || !isFinite(cam.zoom)) cam.zoom = 1;
  }

  function makeLabelProvider(canvasEl) {
    var items = [];
    // The default label set is now EVERY node (the toggle must show
    // all names). Iterating + projecting all of them per frame is
    // cheap, but measuring/filling a pill for thousands of on-screen
    // ones is not -- so bound the number actually DRAWN per frame.
    // items arrive degree-sorted, so at the overview the budget is
    // spent on the most-connected nodes; zoomed into a region the
    // on-screen count is well under budget so every node there is
    // labelled. Keeps the perpetual loop smooth (the no-jank rule).
    var LABEL_BUDGET = 900;
    var api = {
      setLabels: function (list) { items = list || []; },
      clear: function () { items = []; },
      // called by the renderer each frame with the live camera + the
      // galactic rotation angle ``ga``. The label point MUST be
      // rotated by +ga exactly like the node vertex shader, or the
      // pill detaches from its (rotating) node -- the reported
      // "labels not working" after rotation landed.
      _render: function (cam, ga, vw, vh, dpr, on) {
        var ctx = canvasEl.getContext('2d');
        if (canvasEl.width !== vw || canvasEl.height !== vh) {
          canvasEl.width = vw;
          canvasEl.height = vh;
        }
        ctx.clearRect(0, 0, vw, vh);
        if (!on || !items.length) return;
        ctx.font = (12 * dpr) + 'px ui-sans-serif,system-ui,sans-serif';
        ctx.textBaseline = 'middle';
        var gs = Math.sin(ga), gc = Math.cos(ga);
        var drawn = 0;
        for (var i = 0; i < items.length; i++) {
          if (drawn >= LABEL_BUDGET) break;  // per-frame draw bound
          var it = items[i];
          var wx = it.x * gc - it.y * gs;  // same +ga the shader applies
          var wy = it.x * gs + it.y * gc;
          var sx = (wx - cam.x) * cam.zoom + vw / 2;
          var sy = -(wy - cam.y) * cam.zoom + vh / 2;
          if (sx < -200 || sx > vw + 200 || sy < -50 || sy > vh + 50) continue;
          drawn++;
          var pad = 5 * dpr;
          var tw = ctx.measureText(it.text).width;
          var bx = sx + 8 * dpr, by = sy - 9 * dpr;
          ctx.fillStyle = 'rgba(7,9,15,0.86)';
          ctx.beginPath();
          if (ctx.roundRect) {
            ctx.roundRect(bx - pad, by - pad, tw + pad * 2,
              18 * dpr + pad * 2, 5 * dpr);
            ctx.fill();
          } else {
            ctx.fillRect(bx - pad, by - pad, tw + pad * 2, 18 * dpr + pad * 2);
          }
          if (it.accent) {
            ctx.strokeStyle = it.accent;
            ctx.lineWidth = dpr;
            ctx.stroke();
          }
          ctx.fillStyle = '#e6edf3';
          ctx.fillText(it.text, bx, by + 9 * dpr);
        }
      },
      _canvas: canvasEl,
    };
    return api;
  }

  function create(canvas, opts) {
    opts = opts || {};
    var theme = opts.theme || {};
    var bg = theme.bg || [0.027, 0.035, 0.059, 1.0];
    var edgeColor = theme.edge || [0.36, 0.42, 0.55, 0.10];
    var glow = theme.glow == null ? 0.9 : theme.glow;
    var labels = opts.labels || null;
    var nodes = opts.nodes || [];
    var edges = opts.edges || [];
    var dpr = Math.min(global.devicePixelRatio || 1, 2);

    var regl = global.createREGL({
      canvas: canvas,
      attributes: { antialias: false, alpha: false, depth: false },
    });
    var cam = { x: 0, y: 0, zoom: 1 };
    var raf = 0, disposed = false, fitted = false;
    var easing = false;  // a camera fly is in progress -> the loop
                         // must keep rendering even while a node is
                         // focused (rotation frozen). Outside an ease,
                         // a focused/still graph IDLES (no perpetual
                         // re-render of the heavy scene == the
                         // post-click lag) until the next invalidate().
    var gA = 0;          // galactic rotation angle (radians)
    var gT = 0;          // last animation timestamp
    var edgesOn = true;  // edge/label visibility toggles (the
    var labelsOn = true; // reported dead toggle controls)
    var GOMEGA = 0.013;  // rad/s -- a full turn ~8 min: a gentle,
                         // never-distracting "the galaxy is alive"
                         // drift (NOT the v4.5.2 camera-fight loop).
    var fitZoom = 1;     // the zoom fitCamera last produced (the
                         // whole-graph scale) -- select() must stay
                         // anchored to THIS, never a hard 0.5 floor
                         // (0.5 on a fit~0.07 graph = fly 7x into the
                         // void = the "click -> black" report).
    var hoverId = -1, selId = -1, dragId = -1;
    var hl = null; // Set | null
    var cbs = {};

    var posArr = new Float32Array(flat(nodes, function (d) {
      return [d.x, d.y];
    }));
    var colArr = new Float32Array((function () {
      var o = [];
      for (var i = 0; i < nodes.length; i++) {
        var c = nodes[i].color || [0.5, 0.8, 0.85];
        o.push(c[0], c[1], c[2]);
      }
      return o;
    })());
    var sizArr = new Float32Array(nodes.map(function (d) {
      return d.size || 4;
    }));
    // galaxy radius (max node distance from world origin; the layout
    // centres the disc at 0). Drives the galactic radial palette +
    // the radius-falloff brightness/bloom -> the bulge.
    var worldR = 1.0;
    for (var _w = 0; _w < nodes.length; _w++) {
      var _d = Math.hypot(nodes[_w].x || 0, nodes[_w].y || 0);
      if (_d > worldR) worldR = _d;
    }
    var posBuf = regl.buffer({ usage: 'dynamic', data: posArr });
    var colBuf = regl.buffer(colArr);
    var sizBuf = regl.buffer(sizArr);
    // per-node focus weight: 1 = lit, 0 = dimmed to dust. Drives the
    // "spotlight the selection + neighbours, blur everything else"
    // behaviour (the reported broken node-focus).
    var hlArr = new Float32Array(nodes.length);
    for (var _h = 0; _h < hlArr.length; _h++) hlArr[_h] = 1.0;
    var hlBuf = regl.buffer({ usage: 'dynamic', data: hlArr });
    var hlActive = false;  // is a focus/filter set in effect?
    // adjacency (node -> Set of neighbour indices) so a click can
    // spotlight the node + its neighbours with zero graph.html help.
    var adj = (function () {
      var a = [];
      for (var i = 0; i < nodes.length; i++) a.push(null);
      for (var e = 0; e < edges.length; e++) {
        var s = edges[e].s, t = edges[e].t;
        (a[s] || (a[s] = {}))[t] = 1;
        (a[t] || (a[t] = {}))[s] = 1;
      }
      return a;
    })();
    function applyHL(setObj) {
      // setObj: a JS Set/obj of lit indices, or null = all lit.
      hlActive = !!setObj;
      for (var i = 0; i < hlArr.length; i++) {
        hlArr[i] = (!setObj || (setObj.has ? setObj.has(i) : setObj[i]))
          ? 1.0 : 0.0;
      }
      hlBuf({ data: hlArr });
    }
    function focusSet(i) {
      // selected node + its direct neighbours = the lit set.
      var s = new Set();
      s.add(i);
      var nb = adj[i];
      if (nb) for (var k in nb) s.add(+k);
      return s;
    }

    // --- edges: ONE non-instanced LINES draw, 2 vertices per edge.
    // The prior per-instance setup had no per-vertex attribute stream
    // and rendered nothing. A flat per-vertex position buffer is the
    // robust, extension-free pattern with no instancing pitfalls.
    // Straight chords read as harsh pixelated wires. Tessellate each
    // edge into EDGE_SEG segments along a quadratic Bezier whose
    // control point bows the chord by a CONSISTENT-sign perpendicular
    // -> every edge curves the same handed way = a coherent swirl in
    // the galactic-rotation sense. With the graded low-alpha additive
    // blend the overlapping curves sum into smooth elegant filaments
    // that flow with the disc (the intended "silk"), never a straight
    // wire tangle. Shared by the base web AND the accent incident
    // pass so the accent overlays the SAME curve exactly.
    var EDGE_SEG = 10;
    var BOW = 0.16;
    function tessEdge(sx, sy, tx, ty, parr, larr, po, lo) {
      var dx = tx - sx, dy = ty - sy;
      var L = Math.sqrt(dx * dx + dy * dy) || 1e-6;
      var nx = -dy / L, ny = dx / L;            // unit perpendicular
      var cx = (sx + tx) * 0.5 + nx * L * BOW;  // bowed control point
      var cy = (sy + ty) * 0.5 + ny * L * BOW;
      var px = sx, py = sy;                     // u=0 == S
      for (var k = 0; k < EDGE_SEG; k++) {
        var u = (k + 1) / EDGE_SEG, iu = 1 - u;
        var qx = iu * iu * sx + 2 * iu * u * cx + u * u * tx;
        var qy = iu * iu * sy + 2 * iu * u * cy + u * u * ty;
        var o = po + k * 4, l = lo + k * 2;
        parr[o] = px; parr[o + 1] = py;
        parr[o + 2] = qx; parr[o + 3] = qy;
        larr[l] = L; larr[l + 1] = L;
        px = qx; py = qy;
      }
    }
    var edgePos = new Float32Array(edges.length * EDGE_SEG * 4);
    var edgeLen = new Float32Array(edges.length * EDGE_SEG * 2);
    function rebuildEdges() {
      for (var i = 0; i < edges.length; i++) {
        var s = nodes[edges[i].s], t = nodes[edges[i].t];
        tessEdge(s.x, s.y, t.x, t.y, edgePos, edgeLen,
          i * EDGE_SEG * 4, i * EDGE_SEG * 2);
      }
    }
    rebuildEdges();
    var edgeBuf = regl.buffer({ usage: 'dynamic', data: edgePos });
    var edgeLenBuf = regl.buffer({ usage: 'dynamic', data: edgeLen });

    function res(ctx) { return [ctx.drawingBufferWidth, ctx.drawingBufferHeight]; }

    // uA = the slow galactic rotation angle (the disc "travels");
    // applied in-shader about the galaxy centre (world origin) so it
    // is a pure GPU transform -- ZERO per-frame CPU graph mutation
    // (the v4.5.2 jank class). screenToWorld inverse-rotates so
    // pick/drag stay exact.
    var EDGE_VERT =
      'precision highp float; attribute vec2 position;' +
      'attribute float len; uniform vec2 cam,res;' +
      'uniform float zoom; uniform float uA; varying float vLen;' +
      'void main(){ vLen=len; float s=sin(uA),c=cos(uA);' +
      ' vec2 rp=vec2(position.x*c-position.y*s,' +
      '              position.x*s+position.y*c);' +
      ' vec2 p=(rp-cam)*zoom;' +
      ' gl_Position=vec4(p.x/(res.x*0.5),p.y/(res.y*0.5),0.0,1.0);}';
    // KEEP edges, but make them read as galactic FILAMENTS not a
    // hairball: a flat draw of all ~15.5k chords on a weakly-modular
    // graph sums (additively) into a central wash that buries the
    // star spiral ("when no edge it's look good"). So grade each
    // edge's alpha by (a) its world length -- short local edges trace
    // real structure and stay; long cross-disc chords (the hairball
    // + the boxy crossing pattern) fall to ~0, so the spiral survives
    // -- and (b) zoom relative to the whole-graph fit: at the
    // overview edges are barely there (the clean spiral), zooming
    // into a region ramps the local filaments in for inspection.
    // ``fade``=0 on the accent (incident) pass keeps it full + sharp.
    var EDGE_FRAG =
      'precision highp float; uniform vec4 ec;' +
      'uniform float wr,uz,uf,fade; varying float vLen;' +
      'void main(){' +
      ' float Ln=vLen/wr;' +
      ' float lf=exp(-Ln*Ln*8.0);' +
      ' float zf=clamp((uz/uf-1.0)*0.5+0.16,0.16,1.0);' +
      ' float k=mix(1.0, lf*zf, fade);' +
      ' gl_FragColor=vec4(ec.rgb, ec.a*k); }';

    var drawEdges = regl({
      vert: EDGE_VERT,
      frag: EDGE_FRAG,
      attributes: { position: edgeBuf, len: edgeLenBuf },
      uniforms: {
        cam: function () { return [cam.x, cam.y]; },
        zoom: function () { return cam.zoom; },
        res: function (c) { return res(c); },
        uA: function () { return gA; },
        wr: function () { return worldR; },
        uz: function () { return cam.zoom; },
        uf: function () { return fitZoom || 1; },
        fade: function () { return 1; },  // base web: length+zoom graded
        ec: function () {
          // when a node is focused, fade the base web HARD so only the
          // accent incident filaments read.
          var a = edgeColor[3] * (hlActive ? 0.12 : 1.0);
          return [edgeColor[0], edgeColor[1], edgeColor[2], a];
        },
      },
      count: edges.length * EDGE_SEG * 2,
      primitive: 'lines',
      blend: {
        enable: true,
        func: { src: 'src alpha', dst: 'one' },
      },
      depth: { enable: false },
    });

    var drawNodes = regl({
      vert:
        'precision highp float; attribute vec2 pos; attribute vec3 col;' +
        'attribute float siz; attribute float hl;' +
        'uniform vec2 cam,res; uniform float zoom; uniform float wr;' +
        'uniform float uA;' +
        'varying vec3 vC; varying float vH; varying float vB;' +
        'varying float vR;' +
        'void main(){' +
        ' float rN=clamp(length(pos)/wr,0.0,1.0); vR=rN;' +
        // STELLAR palette: a per-star stable hash picks a real
        // stellar tint (mostly blue-white/white, some gold, a few
        // warm orange -- a true star field, not one flat hue)...
        ' float h=fract(sin(dot(pos,vec2(12.9898,78.233)))*43758.5453);' +
        ' vec3 star = h<0.46 ? vec3(0.80,0.88,1.00)' +     // blue-white
        '           : h<0.72 ? vec3(0.95,0.97,1.00)' +     // white
        '           : h<0.84 ? vec3(1.00,0.92,0.78)' +     // gold
        '           : h<0.92 ? vec3(1.00,0.78,0.58)' +     // warm orange
        '                    : vec3(1.00,0.43,0.74);' +     // pink HII knot
        // ...graded by a GALACTIC temperature: warm-gold luminous
        // bulge -> blue-white arms -> faint cool steel outskirts.
        ' vec3 gold=vec3(1.00,0.89,0.66);' +
        ' vec3 blu=vec3(0.80,0.89,1.00);' +
        ' vec3 steel=vec3(0.52,0.64,0.88);' +
        ' vec3 gal = rN<0.5 ? mix(gold,blu,rN/0.5)' +
        '                   : mix(blu,steel,(rN-0.5)/0.5);' +
        ' vC = mix(mix(star,gal,0.55), col, 0.13); vH=hl;' +
        // brightness falls hard with radius (bright bulge -> faint
        // halo) + a per-star sparkle + a mild hub boost.
        // gentler core brightness (was 1.32-0.94*rN -> the dense core
        // additively blew out to a featureless white blob; nodes must
        // stay individually resolvable in dense regions).
        ' vB=(1.00 - 0.58*rN) * (0.80+0.42*h)' +
        '   * (0.86+0.26*clamp(siz/8.0,0.0,1.0));' +
        // slow galactic rotation about the centre (GPU-only).
        ' float s=sin(uA),c=cos(uA);' +
        ' vec2 rpos=vec2(pos.x*c-pos.y*s, pos.x*s+pos.y*c);' +
        ' vec2 p=(rpos-cam)*zoom;' +
        ' gl_Position=vec4(p.x/(res.x*0.5),p.y/(res.y*0.5),0.0,1.0);' +
        // SMALL crisp star points: at fit the world-space size must
        // stay <= the layout no-overlap spacing or distinct stars
        // visually merge again. Low zoom-growth -> tiny at fit (the
        // reference is countless tiny stars), grows when zoomed in.
        ' gl_PointSize=clamp(siz*(0.35+4.0*zoom),1.5,15.0);}',
      // NO screen-space-derivative call: on a WebGL1 context (regl's
      // default) those need the OES_standard_derivatives extension +
      // an #extension pragma; without it the fragment shader FAILS TO
      // COMPILE -> an invalid program -> useProgram INVALID_OPERATION
      // (1282) every frame -> the whole canvas renders nothing (THE
      // "black" root cause, proven by the console diagnostic). A
      // fixed-width SDF feather is extension-free, works everywhere;
      // 0.07 of the point radius is a crisp ~1-4 px edge at 3..64 px.
      frag:
        'precision highp float; varying vec3 vC; varying float vH;' +
        'varying float vB; varying float vR; uniform float glow;' +
        'void main(){ vec2 q=gl_PointCoord-0.5; float d=length(q);' +
        ' float aa=0.09;' +
        // clean soft-edged star disc (no glossy 3D rim).
        ' float core=1.0-smoothstep(0.5-aa,0.5,d);' +
        // bloom is STRONG in the bulge, fades to almost nothing in the
        // outskirts -> the luminous galactic core emerges from the
        // dense bright centre (no separate fullscreen pass).
        // MUCH gentler bloom (was *0.42*(1.15-..) -> additively
        // stacked to a white-out in the dense core). ~3x less so
        // dense stars stay discrete, faint halo only.
        ' float bloom=glow*(1.0-smoothstep(0.06,0.5,d))*0.15*(1.0-0.8*vR);' +
        // FOCUS: vH=1 = related (full colour + bloom, pops); vH=0 =
        // unrelated -> tinted cool BLUE and dimmed but still VISIBLE
        // as a blue backdrop field (the user: "blue all others if not
        // related"), not faded to invisible dust.
        ' vec3 blue=vec3(0.30,0.45,0.98);' +
        ' vec3 c = mix(mix(vC,blue,0.80)*0.62, vC*vB, vH);' +
        ' float lit = mix(0.34, 1.0, vH);' +
        ' gl_FragColor=vec4(c, core*lit)+vec4(vC*bloom*vH, 0.0);' +
        ' if(gl_FragColor.a<0.008) discard;}',
      attributes: { pos: posBuf, col: colBuf, siz: sizBuf, hl: hlBuf },
      uniforms: {
        cam: function () { return [cam.x, cam.y]; },
        zoom: function () { return cam.zoom; },
        res: function (c) { return res(c); },
        glow: function () { return glow; },
        wr: function () { return worldR; },
        uA: function () { return gA; },
      },
      count: nodes.length,
      primitive: 'points',
      blend: {
        enable: true,
        func: { src: 'src alpha', dst: 'one minus src alpha' },
      },
      depth: { enable: false },
    });

    // --- galactic CORE GLOW: a soft warm-gold luminous bulge at the
    // galaxy centre (world origin -- the layout centres the disc
    // there). A locality-preserving graph layout cannot pile a real
    // density bulge without scrambling edges, so the bright bulge is
    // rendered: an additive radial quad (NOT a gl.POINTS sprite --
    // point size is driver-capped; and extension-free GLSL only --
    // no derivatives). Scales with zoom so it stays the galaxy core.
    // The core glow is sized to the DENSE disc, NOT worldR (the max
    // node distance) -- the sparse far outer field/halo inflates
    // worldR to several x the bright spiral, which blew the glow up
    // into a huge wash. An 80th-percentile node radius tracks the
    // spiral itself; the glow is a fraction of that so it reads as a
    // compact luminous core.
    var _dist = new Float64Array(nodes.length || 1);
    for (var _q = 0; _q < nodes.length; _q++) {
      _dist[_q] = Math.hypot(nodes[_q].x || 0, nodes[_q].y || 0);
    }
    _dist.sort();
    var discR = nodes.length
      ? (_dist[Math.floor(nodes.length * 0.8)] || worldR) : worldR;
    var coreR = Math.max(discR * 0.62, 1.0);
    var coreQuad = regl.buffer(
      new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]));
    var drawCore = regl({
      // FULL-VIEWPORT pass (same proven pattern as drawBg): the quad
      // IS the screen every frame, the fragment reconstructs the
      // world point per pixel and measures the bar/bulge in WORLD
      // space scaled by cr -> the warm core fades smoothly to nothing
      // with NO geometric edge (the old uv*cr square cut the glow off
      // at a hard boundary bigger than the spiral).
      vert:
        'precision highp float; attribute vec2 uv; varying vec2 vN;' +
        'void main(){ vN=uv; gl_Position=vec4(uv,0.0,1.0); }',
      frag:
        'precision highp float; varying vec2 vN;' +
        'uniform vec2 cam,res; uniform float zoom;' +
        'uniform float cr; uniform float inten;' +
        'void main(){' +
        ' vec2 w=cam + vN*(res*0.5)/zoom;' +  // world point this pixel
        ' vec2 q=w/cr;' +                      // in core-radius units
        // THE BAR: rotate into the bar frame (~-34 deg, like the
        // reference's slanted bar) and measure an ELLIPTICAL distance
        // (long axis = the bar). A broad elongated warm glow + a
        // tighter brighter central bulge -> a golden barred core.
        ' float s=-0.555,c=0.832;' +  // sin/cos of ~-0.585 rad
        ' vec2 rv=vec2(q.x*c-q.y*s, q.x*s+q.y*c);' +
        ' vec2 ev=vec2(rv.x*0.52, rv.y*1.55);' +     // bar elongation
        ' float bar=exp(-dot(ev,ev)*2.7);' +          // the bar
        ' float bulge=exp(-dot(rv,rv)*8.5)*1.25;' +   // bright bulge
        ' float g=(bar+bulge)*inten;' +
        ' vec3 col=mix(vec3(1.0,0.90,0.66), vec3(0.98,0.80,0.92),' +
        '              clamp(length(rv),0.0,1.0));' +
        ' gl_FragColor=vec4(col*g, g); }',
      attributes: { uv: coreQuad },
      uniforms: {
        cam: function () { return [cam.x, cam.y]; },
        zoom: function () { return cam.zoom; },
        res: function (c) { return res(c); },
        cr: function () { return coreR; },
        // restrained -- a gentle warm halo, NOT a white-out (the core
        // glow + dense node bloom were additively saturating to a
        // featureless white blob; nodes must stay resolvable there).
        inten: function () { return 0.16; },
      },
      count: 4,
      primitive: 'triangle strip',
      blend: { enable: true, func: { src: 'one', dst: 'one' } },
      depth: { enable: false },
    });

    // --- background DUST: deep-space cirrus behind everything. The
    // prior version scaled a unit quad by a FINITE world radius
    // (uv*cr) -> a square whose radial wash had not decayed at the
    // quad boundary, so its edge showed and outside it was hard black
    // (the reported "the dust are limited into 1 square"). This is now
    // a FULL-VIEWPORT clip-space pass (the quad IS the screen, every
    // frame, at any zoom/pan) that reconstructs the world point per
    // pixel by inverting the node camera transform -- so the wash
    // stays anchored to the galaxy in world space yet has NO geometric
    // edge: it just fades to deep space everywhere off-screen.
    // Extension-free (no derivatives), additive, no point cap.
    var bgR = Math.max(worldR * 2.6, 1.0);
    var drawBg = regl({
      vert:
        'precision highp float; attribute vec2 uv;' +
        'varying vec2 vN;' +
        'void main(){ vN=uv; gl_Position=vec4(uv,0.0,1.0); }',
      frag:
        'precision highp float; varying vec2 vN;' +
        'uniform vec2 cam,res; uniform float zoom; uniform float br;' +
        'void main(){' +
        // inverse of the node transform p=(w-cam)*zoom,
        // clip=p/(res*0.5): the world point under THIS screen pixel.
        ' vec2 w=cam + vN*(res*0.5)/zoom;' +
        ' float d=length(w)/br;' +
        // broad soft cool wash (violet->blue) decayed to ~0 far from
        // the galaxy + a faint deep-space floor so the void is
        // near-black, never a flat panel and never a square.
        ' float g=exp(-d*d*1.7)*0.125 + 0.006*exp(-d*0.6);' +
        ' vec3 col=mix(vec3(0.34,0.30,0.62), vec3(0.13,0.20,0.42),' +
        '              clamp(d,0.0,1.0));' +
        ' gl_FragColor=vec4(col*g, g); }',
      attributes: { uv: coreQuad },
      uniforms: {
        cam: function () { return [cam.x, cam.y]; },
        zoom: function () { return cam.zoom; },
        res: function (c) { return res(c); },
        br: function () { return bgR; },
      },
      count: 4,
      primitive: 'triangle strip',
      blend: { enable: true, func: { src: 'one', dst: 'one' } },
      depth: { enable: false },
    });

    // highlight pass: the selected/hovered node's incident edges,
    // accent + on top -- SAME robust non-instanced LINES pattern,
    // its own dynamic buffer (never instanced).
    var accent = theme.accent || [0.494, 0.906, 0.878]; // #7ee7e0
    var hiPos = new Float32Array(4);
    var hiBuf = regl.buffer({ usage: 'dynamic', data: hiPos });
    var hiLen = new Float32Array(2);  // EDGE_VERT requires a len attr;
    var hiLenBuf = regl.buffer({ usage: 'dynamic', data: hiLen });
    var hiVerts = 0;  // highlighted VERTICES (2 per incident edge)
    function rebuildHi() {
      var foc = selId >= 0 ? selId : hoverId;
      var cnt = 0;
      if (foc >= 0) {
        for (var i = 0; i < edges.length; i++) {
          if (edges[i].s === foc || edges[i].t === foc) cnt++;
        }
      }
      if (cnt === 0) { hiVerts = 0; return; }
      var need4 = cnt * EDGE_SEG * 4, need2 = cnt * EDGE_SEG * 2;
      if (hiPos.length < need4) hiPos = new Float32Array(need4);
      if (hiLen.length < need2) hiLen = new Float32Array(need2);
      var q = 0;  // q-th incident edge -> its curve block
      for (var j = 0; j < edges.length; j++) {
        if (edges[j].s === foc || edges[j].t === foc) {
          var s = nodes[edges[j].s], t = nodes[edges[j].t];
          tessEdge(s.x, s.y, t.x, t.y, hiPos, hiLen,
            q * EDGE_SEG * 4, q * EDGE_SEG * 2);
          q++;
        }
      }
      hiVerts = cnt * EDGE_SEG * 2;
      hiBuf({ data: hiPos.subarray(0, need4) });
      hiLenBuf({ data: hiLen.subarray(0, need2) });
    }
    var hiEdges = regl({
      vert: EDGE_VERT,
      frag: EDGE_FRAG,
      attributes: { position: hiBuf, len: hiLenBuf },
      uniforms: {
        cam: function () { return [cam.x, cam.y]; },
        zoom: function () { return cam.zoom; },
        res: function (c) { return res(c); },
        uA: function () { return gA; },
        wr: function () { return worldR; },
        uz: function () { return cam.zoom; },
        uf: function () { return fitZoom || 1; },
        fade: function () { return 0; },  // accent: never length/zoom faded
        ec: function () {
          // bright, near-opaque accent so the related constellation's
          // filaments are UNMISTAKABLE against the blue-dimmed rest.
          return [
            Math.min(1.0, accent[0] * 1.15 + 0.1),
            Math.min(1.0, accent[1] * 1.15 + 0.1),
            Math.min(1.0, accent[2] * 1.15 + 0.1),
            0.95,
          ];
        },
      },
      count: function () { return hiVerts; },
      primitive: 'lines',
      blend: { enable: true, func: { src: 'src alpha', dst: 'one' } },
      depth: { enable: false },
    });

    var pick = buildPickIndex(nodes);

    // The display frame is the static layout rotated by +gA (exactly
    // what the node vertex shader applies). A camera fly onto a node
    // MUST aim at that rotated point, not the raw layout coord. While
    // a node is focused the rotation is frozen (frame() gates the gA
    // advance on selId<0), so this single rotated target stays locked
    // under the eased camera -- fixing the reported "zoom only knows
    // the node's original position, not where it travelled to".
    function focusTarget(i) {
      var s = Math.sin(gA), c = Math.cos(gA);
      return {
        x: nodes[i].x * c - nodes[i].y * s,
        y: nodes[i].x * s + nodes[i].y * c,
      };
    }

    function screenToWorld(px, py) {
      var vw = canvas.width, vh = canvas.height;
      // display-frame world point...
      var dx = cam.x + (px * dpr - vw / 2) / cam.zoom;
      var dy = cam.y - (py * dpr - vh / 2) / cam.zoom;
      // ...inverse-rotate by the galactic angle so it lands in the
      // STATIC frame the node coords + pick index live in (the shader
      // applies +gA for display; pick/drag must use -gA). Keeps
      // click + node-drag exact while the galaxy rotates.
      var s = Math.sin(-gA), c = Math.cos(-gA);
      return { x: dx * c - dy * s, y: dx * s + dy * c };
    }

    function resize() {
      // The canvas frequently has NO layout size when create() runs
      // (renderCanvas fires before the panel is painted), so the
      // create()-time fitCamera used the 800x600 fallback and the
      // whole world squeezed into a ~500px speck of 2px points (the
      // "black with one line" report). Authoritative fix: size the
      // drawing buffer from the real client box, and the FIRST time
      // we get a real size, fit the camera to it (drawing-buffer px,
      // consistent with the shader's res). Subsequent resizes only
      // resize the buffer -- the user's camera is preserved.
      var w = Math.max(1, Math.floor(canvas.clientWidth * dpr));
      var h = Math.max(1, Math.floor(canvas.clientHeight * dpr));
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
        invalidate();
      }
      if (!fitted && canvas.width > 2 && canvas.height > 2) {
        fitCamera(cam, nodes, canvas.width, canvas.height);
        fitZoom = cam.zoom;
        fitted = true;
      }
    }

    function frame() {
      raf = 0;
      if (disposed) return;
      // advance the slow galactic rotation from real elapsed time
      // (frame-rate independent); paused while the tab is hidden AND
      // while a node is focused (selId>=0) -- a frozen frame keeps the
      // eased camera locked exactly on the selected node (else the
      // perpetual spin carries it off the static camera every frame).
      var t = (global.performance || Date).now();
      if (gT && selId < 0) {
        gA = (gA + (t - gT) / 1000 * GOMEGA) % (Math.PI * 2);
      }
      gT = t;
      resize();
      regl.poll();
      regl.clear({ color: bg, depth: 1 });
      // a transient GL hiccup must NEVER kill the perpetual loop.
      try {
        drawBg();                         // deep-space cirrus
        drawCore();                       // galactic bulge underglow
        if (edgesOn && edges.length) drawEdges();
        drawNodes();
        if (edgesOn && hiVerts) hiEdges();
      } catch (e) { /* skip this frame; the loop continues */ }
      if (labels && labels._render) {
        labels._render(cam, gA, canvas.width, canvas.height, dpr, labelsOn);
      }
      // SINGLE-SCHEDULER loop. Re-arm ONLY while something is
      // genuinely animating: the galaxy is rotating (no node focused,
      // selId<0) OR a camera fly is in progress (easing). When a node
      // is focused and still, OR the tab is hidden, the loop IDLES
      // (raf=0) and waits for the next invalidate() (hover / drag /
      // wheel / select / deselect / visibility). This restores the
      // design's "renders only when dirty, idle == zero cost": the
      // perpetual re-render of the heavy scene for an UNCHANGING
      // image was the post-click lag. gT is cleared on idle so
      // rotation resumes with no time-jump when it next runs.
      var animating = (selId < 0 || easing)
        && (!global.document || !global.document.hidden);
      if (animating) {
        raf = global.requestAnimationFrame(frame);
      } else {
        raf = 0;
        gT = 0;
      }
    }
    function invalidate() {
      if (disposed) return;
      if (!raf) raf = global.requestAnimationFrame(frame);
    }
    if (global.document) {
      global.document.addEventListener('visibilitychange', function () {
        if (!global.document.hidden) invalidate();
      });
    }

    // step() ONLY mutates the camera + requests a render via
    // invalidate(); it never calls frame() directly. Calling the
    // self-perpetuating frame() from here forked an extra rAF chain
    // per click (the never-cancelled handles compounded into the
    // post-click lag). ``easing`` keeps the single loop alive for the
    // fly's duration even though the node is focused (rotation
    // frozen); when it ends the loop renders once more and idles.
    function easeTo(tx, ty, tz) {
      var sx = cam.x, sy = cam.y, sz = cam.zoom;
      var t0 = (global.performance || Date).now();
      var dur = 420;
      easing = true;
      function step() {
        if (disposed) { easing = false; return; }
        var k = Math.min(1, ((global.performance || Date).now() - t0) / dur);
        var e = 1 - Math.pow(1 - k, 3);
        cam.x = sx + (tx - sx) * e;
        cam.y = sy + (ty - sy) * e;
        cam.zoom = sz + (tz - sz) * e;
        if (k < 1) {
          invalidate();
          global.requestAnimationFrame(step);
        } else {
          easing = false;
          invalidate();  // final frame, then the loop idles (focused)
        }
      }
      invalidate();
      global.requestAnimationFrame(step);
    }

    // --- input: wheel zoom-to-cursor, drag pan, node drag, click ---
    var down = null, moved = false;
    canvas.addEventListener('wheel', function (e) {
      e.preventDefault();
      var before = screenToWorld(e.offsetX, e.offsetY);
      var f = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      cam.zoom = Math.max(1e-4, Math.min(1e4, cam.zoom * f));
      var after = screenToWorld(e.offsetX, e.offsetY);
      cam.x += before.x - after.x;
      cam.y += before.y - after.y;
      invalidate();
    }, { passive: false });

    canvas.addEventListener('mousedown', function (e) {
      var w = screenToWorld(e.offsetX, e.offsetY);
      var r = 14 / cam.zoom;
      var hit = pick.nearest(w.x, w.y, r);
      down = { px: e.offsetX, py: e.offsetY, wx: w.x, wy: w.y,
        cx: cam.x, cy: cam.y, hit: hit };
      dragId = hit;
      moved = false;
    });
    function endDrag() { down = null; dragId = -1; }
    // a mouseup released OUTSIDE the window (or eaten by another
    // layer) never reaches our handler -> down/dragId stayed set and
    // every later move kept dragging the node with no button held
    // (the reported "D&D doesn't stop off the board"). Heal it: any
    // move with no button pressed ends the drag; a window blur too.
    global.addEventListener('blur', endDrag);
    global.addEventListener('mousemove', function (e) {
      if (down && e.buttons === 0) { endDrag(); return; }
      if (down) {
        var dx = e.offsetX - down.px, dy = e.offsetY - down.py;
        if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
        if (dragId >= 0) {
          var w = screenToWorld(e.offsetX, e.offsetY);
          nodes[dragId].x = w.x;
          nodes[dragId].y = w.y;
          posArr[dragId * 2] = w.x;
          posArr[dragId * 2 + 1] = w.y;
          posBuf({ data: posArr });
          rebuildEdges();
          edgeBuf({ data: edgePos });
          edgeLenBuf({ data: edgeLen });
          rebuildHi();
          invalidate();
        } else {
          cam.x = down.cx - (e.offsetX - down.px) * dpr / cam.zoom;
          cam.y = down.cy + (e.offsetY - down.py) * dpr / cam.zoom;
          invalidate();
        }
      } else {
        var w2 = screenToWorld(e.offsetX, e.offsetY);
        var h = pick.nearest(w2.x, w2.y, 14 / cam.zoom);
        if (h !== hoverId) {
          hoverId = h;
          canvas.style.cursor = h >= 0 ? 'pointer' : '';
          rebuildHi();
          emit('hoverNode', h >= 0 ? h : null);
          invalidate();
        }
      }
    });
    global.addEventListener('mouseup', function (e) {
      if (down && !moved) {
        if (down.hit >= 0) emit('clickNode', down.hit);
        else emit('clickStage', null);
      }
      endDrag();
    });

    function emit(ev, a) {
      (cbs[ev] || []).forEach(function (f) { f(a); });
    }

    fitCamera(cam, nodes, canvas.clientWidth * dpr || 800,
      canvas.clientHeight * dpr || 600);

    var handle = {
      camera: cam,
      on: function (ev, cb) { (cbs[ev] || (cbs[ev] = [])).push(cb); },
      setHighlight: function (set) {
        hl = set;
        applyHL(set || null);  // dim everything outside the set
        invalidate();
      },
      select: function (i) {
        selId = (i == null) ? -1 : i;
        rebuildHi();
        // spotlight the node + its neighbours; blur all the rest
        // (the reported broken focus -- now the renderer does it).
        applyHL(selId >= 0 ? focusSet(selId) : (hl || null));
        if (selId >= 0) {
          // Pan to the node and gently zoom IN -- but stay anchored
          // to the whole-graph scale (fitZoom). Clamp to
          // [fitZoom .. 6x fitZoom]: from a full-fit view a click
          // zooms ~3x (node + its local cluster, context kept), and
          // it can NEVER fly to the old 0.5 floor (~7x past fit ->
          // the surrounding nebula vanished = the "black" report).
          var tz = Math.min(Math.max(cam.zoom, fitZoom * 3.0),
            fitZoom * 6.0);
          // rotation is now frozen (selId>=0); aim at the node's
          // CURRENT on-screen (rotated) position so the fly is exact.
          var ft = focusTarget(selId);
          easeTo(ft.x, ft.y, tz);
        } else {
          invalidate();
        }
      },
      hover: function (i) {
        hoverId = (i == null) ? -1 : i;
        rebuildHi();
        invalidate();
      },
      highlightSet: function () { return hl; },
      setEdgesVisible: function (b) { edgesOn = !!b; invalidate(); },
      setLabelsVisible: function (b) { labelsOn = !!b; invalidate(); },
      fit: function () {
        fitCamera(cam, nodes, canvas.width, canvas.height);
        fitZoom = cam.zoom;
        invalidate();
      },
      destroy: function () {
        disposed = true;
        if (ro) { try { ro.disconnect(); } catch (e) { /* gone */ } }
        if (raf) global.cancelAnimationFrame(raf);
        try { regl.destroy(); } catch (e) { /* already gone */ }
      },
    };
    // The canvas is routinely 0x0 when create() runs (panel not yet
    // painted) and a plain rAF can fire before first layout. A
    // ResizeObserver on the canvas fires when its box goes 0 -> real
    // (first layout, panel toggles, window resize) -> a frame runs ->
    // resize() sizes the buffer + fits the camera. (This observes the
    // ELEMENT box, which DOES change on layout, unlike a CDP viewport
    // resize which fires nothing.)
    var ro = null;
    if (typeof global.ResizeObserver === 'function') {
      ro = new global.ResizeObserver(function () { invalidate(); });
      try { ro.observe(canvas); } catch (e) { ro = null; }
    }
    invalidate();
    return handle;
  }

  global.NebulaGL = { create: create, LabelProvider: makeLabelProvider };
})(window);
