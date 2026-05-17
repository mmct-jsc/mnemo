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
    var api = {
      setLabels: function (list) { items = list || []; },
      clear: function () { items = []; },
      // called by the renderer each frame with the live camera.
      _render: function (cam, vw, vh, dpr) {
        var ctx = canvasEl.getContext('2d');
        if (canvasEl.width !== vw || canvasEl.height !== vh) {
          canvasEl.width = vw;
          canvasEl.height = vh;
        }
        ctx.clearRect(0, 0, vw, vh);
        if (!items.length) return;
        ctx.font = (12 * dpr) + 'px ui-sans-serif,system-ui,sans-serif';
        ctx.textBaseline = 'middle';
        for (var i = 0; i < items.length; i++) {
          var it = items[i];
          var sx = (it.x - cam.x) * cam.zoom + vw / 2;
          var sy = -(it.y - cam.y) * cam.zoom + vh / 2;
          if (sx < -200 || sx > vw + 200 || sy < -50 || sy > vh + 50) continue;
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
    var raf = 0, disposed = false;
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
    var posBuf = regl.buffer({ usage: 'dynamic', data: posArr });
    var colBuf = regl.buffer(colArr);
    var sizBuf = regl.buffer(sizArr);

    var ea = new Float32Array(edges.length * 2);
    var eb = new Float32Array(edges.length * 2);
    function rebuildEdges() {
      for (var i = 0; i < edges.length; i++) {
        var s = nodes[edges[i].s], t = nodes[edges[i].t];
        ea[i * 2] = s.x; ea[i * 2 + 1] = s.y;
        eb[i * 2] = t.x; eb[i * 2 + 1] = t.y;
      }
    }
    rebuildEdges();
    var eaBuf = regl.buffer({ usage: 'dynamic', data: ea });
    var ebBuf = regl.buffer({ usage: 'dynamic', data: eb });

    function res(ctx) { return [ctx.drawingBufferWidth, ctx.drawingBufferHeight]; }

    var drawEdges = regl({
      vert:
        'precision highp float; attribute vec2 a,b; attribute float t;' +
        'uniform vec2 cam,res; uniform float zoom;' +
        'void main(){ vec2 w=mix(a,b,t); vec2 p=(w-cam)*zoom;' +
        ' gl_Position=vec4(p.x/(res.x*0.5),p.y/(res.y*0.5),0.0,1.0);}',
      frag:
        'precision highp float; uniform vec4 ec;' +
        'void main(){ gl_FragColor=ec; }',
      attributes: {
        a: { buffer: eaBuf, divisor: 1 },
        b: { buffer: ebBuf, divisor: 1 },
        t: [0, 1],
      },
      uniforms: {
        cam: function () { return [cam.x, cam.y]; },
        zoom: function () { return cam.zoom; },
        res: function (c) { return res(c); },
        ec: function () { return edgeColor; },
      },
      count: 2,
      instances: function () { return edges.length; },
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
        'attribute float siz; uniform vec2 cam,res; uniform float zoom;' +
        'varying vec3 vC;' +
        'void main(){ vC=col; vec2 p=(pos-cam)*zoom;' +
        ' gl_Position=vec4(p.x/(res.x*0.5),p.y/(res.y*0.5),0.0,1.0);' +
        ' gl_PointSize=clamp(siz*zoom*2.0,2.0,90.0);}',
      frag:
        'precision highp float; varying vec3 vC; uniform float glow;' +
        'void main(){ vec2 q=gl_PointCoord-0.5; float d=length(q);' +
        ' float aa=fwidth(d)+0.001;' +
        ' float core=1.0-smoothstep(0.5-aa,0.5,d);' +
        ' float rim=smoothstep(0.40-aa,0.46,d)*core;' +
        ' float halo=glow*(1.0-smoothstep(0.0,0.5,d))*0.45;' +
        ' vec3 c=mix(vC, vC*0.55, rim);' +
        ' gl_FragColor=vec4(c*core, core)+vec4(vC*halo, 0.0);' +
        ' if(gl_FragColor.a<0.01 && halo<0.01) discard;}',
      attributes: { pos: posBuf, col: colBuf, siz: sizBuf },
      uniforms: {
        cam: function () { return [cam.x, cam.y]; },
        zoom: function () { return cam.zoom; },
        res: function (c) { return res(c); },
        glow: function () { return glow; },
      },
      count: nodes.length,
      primitive: 'points',
      blend: {
        enable: true,
        func: { src: 'src alpha', dst: 'one minus src alpha' },
      },
      depth: { enable: false },
    });

    // highlight pass: a selected/hovered/highlighted node + its
    // incident edges, brighter, on top -- via per-call color override.
    var accent = theme.accent || [0.494, 0.906, 0.878]; // #7ee7e0
    var hiEdges = regl({
      vert: drawEdges._vert ||
        'precision highp float; attribute vec2 a,b; attribute float t;' +
        'uniform vec2 cam,res; uniform float zoom;' +
        'void main(){ vec2 w=mix(a,b,t); vec2 p=(w-cam)*zoom;' +
        ' gl_Position=vec4(p.x/(res.x*0.5),p.y/(res.y*0.5),0.0,1.0);}',
      frag:
        'precision highp float; uniform vec4 ec;' +
        'void main(){ gl_FragColor=ec; }',
      attributes: {
        a: { buffer: eaBuf, divisor: 1 },
        b: { buffer: ebBuf, divisor: 1 },
        t: [0, 1],
      },
      uniforms: {
        cam: function () { return [cam.x, cam.y]; },
        zoom: function () { return cam.zoom; },
        res: function (c) { return res(c); },
        ec: function () {
          return [accent[0], accent[1], accent[2], 0.55];
        },
      },
      count: 2,
      instances: function () { return hiCount; },
      primitive: 'lines',
      blend: { enable: true, func: { src: 'src alpha', dst: 'one' } },
      depth: { enable: false },
    });
    var hiA = regl.buffer({ usage: 'dynamic', data: new Float32Array(2) });
    var hiB = regl.buffer({ usage: 'dynamic', data: new Float32Array(2) });
    var hiCount = 0;
    function rebuildHi() {
      var foc = selId >= 0 ? selId : hoverId;
      var sa = [], sb = [];
      if (foc >= 0) {
        for (var i = 0; i < edges.length; i++) {
          if (edges[i].s === foc || edges[i].t === foc) {
            var s = nodes[edges[i].s], t = nodes[edges[i].t];
            sa.push(s.x, s.y); sb.push(t.x, t.y);
          }
        }
      }
      hiCount = sa.length / 2;
      if (hiCount) {
        hiA({ data: new Float32Array(sa) });
        hiB({ data: new Float32Array(sb) });
      }
    }

    var pick = buildPickIndex(nodes);

    function screenToWorld(px, py) {
      var vw = canvas.width, vh = canvas.height;
      return {
        x: cam.x + (px * dpr - vw / 2) / cam.zoom,
        y: cam.y - (py * dpr - vh / 2) / cam.zoom,
      };
    }

    function resize() {
      var w = Math.max(1, Math.floor(canvas.clientWidth * dpr));
      var h = Math.max(1, Math.floor(canvas.clientHeight * dpr));
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
        invalidate();
      }
    }

    function frame() {
      raf = 0;
      if (disposed) return;
      resize();
      regl.poll();
      regl.clear({ color: bg, depth: 1 });
      if (edges.length) drawEdges();
      drawNodes();
      if (hiCount) hiEdges();
      if (labels && labels._render) {
        labels._render(cam, canvas.width, canvas.height, dpr);
      }
    }
    function invalidate() {
      if (disposed) return;
      if (!raf) raf = global.requestAnimationFrame(frame);
    }

    function easeTo(tx, ty, tz) {
      var sx = cam.x, sy = cam.y, sz = cam.zoom;
      var t0 = (global.performance || Date).now();
      var dur = 420;
      function step() {
        if (disposed) return;
        var k = Math.min(1, ((global.performance || Date).now() - t0) / dur);
        var e = 1 - Math.pow(1 - k, 3);
        cam.x = sx + (tx - sx) * e;
        cam.y = sy + (ty - sy) * e;
        cam.zoom = sz + (tz - sz) * e;
        frame();
        if (k < 1) global.requestAnimationFrame(step);
      }
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
    global.addEventListener('mousemove', function (e) {
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
          eaBuf({ data: ea });
          ebBuf({ data: eb });
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
      down = null;
      dragId = -1;
    });

    function emit(ev, a) {
      (cbs[ev] || []).forEach(function (f) { f(a); });
    }

    fitCamera(cam, nodes, canvas.clientWidth * dpr || 800,
      canvas.clientHeight * dpr || 600);

    var handle = {
      camera: cam,
      on: function (ev, cb) { (cbs[ev] || (cbs[ev] = [])).push(cb); },
      setHighlight: function (set) { hl = set; invalidate(); },
      select: function (i) {
        selId = (i == null) ? -1 : i;
        rebuildHi();
        if (selId >= 0) {
          easeTo(nodes[selId].x, nodes[selId].y,
            Math.max(cam.zoom, 0.5));
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
      fit: function () {
        fitCamera(cam, nodes, canvas.width, canvas.height);
        invalidate();
      },
      destroy: function () {
        disposed = true;
        if (raf) global.cancelAnimationFrame(raf);
        try { regl.destroy(); } catch (e) { /* already gone */ }
      },
    };
    invalidate();
    return handle;
  }

  global.NebulaGL = { create: create, LabelProvider: makeLabelProvider };
})(window);
