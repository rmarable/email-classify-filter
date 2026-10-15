"""Preset D in the service (v3.0.0 step 4; OD-509, OD-510; plan v3.0.0-STEP4 §2, §7): where D
items wait, the drain (pick order, claims, the guarded store, the service-mode client), failures
and Jev down, the probe and the alert, the keys. No real calls: TypeSafe is a MockTransport and
`tests/conftest.py` makes the real transport raise."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from ecf.schema import load_schema
from ecf_server import (
    classifier,
    claude_pins,
    claude_queue,
    claude_review,
    jev,
    jev_dispatch,
    jev_keys,
    jev_mapping,
    rules,
    settings,
)
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.jev_dispatch import Live
from ecf_server.notify import FakeNotifier
from ecf_server.secretstore.memory import MemorySecretStore
from tests.test_claude_review import REQUEST, add, row, waiting_item
from tests.test_jev import body as jev_body

KEY = "ts-test-key-CANARY-91c2"  # invented
PIN = jev.load_pin()
# Jev's digests for the shipped schema and starter rules, pinned byte for byte (step 4, P5);
# re-pinned to step 3's settings (OD-512)
SHIPPED_GATE_KEY = "jev:jev-1.13.0+80bfb4452a206f02+jev-s1-state-591085348159+756d5a46eeb1a384"
SHIPPED_PROMPT_DIGEST = "f813d59a29cf2f943be5421d0acced4141f97b6f0313dbf1b5babf27cc9d3297"


def ok(**over: Any) -> Any:
    def go(_r: httpx.Request) -> httpx.Response:
        body = jev_body() | {"usage": {"input_tokens": 2374, "output_tokens": 437}} | over
        return httpx.Response(200, json=body)

    return go


def status(code: int, headers: dict[str, str] | None = None) -> Any:
    def go(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(code, json={"detail": "x"}, headers=headers or {})

    return go


class Script:
    """A MockTransport handler answering from a list (the last answer repeats)."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.seen: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.seen.append(req)
        go = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return go(req)


def store(key: str | None = KEY) -> MemorySecretStore:
    s = MemorySecretStore()
    if key:
        s.set(jev_keys.KEY_SECRET, key)
    return s


def rig(*answers: Any, clock: FakeClock | None = None) -> tuple[Live, Script, list[float]]:
    script = Script(*answers)
    waits: list[float] = []

    def wait(s: float) -> bool:
        waits.append(s)
        if clock is not None:
            clock.advance(s)
        return False

    live = Live(transport=httpx.MockTransport(script), wait=wait, spawn=lambda fn: fn(),
                resolves=lambda _h: True)  # fmt: skip
    return live, script, waits


def add_d(conn: sqlite3.Connection, clock: FakeClock, aid: str = "d", *, consent: bool = True,
          paused: bool = False) -> None:  # fmt: skip
    add(conn, clock, aid, "D")
    with write_tx(conn):
        if consent:
            conn.execute("UPDATE addresses SET overrides = json_set(overrides, ?, ?) WHERE"
                         " address_id = ?",
                         (f"$.{jev_keys.CONSENT}", to_ts(clock.now()), aid))  # fmt: skip
        conn.execute("UPDATE addresses SET paused = ? WHERE address_id = ?", (int(paused), aid))


def d_item(conn: sqlite3.Connection, clock: FakeClock, aid: str = "d", n: int = 0, *,
           marked: bool = False) -> str:  # fmt: skip
    facts = {"reprocess": "run-1"} if marked else None
    from tests.test_decide import KNOWN_BULK  # noqa: PLC0415

    return waiting_item(conn, clock, aid, n, facts=(KNOWN_BULK | facts) if facts else None)


def drain(conn: sqlite3.Connection, clock: FakeClock, live: Live,
          s: MemorySecretStore | None = None) -> jev_dispatch.Outcome:  # fmt: skip
    jev_dispatch.refresh_keys(conn, clock)
    the_store = s or store()
    return jev_dispatch.drain(conn, clock, lambda: the_store, live, FakeNotifier())


def tick(conn: sqlite3.Connection, clock: FakeClock, live: Live, db_path: Path, *,
         awake: float = 60.0, woke: bool = False, s: MemorySecretStore | None = None,
         notifier: FakeNotifier | None = None) -> dict[str, Any]:  # fmt: skip
    from ecf_server import db  # noqa: PLC0415

    the_store = s or store()
    return jev_dispatch.tick(conn, clock, live, notifier or FakeNotifier(),
                             lambda: db.connect(db_path), lambda: the_store, awake=awake,
                             woke=woke)  # fmt: skip


@pytest.fixture(autouse=True)
def _wake() -> Any:
    jev_keys.WAKE.clear()
    yield
    jev_keys.WAKE.clear()


# The keys: byte-stable digests, stored for the readers that can't import `jev` (P6).


def test_jevs_gate_key_and_prompt_digest_are_byte_stable() -> None:
    """A change to either moves D's pin key and restarts every D gate: deliberate only. The one
    place the shipped digests are written down (SHIPPED_GATE_KEY, SHIPPED_PROMPT_DIGEST)."""
    s = load_schema()
    assert jev.gate_key(s, rules.load_starter_rules(s)) == SHIPPED_GATE_KEY
    assert jev.prompt_digest(s) == SHIPPED_PROMPT_DIGEST


def test_the_keys_are_stored_for_ds_pins(conn: sqlite3.Connection, clock: FakeClock) -> None:
    add_d(conn, clock)
    before = claude_pins.pins(conn, "D")
    assert before["jev"] == before["prompt"] == jev_keys.UNKNOWN  # no service ran yet
    doc = jev_dispatch.refresh_keys(conn, clock)
    s = load_schema()
    assert doc["gate_key"] == jev.gate_key(s, rules.load_starter_rules(s))
    assert jev_keys.stored(conn) == doc
    pins = claude_pins.pins(conn, "D")
    assert set(pins) == {"actor", "actor_high", "schema", "jev", "prompt"}
    assert pins["jev"] == doc["gate_key"] and pins["prompt"] == doc["prompt_digest"][:16]
    assert claude_pins.address_key(conn, "d") != claude_pins.key(claude_pins.pins(conn, "C"))
    clock.advance(5)
    assert jev_dispatch.refresh_keys(conn, clock) == doc  # unchanged: not written again
    assert conn.execute("SELECT updated_at FROM settings WHERE key = ?",
                        (jev_keys.KEYS,)).fetchone()[0] < to_ts(clock.now())  # fmt: skip


