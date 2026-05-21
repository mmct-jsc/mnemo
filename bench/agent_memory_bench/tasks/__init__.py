"""Per-task implementations.

Each module here pairs a benchmark task (T1-T8 in the v0 spec) with
its scoring code. ``answer_follow_up`` ships with v0.1.0 (Task 3.3
of the enterprise execution plan); T2-T8 land in subsequent
benchmark releases.

The shape every task module exposes:

- ``FIXTURE_DIR`` -- ``Path`` to the task's ``bench/fixtures/<id>/``
  directory.
- ``load_fixture()`` -- parses ``corpus.jsonl`` + ``prompts.json`` +
  ``expected.json`` and returns a ``Fixture`` dataclass.
- ``run(agent_factory)`` -- drives the agent through the prompt
  sequence with a tracking Memory; returns a populated
  :class:`agent_memory_bench.runner.TaskResult`.
- ``score(...)`` -- per-task metric scorer (called by ``run``;
  exposed for downstream re-scoring against different judges).
"""
