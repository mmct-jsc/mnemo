"""v3 phase 10: doc-helper draft fences + the mnemo:doc skill (design S6.F)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CHAT_HTML = (
    Path(__file__).resolve().parents[2]
    / "mnemo"
    / "ui"
    / "templates"
    / "chat.html"
)


def test_chat_page_parses_draft_fences_and_saves() -> None:
    html = CHAT_HTML.read_text(encoding="utf-8")
    assert "mnemo-draft" in html  # the fence tag
    assert "extractDrafts" in html  # the parser
    assert "Save as memory" in html  # the one-click button
    assert "/v1/nodes" in html  # POST target
    assert "/v1/reindex" in html  # triggers a memory reindex


def test_mnemo_doc_skill_exists_and_documents_the_fence() -> None:
    skill = ROOT / "skills" / "mnemo-doc" / "SKILL.md"
    assert skill.is_file(), skill
    text = skill.read_text(encoding="utf-8")
    assert "name: mnemo-doc" in text
    assert "mnemo-draft" in text
    # documents the frontmatter contract
    assert "name" in text
    assert "type" in text
    assert "projectKey" in text
