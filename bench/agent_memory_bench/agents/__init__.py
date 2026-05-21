"""Reference agent baselines for the benchmark.

- :mod:`agent_memory_bench.agents.vanilla` -- no-memory baseline.
  Re-derives on every turn. The worst-case baseline every typed-
  Graph-RAG agent must beat.
- :mod:`agent_memory_bench.agents.mnemo` -- two reference variants:
  a deterministic mock for CI (``make_mnemo_mock_agent``) and an
  HTTP adapter against a live mnemo daemon
  (``make_mnemo_http_agent``, gated on the ``MNEMO_DAEMON_URL``
  env var).

External implementers register their own agents by satisfying the
``Memory`` protocol from :mod:`agent_memory_bench.runner`. No base
class to inherit.
"""
