from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Iterable

from .spec import ResearchSpec


def stable_json_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_version() -> str:
    try:
        return metadata.version("research-evolve")
    except metadata.PackageNotFoundError:
        return "source-tree"


def build_manifest(
    spec: ResearchSpec,
    seeds: list[dict[str, Any]],
    evaluator_paths: Iterable[str | Path],
    *,
    mutator_name: str,
    domain_pack: str | None = None,
    explorer_name: str | None = None,
    conjecturer_name: str | None = None,
) -> dict[str, Any]:
    evaluators = [
        {"path": str(Path(path)), "sha256": sha256_file(path)}
        for path in evaluator_paths
    ]
    stable_inputs = {
        "spec": spec.to_dict(),
        "seeds_sha256": stable_json_hash(seeds),
        "evaluators": evaluators,
        "mutator": mutator_name,
        "domain_pack": domain_pack,
        "explorer": explorer_name,
        "conjecturer": conjecturer_name,
    }
    return {
        "schema_version": 3,
        "fingerprint": stable_json_hash(stable_inputs),
        "inputs": stable_inputs,
        "runtime": {
            "research_evolve_version": package_version(),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "reproducibility_note": (
            "Evaluator/mutator inputs are fingerprinted. External LLM explorers/conjecturers may be nondeterministic; "
            "their structured proposals, conjectures, empirical tests, and counterexamples are persisted in SQLite journals."
        ),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def write_manifest(path: str | Path, manifest: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
