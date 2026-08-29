#!/usr/bin/env python3
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ROOT / "repository.yaml",
    ROOT / "livongo_bp_collector" / "config.yaml",
    ROOT / "README.md",
]


def main() -> int:
    owner = (sys.argv[1] if len(sys.argv) > 1 else input("GitHub username/organization: ")).strip()
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", owner):
        print("That does not look like a valid GitHub username/organization.", file=sys.stderr)
        return 2
    image_owner = owner.lower()
    for path in FILES:
        text = path.read_text(encoding="utf-8")
        text = text.replace("__GHCR_OWNER__", image_owner)
        path.write_text(text, encoding="utf-8")
    print("Configured repository for:", owner)
    print("GHCR image: ghcr.io/%s/livongo-bp-collector" % image_owner)
    print("Next: commit/push the repository to GitHub and run the build workflow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
