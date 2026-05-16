"""C3 (v4.3): 1-message-at-a-time welcome + shared examples/composer.

The eager `sending` flag (set synchronously in sendExample/sendMessage
BEFORE the async newConversation() await window) makes the welcome /
examples vanish on click, so a suggested question can't coexist with
the outgoing message.
"""

from pathlib import Path

_UI = Path(__file__).resolve().parents[2] / "mnemo" / "ui"
JS = (_UI / "static" / "chat.js").read_text(encoding="utf-8")
PAGE = (_UI / "templates" / "chat.html").read_text(encoding="utf-8")
BASE = (_UI / "templates" / "base.html").read_text(encoding="utf-8")
EX = (_UI / "templates" / "_chat_examples.html").read_text(encoding="utf-8")
COMP = (_UI / "templates" / "_chat_composer.html").read_text(encoding="utf-8")


def test_factory_has_eager_sending_flag_and_send_example() -> None:
    assert "sending:" in JS or "sending :" in JS, "eager 'sending' data flag"
    assert "sendExample" in JS, "example click -> eager flag THEN send"
    # set synchronously before the async chain (not only after await):
    assert "this.sending = true" in JS


def test_examples_partial_shared_and_gated_on_sending() -> None:
    assert "_chat_examples.html" in PAGE
    assert "_chat_examples.html" in BASE
    assert "_chat_composer.html" in PAGE
    assert "_chat_composer.html" in BASE
    # the 1-at-a-time gate: welcome hides while a send is in flight
    assert "!messages.length" in EX
    assert "!sending" in EX
    assert "sendExample(ex)" in EX
    assert "chat_surfaces[surface].examples" in EX
    assert "chat_surfaces[surface].composer" in COMP


def test_composer_single_sources_the_send_button() -> None:
    # the send button/icon live ONCE in the composer partial (so Task 6
    # path fix is a single edit both surfaces inherit):
    assert "send-ic" in COMP
    assert 'class="send"' in COMP  # page send button
    assert 'class="mc-send"' in COMP  # dock send button
