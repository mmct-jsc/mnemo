"""v5.5.1 — eager-load the embedder at MCP server startup.

Cold-start bug found in v5.5.0:

Each MCP host (Claude Desktop / Cursor / Windsurf / Zed / Continue)
spawns its own ``mnemo mcp`` subprocess per conversation. The MCP
server holds its own ``Embedder()`` instance (separate from the
daemon's), which lazy-loads the ``all-MiniLM-L6-v2`` model on first
``mnemo_query``. The cold sentence-transformer load takes ~15s on
typical hardware — longer than the MCP client's tool-call timeout —
so the FIRST ``mnemo_query`` from any new conversation always times
out.

Fix: eager-load the embedder during MCP startup (before the stdio
JSON-RPC loop starts). The handshake has no client-side timeout
pressure, so the ~15s load happens transparently while the host is
still negotiating capabilities. Then the first user-facing
``mnemo_query`` is sub-100ms warm.

Implementation contract (this test):

1. ``prepare_stdio_server()`` exists in ``mnemo.mcp_server`` and
   returns ``(server, ctx)``.
2. The returned ``ctx.embedder._model`` is **not None** — proving
   the cold load already happened during prepare, not deferred to
   the first query.
3. ``serve_stdio()`` calls ``prepare_stdio_server()`` (refactor,
   verified by a second test that imports the module + greps).

Why this layout: ``serve_stdio()`` itself is hard to test because
it goes straight into ``anyio.run``. Pulling the build-and-warm
step into a returnable helper makes the warming testable without
spinning up the stdio transport.
"""

from __future__ import annotations

import inspect


def test_prepare_stdio_server_exists() -> None:
    """The v5.5.1 entry-point ``prepare_stdio_server`` is the
    testable surface that does build-server + warm-embedder."""
    from mnemo import mcp_server

    assert hasattr(mcp_server, "prepare_stdio_server"), (
        "v5.5.1 contract: mnemo.mcp_server must expose "
        "prepare_stdio_server() so serve_stdio()'s warmup can be "
        "tested without spinning up the stdio JSON-RPC loop."
    )


def test_prepare_stdio_server_returns_server_and_ctx() -> None:
    """``prepare_stdio_server()`` returns ``(server, ctx)`` so the
    caller (``serve_stdio``) can hand the server into ``anyio.run``
    and keep the ctx alive (it owns the warm embedder + store)."""
    from mnemo.mcp_server import prepare_stdio_server

    result = prepare_stdio_server()
    assert isinstance(result, tuple), (
        f"prepare_stdio_server() must return a tuple; got {type(result).__name__}"
    )
    assert len(result) == 2, (
        f"prepare_stdio_server() must return (server, ctx); got {len(result)}-tuple"
    )
    server, ctx = result
    assert server is not None, "server cannot be None"
    assert ctx is not None, "ctx cannot be None"
    assert hasattr(ctx, "embedder"), "ctx must have an embedder"
    assert hasattr(ctx, "store"), "ctx must have a store"


def test_prepare_stdio_server_warms_embedder() -> None:
    """**The core v5.5.1 contract.**

    After ``prepare_stdio_server()`` returns, the ctx's embedder
    must already have its sentence-transformer model loaded
    (``_model is not None``). This proves the ~15s cold load
    happened during MCP handshake — NOT during the first user-facing
    ``mnemo_query`` call where the MCP client's tool-call timeout
    would interrupt it.
    """
    from mnemo.mcp_server import prepare_stdio_server

    _server, ctx = prepare_stdio_server()
    assert ctx.embedder._model is not None, (
        "Embedder._model still None after prepare_stdio_server(); "
        "the first mnemo_query through any MCP host (Claude Desktop "
        "/ Cursor / Windsurf / Zed / Continue) will time out because "
        "the ~15s cold sentence-transformer load runs during the "
        "host's tool-call timeout window instead of during the "
        "no-timeout handshake."
    )


def test_serve_stdio_uses_prepare_stdio_server() -> None:
    """``serve_stdio`` must call ``prepare_stdio_server`` — otherwise
    the warmup is bypassed and we're back to the cold-start bug.

    Source-level check (not a behavioural test) because
    ``serve_stdio`` itself goes straight into ``anyio.run`` and
    blocks; running it would either spin forever or require mocking
    the entire stdio stack. The grep confirms the refactor stuck.
    """
    from mnemo import mcp_server

    src = inspect.getsource(mcp_server.serve_stdio)
    assert "prepare_stdio_server" in src, (
        "serve_stdio() must call prepare_stdio_server() so the "
        "eager-warm path is wired. Otherwise warmup is bypassed and "
        "the v5.5.0 cold-start bug returns."
    )
