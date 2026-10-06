"""`ecf destroy`, the service's part (V1.5 step 12a; OD-383 to OD-393): the order of the steps,
the Slack app deleted or its bot token revoked, secrets, the record outside the data folder,
retries that skip what's done, refusals, the route and the start guard."""

from __future__ import annotations

import json
import sqlite3
import stat
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import (
    ConflictError,
    InvalidInputError,
    ServiceUnavailableError,
    StepupRequiredError,
)
from ecf.paths import Paths
from ecf_server import alert_mail, deadman, destroy, slack_admin, stepup
from ecf_server._slack import SlackError, SlackNetworkError
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.mail.fake import FakeSender
from ecf_server.notify import FakeNotifier
from ecf_server.secretstore.memory import MemorySecretStore
from ecf_server.stepper import FakeStepper
from tests.test_addresses import call, make_state
from tests.test_slack_out import FakeWeb

ME = "U0ME1"
BOT, CONFIG = "xoxb-bot", "xoxe-config"  # fake tokens


class Env:
    def __init__(self, conn: sqlite3.Connection, root: Path) -> None:
        self.conn, self.root = conn, root
        self.clock = FakeClock()
        self.notifier = FakeNotifier()
        self.store = MemorySecretStore()
        self.webs: dict[str, FakeWeb] = {BOT: FakeWeb(), CONFIG: FakeWeb()}
        self.sender = FakeSender()

    def web(self, token: str) -> FakeWeb:
        return self.webs[token]

    def ctx(self) -> destroy.Context:
        return destroy.Context("t", self.root, self.clock, self.notifier, self.store, self.web,
                               lambda _c, _aid: self.sender)  # fmt: skip

    def nonce(self) -> str:
        issued = stepup.issue(self.conn, self.clock, FakeStepper(), "destroy", {"install": "t"})
        stepup.verify(self.conn, self.clock, FakeStepper(), issued.nonce_id)
        return issued.nonce_id

    def run(self, *, token: str | None = None, typed: str = "t", **kw: Any) -> dict[str, Any]:
        return destroy.run(self.conn, self.ctx(), typed=typed, config_token=token,
                           nonce=self.nonce(), **kw)  # fmt: skip

    def record(self) -> dict[str, Any]:
        rec = destroy.read_record(self.root, "t")
        assert rec is not None
        return rec


@pytest.fixture
def env(conn: sqlite3.Connection, tmp_path: Path) -> Env:
    return make_env(conn, tmp_path / "root")


def make_env(conn: sqlite3.Connection, root: Path) -> Env:
    """Two addresses, Slack with its channels and dead-man's message, alert email, secrets."""
    e = Env(conn, root)
    now = to_ts(e.clock.now())
    with write_tx(conn):
        for aid in ("ap", "ar"):
            conn.execute(
                "INSERT INTO addresses (address_id, email, sensitivity, preset,"
                " created_at, smtp_host, smtp_port) VALUES (?, ?, 'standard', 'A', ?,"
                " 'smtp.acme.example', 465)",
                (aid, f"{aid}@acme.example", now),
            )
            conn.execute(
                "INSERT INTO routes (address_id, surface, route_ref, name) VALUES"
                " (?, 'slack', ?, ?)",
                (aid, f"C{aid.upper()}", f"ecf-t-{aid}"),
            )
        conn.execute(
            "INSERT INTO probe (address_id, host, probed_at) VALUES ('ap', 'imap.acme.example', ?)",
            (now,),
        )
        for k, v in (
            ("slack_app_id", "A1"),
            ("slack_team_id", "T1"),
            ("slack_member_id", ME),
            ("slack_summary_channel", "CSUM"),
            ("slack_summary_name", "ecf-t-summary"),
            (deadman.ID, "Q1"),
            (deadman.CHANNEL, "CSUM"),
            (alert_mail.FROM_KEY, "ap"),
            (alert_mail.TO_KEY, "owner@home.example"),
        ):
            slack_admin.put_setting(conn, k, v, now, actor="test")
    for name in ("mailbox/ap", "mailbox/ar", "slack/bot", "slack/app", "export-signing-seed"):
        e.store.set(name, BOT if name == "slack/bot" else "x")
    return e


