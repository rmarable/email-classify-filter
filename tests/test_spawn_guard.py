import json
import subprocess
import sys
from pathlib import Path

import pytest

from ecf import spawn_guard as sg

GOOD = {"subagent_type": "ecf-classifier", "description": "Classify 3 items",
        "prompt": "id=a claim_token=b"}  # fmt: skip


def hook(tool_input: object) -> dict[str, object]:
    return {"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": tool_input}


def run_guard(stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-I", "-m", "ecf.spawn_guard"], input=stdin,
                          capture_output=True, text=True, timeout=30, check=False)  # fmt: skip


def test_the_three_keys_pass() -> None:
    assert sg.allowed(hook(GOOD))
    assert sg.allowed(hook({"subagent_type": "ecf-actor", "prompt": "id=a claim_token=b"}))


@pytest.mark.parametrize(
    "extra",
    [{"isolation": "remote"}, {"isolation": "worktree"}, {"model": "opus"}, {"model": ""},
     {"effort": "high"}, {"name": "x"}, {"run_in_background": True}, {"isolation": None}],
)  # fmt: skip
def test_any_other_key_is_refused(extra: dict[str, object]) -> None:
    assert not sg.allowed(hook({**GOOD, **extra}))


@pytest.mark.parametrize(
    "data",
    [None, [], "x", {}, {"tool_input": None}, {"tool_input": {}}, {"tool_input": []},
     hook({"description": "d", "prompt": "p"}), hook({**GOOD, "subagent_type": ""}),
     hook({**GOOD, "subagent_type": "  "}), hook({**GOOD, "prompt": 3}),
     {**hook(GOOD), "tool_name": "Bash"}, {"tool_input": GOOD}],
)  # fmt: skip
def test_unreadable_or_incomplete_input_is_refused(data: object) -> None:
    assert not sg.allowed(data)


def test_exit_codes() -> None:
    ok = run_guard(json.dumps(hook(GOOD)))
    assert ok.returncode == 0 and ok.stderr == "" and ok.stdout == ""
    for stdin in (json.dumps(hook({**GOOD, "isolation": "remote"})), "{not json", ""):
        refused = run_guard(stdin)
        assert refused.returncode == 2 and refused.stdout == ""
        assert refused.stderr.strip() == sg.REASON  # fixed text: nothing from the input


def test_a_module_in_the_working_folder_is_not_imported(tmp_path: Path) -> None:
    """-I keeps the working folder off the import path: a planted json.py can't pass a spawn."""
    (tmp_path / "json.py").write_text("import sys\nsys.exit(0)\n")
    refused = subprocess.run([sys.executable, "-I", "-m", "ecf.spawn_guard"], cwd=tmp_path,
                             input=json.dumps(hook({**GOOD, "isolation": "remote"})),
                             capture_output=True, text=True, timeout=30, check=False)  # fmt: skip
    assert refused.returncode == 2


def test_standard_library_only() -> None:
    """Starts fast and never imports the rest of ecf (it runs on every spawn)."""
    code = ("import sys, ecf.spawn_guard; "
            "print(sorted(m for m in sys.modules if m.startswith('ecf')))")  # fmt: skip
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         timeout=30, check=True).stdout  # fmt: skip
    assert out.strip() == "['ecf', 'ecf.spawn_guard']"
