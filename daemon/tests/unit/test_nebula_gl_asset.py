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
        "fwidth",  # SDF anti-aliasing
        "regl.clear",  # opaque dark themed clear every frame (gl.clearColor)
        "requestAnimationFrame",
        "cancelAnimationFrame",  # idle == zero cost
        "LabelProvider",
    ):
        assert tok in src, f"nebula-gl.js must expose/contain {tok!r}"
    low = src.lower()
    assert "sigma" not in low, "v4.6 renderer must not reference sigma"
    assert "graphology" not in low, "v4.6 renderer must not reference graphology"


def test_nebula_gl_has_no_stub_placeholders() -> None:
    """The plan elided helper bodies with /* ... */ -- they MUST be
    fully implemented, never shipped as placeholders."""
    src = (V / "nebula-gl.js").read_text(encoding="utf-8")
    assert "/* ... */" not in src, "no elided helper stubs may ship"
    assert "TODO" not in src and "FIXME" not in src