def test_service_part_in_order(env: Env) -> None:
    rec = env.run()
    bot = env.webs[BOT].calls
    methods = [m for m, _ in bot]
    # the notice first (a DM and the summary channel), then the dead-man's message, the channels,
    # and the bot token last
    assert methods == ["chat.postMessage", "chat.postMessage", "chat.deleteScheduledMessage",
                       "conversations.archive", "conversations.archive", "conversations.archive",
                       "auth.revoke"]  # fmt: skip
    assert [p["channel"] for m, p in bot if m == "conversations.archive"] == ["CSUM", "CAP", "CAR"]
    assert env.webs[CONFIG].calls == []
    assert env.notifier.sent and "destroying the install t" in env.notifier.sent[0][1]
    assert len(env.sender.sent) == 1  # the alert email, sent there and then
    for name in ("mailbox/ap", "mailbox/ar", "slack/bot", "slack/app", "export-signing-seed"):
        assert env.store.get(name) is None
    assert env.conn.execute("SELECT count(*) FROM addresses WHERE paused = 0").fetchone()[0] == 0
    events = [r[0] for r in env.conn.execute("SELECT event FROM audit WHERE event LIKE"
                                             " 'destroy.%' ORDER BY id")]  # fmt: skip
    assert events == ["destroy.started", "destroy.completed"]
    path = destroy.record_path(env.root, "t")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    on_disk = env.record()
    assert on_disk["phase"] == destroy.SERVICE_DONE
    assert on_disk["steps"]["slack_app"] == {"app_id": "A1", "result": "revoked"}
    assert "x" not in json.dumps(on_disk["steps"]["secrets"]).split('"')  # names, no values
    assert destroy.blocks_start(env.root, "t")
    text = "\n".join(rec["residue"])
    assert "Slack app A1 still exists (its bot token is revoked)" in text
    assert "ap@acme.example at your mail provider (imap.acme.example)" in text
    assert "ar@acme.example at your mail provider\n" in text + "\n"


def test_the_residue_names_googles_app_password_page_for_a_gmail_address() -> None:
    """V1.6 step 8: where to revoke it, not just "at your mail provider"."""
    rec: dict[str, Any] = {"addresses": [
        {"email": "Pat.Lee@googlemail.com", "imap_host": "imap.gmail.com"},
        {"email": "ap@acme.example", "imap_host": "imap.acme.example"},
    ]}  # fmt: skip
    assert destroy.residue(rec) == [
        "revoke the app password for Pat.Lee@googlemail.com: remove it at"
        " https://myaccount.google.com/apppasswords, signed in to that Google account",
        "revoke the app password for ap@acme.example at your mail provider (imap.acme.example)",
    ]


def test_config_token_deletes_the_app(env: Env) -> None:
    rec = env.run(token=CONFIG)
    assert env.webs[CONFIG].calls == [("apps.manifest.delete", {"app_id": "A1"})]
    assert "auth.revoke" not in env.webs[BOT].methods()
    assert rec["steps"]["slack_app"]["result"] == "deleted"
    assert not any("Slack app" in line for line in rec["residue"])


def test_a_refused_config_token_falls_back_to_revoking(env: Env) -> None:
    env.webs[CONFIG].fail["apps.manifest.delete"] = [SlackError("apps.manifest.delete",
                                                                "invalid_auth")]  # fmt: skip
    rec = env.run(token=CONFIG)
    assert rec["steps"]["slack_app"] == {"app_id": "A1", "delete_failed": "invalid_auth",
                                         "result": "revoked"}  # fmt: skip


def test_a_later_token_still_deletes_the_app(env: Env) -> None:
    env.run()
    rec = env.run(token=CONFIG)  # a retry: the other steps are skipped
    assert env.webs[CONFIG].methods() == ["apps.manifest.delete"]
    assert rec["steps"]["slack_app"]["result"] == "deleted"
    assert env.webs[BOT].methods().count("chat.postMessage") == 2


def test_gone_channels_count_as_archived(env: Env) -> None:
    env.webs[BOT].fail["conversations.archive"] = [
        SlackError("conversations.archive", "channel_not_found"),
        SlackError("conversations.archive", "already_archived"),
        SlackError("conversations.archive", "restricted_action"),
    ]
    rec = env.run()
    assert rec["steps"]["channels"]["archived"] == ["CSUM", "CAP"]
    assert rec["steps"]["channels"]["failed"] == [
        {"name": "ecf-t-ar", "channel": "CAR", "code": "restricted_action"}
    ]
    assert any("ecf-t-ar wasn't archived (restricted_action)" in x for x in rec["residue"])
    assert env.store.get("slack/bot") is None  # a refusal doesn't stop the run