def test_jev_keys_reads_the_lock_and_names_the_model() -> None:
    assert jev_keys.pin() == PIN
    assert jev_keys.display_name(PIN) == f"TypeSafe Jev {PIN.removeprefix('jev-')}"
    assert jev_keys.display_name("claude-sonnet-5-5") is None
    assert jev_keys.KEY_SECRET == jev.KEY_SECRET and jev_keys.HOST == jev.HOST
    assert jev_keys.mapping_digest() == jev_mapping.digest()  # step 3 ships the mapping


# Where D items wait (§2.1, P13): the Claude queue, but never C's counts or /ecf-review's classify.


def test_d_mail_waits_for_jev_not_for_ecf_review(conn: sqlite3.Connection,
                                                  clock: FakeClock) -> None:  # fmt: skip
    from tests.test_claude_queue import new_item, run_precheck  # noqa: PLC0415

    add_d(conn, clock)
    add(conn, clock, "c", "C")
    sid = new_item(conn, clock, "d")
    run_precheck(conn, clock, "d", [sid])
    assert row(conn, sid)["status"] == "awaiting_claude" and jev_keys.WAKE.is_set()
    c_sid = waiting_item(conn, clock, "c")
    assert claude_queue.waiting(conn) == {"c": 1}
    assert claude_queue.waiting_for_jev(conn) == {"d": 1}
    assert claude_queue.backlog(conn) == 1
    clock.advance(3 * 24 * 3600)
    late, later = claude_queue.overdue_twice(conn, clock.now(), 2)
    assert [o.address_id for o in late] == [o.address_id for o in later] == ["c"]
    q = claude_review.review_queue(conn, clock, "s1")
    assert [i["id"] for i in q["items"]] == [c_sid]  # never D's item to classify


def test_a_classified_d_item_goes_to_claudes_actor(conn: sqlite3.Connection,
                                                   clock: FakeClock) -> None:  # fmt: skip
    add_d(conn, clock)
    sid = waiting_item(conn, clock, "d", classification=REQUEST)  # the rule sends it to the actor
    assert claude_queue.waiting(conn) == {"d": 1} and not claude_queue.waiting_for_jev(conn)
    q = claude_review.review_queue(conn, clock, "s1")
    assert [(i["id"], i["need"], i["agent"]) for i in q["items"]] == [(sid, "act", "ecf-actor")]


# The drain (§2.2): pick order, scope, claims, the request, the store and its record.


