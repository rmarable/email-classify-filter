"""A Dovecot test server in a container (OD-186: Colima on macOS, Docker Engine on Linux and CI).

One container per test session, TLS only (a throwaway self-signed certificate copied in before
start), and any user name logs in with PASSWORD, so each test gets a fresh, empty mailbox by
using a new name. Tests are skipped when no Docker socket exists, except in CI, where that fails.
The harness delivers and removes mail with stdlib imaplib, independently of the adapter under test.
"""

from __future__ import annotations

import imaplib
import os
import ssl
import subprocess
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

IMAGE = (
    "dovecot/dovecot:2.4.5@sha256:c807be4fb5a97d9c3a90770569d3a6c4cbdcb36742ad41f90409cbd929166553"
)
IMAPS = 31993  # the image's TLS IMAP port (Docker Hub page, read 2026-09-28)
PASSWORD = "ecf-test-pass"


def docker_socket() -> str | None:
    """DOCKER_HOST, Colima's socket, or the standard socket; None if there is none."""
    if host := os.environ.get("DOCKER_HOST"):
        return host
    colima = Path.home() / ".colima" / "default" / "docker.sock"
    if colima.exists():
        return f"unix://{colima}"
    if Path("/var/run/docker.sock").exists():
        return "unix:///var/run/docker.sock"
    return None


def _cert(folder: Path) -> tuple[Path, Path]:
    key, crt = folder / "tls.key", folder / "tls.crt"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "2",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(crt),
        ],
        check=True,
        capture_output=True,
    )
    return key, crt


@dataclass
class Dovecot:
    host: str
    port: int
    ca_file: Path

    def context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context(cafile=str(self.ca_file))
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx

    def admin(self, user: str) -> imaplib.IMAP4_SSL:
        c = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=self.context(), timeout=15)
        c.login(user, PASSWORD)
        c.select("INBOX")
        return c


def start() -> Iterator[Dovecot]:
    sock = docker_socket()
    if sock is None:
        if os.environ.get("CI"):
            pytest.fail("no Docker socket in CI")
        pytest.skip("no Docker socket (start Colima or Docker to run the IMAP tests)")
    os.environ.setdefault("DOCKER_HOST", sock)
    from testcontainers.core.config import testcontainers_config  # noqa: PLC0415
    from testcontainers.core.container import DockerContainer  # noqa: PLC0415

    if "colima" in sock:  # the reaper mounts the socket path as seen inside Colima's VM
        testcontainers_config.ryuk_docker_socket = "/var/run/docker.sock"
    with tempfile.TemporaryDirectory(prefix="ecf-dovecot-") as d:
        key, crt = _cert(Path(d))
        container = (
            DockerContainer(IMAGE)
            .with_env("USER_PASSWORD", f"{{PLAIN}}{PASSWORD}")
            .with_exposed_ports(IMAPS)
            .with_copy_into_container(crt, "/etc/dovecot/ssl/tls.crt")
            .with_copy_into_container(key, "/etc/dovecot/ssl/tls.key")
        )
        with container:
            dv = Dovecot(
                container.get_container_host_ip(), int(container.get_exposed_port(IMAPS)), crt
            )
            _wait(dv)
            yield dv


def _wait(dv: Dovecot, seconds: float = 60) -> None:
    deadline = time.monotonic() + seconds
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            dv.admin("ecf-ready").logout()
            return
        except (OSError, imaplib.IMAP4.error) as exc:
            last = exc
            time.sleep(0.5)
    raise RuntimeError(f"Dovecot didn't accept logins within {seconds} s: {last!r}")


def append(c: imaplib.IMAP4_SSL, raw: bytes, when: datetime) -> None:
    typ, data = c.append("INBOX", "", imaplib.Time2Internaldate(when.timestamp()), raw)
    if typ != "OK":
        raise RuntimeError(f"APPEND failed: {data!r}")


def expunge(c: imaplib.IMAP4_SSL, uid: int) -> None:
    c.uid("STORE", str(uid), "+FLAGS.SILENT", r"(\Deleted)")
    c.expunge()
