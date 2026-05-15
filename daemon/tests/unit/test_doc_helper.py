"""v3 phase 10: doc-helper draft fences + the mnemo:doc skill (design S6.F)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
_UI = Path(__file__).resolve().parents[2] / "mnemo" / "ui"
CHAT_HTML = _UI / "templates" / "chat.html"
# v3.1 phase 6: the chat logic moved to the shared static module.
CHAT_JS = _UI / "static" / "chat.js"


def test_chat_page_parses_draft_fences_and_saves() -> None:
    html = CHAT_HTML.read_text(encoding="utf-8")
    js = CHAT_JS.read_text(encoding="utf-8")
    assert "mnemo-draft" in html  # the fence tag (template card)
    assert "Save as memory" in html  # the one-click button
    assert "extractDrafts" in js  # the parser (shared module)
    assert "/v1/nodes" in js  # POST target
    assert "/v1/reindex" in js  # triggers a memory reindex


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
