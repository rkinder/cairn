from __future__ import annotations

import sys
from pathlib import Path

import yaml

from cairn.models.methodology import ProcedureMethodology


def validate_directory(procedures_dir: Path) -> int:
    if not procedures_dir.exists() or not procedures_dir.is_dir():
        print(f"FAIL {procedures_dir}: directory does not exist", file=sys.stderr)
        return 1

    failures = 0
    for file in sorted(procedures_dir.rglob("*.procedure.yml")):
        try:
            payload = yaml.safe_load(file.read_text(encoding="utf-8"))
            ProcedureMethodology.model_validate(payload)
            print(f"OK {file}")
        except Exception as exc:
            failures += 1
            print(f"FAIL {file}: {exc}", file=sys.stderr)

    return failures


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m cairn.cli.validate_procedures <procedures_dir>", file=sys.stderr)
        raise SystemExit(1)

    failures = validate_directory(Path(sys.argv[1]))
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
