"""Sender authentication: ecf's own DKIM and DMARC evaluation (SPEC §7.2, §7.3). V1.1 step 7.

**auth_result**
- `pass`: at least one DKIM signature is valid, aligned with the From domain (the policy's
  `adkim`, relaxed by default), has no `l=` tag, signs From, Subject, Date, To and (when present)
  Reply-To, and no extra unsigned copies of those headers exist (OD-047). Unsigned MIME headers
  don't stop a pass; they set `mime_headers_signed = false`, which payment and fraud rules treat as
  `none` (OD-187).
- `fail` (OD-192): more than one From header; or the From domain's
  effective policy (after `t=y`) is quarantine or reject, signatures aligned with it are present,
  and every one of them is cryptographically broken (bad signature or body hash, with the key
  present). A missing aligned signature is never `fail`: the sender may pass DMARC by SPF, which
  ecf can't check after delivery.
- `none`: everything else, including DNS errors and messages over the size limit.

**DMARC discovery and Organizational Domains** follow RFC 9989 §4.10 (read 2026-09-29,
rfc-editor.org/rfc/rfc9989): query `_dmarc.<Author Domain>`; otherwise a DNS Tree Walk from the
parent (shortened to 7 labels when longer), stopping at a record with `psd=`; multiple records at
one name are discarded. The Organizational Domain is the first record with `psd=n`, else one label
below a `psd=y` record other than the start, else the record with the fewest labels; with no
record at all, the domain itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from ecf_server import _dkim
from ecf_server.dnscache import DnsCache
from ecf_server.message import PARTIAL_HASH_VERSION, ParsedMessage

Result = Literal["pass", "fail", "none"]
REQUIRED = ("from", "subject", "date", "to", "reply-to")  # when present (OD-047)
MIME = ("content-type", "mime-version", "content-transfer-encoding")  # OD-048, narrowed by OD-187
BROKEN = ("bad_signature", "bad_body_hash")
MAX_LABELS = 8
MAX_SIGNATURES = 8  # checked per message (V1.1 review, 2026-09-29)


@dataclass(frozen=True)
class Policy:
    domain: str  # where the record was found
    tags: dict[str, str]
    level: Literal["author", "organizational"]
    effective: str  # none | quarantine | reject after sp/np and t=y
    adkim: str  # r | s


@dataclass
class SigResult:
    d: str
    s: str
    outcome: str
    aligned: bool
    has_l: bool
    unsigned_required: list[str]
    mime_signed: bool
    passes: bool


@dataclass
class AuthOutcome:
    result: Result
    reason: str
    from_domain: str | None = None
    policy: dict[str, Any] | None = None
    signatures: list[SigResult] = field(default_factory=list[SigResult])
    mime_headers_signed: bool | None = None

    def facts(self) -> dict[str, Any]:
        """What goes into the item's facts (SPEC §7.3: shown on cards)."""
        return {
            "auth_result": self.result,
            "auth": {
                "reason": self.reason,
                "from_domain": self.from_domain,
                "dmarc_policy": self.policy,
                "dkim_domains_valid": sorted(
                    {s.d for s in self.signatures if s.outcome == "valid"}
                ),
                "alignment": [asdict(s) for s in self.signatures],
                "mime_headers_signed": self.mime_headers_signed,
            },
        }


def _unverifiable(parsed: ParsedMessage) -> AuthOutcome | None:
    """An outcome decided by the message's shape alone, before any signature is checked."""
    reasons: list[tuple[bool, Result, str]] = [
        (parsed.hash_version == PARTIAL_HASH_VERSION, "none", "oversized: not verified"),
        (parsed.from_count > 1, "fail", "more than one From header"),
        (not parsed.from_addr or "@" not in parsed.from_addr, "none", "no usable From address"),
        (
            parsed.from_ambiguous,
            "none",
            "ambiguous From header: parsers may disagree on the sender",
        ),
        (parsed.headers_ambiguous, "none", "bare CR in the headers: parsers may disagree on them"),
    ]
    return next((AuthOutcome(r, why) for hit, r, why in reasons if hit), None)


