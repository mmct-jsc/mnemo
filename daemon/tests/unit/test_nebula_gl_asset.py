"""nebula-gl.js (v4.6 custom WebGL renderer) asset contract.

The headless preview is software-WebGL in a hidden 0-viewport tab, so
the RENDERER SURFACE is locked here (substring/structure probes are
authoritative + GPU-free); the visual is the user's GPU browser. The
layout quality -- the actual historical failure -- is gated
numerically in test_graph_layout_server.py.
"""

from pathlib import Path

V = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "static" / "vendor"


def test_regl_vendored_not_sigma() -> None:
    assert (V / "regl.min.js").stat().st_size > 50_000, "regl must be vendored"
    assert not (V / "sigma.min.js").exists(), "sigma must be gone (v4.6)"
    assert not (V / "graphology.umd.min.js").exists(), "graphology must be gone"


def test_nebula_gl_surface() -> None:
    src = (V / "nebula-gl.js").read_text(encoding="utf-8")
    for tok in (
        "NebulaGL",
        "function create",
        "setHighlight",
        "select",
        "hover",
        "destroy",
        "gl_FragColor",
        "smoothstep",  # extension-free SDF antialiasing
        "regl.clear",  # opaque dark themed clear every frame (gl.clearColor)
        "requestAnimationFrame",
        "cancelAnimationFrame",  # idle == zero cost
        "LabelProvider",
    ):
        assert tok in src, f"nebula-gl.js must expose/contain {tok!r}"
    low = src.lower()
    assert "sigma" not in low, "v4.6 renderer must not reference sigma"
    assert "graphology" not in low, "v4.6 renderer must not reference graphology"
    # fwidth/dFdx need OES_standard_derivatives on a WebGL1 context;
    # without it the shader fails to compile -> invalid program ->
    # GL_INVALID_OPERATION every frame -> black canvas (the proven
    # v4.6 "black" root cause). The SDF must stay extension-free.
    assert "fwidth" not in src, (
        "fwidth/derivatives must NOT be used (WebGL1 needs an "
        "extension; its absence = invalid program = black render)"
    )
    assert "dFdx" not in src, "no screen-space derivatives (dFdx)"
    assert "dFdy" not in src, "no screen-space derivatives (dFdy)"
    # the Milky-Way bulge is a RENDERED luminous core glow (a
    # locality-preserving graph layout cannot pile a density bulge
    # without scrambling edges) -- it must exist as an additive quad.
    assert "drawCore" in src, "the galactic core-glow draw must exist (the rendered bulge)"
    assert "triangle strip" in src, "core glow must be a quad (gl.POINTS size is driver-capped)"
    # the instanced-edge GL_INVALID_OPERATION trap must not return:
    # edges are a non-instanced LINES draw (no divisor on a buffer).
    assert "divisor" not in src, (
        "edges must be a non-instanced LINES draw (the instanced "
        "t:[0,1]+divisor setup raised GL_INVALID_OPERATION every frame)"
    )


def test_focus_fly_is_rotation_correct() -> None:
    """The perpetual galactic rotation displays every node at
    rotate(pos,+gA). The focus camera-fly therefore must target the
    ROTATED point, not the static layout coord, AND the rotation must
    freeze while a node is focused -- otherwise the still-spinning node
    slides off the (static) eased camera. That divergence is the
    reported 'zoom only knows the original position, not where the
    node moved to'. This locks the fix so the static-target bug and
    the never-freezing rotation can't silently return."""
    src = (V / "nebula-gl.js").read_text(encoding="utf-8")
    # the bug: easing the camera to the un-rotated layout coordinate.
    assert "easeTo(nodes[selId].x, nodes[selId].y" not in src, (
        "select() must NOT ease to the static layout coord -- the node "
        "vertex shader draws rotate(pos,+gA), so a static target lands "
        "the fly off the visible node."
    )
    # the fix: a named rotated-focus helper drives the fly.
    assert "focusTarget" in src, (
        "select() must fly to focusTarget(i) (pos rotated by +gA, the "
        "same transform the node shader applies)."
    )
    # and the rotation must be gated by selection so the frozen camera
    # and the node stay locked together while focused.
    assert "selId < 0" in src, (
        "the galactic rotation advance must be gated on selId<0 "
        "(frozen while a node is focused) so the eased camera stays "
        "centered on it."
    )


