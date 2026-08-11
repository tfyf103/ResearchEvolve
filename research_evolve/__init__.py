"""ResearchEvolve public API."""

from .conjectures import Conjecture, Counterexample, Observation, Predicate, ValueRef
from .formal import FormalArtifact, FormalizationSpec, KernelResult, LeanDiagnostic
from .ideas import IdeaGenome, ResearchProposal, SemanticPatch
from .proofs import (
    LemmaSpec,
    ProofArtifact,
    ProofPlan,
    ProofReview,
    ProofSpec,
    VerificationIssue,
)
from .spec import (
    Budget,
    ConjecturePolicy,
    Constraint,
    ExplorerPolicy,
    Objective,
    ResearchSpec,
    SearchPolicy,
)

__all__ = [
    "ResearchSpec",
    "Objective",
    "Constraint",
    "Budget",
    "SearchPolicy",
    "ExplorerPolicy",
    "ConjecturePolicy",
    "IdeaGenome",
    "ResearchProposal",
    "SemanticPatch",
    "Observation",
    "Conjecture",
    "Counterexample",
    "Predicate",
    "ValueRef",
    "ProofSpec",
    "ProofPlan",
    "LemmaSpec",
    "ProofArtifact",
    "ProofReview",
    "VerificationIssue",
    "FormalizationSpec",
    "FormalArtifact",
    "KernelResult",
    "LeanDiagnostic",
]
__version__ = "0.6.0"
