"""Case card -> byte-identical `.eml` (GENERATE-FAKE-TESTING-EMAILS.md).

Everything that would normally vary is fixed: the Message-ID comes from the card id, the Date
from the card, MIME boundaries from the card id, and the Received chain uses mx.example.net and
the TEST-NET-1 block (192.0.2.0/24). Files up to 1 MB go to `eml/`; larger ones to `.build/`.
"""

from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage, Message
from email.utils import format_datetime, parseaddr
from pathlib import Path

from ecf.eval import labels as label_file
from ecf.eval.cards import Card, load_cards
from ecf.eval.hygiene import Finding, dedupe, scan_text

COMMIT_LIMIT = 1024 * 1024
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

    body = card.body
    if card.pad_to_mb:
        body += _filler(int(card.pad_to_mb * 1024 * 1024))
    msg.set_content(body, cte=card.encoding)
    html_body = card.html
    if card.hidden_text is not None:
        base = html_body or f"<p>{html.escape(card.body)}</p>"
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


def scan_card(card: Card, source: str) -> list[Finding]:
    texts = {
        "header": json.dumps(
            card.model_dump(mode="json", by_alias=True, exclude={"body", "html"}),
            ensure_ascii=False,
        ),
        "body": card.body,
        "html": card.html or "",
    }
    return dedupe([f for part, text in texts.items() for f in scan_text(text, f"{source}:{part}")])


@dataclass(frozen=True)
class BuildReport:
    built: list[str]
    large: list[str]
    findings: list[Finding]


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
                "expected": card.expected.model_dump(mode="json"),
            }
        )
    # your confirmations stay while the case is unchanged (ecf eval label; OD-241)
    label_file.write(root, label_file.carry_over(label_file.read(root), labels))
    return BuildReport(built, large, [])
