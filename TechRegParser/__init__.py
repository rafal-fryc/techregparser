"""TechRegParser - Multi-agent system for extracting requirements from tech regulation statutes.

This package provides a multi-agent system using the Anthropic Agent SDK
to read data privacy and tech regulation statutes, extract significant
requirements, and provide verified citations to prevent hallucinations.

Example usage:
    from TechRegParser import TechRegParserOrchestrator

    async def main():
        parser = TechRegParserOrchestrator()
        results = await parser.analyze_statute("path/to/statute.txt")

        for req in results.requirements:
            print(f"{req.description}")
            print(f"  Citation: {req.citation.section}")
            print(f"  Verified: {req.verified}")
"""

from .agents import TechRegParserOrchestrator
from .models import (
    AnalysisResult,
    Citation,
    Definition,
    LegislativeIntent,
    Requirement,
    RequirementCategory,
    StatuteStructure,
    StatuteSection,
    SectionType,
)
from .config import OrchestratorConfig

__version__ = "0.1.0"

__all__ = [
    "TechRegParserOrchestrator",
    "OrchestratorConfig",
    "AnalysisResult",
    "Citation",
    "Definition",
    "LegislativeIntent",
    "Requirement",
    "RequirementCategory",
    "StatuteStructure",
    "StatuteSection",
    "SectionType",
]