def test_background_dust_is_full_viewport_not_a_world_square() -> None:
    """The deep-space dust must fill the whole viewport at ANY
    zoom/pan, never a finite world-space square quad whose radial
    falloff is still visible at the quad boundary (-> a hard square
    edge with black outside: the reported 'the dust are limited into
    1 square'). The fix is a full-viewport clip-space pass that
    reconstructs the world position per pixel (inverse camera) so the
    wash stays anchored to the galaxy yet has no geometric edge."""
    src = (V / "nebula-gl.js").read_text(encoding="utf-8")
    bg = src[src.index("drawBg = regl(") : src.index("drawBg = regl(") + 1400]
    # the background quad must be emitted directly in clip space
    # (covers the entire NDC viewport every frame, regardless of cam).
    assert "gl_Position=vec4(uv,0.0,1.0)" in bg, (
        "drawBg must be a full-viewport clip-space pass "
        "(gl_Position=vec4(uv,0.0,1.0)), not a world-scaled square quad."
    )
    # and it must reconstruct the world point per pixel (inverse of the
    # node transform) so the wash is world-anchored without an edge.
    assert "(res*0.5)/zoom" in bg, (
        "drawBg must reconstruct the world position per pixel "
        "(cam + clip*(res*0.5)/zoom) so the dust is anchored to the "
        "galaxy in world space with no square boundary."
    )
    # the old finite world-square basis (uv*cr - cam) must be GONE
    # from the background pass specifically.
    assert "uv*cr - cam" not in bg, (
        "drawBg must NOT scale a unit quad by a finite world radius "
        "(uv*cr - cam) -- that is the visible square."
    )


def test_edges_are_length_and_zoom_graded_not_a_flat_hairball() -> None:
    """On the real weakly-modular 11k graph a flat-alpha draw of all
    ~15.5k edges is a hairball: long cross-disc chords sum into a
    central wash that buries the star spiral ('when no edge it's look
    good'). The fix keeps edges but grades each edge's alpha by its
    world length (short local filaments stay; long chords -> ~0, so
    the spiral survives) and by zoom relative to the whole-graph fit
    (overview ~invisible; zoom-in reveals local structure). A flat
    LINES vertex can't know the other endpoint, so the length must be
    a real per-edge attribute carried on its own dynamic buffer."""
    src = (V / "nebula-gl.js").read_text(encoding="utf-8")
    assert "attribute float len" in src, (
        "the edge vertex shader must take a per-edge world-length "
        "attribute (a flat LINES draw cannot derive it in-shader)."
    )
    assert "edgeLen" in src, (
        "there must be a per-edge length buffer (edgeLen) rebuilt with "
        "edge positions so it stays correct under node drag."
    )
    assert "exp(-Ln*Ln*8.0)" in src, (
        "the edge fragment must fade alpha by normalized length so "
        "long cross-disc chords vanish (the hairball) while short "
        "local filaments remain (galactic texture)."
    )
    assert "uz/uf" in src, (
        "edge alpha must also scale with zoom relative to the fit "
        "(overview ~invisible -> the clean spiral; zoom-in reveals "
        "local edges)."
    )


