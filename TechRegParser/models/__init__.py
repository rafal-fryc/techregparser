"""Data models for the Statute Parser system."""

from .citation import Citation
from .requirement import Requirement, RequirementCategory
from .statute_structure import (
    StatuteStructure,
    StatuteSection,
    SectionType,
    Definition,
    AnalysisResult,
)

__all__ = [
    "Citation",
    "Requirement",
    "RequirementCategory",
    "StatuteStructure",
    "StatuteSection",
    "SectionType",
    "Definition",
    "AnalysisResult",
]
