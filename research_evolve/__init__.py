"""ResearchEvolve public API."""

from .conjectures import Conjecture, Counterexample, Observation, Predicate, ValueRef
from .certificate import CertificateVerification, ResearchCertificate
from .formal import FormalArtifact, FormalizationSpec, KernelResult, LeanDiagnostic
from .formal_project import LeanProjectEnvironment, LeanProjectLock, LockedProjectFile
from .formal_retrieval import Premise, PremiseIndex, PremiseSelection, PremiseSelector, ProofSearchBudget, ScoredPremise
from .formal_retrieval_pipeline import RetrievalFormalPipeline
from .formal_search import (
    CommandTacticGenerator,
    FrozenLeanProofWorker,
    InteractiveProofSearchBudget,
    LeanGoal,
    LeanProofState,
    ProofSearchExhausted,
    ProofSearchFormalizer,
    ProofSearchSummary,
    TacticCandidate,
    TacticTransition,
)
from .ideas import IdeaGenome, ResearchProposal, SemanticPatch
from .project_kernel import ProjectCheckResult, ProjectLeanKernel
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
from .semantic_bridge import (
    CertifiedSemanticBridge,
    MathExpr,
    PredicateIRCompiler,
    SemanticAuditFailure,
    SemanticAuditMemory,
    SemanticAuditResult,
    SemanticContractCompiler,
    SemanticRegistry,
    TrustedSymbol,
    UnsupportedSemantics,
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
    "LockedProjectFile",
    "LeanProjectLock",
    "LeanProjectEnvironment",
    "Premise",
    "PremiseIndex",
    "ScoredPremise",
    "PremiseSelection",
    "PremiseSelector",
    "ProofSearchBudget",
    "RetrievalFormalPipeline",
    "ProjectCheckResult",
    "ProjectLeanKernel",
    "LeanGoal",
    "LeanProofState",
    "TacticCandidate",
    "TacticTransition",
    "InteractiveProofSearchBudget",
    "ProofSearchSummary",
    "ProofSearchExhausted",
    "CommandTacticGenerator",
    "FrozenLeanProofWorker",
    "ProofSearchFormalizer",
    "MathExpr",
    "TrustedSymbol",
    "SemanticRegistry",
    "PredicateIRCompiler",
    "SemanticContractCompiler",
    "SemanticAuditResult",
    "SemanticAuditMemory",
    "CertifiedSemanticBridge",
    "UnsupportedSemantics",
    "SemanticAuditFailure",
    "ResearchCertificate",
    "CertificateVerification",
]
__version__ = "1.2.0"