def evaluate(raw: bytes, parsed: ParsedMessage, dns: DnsCache) -> AuthOutcome:
    early = _unverifiable(parsed)
    if early is not None:
        return early
    author = _ascii_domain((parsed.from_addr or "").rsplit("@", 1)[1])
    if author is None:
        return AuthOutcome("none", "From domain isn't a valid domain name")
    try:
        sigs = _first_signatures(_dkim.signatures(raw), author)
        names = _dkim.header_names(raw)
    except _dkim.HeadersUnreadable:
        return AuthOutcome("none", "malformed header block: DKIM can't read it")
    dns.prefetch(
        [("_dmarc." + author, "TXT"), ("_dmarc." + _parent(author), "TXT")]
        + [(f"{g.tags['s']}._domainkey.{g.tags['d']}", "TXT") for g in sigs if _usable(g)]
    )
    try:
        policy = discover_policy(dns, author)
    except PolicyUnknownError:
        return AuthOutcome("none", "DNS error looking up the DMARC policy", author)
    adkim = policy.adkim if policy else "r"
    results = [_check(raw, g, author, adkim, names, dns) for g in sigs]
    out = AuthOutcome("none", "", author, asdict(policy) if policy else None, results)
    passing = [r for r in results if r.passes]
    if passing:
        out.result, out.reason = "pass", f"aligned DKIM pass (d={passing[0].d})"
        out.mime_headers_signed = any(r.mime_signed for r in passing)
        return out
    aligned = [r for r in results if r.aligned]
    enforcing = policy is not None and policy.effective in ("quarantine", "reject")
    if enforcing and aligned and all(r.outcome in BROKEN for r in aligned):
        out.result, out.reason = "fail", "every aligned DKIM signature is broken"
    else:
        out.reason = _why_none(results, policy, dns)
    return out


# ---- DMARC ----------------------------------------------------------------------------------


class PolicyUnknownError(Exception):
    """A DNS error on the way to the policy: SPEC §7.3 fails closed (`none`, never `pass`). RFC
    9989 §4.10.1 leaves DNS errors to the receiver; falling through to a parent's record could
    swap an author's strict alignment for a relaxed one (V1.1 review, 2026-09-29)."""


def _ascii_domain(domain: str) -> str | None:
    """The From domain as DKIM `d=` values are written: lowercase, A-labels for IDNs."""
    d = domain.rstrip(".").lower()
    try:
        return d.encode("idna").decode("ascii") if not d.isascii() else d
    except UnicodeError:
        return None


def _first_signatures(sigs: list[_dkim.Signature], author: str) -> list[_dkim.Signature]:
    """At most MAX_SIGNATURES, those that could align with the author first: each one re-reads
    the whole message, so a message with thousands would hold the check for hours (RFC 6376
    §6.1 lets a verifier limit how many it checks)."""

    def related(s: _dkim.Signature) -> bool:
        d = s.tags.get("d", "").rstrip(".").lower()
        return bool(d) and (d == author or author.endswith("." + d) or d.endswith("." + author))

    return sorted(sigs, key=lambda s: not related(s))[:MAX_SIGNATURES]


def discover_policy(dns: DnsCache, author: str) -> Policy | None:
    """RFC 9989 §4.10.1: the record at the Author Domain, else at its Organizational Domain.
    Raises PolicyUnknownError on a DNS error before a record is found."""
    own = _record(dns, author)
    if own is not None:
        return _policy(author, own, "author", dns, author)
    walked = _walk(dns, author, include_start=False)
    if not walked:
        return None
    org = _org_from(author, walked)
    chosen = next(((d, t) for d, t in walked if d == org), None)
    if chosen is None:  # e.g. the Org Domain came from a psd=y record one label up
        chosen = next(((d, t) for d, t in walked if t.get("psd") == "y"), walked[-1])
    return _policy(chosen[0], chosen[1], "organizational", dns, author)


def org_domain(dns: DnsCache, domain: str) -> str:
    return _org_from(domain, _walk(dns, domain, include_start=True))


def _policy(
    at: str,
    tags: dict[str, str],
    level: Literal["author", "organizational"],
    dns: DnsCache,
    author: str,
) -> Policy:
    p = tags.get("p", "none")
    if level == "organizational":
        if "np" in tags and not _exists(dns, author):
            p = tags["np"]
        elif "sp" in tags:
            p = tags["sp"]
    if p not in ("none", "quarantine", "reject"):
        p = "none"  # RFC 9989: an invalid policy is treated as none (with rua) or not applied
    if tags.get("t", "n") == "y":
        p = {"reject": "quarantine", "quarantine": "none"}.get(p, p)
    return Policy(at, tags, level, p, "s" if tags.get("adkim") == "s" else "r")


def _walk(dns: DnsCache, domain: str, *, include_start: bool) -> list[tuple[str, dict[str, str]]]:
    """(domain, record) pairs found on the way up, starting domain first; stops at psd=."""
    labels = domain.lower().rstrip(".").split(".")
    found: list[tuple[str, dict[str, str]]] = []
    targets: list[list[str]] = [labels] if include_start else []
    rest = labels[-(MAX_LABELS - 1) :] if len(labels) >= MAX_LABELS else labels[1:]
    while rest:
        targets.append(rest)
        rest = rest[1:]
    for t in targets:
        name = ".".join(t)
        tags = _record(dns, name)
        if tags is not None:
            found.append((name, tags))
            if tags.get("psd") in ("y", "n"):
                break
    return found


