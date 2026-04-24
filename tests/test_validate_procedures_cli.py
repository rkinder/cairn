from __future__ import annotations

import subprocess
import sys


def _run_cli(path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cairn.cli.validate_procedures", path],
        capture_output=True,
        text=True,
        check=False,
    )


def test_valid_dir_exits_0(tmp_path) -> None:
    f = tmp_path / "ok.procedure.yml"
    f.write_text(
        """
        title: Valid Procedure
        tags: [phishing]
        steps:
          - Collect full message headers for review.
          - Validate SPF DKIM and DMARC signals in headers.
        """,
        encoding="utf-8",
    )
    proc = _run_cli(str(tmp_path))
    assert proc.returncode == 0


def test_invalid_file_exits_1(tmp_path) -> None:
    f = tmp_path / "bad.procedure.yml"
    f.write_text(
        """
        tags: [phishing]
        steps:
          - Collect full message headers for review.
          - Validate SPF DKIM and DMARC signals in headers.
        """,
        encoding="utf-8",
    )
    proc = _run_cli(str(tmp_path))
    assert proc.returncode == 1


def test_error_names_field(tmp_path) -> None:
    f = tmp_path / "bad.procedure.yml"
    f.write_text(
        """
        title: Missing steps
        tags: [phishing]
        """,
        encoding="utf-8",
    )
    proc = _run_cli(str(tmp_path))
    assert proc.returncode == 1
    assert "steps" in proc.stderr.lower()


def test_empty_dir_exits_0(tmp_path) -> None:
    proc = _run_cli(str(tmp_path))
    assert proc.returncode == 0


def test_missing_dir_exits_1(tmp_path) -> None:
    proc = _run_cli(str(tmp_path / "missing"))
    assert proc.returncode == 1
