import subprocess
import sys

from typer.testing import CliRunner

import ecf
import ecf_server
from ecf.cli import app


def test_lockstep_version() -> None:
    assert ecf_server.__version__ == ecf.__version__


def test_cli_version() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == ecf.__version__


def test_server_version() -> None:
    out = subprocess.run(
        [sys.executable, "-m", "ecf_server", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == ecf.__version__
