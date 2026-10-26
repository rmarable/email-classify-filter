"""Case card -> byte-identical `.eml` (GENERATE-FAKE-TESTING-EMAILS.md).

Everything that would normally vary is fixed: the Message-ID comes from the card id, the Date
from the card, MIME boundaries from the card id, and the Received chain uses mx.example.net and
the TEST-NET-1 block (192.0.2.0/24). Files up to 1 MB go to `eml/`; larger ones to `.build/`.
"""

from __future__ import annotations

import hashlib
import html
from collections.abc import Iterator
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage, Message
from email.utils import format_datetime, parseaddr
from pathlib import Path
from typing import cast

from ecf.eval import labels as label_file
from ecf.eval.cards import Card, load_cards
from ecf.eval.hygiene import Finding, cut, dedupe, scan_text

COMMIT_LIMIT = 1024 * 1024
LONG_FIELDS = ("signature", "quoted_thread", "footer")  # a long card's text besides its body
MX = "mx.example.net"


def message_id(card: Card) -> str:
    if card.message_id:
        return card.message_id
    h = hashlib.sha256(card.id.encode()).hexdigest()[:10]
    return f"<{card.id}.{h}@synthetic.acme.example>"


def _filler(target_bytes: int) -> str:
    lines: list[str] = []
    size, n = 0, 0
    while size < target_bytes:
        n += 1
        line = f"Filler line {n:07d}: the quick brown fox jumps over the lazy dog.\n"
        lines.append(line)
        size += len(line)
    return "".join(lines)


def _set_boundaries(msg: Message, card_id: str) -> None:
    for i, part in enumerate(p for p in msg.walk() if p.is_multipart()):
        part.set_boundary(f"=_ecf_{card_id}_{i}")


def build_message(card: Card) -> EmailMessage:
    msg = EmailMessage(policy=policy.SMTP)
    sender_domain = parseaddr(card.from_)[1].rsplit("@", 1)[-1] or "sender.example"
    msg["Received"] = (
        f"from mail.{sender_domain} (mail.{sender_domain} [192.0.2.10]) by {MX} "
        f"with ESMTPS id {card.id}; {format_datetime(card.date)}"
    )
    for ar in card.auth_results:
        msg["Authentication-Results"] = ar
    msg["From"] = card.from_
    msg["To"] = ", ".join(card.to)
    if card.cc:
        msg["Cc"] = ", ".join(card.cc)
    if card.reply_to:
        msg["Reply-To"] = card.reply_to
    msg["Subject"] = card.subject
    msg["Date"] = format_datetime(card.date)
    msg["Message-ID"] = message_id(card)
    msg["MIME-Version"] = "1.0"
    if card.bulk:
        msg["List-Id"] = "<news.newsletters.example>"
        msg["List-Unsubscribe"] = "<mailto:unsubscribe@newsletters.example>"
    for name, value in card.headers.items():
        del msg[name]
        msg[name] = value

    body = card.plain_text()
    if card.pad_to_mb:
        body += _filler(int(card.pad_to_mb * 1024 * 1024))
    msg.set_content(body, cte=card.encoding)
    html_body = card.html
    if card.hidden_text is not None:
        base = html_body or f"<p>{html.escape(card.plain_text())}</p>"
        html_body = base + f'<span style="display:none">{html.escape(card.hidden_text)}</span>'
    if html_body is not None:
        msg.add_alternative(html_body, subtype="html", cte=card.encoding)
    for att in card.attachments:
        if att.generate == "invoice_pdf":
            from ecf.eval.pdf import invoice_pdf  # noqa: PLC0415 - optional extra

            data = invoice_pdf(att, seed=f"{card.id}/{att.name}")
            msg.add_attachment(data, maintype="application", subtype="pdf", filename=att.name)
        else:
            maintype, _, subtype = att.content_type.partition("/")
            data = (att.text or "").encode("utf-8")
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=att.name)
    _set_boundaries(msg, card.id)
    return msg


def build_bytes(card: Card) -> bytes:
    return build_message(card).as_bytes(policy=policy.SMTP)


def _header_texts(value: object, path: str) -> Iterator[tuple[str, str]]:
    """Every string in the card's header with its field path, dict keys included (header names,
    label and fact names), so each is scanned on its own (eval generate plan, Checks)."""
    if isinstance(value, dict):
        for k, v in cast(dict[str, object], value).items():
            # the top-level names are the fixed Card fields; nested keys can be the author's
            if path:
                yield path, k
            yield from _header_texts(v, f"{path}.{k}" if path else k)
    elif isinstance(value, list):
        for i, v in enumerate(cast(list[object], value)):
            yield from _header_texts(v, f"{path}[{i}]")
    elif value is not None and not isinstance(value, bool):
        yield path, str(value)


def scan_card(card: Card, source: str) -> list[Finding]:
    """Findings named by field: `<source>:<field path>`, `<source>:body`, `<source>:html`."""
    header = card.model_dump(mode="json", by_alias=True, exclude={"body", "html"})
    # the body as built: a long card's padding and quote headers are scanned too
    texts = [*_header_texts(header, ""), ("body", card.plain_text()), ("html", card.html or "")]
    found = cut(dedupe([f for part, text in texts for f in scan_text(text, f"{source}:{part}")]))
    # a long field's value is in the built body too: reported once, under its field
    named = {f.value for f in found if f.where.split(":", 1)[1].startswith(LONG_FIELDS)}
    return [f for f in found if not (f.where == f"{source}:body" and f.value in named)]


@dataclass(frozen=True)
class BuildReport:
    built: list[str]
    large: list[str]
    findings: list[Finding]
    # (card id, phrase name, where it starts in the plain body, moved below the mark): so an
    # author sees each long card's band before labelling it (window tuning B3)
    placed: list[tuple[str, str, int, bool]] = field(
        default_factory=list[tuple[str, str, int, bool]]
    )


def build_all(root: Path) -> BuildReport:
    """Build every card under `root/cases`. Stops before writing anything if hygiene fails."""
    cards = load_cards(root / "cases")
    findings = [f for c in cards for f in scan_card(c, f"{c.id}.md")]
    if findings:
        return BuildReport([], [], findings)
    eml_dir, big_dir = root / "eml", root / ".build"
    eml_dir.mkdir(exist_ok=True)
    built: list[str] = []
    large: list[str] = []
    labels: list[dict[str, object]] = []
    for card in cards:
        data = build_bytes(card)
        target = (big_dir if len(data) > COMMIT_LIMIT else eml_dir) / f"{card.id}.eml"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(data)
        (large if target.parent == big_dir else built).append(card.id)
        labels.append(
            {
                "id": card.id,
                "file": str(target.relative_to(root)),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "author": card.author,
                **({"generator": card.generator} if card.generator else {}),
                # only when not the default, so older sets keep their version (OD-443)
                **({"profile": card.profile} if card.profile != "org" else {}),
                # only when set, so other cards' rows stay as they were (OD-555)
                **({"signal": card.signal} if card.signal else {}),
                **({"injection_phrase": card.injection_phrase} if card.injection_phrase else {}),
                "expected": card.expected.model_dump(mode="json"),
            }
        )
    # your confirmations stay while the case is unchanged (ecf eval label; OD-241)
    label_file.write(
        root,
        label_file.carry_over(label_file.read(root), labels, models_ok=label_file.models_ok(root)),
    )
    placed = [(c.id, *p) for c in cards for p in c.phrase_offsets()]
    return BuildReport(built, large, [], placed)