def _org_from(start: str, walked: list[tuple[str, dict[str, str]]]) -> str:
    for d, t in walked:
        if t.get("psd") == "n":
            return d
    for d, t in walked:
        if t.get("psd") == "y" and d != start:
            below = start.split(".")[-(len(d.split(".")) + 1) :]
            return ".".join(below)
    return min(walked, key=lambda x: x[0].count("."))[0] if walked else start


def _record(dns: DnsCache, domain: str) -> dict[str, str] | None:
    """The single valid DMARC record at `_dmarc.<domain>`, or None (none or several); a DNS
    error raises PolicyUnknownError."""
    a = dns.get(f"_dmarc.{domain}", "TXT")
    if a.status == "error":
        raise PolicyUnknownError(domain)
    records = [r for r in a.records if r.replace(" ", "").lower().startswith("v=dmarc1")]
    if a.status != "ok" or len(records) != 1:
        return None
    return _tags(records[0])


def _tags(record: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in record.split(";"):
        k, sep, v = part.strip().partition("=")
        if sep:
            out[k.strip().lower()] = v.strip().lower()
    return out


def _exists(dns: DnsCache, domain: str) -> bool:
    """RFC 9989 non-existent subdomain: NXDOMAIN for A, AAAA and MX. Unsure means it exists."""
    return not all(dns.get(domain, t).status == "nxdomain" for t in ("A", "AAAA", "MX"))


def _parent(domain: str) -> str:
    return domain.split(".", 1)[1] if "." in domain else domain


# ---- DKIM -----------------------------------------------------------------------------------


def _usable(sig: _dkim.Signature) -> bool:
    return bool(sig.tags.get("d")) and bool(sig.tags.get("s"))


def _check(
    raw: bytes, sig: _dkim.Signature, author: str, adkim: str, names: list[str], dns: DnsCache
) -> SigResult:
    d = sig.tags.get("d", "").rstrip(".").lower()
    s = sig.tags.get("s", "")
    if not _usable(sig):
        return SigResult(d, s, "format", False, False, [], False, False)
    seen: dict[str, str] = {}

    def dnsfunc(name: bytes, timeout: float = 3) -> bytes | None:
        del timeout  # the cache applies SPEC §7.3's timeouts
        a = dns.get(name.decode("ascii", "replace"), "TXT")
        seen["status"] = {"ok": "ok", "error": "error"}.get(a.status, "missing")
        return a.records[0].encode() if a.status == "ok" and a.records else None

    outcome = _dkim.verify(raw, sig.index, dnsfunc, lambda: seen.get("status", "missing"))
    signed = [h.strip().lower() for h in sig.tags.get("h", "").split(":") if h.strip()]
    present = {h: names.count(h) for h in (*REQUIRED, *MIME)}
    # a header counts as covered when h= lists it at least as often as it appears (§7.3)
    unsigned = [h for h in REQUIRED if present[h] and signed.count(h) < present[h]]
    if present["from"] == 0:
        unsigned.append("from")
    mime_signed = all(signed.count(h) >= present[h] for h in MIME if present[h])
    aligned = d == author if adkim == "s" else _relaxed(dns, d, author)
    has_l = "l" in sig.tags
    passes = outcome == "valid" and aligned and not has_l and not unsigned
    return SigResult(d, s, outcome, aligned, has_l, unsigned, mime_signed, passes)


def _relaxed(dns: DnsCache, d: str, author: str) -> bool:
    """Relaxed alignment: the same Organizational Domain. Domains under different top-level
    labels can't share one, which saves tree walks for unrelated signers (a mail service)."""
    if d == author:
        return True
    if d.rsplit(".", maxsplit=1)[-1] != author.rsplit(".", maxsplit=1)[-1]:
        return False
    try:
        return org_domain(dns, d) == org_domain(dns, author)
    except PolicyUnknownError:
        return False  # can't tell, so not aligned: `none`, never `pass`


def _why_none(results: list[SigResult], policy: Policy | None, dns: DnsCache) -> str:
    """The first reason that applies, for the card and the log."""
    aligned = [r for r in results if r.aligned]
    valid = [r for r in aligned if r.outcome == "valid"]
    checks: list[tuple[bool, str]] = [
        (dns.over_budget(), "DNS budget used up; retried next check"),
        (not results, "no DKIM signature"),
        (not aligned, "no DKIM signature aligned with the From domain"),
        (any(r.outcome == "dns_error" for r in results), "DNS error while fetching a DKIM key"),
        (bool(valid) and valid[0].has_l, "aligned signature has an l= tag"),
        (
            bool(valid),
            f"aligned signature leaves unsigned: {', '.join(valid[0].unsigned_required)}"
            if valid
            else "",
        ),
        (
            policy is None or policy.effective == "none",
            "aligned DKIM failed; no enforcing DMARC policy",
        ),
    ]
    return next((why for hit, why in checks if hit), "aligned DKIM not verifiable")
