from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

FormalStatus = Literal[
    "planned",
    "generated",
    "kernel_rejected",
    "search_exhausted",
    "repair_exhausted",
    "formal_verified",
    "environment_error",
    "invalid",
    "invalidated",
]
DiagnosticSeverity = Literal["error", "warning", "info"]
DEFAULT_ALLOWED_LEAN_AXIOMS = ["propext", "Classical.choice", "Quot.sound"]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_sha256(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class FormalizationSpec:
    """Frozen machine-checkable target for one natural-language proof lineage.

    Imports, trusted preamble definitions, theorem signature, toolchain, and the
    allowed axiom set come from a source ResearchSpec formal contract. A
    Formalizer may only propose a proof term; it cannot rewrite the target.
    """

    proof_spec_id: str
    proof_artifact_id: str
    conjecture_id: str
    conjecture_statement: str
    theorem_name: str
    theorem_signature: str
    imports: list[str] = field(default_factory=list)
    preamble: str = ""
    allowed_axioms: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_LEAN_AXIOMS))
    backend: str = "lean4"
    toolchain: str = "leanprover/lean4:v4.30.0"
    generation: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"formal-spec-{uuid.uuid4().hex}")
    created_at: str = field(default_factory=_utcnow)

    def validate(self) -> None:
        if not self.proof_spec_id or not self.proof_artifact_id or not self.conjecture_id:
            raise ValueError("formalization spec requires proof/conjecture lineage ids")
        if self.backend != "lean4":
            raise ValueError(f"unsupported formal backend: {self.backend!r}")
        if not self.theorem_name.strip() or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_'.]*", self.theorem_name):
            raise ValueError("theorem_name must be a Lean identifier")
        signature = self.theorem_signature.strip()
        if not signature:
            raise ValueError("theorem_signature must not be empty")
        if ":=" in signature:
            raise ValueError("theorem_signature must freeze only the declaration head, not a proof body")
        if not re.match(r"^(theorem|lemma)\s+", signature):
            raise ValueError("theorem_signature must begin with theorem or lemma")
        if not re.search(rf"\b{re.escape(self.theorem_name)}\b", signature):
            raise ValueError("theorem_signature does not contain theorem_name")
        if not self.toolchain.strip():
            raise ValueError("formal toolchain must not be empty")
        for module in self.imports:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", module):
                raise ValueError(f"invalid Lean import module: {module!r}")
        if len(set(self.allowed_axioms)) != len(self.allowed_axioms):
            raise ValueError("allowed_axioms must be unique")
        for axiom in self.allowed_axioms:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_'.]*", axiom):
                raise ValueError(f"invalid Lean axiom identifier: {axiom!r}")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(slots=True)
class FormalArtifact:
    formal_spec_id: str
    proof_term: str
    helper_source: str = ""
    attempt: int = 0
    parent_artifact_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"formal-artifact-{uuid.uuid4().hex}")
    created_at: str = field(default_factory=_utcnow)

    def validate(self) -> None:
        if not self.formal_spec_id:
            raise ValueError("formal artifact requires formal_spec_id")
        if self.attempt < 0:
            raise ValueError("formal artifact attempt must be non-negative")
        if not self.proof_term.strip():
            raise ValueError("formal artifact requires a non-empty proof_term")

    def build_source(self, spec: FormalizationSpec) -> str:
        self.validate()
        spec.validate()
        if self.formal_spec_id != spec.id:
            raise ValueError("formal artifact does not target the supplied FormalizationSpec")
        chunks: list[str] = []
        if spec.imports:
            chunks.extend(f"import {module}" for module in spec.imports)
            chunks.append("")
        preamble = spec.preamble.strip()
        if preamble:
            chunks.append(preamble)
            chunks.append("")
        helper = self.helper_source.strip()
        if helper:
            chunks.append(helper)
            chunks.append("")
        chunks.append(f"{spec.theorem_signature.strip()} := {self.proof_term.strip()}")
        chunks.append("")
        chunks.append(f"#print axioms {spec.theorem_name}")
        chunks.append("")
        return "\n".join(chunks)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(slots=True)