def test_the_drain_classifies_new_mail_first_and_records_each_call(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add_d(conn, clock)
    old = d_item(conn, clock, "d", 1, marked=True)
    new = d_item(conn, clock, "d", 2)
    live, script, waits = rig(ok())
    out = drain(conn, clock, live)
    assert (out.stopped, out.ok) == (None, 2) and waits == []
    sent = [json.loads(r.content) for r in script.seen]
    assert [b["model"] for b in sent] == [PIN, PIN]
    assert "From: Ann <ann@cust.example>" in sent[0]["state"]  # as Claude's classifier gets it
    assert "Attachment: w9.pdf; application/pdf; 1200 bytes" in sent[0]["state"]
    assert "short text" in sent[0]["state"] and "image/png" not in sent[0]["state"]
    order = [r[0] for r in conn.execute("SELECT stable_id FROM audit WHERE event ="
                                        " 'item.classified' ORDER BY id")]  # fmt: skip
    assert order == [new, old]  # P12
    for sid in (new, old):
        r = row(conn, sid)
        assert r["classification"] is not None
        pinned = json.loads(r["pinned_models"])
        assert pinned["provider"] == "jev" and pinned["classifier"] == PIN
        assert pinned["pin_key"] == claude_pins.address_key(conn, "d")
        assert pinned["prompt"] == jev_keys.stored(conn)["prompt_digest"]
    calls = conn.execute("SELECT preset, role, outcome, backend, model, prompt_tokens, tries,"
                         " fault FROM model_calls ORDER BY id").fetchall()  # fmt: skip
    assert [tuple(c) for c in calls] == [("D", "classifier", "ok", "jev", PIN, 2374, 1, None)] * 2
    claims = conn.execute("SELECT session_id, state FROM claims").fetchall()
    assert {tuple(c) for c in claims} == {("jev", "done")}
    assert live.had_ok and live.last_ok is not None


def test_the_drain_skips_paused_removed_and_unagreed_addresses(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add_d(conn, clock, "p", paused=True)
    add_d(conn, clock, "n", consent=False)
    add_d(conn, clock, "r")
    for aid in ("p", "n", "r"):
        d_item(conn, clock, aid)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET removed_at = ? WHERE address_id = 'r'",
                     (to_ts(clock.now()),))  # fmt: skip
    live, script, _ = rig(ok())
    assert drain(conn, clock, live).ok == 0 and script.seen == []
    assert jev_dispatch.claimable(conn, clock.now()) == 0


def test_a_live_claim_and_its_grace_keep_the_drain_off(conn: sqlite3.Connection,
                                                        clock: FakeClock) -> None:  # fmt: skip
    add_d(conn, clock)
    sid = d_item(conn, clock)
    with write_tx(conn):
        conn.execute("INSERT INTO claims (stable_id, address_id, session_id, token_hash, fence,"
                     " need, agent, batch_id, claimed_at, expires_at, state) VALUES (?, 'd',"
                     " 's1', 'h', 1, 'classify', 'ecf-classifier', 'b', ?, ?, 'claimed')",
                     (sid, to_ts(clock.now()),
                      to_ts(clock.now() + timedelta(seconds=60))))  # fmt: skip
    live, script, _ = rig(ok())
    assert drain(conn, clock, live).ok == 0
    clock.advance(60 + 299)  # expired, but within GRACE: its agent may still be running
    assert drain(conn, clock, live).ok == 0
    clock.advance(2)
    assert drain(conn, clock, live).ok == 1 and len(script.seen) == 1
    assert conn.execute("SELECT session_id, fence FROM claims").fetchone()[:] == ("jev", 2)


def test_the_drains_claim_keeps_ecf_review_off_and_the_store_is_guarded(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """P3, both orders: the drain's claim hides the item from `/ecf-review`; a store over a
    classification touches nothing and writes no audit row."""
    add_d(conn, clock)
    sid = d_item(conn, clock)
    item = jev_dispatch.pick(conn, clock, [])
    assert item is not None and item["stable_id"] == sid
    with write_tx(conn):  # as if it were classified meanwhile
        conn.execute("UPDATE items SET classification = ? WHERE stable_id = ?",
                     (json.dumps(REQUEST), sid))  # fmt: skip
    audits = conn.execute("SELECT count(*) FROM audit").fetchone()[0]
    assert not classifier.store(conn, clock, sid, REQUEST, {"classifier": PIN}, provider="jev",
                                expected=claude_queue.Status.AWAITING_CLAUDE)  # fmt: skip
    assert conn.execute("SELECT count(*) FROM audit").fetchone()[0] == audits
    assert row(conn, sid)["status"] == "awaiting_claude"
    assert claude_review.review_queue(conn, clock, "s1")["items"] == []  # the drain's claim


# The service-mode client (P4): faults return at once; at most one short Retry-After.


def test_the_service_client_never_retries_or_waits_on_a_fault() -> None:
    clock = FakeClock()
    live, script, waits = rig(status(503), clock=clock)
    c = jev_dispatch.ServiceClient(lambda: KEY, transport=live.transport, clock=clock,
                                   wait=live.wait)  # fmt: skip
    res = c.classify(load_schema(), jev.Email("A", "a@x.example", "s", "", "t"), ())
    assert (res.fault, res.tries, waits) == ("provider", 1, [])
    script.answers = [status(429, {"retry-after": "5"}), status(429, {"retry-after": "5"})]
    with pytest.raises(jev.JevError) as err:
        c.classify(load_schema(), jev.Email("A", "a@x.example", "s", "", "t"), ())
    assert err.value.cls == "provider" and waits == [5.0]  # one short wait, then a fault
    script.answers = [status(429, {"retry-after": "5"}), ok()]
    waits.clear()
    res = c.classify(load_schema(), jev.Email("A", "a@x.example", "s", "", "t"), ())
    assert res.classification is not None and waits == [5.0]
    script.answers = [status(429, {"retry-after": str(int(jev.SHORT_WAIT_S) + 1)})]
    waits.clear()
    with pytest.raises(jev.JevError) as err:
        c.classify(load_schema(), jev.Email("A", "a@x.example", "s", "", "t"), ())
    assert err.value.cls == "long_wait" and waits == []
    assert max([*waits, 0.0]) <= jev.SHORT_WAIT_S


def test_each_drain_builds_its_own_http_client(conn: sqlite3.Connection, clock: FakeClock,
                                              monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """A transport error (after a wake, say) ends the drain; the next drain's client is new."""
    made: list[httpx.Client] = []
    real = jev.http_client

    def counting(*a: Any, **kw: Any) -> httpx.Client:
        made.append(real(*a, **kw))
        return made[-1]

    monkeypatch.setattr(jev, "http_client", counting)

    def broken(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=r)

    add_d(conn, clock)
    d_item(conn, clock)
    live, script, _ = rig(broken)
    assert drain(conn, clock, live).stopped == "provider"
    script.answers = [ok()]
    assert drain(conn, clock, live).ok == 1
    assert len(made) == 2 and made[0] is not made[1] and made[0].is_closed


# Failures (§2.3, P11): item-specific ones count; a locked store is quiet.


def test_an_answer_that_doesnt_map_counts_and_the_limit_waits_with_an_error(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add_d(conn, clock)
    sid = d_item(conn, clock)
    live, _, _ = rig(ok(answers={}))
    notifier = FakeNotifier()
    jev_dispatch.refresh_keys(conn, clock)
    for n in range(1, jev_dispatch.ATTEMPTS + 1):
        out = jev_dispatch.drain(conn, clock, store, live, notifier)
        assert out.ok == 0
        assert json.loads(row(conn, sid)["facts"])["jev_attempts"] == n
    assert jev_dispatch.claimable(conn, clock.now()) == 0  # it waits
    assert [t for t, _ in notifier.sent] == ["[ecf-alert] System Error"]
    assert sid[:8] in notifier.sent[0][1] and "W-9" not in notifier.sent[0][1]
    st = jev_dispatch.status(conn, live, store())
    assert st["at_limit"] == [sid[:8]]


def test_request_errors_before_any_success_read_as_the_pin(conn: sqlite3.Connection,
                                                            clock: FakeClock) -> None:  # fmt: skip
    add_d(conn, clock)
    sids = [d_item(conn, clock, "d", n) for n in range(4)]
    live, script, _ = rig(status(400))
    out = drain(conn, clock, live)
    assert out.stopped == "pin" and len(out.request_errors) == jev_dispatch.PIN_AFTER
    assert len(script.seen) == jev_dispatch.PIN_AFTER  # distinct items, then it stopped
    assert all("jev_attempts" not in json.loads(row(conn, s)["facts"]) for s in sids)
    live.had_ok = True  # once Jev answered, a request error is the item's own
    assert drain(conn, clock, live).stopped == "pin"
    assert sum("jev_attempts" in json.loads(row(conn, s)["facts"]) for s in sids) == 3


def test_a_locked_store_is_a_quiet_pause(conn: sqlite3.Connection, clock: FakeClock,
                                         db_path: Path) -> None:  # fmt: skip
    add_d(conn, clock)
    d_item(conn, clock)
    s = store()
    s.locked = True
    live, script, _ = rig(ok())
    assert drain(conn, clock, live, s).stopped == "locked" and script.seen == []
    live.outcomes.append(jev_dispatch.Outcome(stopped="locked"))
    st = tick(conn, clock, live, db_path, s=s)
    assert not st.get("down") and not st.get("faulting")


# Jev down (§2.3, OD-510): at once, by the awake-online timer, liveness; the probe; the alert.


def test_auth_pin_and_long_waits_take_jev_down_at_once(conn: sqlite3.Connection,
                                                        clock: FakeClock,
                                                        db_path: Path) -> None:  # fmt: skip
    add_d(conn, clock)
    d_item(conn, clock)
    for cls, reason in (("auth", "key"), ("pin", "pin"), ("long_wait", "long_wait")):
        with write_tx(conn):
            conn.execute("DELETE FROM settings WHERE key = ?", (jev_keys.STATE,))
        live, _, _ = rig(status(500))
        live.spawn = lambda _fn: None  # no drain or probe this time
        live.outcomes.append(jev_dispatch.Outcome(stopped=cls))
        st = tick(conn, clock, live, db_path)
        assert st["down"] and st["reason"] == reason, cls


def test_the_drain_takes_jev_down_on_a_refused_key(conn: sqlite3.Connection, clock: FakeClock,
                                                   db_path: Path) -> None:  # fmt: skip
    add_d(conn, clock)
    sid = d_item(conn, clock)
    live, _, _ = rig(status(401))
    notifier = FakeNotifier()
    st = tick(conn, clock, live, db_path, notifier=notifier)  # starts the drain (inline)
    assert not st.get("down")
    st = tick(conn, clock, live, db_path, notifier=notifier)
    assert st["down"] and st["reason"] == "key"
    assert row(conn, sid)["status"] == "awaiting_claude"  # it waits: no Claude fallback
    assert conn.execute("SELECT state FROM claims").fetchone()[0] == "released"
    alert = conn.execute("SELECT kind, address_id, detail FROM alerts").fetchone()
    assert alert[0] == "jev_down" and alert[1] is None
    assert "1 preset D email(s) wait" in alert[2] and "no Claude fallback" in alert[2]
    assert conn.execute("SELECT data FROM audit WHERE event = 'jev.down'").fetchone()[0] == (
        '{"reason": "key"}')  # fmt: skip


def test_the_down_timer_counts_awake_online_time_only(conn: sqlite3.Connection,
                                                       clock: FakeClock,
                                                       db_path: Path) -> None:  # fmt: skip
    add_d(conn, clock)
    d_item(conn, clock)
    live, _, _ = rig(status(503))
    online = [True]
    live.resolves = lambda _h: online[0]
    st = tick(conn, clock, live, db_path)  # the drain faults
    st = tick(conn, clock, live, db_path, awake=600)
    assert st["faulting"] and st["fault_s"] == 600 and not st.get("down")
    online[0] = False
    st = tick(conn, clock, live, db_path, awake=600)  # offline: paused
    assert st["fault_s"] == 600 and not st.get("down")
    online[0] = True
    st = tick(conn, clock, live, db_path, awake=600, woke=True)  # a wake resets it
    assert st["fault_s"] == 0 and not st.get("down")
    for _ in range(14):
        st = tick(conn, clock, live, db_path, awake=60)
        assert not st.get("down")
    st = tick(conn, clock, live, db_path, awake=60)  # 15 minutes awake and online
    assert st["down"] and st["reason"] == "faults"


def test_the_down_after_setting_bounds(conn: sqlite3.Connection, clock: FakeClock) -> None:
    assert settings.get(conn, "jev_down_after_minutes") == 15
    settings.set_value(conn, clock, "jev_down_after_minutes", "5", address=None, actor="t")
    assert settings.get(conn, "jev_down_after_minutes") == 5
    from ecf.errors import InvalidInputError  # noqa: PLC0415

    for bad in ("4", "1441"):
        with pytest.raises(InvalidInputError):
            settings.set_value(conn, clock, "jev_down_after_minutes", bad, address=None,
                               actor="t")  # fmt: skip


def test_a_stalled_drain_takes_jev_down(conn: sqlite3.Connection, clock: FakeClock,
                                        db_path: Path) -> None:  # fmt: skip
    add_d(conn, clock)
    d_item(conn, clock)
    live, _, _ = rig(ok())
    live.running, live.progress_at = True, to_ts(clock.now())
    assert not tick(conn, clock, live, db_path).get("down")
    clock.advance(15 * 60)
    st = tick(conn, clock, live, db_path)
    assert st["down"] and st["reason"] == "stalled"


def test_the_probe_brings_jev_back_after_two_successes(conn: sqlite3.Connection,
                                                        clock: FakeClock,
                                                        db_path: Path) -> None:  # fmt: skip
    add_d(conn, clock)
    d_item(conn, clock)
    live, script, _ = rig(status(503))
    notifier = FakeNotifier()
    live.outcomes.append(jev_dispatch.Outcome(stopped="auth"))
    st = tick(conn, clock, live, db_path, notifier=notifier)
    assert st["down"] and script.seen == []  # the first probe waits out its cool-down
    clock.advance(30)
    st = tick(conn, clock, live, db_path, notifier=notifier)  # the probe faults (503)
    assert len(script.seen) == 1 and json.loads(script.seen[0].content)["state"] == (
        jev.KEY_CHECK_STATE)  # no email content  # fmt: skip
    st = tick(conn, clock, live, db_path, notifier=notifier)
    assert st["down"] and st["level"] == 1 and st["probe_ok"] == 0
    script.answers = [ok(answers={"check": {"noul": 0.9}})]
    clock.advance(60)
    tick(conn, clock, live, db_path, notifier=notifier)  # probe 1 succeeds
    st = tick(conn, clock, live, db_path, notifier=notifier)  # read; probe 2 at once
    assert st["down"] and st["probe_ok"] == 1
    st = tick(conn, clock, live, db_path, notifier=notifier)  # read: back
    assert not st["down"]
    alert = conn.execute("SELECT resolved_at FROM alerts WHERE kind = 'jev_down'").fetchone()
    assert alert[0] is not None
    events = [r[0] for r in conn.execute("SELECT event FROM audit WHERE event LIKE 'jev.%' OR"
                                         " event LIKE 'alert.%' ORDER BY id")]  # fmt: skip
    assert events == ["jev.down", "alert.opened", "jev.up", "alert.resolved"]
    probes = conn.execute("SELECT role, outcome FROM model_calls WHERE role = 'probe'").fetchall()
    assert [tuple(p) for p in probes] == [("probe", "error"), ("probe", "ok"), ("probe", "ok")]


def test_a_retired_pin_probe_keeps_jev_down_and_says_upgrade(
    conn: sqlite3.Connection, clock: FakeClock, db_path: Path
) -> None:
    add_d(conn, clock)
    d_item(conn, clock)
    live, _, _ = rig(status(400))
    live.outcomes.append(jev_dispatch.Outcome(stopped="provider"))
    with write_tx(conn):  # down for faults first
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?,"
                     " ?, 'service')", (jev_keys.STATE, json.dumps(
                         {"down": True, "reason": "faults", "since": to_ts(clock.now()),
                          "probe_at": to_ts(clock.now()), "level": 0, "probe_ok": 0}),
                         to_ts(clock.now())))  # fmt: skip
    tick(conn, clock, live, db_path)
    st = tick(conn, clock, live, db_path)
    assert st["down"] and st["reason"] == "pin"
    assert "upgrade ecf" in jev_dispatch.detail(conn, st)


def test_the_alert_opens_once_updates_and_is_capped_per_day(
    conn: sqlite3.Connection, clock: FakeClock, db_path: Path
) -> None:
    add_d(conn, clock)
    d_item(conn, clock)
    live, _, _ = rig(status(503))
    live.spawn = lambda _fn: None
    notifier = FakeNotifier()
    for n in range(jev_dispatch.REOPEN_CAP + 1):
        live.outcomes.append(jev_dispatch.Outcome(stopped="auth"))
        st = tick(conn, clock, live, db_path, notifier=notifier)
        assert st["down"]
        d_item(conn, clock, "d", 10 + n)  # one more waiting: the detail follows
        tick(conn, clock, live, db_path, notifier=notifier)
        detail = conn.execute("SELECT detail FROM alerts WHERE kind = 'jev_down'").fetchone()[0]
        if n < jev_dispatch.REOPEN_CAP:
            assert f"{n + 2} preset D email(s) wait" in detail
        live.probes += ["ok", "ok"]  # back
        assert not tick(conn, clock, live, db_path, notifier=notifier)["down"]
        assert conn.execute("SELECT count(*) FROM alerts WHERE kind = 'jev_down' AND"
                            " resolved_at IS NULL").fetchone()[0] == 0  # fmt: skip
    opened = [t for t, _ in notifier.sent if t == "[ecf-alert] System Error"]
    assert len(opened) == jev_dispatch.REOPEN_CAP  # the fourth down that day raises none


# Records (§5): `ecf stats`' Jev line, and what `ecf status` and doctor read.


def test_stats_show_a_jev_line(conn: sqlite3.Connection, clock: FakeClock) -> None:
    from ecf_server import stats  # noqa: PLC0415

    add_d(conn, clock)
    d_item(conn, clock)
    live, _, _ = rig(ok())
    drain(conn, clock, live)
    r = stats.report(conn, clock.now() - timedelta(days=1))
    assert r["all"]["calls"] == 0  # the local model's figures leave Jev out
    assert r["jev"]["calls"] == r["jev"]["emails"] == 1
    assert r["jev"]["line"].startswith("TypeSafe Jev: 1 calls for 1 email(s), about 2374 input")


def test_status_names_what_waits_and_who_hasnt_agreed(conn: sqlite3.Connection,
                                                      clock: FakeClock) -> None:  # fmt: skip
    from ecf.doctor import Level, jev_status_line, judge_jev  # noqa: PLC0415

    add_d(conn, clock)
    add_d(conn, clock, "n", consent=False)
    d_item(conn, clock)
    live, _, _ = rig(ok())
    st = jev_dispatch.status(conn, live, store())
    assert st["addresses"] == ["d", "n"] and st["no_consent"] == ["n"]
    assert st["waiting"] == 1 and st["key"] is True and st["pin"] == PIN
    line = jev_status_line(st)
    assert line is not None and "up, pin" in line and "1 waiting for Jev" in line
    checks = judge_jev({"jev": st})
    assert [(c.name, c.level) for c in checks] == [("jev", Level.OK),
                                                   ("jev consent n", Level.WARN)]  # fmt: skip
    assert judge_jev({"jev": st | {"key": False}})[0].level == Level.FAIL
    assert jev_status_line({"addresses": []}) is None


def test_another_version_in_a_reply_is_the_pin(conn: sqlite3.Connection, clock: FakeClock,
                                               db_path: Path) -> None:  # fmt: skip
    """§2.4: every reply's `model` must be `jev.lock`'s; another one takes Jev down (the pin)."""
    add_d(conn, clock)
    sid = d_item(conn, clock)
    live, _, _ = rig(ok(model="jev-9.9.9"))
    assert drain(conn, clock, live).stopped == "pin"
    assert row(conn, sid)["classification"] is None
    live.outcomes.append(jev_dispatch.Outcome(stopped="pin"))
    live.spawn = lambda _fn: None
    st = tick(conn, clock, live, db_path)
    assert st["down"] and st["reason"] == "pin"


def test_the_daily_summary_counts_mail_waiting_for_jev(conn: sqlite3.Connection,
                                                       clock: FakeClock) -> None:  # fmt: skip
    from ecf_server import daily  # noqa: PLC0415

    add_d(conn, clock)
    d_item(conn, clock)
    c = daily.card(conn, clock.now(), "2026-10-14")
    assert dict(c.fields)["d"].startswith("0 waiting on you, 1 waiting for Jev;")
    with write_tx(conn):
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?,"
                     " 'service')",
                     (jev_keys.STATE, '{"down": true}', to_ts(clock.now())))  # fmt: skip
    assert "TypeSafe Jev is down: 1 preset D email(s) wait" in daily.card(
        conn, clock.now(), "2026-10-14").text  # fmt: skip


# The step 4 diff review's fixes (R1-R15, 2026-10-15).


def _set_state(conn: sqlite3.Connection, clock: FakeClock, st: dict[str, Any]) -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?,"
                     " 'service') ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                     (jev_keys.STATE, json.dumps(st), to_ts(clock.now())))  # fmt: skip


def _attempts(conn: sqlite3.Connection, sid: str) -> int:
    return int(json.loads(row(conn, sid)["facts"]).get("jev_attempts") or 0)


def test_a_wake_restarts_the_liveness_clock(conn: sqlite3.Connection, clock: FakeClock,
                                            db_path: Path) -> None:  # fmt: skip
    """R1: a computer asleep longer than the limit isn't a stalled drain."""
    add_d(conn, clock)
    d_item(conn, clock)
    live, _, _ = rig(ok())
    live.running, live.progress_at = True, to_ts(clock.now())
    clock.advance(16 * 60)  # asleep
    st = tick(conn, clock, live, db_path, awake=1, woke=True)
    assert not st.get("down") and live.progress_at == to_ts(clock.now())
    clock.advance(60)
    assert not tick(conn, clock, live, db_path).get("down")
    clock.advance(15 * 60)  # awake and still no progress: stalled
    assert tick(conn, clock, live, db_path)["reason"] == "stalled"


def test_the_probe_ladder_is_capped(conn: sqlite3.Connection, clock: FakeClock,
                                    db_path: Path) -> None:  # fmt: skip
    """R2: days of failing probes never overflow the ladder."""
    cool_down = jev_dispatch._cool_down  # pyright: ignore[reportPrivateUsage]
    assert cool_down(2000) == jev.COOL_DOWN_MAX_S
    assert cool_down(jev_dispatch.LEVEL_CAP) == jev.COOL_DOWN_MAX_S > cool_down(
        jev_dispatch.LEVEL_CAP - 1)  # fmt: skip
    add_d(conn, clock)
    d_item(conn, clock)
    now = to_ts(clock.now())
    _set_state(conn, clock, {"down": True, "reason": "faults", "since": now, "probe_at": now,
                             "level": 2000, "probe_ok": 0})  # fmt: skip
    live, _, _ = rig(status(503))
    tick(conn, clock, live, db_path)  # the probe faults
    st = tick(conn, clock, live, db_path)
    assert st["down"] and st["level"] == jev_dispatch.LEVEL_CAP
    assert st["probe_at"] == to_ts(clock.now() + timedelta(seconds=jev.COOL_DOWN_MAX_S))


def test_one_item_typesafe_keeps_refusing_stops_being_sent(
    conn: sqlite3.Connection, clock: FakeClock, db_path: Path
) -> None:
    """R3: with no success yet, a request error that repeats on the same item in a later drain
    counts, so the item reaches the attempt limit and waits, named in a System Error."""
    add_d(conn, clock)
    sid = d_item(conn, clock)
    live, script, _ = rig(status(400))
    notifier = FakeNotifier()
    st: dict[str, Any] = {}
    for _ in range(10):
        st = tick(conn, clock, live, db_path, notifier=notifier)
        clock.advance(60)
    assert len(script.seen) == jev_dispatch.ATTEMPTS + 1  # the first refusal, then 3 counted
    assert _attempts(conn, sid) == jev_dispatch.ATTEMPTS and not st.get("down")
    assert jev_dispatch.claimable(conn, clock.now()) == 0
    errors = [b for t, b in notifier.sent if t == "[ecf-alert] System Error"]
    assert len(errors) == 1 and sid[:8] in errors[0]
    assert jev_dispatch.status(conn, live, store())["at_limit"] == [sid[:8]]


def test_the_request_error_streak_outlives_a_drain(conn: sqlite3.Connection, clock: FakeClock,
                                                   db_path: Path) -> None:  # fmt: skip
    """R3: distinct items refused across drains still read as the pin."""
    add_d(conn, clock)
    for n in range(3):
        d_item(conn, clock, "d", n)
    _set_state(conn, clock, {"request_errors": ["x1", "x2"]})
    live, script, _ = rig(status(400))
    out = drain(conn, clock, live)
    assert out.stopped == "pin" and len(script.seen) == 1 and out.ran
    live.outcomes.append(out)
    live.spawn = lambda _fn: None
    st = tick(conn, clock, live, db_path)
    assert st["down"] and st["reason"] == "pin"
    live.outcomes.append(jev_dispatch.Outcome(ok=1, ran=True))
    _set_state(conn, clock, {"request_errors": ["x1"]})
    assert "request_errors" not in tick(conn, clock, live, db_path)  # a success clears it


def test_an_outage_sends_a_few_emails_not_one_a_tick(conn: sqlite3.Connection,
                                                      clock: FakeClock,
                                                      db_path: Path) -> None:  # fmt: skip
    """R4: while faulting, a drain starts only at the end of a growing cool-down."""
    add_d(conn, clock)
    d_item(conn, clock)
    live, script, _ = rig(status(503))
    st: dict[str, Any] = {}
    for _ in range(30):
        st = tick(conn, clock, live, db_path)
        if st.get("down"):
            break
        clock.advance(60)
    assert st["down"] and st["reason"] == "faults"
    assert len(script.seen) <= 6  # was one a tick (about 15) before Jev went down
    script.answers = [ok()]
    _set_state(conn, clock, {"faulting": True, "fault_s": 0.0, "fault_level": 2,
                             "retry_at": to_ts(clock.now() - timedelta(seconds=1))})  # fmt: skip
    live.outcomes.clear()
    tick(conn, clock, live, db_path)  # the cool-down is over: a drain, which succeeds
    st = tick(conn, clock, live, db_path)
    assert not st.get("faulting") and "retry_at" not in st and "fault_level" not in st


def test_the_drains_queries_use_the_claude_queue_index(conn: sqlite3.Connection,
                                                        clock: FakeClock) -> None:  # fmt: skip
    """R5: the pick, the claimable count, the alert's count and the attempt-limit list read
    `items_claude_waiting`."""
    sql, args = jev_dispatch._claimable(clock.now())  # pyright: ignore[reportPrivateUsage]
    detail_sql = ("SELECT count(*), min(i.claude_since) FROM items i JOIN addresses a USING"  # noqa: S608
                  f" (address_id) WHERE a.removed_at IS NULL AND {claude_queue.IN_QUEUE} AND"
                  f" i.status = 'awaiting_claude' AND {claude_queue.FOR_JEV}")  # fmt: skip
    queries: list[tuple[str, list[Any]]] = [("SELECT count(*)" + sql, args),
               (jev_dispatch.next_sql(sql), [*args, "[]"]),
               (detail_sql, []),
               (jev_dispatch.at_limit_sql(), [jev_dispatch.ATTEMPTS])]  # fmt: skip
    for q, a in queries:
        plan = " | ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + q, a))
        assert "items_claude_waiting" in plan, (q, plan)
    import inspect  # noqa: PLC0415

    assert "claude_queue.IN_QUEUE" in inspect.getsource(jev_dispatch.detail)


def test_the_pick_checks_its_candidate_again_under_the_lock(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R5: a candidate taken between the read and the write transaction isn't claimed."""
    add_d(conn, clock)
    sid = d_item(conn, clock)
    with write_tx(conn):  # classified meanwhile
        conn.execute("UPDATE items SET classification = ? WHERE stable_id = ?",
                     (json.dumps(REQUEST), sid))  # fmt: skip
    stale = f"SELECT '{sid}' WHERE ? IS NOT NULL AND ? IS NOT NULL AND ? IS NOT NULL"
    monkeypatch.setattr(jev_dispatch, "next_sql", lambda _sql: stale)  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    assert jev_dispatch.pick(conn, clock, []) is None
    assert conn.execute("SELECT count(*) FROM claims").fetchone()[0] == 0


def test_answers_that_dont_map_stop_the_drain_and_take_jev_down(
    conn: sqlite3.Connection, clock: FakeClock, db_path: Path
) -> None:
    """R6: UNMAPPED_STOP in a row stop the drain (the answer format), with one System Error for
    the items that reached the limit, not one per item."""
    add_d(conn, clock)
    sids = [d_item(conn, clock, "d", n) for n in range(5)]
    with write_tx(conn):
        conn.execute("UPDATE items SET facts = json_set(facts, '$.jev_attempts', ?)",
                     (jev_dispatch.ATTEMPTS - 1,))  # fmt: skip
    live, script, _ = rig(ok(answers={}))
    notifier = FakeNotifier()
    jev_dispatch.refresh_keys(conn, clock)
    out = jev_dispatch.drain(conn, clock, store, live, notifier)
    assert out.stopped == "unmapped" and len(script.seen) == jev_dispatch.UNMAPPED_STOP
    assert sum(_attempts(conn, s) == jev_dispatch.ATTEMPTS for s in sids) == 3
    errors = [b for t, b in notifier.sent if t == "[ecf-alert] System Error"]
    assert len(errors) == 1 and "3 emails" in errors[0]
    assert sum(s[:8] in errors[0] for s in sids) == 3
    live.outcomes.append(out)
    live.spawn = lambda _fn: None
    st = tick(conn, clock, live, db_path)
    assert st["down"] and st["reason"] == "format"
    assert "answer format" in jev_dispatch.detail(conn, st)


def test_a_long_retry_after_delays_the_first_probe(conn: sqlite3.Connection, clock: FakeClock,
                                                   db_path: Path) -> None:  # fmt: skip
    """R8: the probe never asks before TypeSafe's long `Retry-After` (at most a day)."""
    add_d(conn, clock)
    d_item(conn, clock)
    live, _, _ = rig(status(429, {"retry-after": "3600"}))
    out = drain(conn, clock, live)
    assert out.stopped == "long_wait" and out.retry_after is not None
    assert out.retry_after >= 3599
    live.outcomes.append(out)
    live.spawn = lambda _fn: None
    st = tick(conn, clock, live, db_path)
    assert st["down"] and st["reason"] == "long_wait"
    probe_at = clock.now() + timedelta(seconds=out.retry_after)
    assert st["probe_at"] == to_ts(probe_at)
    _set_state(conn, clock, {})
    live.outcomes.append(jev_dispatch.Outcome(stopped="long_wait", retry_after=10**9))
    st = tick(conn, clock, live, db_path)
    assert st["probe_at"] == to_ts(clock.now() + timedelta(days=1))


def test_the_tick_never_looks_up_typesafe_itself(
    conn: sqlite3.Connection, clock: FakeClock, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R9: the DNS lookup runs in its own thread; the tick reads the last answer."""
    import socket  # noqa: PLC0415

    looked: list[str] = []

    def getaddrinfo(host: str, *_a: Any, **_k: Any) -> Any:
        looked.append(host)
        raise OSError("offline")

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    add_d(conn, clock)
    d_item(conn, clock)
    _set_state(conn, clock, {"faulting": True, "fault_s": 0.0,
                             "retry_at": to_ts(clock.now() + timedelta(hours=1))})  # fmt: skip
    queued: list[Any] = []
    live = Live(transport=httpx.MockTransport(Script(status(503))), spawn=queued.append)
    st = tick(conn, clock, live, db_path, awake=60)
    assert looked == [] and len(queued) == 1 and st["fault_s"] == 60  # last answer: online
    queued.pop()()  # the lookup, in its own thread
    assert looked == [jev_keys.HOST] and live.online is False and not live.resolving
    st = tick(conn, clock, live, db_path, awake=60)
    assert st["fault_s"] == 60 and looked == [jev_keys.HOST]  # offline: paused


def test_the_alert_detail_is_written_only_when_it_changes(
    conn: sqlite3.Connection, clock: FakeClock, db_path: Path
) -> None:
    """R10: not an UPDATE of the alert every tick while Jev is down."""
    add_d(conn, clock)
    d_item(conn, clock)
    live, _, _ = rig(status(503))
    live.spawn = lambda _fn: None
    live.outcomes.append(jev_dispatch.Outcome(stopped="auth"))
    tick(conn, clock, live, db_path)
    seen: list[str] = []
    conn.set_trace_callback(seen.append)
    try:
        for _ in range(5):
            clock.advance(60)
            tick(conn, clock, live, db_path)
        assert not [q for q in seen if q.startswith("UPDATE alerts")]
        d_item(conn, clock, "d", 9)  # one more waiting: the text changes
        tick(conn, clock, live, db_path)
        assert len([q for q in seen if q.startswith("UPDATE alerts")]) == 1
    finally:
        conn.set_trace_callback(None)
    detail = conn.execute("SELECT detail FROM alerts WHERE kind = 'jev_down'").fetchone()[0]
    assert "2 preset D email(s) wait" in detail


def test_the_drain_remembers_only_failed_items(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R11: `tried` holds the items that failed in this drain, not every one it stored."""
    add_d(conn, clock)
    sids = [d_item(conn, clock, "d", n) for n in range(3)]
    seen: list[list[str]] = []
    real = jev_dispatch.pick

    def pick(c: sqlite3.Connection, k: Any, tried: list[str]) -> Any:
        seen.append(list(tried))
        return real(c, k, tried)

    monkeypatch.setattr(jev_dispatch, "pick", pick)
    live, _, _ = rig(ok(answers={}), ok(), ok())
    assert drain(conn, clock, live).ok == 2
    failed = [s for s in sids if _attempts(conn, s)]
    assert len(failed) == 1 and seen[-1] == failed


def test_a_stalled_drain_is_told_to_stop_and_a_new_one_can_start(
    conn: sqlite3.Connection, clock: FakeClock, db_path: Path
) -> None:
    """R13: the stalled drain's own stop event is set and `running` clears; the old thread
    ending later leaves a newer drain alone. The service's halt stops a drain too."""
    add_d(conn, clock)
    d_item(conn, clock)
    queued: list[Any] = []
    live, _, _ = rig(ok())
    live.spawn = queued.append
    assert jev_dispatch.start_drain(lambda: conn, clock, store, live, FakeNotifier())
    old = live.drain_stop
    assert old is not None and live.running
    clock.advance(15 * 60)
    st = tick(conn, clock, live, db_path)
    assert st["reason"] == "stalled" and old.is_set() and not live.running
    assert jev_dispatch.start_drain(lambda: conn, clock, store, live, FakeNotifier())
    new = live.drain_stop
    assert new is not None and new is not old
    live.halt()
    assert new.is_set() and live.stop.is_set()


def test_a_drain_that_cant_spawn_isnt_left_running(clock: FakeClock) -> None:
    """R13: a failed thread start clears `running` (and `probing`) so the next tick tries."""
    live, _, _ = rig(ok())

    def broken(_fn: Any) -> None:
        raise RuntimeError("can't start new thread")

    live.spawn = broken
    assert not jev_dispatch.start_drain(lambda: sqlite3.connect(":memory:"), clock, store,
                                        live, FakeNotifier())  # fmt: skip
    assert not live.running and live.drain_stop is None
    assert not jev_dispatch.start_probe(lambda: sqlite3.connect(":memory:"), clock, store, live)
    assert not live.probing
    jev_dispatch.start_resolve(live)
    assert not live.resolving


def test_a_claim_ends_done_only_when_the_store_wrote(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R15: a store that wrote nothing releases the claim, and the drain doesn't ask again."""
    add_d(conn, clock)
    d_item(conn, clock)

    def store_nothing(*_a: object, **_k: object) -> bool:
        return False

    monkeypatch.setattr(classifier, "store", store_nothing)
    live, script, _ = rig(ok())
    assert drain(conn, clock, live).ok == 1 and len(script.seen) == 1
    assert conn.execute("SELECT state FROM claims").fetchone()[0] == "released"


def test_wake_starts_a_drain_between_ticks(conn: sqlite3.Connection, clock: FakeClock,
                                           db_path: Path) -> None:  # fmt: skip
    """R15: `jev_keys.WAKE` between ticks starts a drain when one may run, and clears."""
    from ecf_server import db  # noqa: PLC0415

    add_d(conn, clock)
    d_item(conn, clock)
    jev_dispatch.refresh_keys(conn, clock)
    live, script, _ = rig(ok())

    def wake() -> bool:
        jev_keys.WAKE.set()
        return jev_dispatch.wake(conn, clock, live, FakeNotifier(), lambda: db.connect(db_path),
                                 store)  # fmt: skip

    _set_state(conn, clock, {"down": True, "probe_at": to_ts(clock.now())})
    assert not wake() and not jev_keys.WAKE.is_set() and script.seen == []
    _set_state(conn, clock, {"faulting": True,
                             "retry_at": to_ts(clock.now() + timedelta(seconds=30))})  # fmt: skip
    assert not wake() and script.seen == []  # in a faulting cool-down
    _set_state(conn, clock, {})
    live.outcomes.append(jev_dispatch.Outcome())
    assert not wake()  # results the tick hasn't read: the tick decides
    live.outcomes.clear()
    assert wake() and len(script.seen) == 1


def test_the_service_timer_answers_wake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """R15: the timer's wait returns for `jev_keys.WAKE`, long before the next tick."""
    import threading  # noqa: PLC0415

    from ecf.paths import Paths  # noqa: PLC0415
    from ecf_server import service  # noqa: PLC0415

    monkeypatch.setattr(service, "WAKE_POLL_S", 0.01)
    svc = service.Service(Paths("t", tmp_path / "home"), FakeClock(),
                          service.Options(tick_seconds=3600), dev=True)  # fmt: skip
    woke = threading.Event()
    ticked: list[int] = []
    monkeypatch.setattr(svc, "_jev_wake", lambda: (jev_keys.WAKE.clear(), woke.set()))
    monkeypatch.setattr(svc, "tick", lambda: ticked.append(1))
    timer = threading.Thread(target=svc._timer, daemon=True)  # pyright: ignore[reportPrivateUsage]
    timer.start()
    jev_keys.WAKE.set()
    try:
        assert woke.wait(5.0) and ticked == []
    finally:
        svc.stop.set()
        timer.join(5.0)
    assert not timer.is_alive()


def test_the_daily_summary_makes_one_stats_report(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R15: the local-model and Jev lines share one `stats.report`."""
    from ecf_server import daily, stats  # noqa: PLC0415

    add_d(conn, clock)
    d_item(conn, clock)
    live, _, _ = rig(ok())
    drain(conn, clock, live)
    calls: list[int] = []
    real = stats.report

    def report(*a: Any, **k: Any) -> dict[str, Any]:
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(stats, "report", report)
    text = daily.card(conn, clock.now(), "2026-10-14").text
    assert len(calls) == 1 and "TypeSafe Jev: 1 calls" in text


def test_the_tick_reads_the_shipped_files_once(
    conn: sqlite3.Connection, clock: FakeClock, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R15: `jev.lock` (and `jev_mapping.json`) are read once, not every tick."""
    pin = jev_dispatch._pin  # pyright: ignore[reportPrivateUsage]
    pin.cache_clear()
    reads: list[int] = []
    real = jev.load_pin

    def load_pin() -> str:
        reads.append(1)
        return real()

    monkeypatch.setattr(jev, "load_pin", load_pin)
    live, _, _ = rig(ok())
    for _ in range(3):
        tick(conn, clock, live, db_path)
    assert len(reads) == 1
    before = jev_keys._data.cache_info().misses  # pyright: ignore[reportPrivateUsage]
    for _ in range(3):
        jev_keys.pin()
    assert jev_keys._data.cache_info().misses <= before + 1  # pyright: ignore[reportPrivateUsage]