def test_core_glow_is_full_viewport_not_a_world_square() -> None:
    """The galactic core-glow had the SAME defect as the old bg dust:
    a finite world-space square quad (uv*cr - cam, cr=worldR*0.42).
    Because worldR is the max node distance -- inflated by the sparse
    far outer field to several x the spiral -- that square is BIGGER
    than the spiral and its warm bar+bulge is still ~0.01 at the quad
    boundary -> a hard square edge with the glow cut off outside (the
    reported '1 outer square bigger than the spiral core, cut off').
    It must be a full-viewport clip-space pass that reconstructs the
    world point per pixel (same proven pattern as drawBg) and be
    sized to the DENSE disc (a high percentile of node radius), not
    the outer-field-inflated max."""
    src = (V / "nebula-gl.js").read_text(encoding="utf-8")
    i = src.index("drawCore = regl(")
    core = src[i : i + 1500]
    assert "gl_Position=vec4(uv,0.0,1.0)" in core, (
        "drawCore must be a full-viewport clip-space pass, not a "
        "world-scaled square quad (the visible outer square)."
    )
    assert "(res*0.5)/zoom" in core, (
        "drawCore must reconstruct the world position per pixel so "
        "the bulge is world-anchored with no geometric edge."
    )
    assert "uv*cr - cam" not in core, (
        "drawCore must NOT scale a unit quad by a finite radius "
        "(uv*cr - cam) -- that is the square that gets cut off."
    )
    # the core must be sized to the dense disc, not max node distance
    # (which the sparse outer halo inflates ~3x past the spiral).
    assert "discR" in src, (
        "the core radius must derive from a high percentile of node "
        "distance (the dense disc), not the outer-field-inflated max."
    )


def test_edges_are_curved_filaments_not_straight_segments() -> None:
    """Straight 2-vertex GL lines read as harsh pixelated wires. The
    edges must be tessellated into a multi-segment quadratic Bezier
    bowed consistently (a coherent swirl in the rotation sense) so
    they flow with the disc as elegant filaments -- drawn with the
    same graded additive blend (overlap -> silk). Both the base web
    and the accent incident pass must use the same tessellation."""
    src = (V / "nebula-gl.js").read_text(encoding="utf-8")
    assert "EDGE_SEG" in src, (
        "edges must be tessellated by an EDGE_SEG segment count, not "
        "drawn as single straight chords."
    )
    # a real quadratic Bezier sample (iu=1-u; iu*iu*S + 2*iu*u*C + u*u*T)
    assert "iu * iu" in src, (
        "edges must follow a quadratic Bezier (bowed control point), not a straight segment."
    )
    assert "2 * iu * u" in src, "the Bezier must include the 2*iu*u*C control term."
    # the base-web draw count must scale with the tessellation.
    assert "edges.length * EDGE_SEG * 2" in src, (
        "drawEdges count must be edges.length * EDGE_SEG * 2 (a "
        "curved polyline), not edges.length * 2 (a straight chord)."
    )
    assert "count: edges.length * 2," not in src, (
        "the old straight 2-vertex-per-edge draw must be gone."
    )


def test_no_debug_scaffolding_ships() -> None:
    """The dev-only diagnostic loop (the throttled '[nebula-gl] ...'
    console line + the on-overlay HUD + the per-frame
    rawgl.getError() sync stall) was the headless debugging aid for
    the 0x0 software-WebGL preview. It MUST be stripped from the
    shipped renderer (a per-frame console.log + GL error sync in a
    perpetual loop is a real production cost). Locked so it cannot
    silently return in a later release."""
    src = (V / "nebula-gl.js").read_text(encoding="utf-8")
    assert "[nebula-gl]" not in src, "the debug console line must be stripped"
    assert "console.log" not in src, "no console.log in the shipped renderer"
    assert "_hud" not in src, "the debug HUD must be removed"
    assert "rawgl" not in src, (
        "the debug-only raw GL context (per-frame getError sync stall) "
        "must be removed from the shipped renderer"
    )


def test_nebula_gl_has_no_stub_placeholders() -> None:
    """The plan elided helper bodies with /* ... */ -- they MUST be
    fully implemented, never shipped as placeholders."""
    src = (V / "nebula-gl.js").read_text(encoding="utf-8")
    assert "/* ... */" not in src, "no elided helper stubs may ship"
    assert "TODO" not in src, "no TODO placeholder may ship"
    assert "FIXME" not in src, "no FIXME placeholder may ship"