class LeanDiagnostic:
    severity: DiagnosticSeverity
    message: str
    line: int | None = None
    column: int | None = None
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class KernelResult:
    formal_artifact_id: str
    passed: bool
    status: FormalStatus
    command: list[str]
    expected_toolchain: str
    detected_version: str | None
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    diagnostics: list[LeanDiagnostic] = field(default_factory=list)
    axioms: list[str] = field(default_factory=list)
    gate_reason: str = ""
    source_sha256: str = ""
    id: str = field(default_factory=lambda: f"kernel-run-{uuid.uuid4().hex}")
    created_at: str = field(default_factory=_utcnow)

    def validate(self) -> None:
        if not self.formal_artifact_id:
            raise ValueError("kernel result requires formal_artifact_id")
        if self.passed and self.status != "formal_verified":
            raise ValueError("passed kernel result must have formal_verified status")
        if self.status == "formal_verified" and not self.passed:
            raise ValueError("formal_verified kernel result must have passed=true")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        data = asdict(self)
        data["diagnostics"] = [item.to_dict() for item in self.diagnostics]
        return data


class FormalMemory:
    """SQLite journal for frozen formal targets, generated sources, and kernel runs."""

    def __init__(self, path: str | Path = ".researchevolve/formal.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS formal_specs (
                id TEXT PRIMARY KEY,
                proof_spec_id TEXT NOT NULL,
                proof_artifact_id TEXT NOT NULL,
                conjecture_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                theorem_name TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_formal_specs_proof_spec ON formal_specs(proof_spec_id);
            CREATE INDEX IF NOT EXISTS idx_formal_specs_conjecture ON formal_specs(conjecture_id);

            CREATE TABLE IF NOT EXISTS formal_artifacts (
                id TEXT PRIMARY KEY,
                formal_spec_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                parent_artifact_id TEXT,
                payload TEXT NOT NULL,
                source TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_formal_artifacts_spec ON formal_artifacts(formal_spec_id);

            CREATE TABLE IF NOT EXISTS kernel_runs (
                id TEXT PRIMARY KEY,
                formal_artifact_id TEXT NOT NULL,
                formal_spec_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                passed INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_kernel_runs_artifact ON kernel_runs(formal_artifact_id);
            CREATE INDEX IF NOT EXISTS idx_kernel_runs_spec ON kernel_runs(formal_spec_id);
            """
        )
        self.conn.commit()

    def record_spec(self, spec: FormalizationSpec, status: FormalStatus = "planned") -> None:
        spec.validate()
        self.conn.execute(
            "INSERT OR REPLACE INTO formal_specs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                spec.id,
                spec.proof_spec_id,
                spec.proof_artifact_id,
                spec.conjecture_id,
                spec.generation,
                spec.theorem_name,
                json.dumps(spec.to_dict(), sort_keys=True),
                status,
                spec.created_at,
            ),
        )
        self.conn.commit()

    def record_artifact(
        self,
        artifact: FormalArtifact,
        source: str,
        status: FormalStatus = "generated",
    ) -> None:
        artifact.validate()
        digest = _source_sha256(source)
        self.conn.execute(
            "INSERT OR REPLACE INTO formal_artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                artifact.id,
                artifact.formal_spec_id,
                artifact.attempt,
                artifact.parent_artifact_id,
                json.dumps(artifact.to_dict(), sort_keys=True),
                source,
                digest,
                status,
                artifact.created_at,
            ),
        )
        self.conn.execute("UPDATE formal_specs SET status = ? WHERE id = ?", (status, artifact.formal_spec_id))
        self.conn.commit()

    def record_kernel_result(self, formal_spec_id: str, result: KernelResult) -> FormalStatus:
        result.validate()
        self.conn.execute(
            "INSERT OR REPLACE INTO kernel_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                result.id,
                result.formal_artifact_id,
                formal_spec_id,
                json.dumps(result.to_dict(), sort_keys=True),
                int(result.passed),
                result.status,
                result.created_at,
            ),
        )
        self.conn.execute(
            "UPDATE formal_artifacts SET status = ? WHERE id = ?",
            (result.status, result.formal_artifact_id),
        )
        self.conn.execute("UPDATE formal_specs SET status = ? WHERE id = ?", (result.status, formal_spec_id))
        self.conn.commit()
        return result.status

    def set_spec_status(self, formal_spec_id: str, status: FormalStatus) -> None:
        self.conn.execute("UPDATE formal_specs SET status = ? WHERE id = ?", (status, formal_spec_id))
        self.conn.commit()

    def set_artifact_status(self, artifact_id: str, status: FormalStatus) -> None:
        self.conn.execute("UPDATE formal_artifacts SET status = ? WHERE id = ?", (status, artifact_id))
        self.conn.commit()

    def invalidate_for_proof_spec(self, proof_spec_id: str, reason: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT id FROM formal_specs WHERE proof_spec_id = ? AND status != 'invalidated'",
            (proof_spec_id,),
        ).fetchall()
        spec_ids = [str(row["id"]) for row in rows]
        for spec_id in spec_ids:
            artifact_rows = self.conn.execute(
                "SELECT id, payload FROM formal_artifacts WHERE formal_spec_id = ?",
                (spec_id,),
            ).fetchall()
            for row in artifact_rows:
                payload = json.loads(row["payload"])
                metadata = dict(payload.get("metadata", {}))
                metadata["invalidated_reason"] = reason
                payload["metadata"] = metadata
                self.conn.execute(
                    "UPDATE formal_artifacts SET payload = ?, status = 'invalidated' WHERE id = ?",
                    (json.dumps(payload, sort_keys=True), row["id"]),
                )
            run_rows = self.conn.execute(
                "SELECT id, payload FROM kernel_runs WHERE formal_spec_id = ?",
                (spec_id,),
            ).fetchall()
            for row in run_rows:
                payload = json.loads(row["payload"])
                payload["historical_status"] = payload.get("status")
                payload["invalidation_reason"] = reason
                payload["status"] = "invalidated"
                payload["passed"] = False
                self.conn.execute(
                    "UPDATE kernel_runs SET payload = ?, passed = 0, status = 'invalidated' WHERE id = ?",
                    (json.dumps(payload, sort_keys=True), row["id"]),
                )
            self.conn.execute("UPDATE formal_specs SET status = 'invalidated' WHERE id = ?", (spec_id,))
        self.conn.commit()
        return spec_ids

    def has_formal_verified_for_proof_spec(self, proof_spec_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM formal_specs WHERE proof_spec_id = ? AND status = 'formal_verified' LIMIT 1",
            (proof_spec_id,),
        ).fetchone()
        return row is not None

    def list_specs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM formal_specs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload"])
            payload["status"] = row["status"]
            output.append(payload)
        return output

    def list_artifacts(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM formal_artifacts ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload"])
            payload["status"] = row["status"]
            payload["source_sha256"] = row["source_sha256"]
            payload["source"] = row["source"]
            output.append(payload)
        return output

    def list_kernel_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM kernel_runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload"])
            payload["status"] = row["status"]
            payload["passed"] = bool(row["passed"])
            output.append(payload)
        return output

    def stats(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for status in [
            "planned",
            "generated",
            "kernel_rejected",
            "repair_exhausted",
            "formal_verified",
            "environment_error",
            "invalid",
            "invalidated",
        ]:
            result[status] = int(
                self.conn.execute("SELECT COUNT(*) FROM formal_specs WHERE status = ?", (status,)).fetchone()[0]
            )
        result["specs"] = int(self.conn.execute("SELECT COUNT(*) FROM formal_specs").fetchone()[0])
        result["artifacts"] = int(self.conn.execute("SELECT COUNT(*) FROM formal_artifacts").fetchone()[0])
        result["kernel_runs"] = int(self.conn.execute("SELECT COUNT(*) FROM kernel_runs").fetchone()[0])
        return result

    def close(self) -> None:
        self.conn.close()

