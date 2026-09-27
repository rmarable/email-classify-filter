import pytest

from ecf.errors import InvalidInputError
from ecf_server.templates import load_default_templates, load_templates, render


def test_shipped_received_is_disabled() -> None:
    t = load_default_templates()
    assert list(t) == ["received"]
    assert t["received"].enabled is False


def test_render_strips_line_breaks() -> None:
    t = load_default_templates()["received"]
    out = render(t, sender_name="Ann\r\nBcc: x@evil.example", subject="Invoice\n42")
    assert out.subject == "Re: Invoice 42"
    assert "\r" not in out.body and "Ann Bcc: x@evil.example," in out.body


@pytest.mark.parametrize(
    "body", ["Hi {name}", "Hi {sender_name!r}", "Hi {sender_name:>10}", "Hi {0}", "Hi {}"]
)
def test_only_two_variables(body: str) -> None:
    with pytest.raises(InvalidInputError):
        load_templates(f"version: 1\ntemplates:\n  - {{id: t, subject: s, body: '{body}'}}\n")


def test_duplicate_ids() -> None:
    item = "  - {id: t, subject: s, body: b}\n"
    doc = "version: 1\ntemplates:\n" + item + item
    with pytest.raises(InvalidInputError, match="duplicate"):
        load_templates(doc)
