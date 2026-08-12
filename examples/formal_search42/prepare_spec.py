from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True)
    parser.add_argument("--project-lock", required=True)
    parser.add_argument("--formal-corpus", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    spec = json.loads(Path(args.template).read_text(encoding="utf-8"))
    lock = json.loads(Path(args.project_lock).read_text(encoding="utf-8"))
    conn = sqlite3.connect(args.formal_corpus)
    try:
        metadata = {key: value for key, value in conn.execute("SELECT key, value FROM metadata")}
    finally:
        conn.close()
    contract_metadata = spec["metadata"]["formal_contracts"][0]["metadata"]
    contract_metadata["project_fingerprint"] = lock["fingerprint"]
    contract_metadata["formal_corpus_fingerprint"] = metadata["fingerprint"]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"output": str(output), "project_fingerprint": lock["fingerprint"], "formal_corpus_fingerprint": metadata["fingerprint"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
