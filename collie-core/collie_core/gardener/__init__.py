"""Gardener — Collie's self-improvement loop (evidence → propose → review).

The Gardener reads run-record telemetry, proposes conservative
improvements to agent instruction files and long-term memory, and applies
them only through the versioned rollback rail after human approval.

MVP scope: no sandbox replay (documented deferral) — deterministic scope
validation + human approval stand in for it.
"""

from collie_core.gardener.evidence import collect_evidence
from collie_core.gardener.propose import (
    ALLOWED_ARTIFACT_TYPES,
    ProposalValidationError,
    propose,
    validate_suggestion,
)
from collie_core.gardener.runner import apply_suggestion, run_gardener

__all__ = [
    "ALLOWED_ARTIFACT_TYPES",
    "ProposalValidationError",
    "apply_suggestion",
    "collect_evidence",
    "propose",
    "run_gardener",
    "validate_suggestion",
]
