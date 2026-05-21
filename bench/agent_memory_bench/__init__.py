"""agent-memory-bench: reproducible benchmark for typed Graph-RAG agent memory.

The full v0 spec lives in the parent repo at
``docs/benchmark/agent-memory-spec-v0.md`` (CC-BY-4.0). This package
is the MIT-licensed reference harness.

Quick start (from the ``bench/`` directory)::

    uv sync --extra dev
    uv run pytest

Public surface (Task 3.2 skeleton)::

    from agent_memory_bench import Metrics, TaskResult, run_task

External implementers consume the same surface; their agents satisfy
the ``Memory`` Protocol defined in :mod:`agent_memory_bench.runner`.
"""

from agent_memory_bench.runner import Memory, Metrics, Retrieval, TaskResult, run_task

__version__ = "0.1.0"

__all__ = [
    "Memory",
    "Metrics",
    "Retrieval",
    "TaskResult",
    "run_task",
]