def test_slack_unreachable_stops_before_secrets_and_a_retry_resumes(env: Env) -> None:
    env.webs[BOT].fail["conversations.archive"] = [SlackNetworkError("conversations.archive")]
    with pytest.raises(ServiceUnavailableError, match="Run `ecf destroy` again"):
        env.run()
    assert env.store.get("slack/bot") == BOT
    assert env.store.get("mailbox/ap") == "x"
    assert set(env.record()["steps"]) == {"notice", "deadman"}
    assert not destroy.blocks_start(env.root, "t")
    rec = env.run()
    assert rec["phase"] == destroy.SERVICE_DONE
    assert env.webs[BOT].methods().count("chat.postMessage") == 2  # the notice went once
    assert env.store.get("slack/bot") is None


def test_a_locked_store_stops_and_says_so(env: Env) -> None:
    def refuse(name: str) -> None:
        from ecf_server.secretstore import SecretStoreNeedsYouError  # noqa: PLC0415

        raise SecretStoreNeedsYouError(f"locked {name}")

    env.store.delete = refuse  # type: ignore[method-assign]
    with pytest.raises(ServiceUnavailableError, match="unlock it"):
        env.run()
    assert "secrets" not in env.record()["steps"]


def test_without_slack_only_secrets_and_the_notice(env: Env) -> None:
    env.store.delete("slack/bot")
    rec = env.run()
    assert env.webs[BOT].calls == []
    assert rec["steps"]["slack_app"]["result"] == "no_token"
    assert rec["steps"]["deadman"] == {"result": "no_slack"}
    assert {c["code"] for c in rec["steps"]["channels"]["failed"]} == {"no_token"}


def test_refusals_change_nothing(env: Env) -> None:
    with pytest.raises(InvalidInputError, match="type the install name"):
        env.run(typed="prod")
    with pytest.raises(StepupRequiredError):
        destroy.run(env.conn, env.ctx(), typed="t", config_token=None, nonce=None)
    with pytest.raises(ConflictError, match="session"):
        env.run(sessions=1)
    with write_tx(env.conn):
        later = to_ts(env.clock.now() + timedelta(minutes=1))
        env.conn.execute("INSERT INTO leases (address_id, holder, fencing_token, expires_at)"
                         " VALUES ('ap', 'h', 1, ?)", (later,))  # fmt: skip
    with pytest.raises(ConflictError, match="lease"):
        env.run()
    assert destroy.read_record(env.root, "t") is None
    assert env.store.get("slack/bot") == BOT
    assert env.webs[BOT].calls == []


def test_step_up_names_this_install(env: Env) -> None:
    issued = stepup.issue(env.conn, env.clock, FakeStepper(), "destroy", {"install": "t"})
    assert "destroy the install t" in issued.prompt


def test_route_previews_runs_and_stops(env: Env, db_path: Path) -> None:
    st = make_state(db_path, env.store)
    st.clock = env.clock
    st.slack_web = env.web
    stopped: list[bool] = []
    st.request_stop = lambda: stopped.append(True)
    r = call(st, "GET", "/v1/destroy")
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["install"] == "t" and p["slack"] == {"app_id": "A1", "channels": 3}
    assert p["role"] is None  # no init: not guessed as prod
    assert p["busy"] == [] and p["record"] is None and p["last_export_at"] is None
    assert [a["address_id"] for a in p["addresses"]] == ["ap", "ar"]
    r = call(st, "POST", "/v1/destroy", {"install": "t"})
    assert r.status_code == 403, r.text  # step-up first
    assert not stopped
    r = call(st, "POST", "/v1/destroy", {"install": "t", "nonce_id": env.nonce()})
    assert r.status_code == 200, r.text
    assert r.json()["phase"] == destroy.SERVICE_DONE
    assert stopped == [True] and st.stopping_on_purpose
    assert call(st, "GET", "/v1/destroy").json()["record"]["phase"] == destroy.SERVICE_DONE
    # the record lives beside the install's folder, not in it
    assert destroy.record_path(db_path.parent.parent, "t").exists()


def test_service_refuses_to_start_mid_destroy(tmp_path: Path) -> None:
    from ecf_server.service import EXIT_OK, Service  # noqa: PLC0415

    paths = Paths("t", tmp_path)
    destroy.write_record(tmp_path, "t", {"install": "t", "phase": destroy.SERVICE_DONE})
    assert Service(paths).run() == EXIT_OK
    assert not paths.lock.exists()
    destroy.write_record(tmp_path, "t", {"install": "t", "phase": destroy.DONE})
    assert not destroy.blocks_start(tmp_path, "t")


def test_foreground_with_no_database_marks_the_service_part_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ECF_HOME", str(tmp_path))
    assert destroy.main("t", "t", ask_token=False) == 0
    assert "nothing for the service to do" in capsys.readouterr().out
    assert destroy.blocks_start(tmp_path, "t")
