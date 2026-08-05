"""Compute exact-file, canonical-JSON, or schema-manifest SHA-256."""

from __future__ import annotations

import argparse
from pathlib import Path

from phase0lib import canonical_sha256, load_json, schemas_manifest_sha256, sha256_file


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", type=Path)
    group.add_argument("--canonical-json", type=Path)
    group.add_argument("--schemas", type=Path)
    args = parser.parse_args()
    if args.file:
        result = sha256_file(args.file)
    elif args.canonical_json:
        result = canonical_sha256(load_json(args.canonical_json))
    else:
        result = schemas_manifest_sha256(args.schemas)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
