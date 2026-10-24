"""scripts/corpus_lengths.py (OD-553): how long a corpus's emails are, as counts only."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _script() -> ModuleType:
    path = Path(__file__).resolve().parent.parent / "scripts" / "corpus_lengths.py"
    spec = importlib.util.spec_from_file_location("corpus_lengths", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lengths_fall_in_bands_and_unknowns_are_counted_apart() -> None:
    s = _script()
    lines = s.summary([100, 1500, 1501, 4000, 4001, 8000, 8001, None, None])
    text = "\n".join(lines)
    assert "messages: 9 (7 with a length; 2 from an older corpus, unknown)" in text
    assert "up to 1,500: 2 (29%)" in text
    assert "1,501 to 4,000: 2 (29%)" in text
    assert "4,001 to 8,000: 2 (29%)" in text
    assert "over 8,000: 1 (14%)" in text
    assert "median: 4,000" in text


def test_an_email_with_no_text_is_in_the_first_band() -> None:
    """Attachments only: length 0, counted, so the bands add up (V1)."""
    text = "\n".join(_script().summary([0, 0, 1500, 9000]))
    assert "up to 1,500: 3 (75%)" in text and "over 8,000: 1 (25%)" in text


def test_deciles_stay_inside_the_data() -> None:
    """Inclusive deciles: none above the longest or below the shortest (V2)."""
    line = next(x for x in _script().summary([0, 0, 1500]) if "deciles" in x)
    values = [int(v.replace(",", "")) for v in line.split(":")[1].split(", ")]
    assert min(values) >= 0 and max(values) <= 1500


def test_no_known_length() -> None:
    assert _script().summary([None]) == ["messages: 1 (0 with a length; 1 from an older corpus,"
                                         " unknown)"]  # fmt: skip


def test_no_terminal_is_one_line_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Run under a pipe: a one-line refusal and exit 2, never an echoed passphrase."""
    s = _script()
    monkeypatch.setattr(sys, "argv", ["corpus_lengths.py", "x.ecfcorpus"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert s.main() == 2
    err = capsys.readouterr().err
    assert err.startswith("corpus_lengths: ") and "Traceback" not in err
