"""Smoke tests for the community-facing docs (CONTRIBUTING, architecture, README).

These don't render or compile docs - they verify the obvious invariants
that catch broken links and missing required content.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


# --- Files exist ----------------------------------------------------------


def test_contributing_exists() -> None:
    p = REPO_ROOT / "CONTRIBUTING.md"
    assert p.is_file()


def test_architecture_doc_exists() -> None:
    assert (REPO_ROOT / "docs" / "architecture.md").is_file()


def test_workflows_index_exists() -> None:
    assert (REPO_ROOT / "docs" / "workflows" / "index.md").is_file()


def test_sample_queries_doc_exists() -> None:
    assert (REPO_ROOT / "docs" / "examples" / "sample-queries.md").is_file()


# --- Content invariants --------------------------------------------------


def test_contributing_calls_out_no_co_author_rule() -> None:
    text = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "Co-Authored-By" in text, "the no-co-author rule must be visible in CONTRIBUTING"
    # And it must say "no" - not just describe what a co-author is.
    assert re.search(r"no\s+`?co-authored-by`?", text.lower())


def test_contributing_documents_lint_and_test_commands() -> None:
    text = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "ruff check" in text
    assert "ruff format" in text
    assert "pytest" in text


def test_architecture_doc_has_required_sections() -> None:
    text = (REPO_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    for required in [
        "Three tiers",
        "Data model",
        "Retrieval",
        "Embedding",
    ]:
        assert required in text, f"architecture doc missing section: {required}"


def test_workflows_index_lists_all_seven() -> None:
    text = (REPO_ROOT / "docs" / "workflows" / "index.md").read_text(encoding="utf-8")
    for skill in [
        "mnemo-implement-platform",
        "mnemo-debug",
        "mnemo-refactor",
        "mnemo-add-knowledge",
        "mnemo-query-knowledge",
        "mnemo-onboard-project",
        "mnemo-review",
    ]:
        assert skill in text, f"workflows index missing: {skill}"


# --- README invariants ---------------------------------------------------


@pytest.fixture(scope="module")
def readme() -> str:
    return (REPO_ROOT / "README.md").read_text(encoding="utf-8")


def test_readme_no_placeholder_org(readme: str) -> None:
    assert "<your-org>" not in readme, "README still has the <your-org> placeholder"


def test_readme_links_to_docs(readme: str) -> None:
    for link in [
        "docs/architecture.md",
        "docs/plans/2026-05-09-mnemo-design.md",
        "docs/workflows/index.md",
        "docs/examples/sample-queries.md",
        "CONTRIBUTING.md",
        "LICENSE",
    ]:
        assert link in readme, f"README missing link: {link}"


def test_readme_internal_links_resolve(readme: str) -> None:
    """Every relative markdown link in the README points at a file that exists."""
    pattern = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")
    bad: list[str] = []
    for match in pattern.finditer(readme):
        href = match.group(1)
        if href.startswith(("http://", "https://", "mailto:")):
            continue
        target = (REPO_ROOT / href).resolve()
        if not target.exists():
            bad.append(href)
    assert not bad, f"broken README links: {bad}"
