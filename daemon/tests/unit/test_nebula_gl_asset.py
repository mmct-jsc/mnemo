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
    assert "dFdx" not in src and "dFdy" not in src, (
        "no screen-space derivatives -- extension-free SDF only"
    )
    # the instanced-edge GL_INVALID_OPERATION trap must not return:
    # edges are a non-instanced LINES draw (no divisor on a buffer).
    assert "divisor" not in src, (
        "edges must be a non-instanced LINES draw (the instanced "
        "t:[0,1]+divisor setup raised GL_INVALID_OPERATION every frame)"
    )


def test_nebula_gl_has_no_stub_placeholders() -> None:
    """The plan elided helper bodies with /* ... */ -- they MUST be
    fully implemented, never shipped as placeholders."""
    src = (V / "nebula-gl.js").read_text(encoding="utf-8")
    assert "/* ... */" not in src, "no elided helper stubs may ship"
    assert "TODO" not in src, "no TODO placeholder may ship"
    assert "FIXME" not in src, "no FIXME placeholder may ship"
