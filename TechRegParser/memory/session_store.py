"""Cross-session memory for storing statute analysis patterns.

Stores patterns, unique provisions, and extraction stats from each analysis
so that subsequent analyses can benefit from accumulated knowledge.
"""

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class StatutePatterns:
    """Patterns observed in a statute analysis.

    Attributes:
        statute_name: Name of the analyzed statute
        jurisdiction: State/jurisdiction if identifiable
        unique_provisions: Provisions not commonly seen in other statutes
        section_structure: Observed section ordering pattern
        definition_count: Number of definitions found
        requirement_count: Number of requirements extracted
        verification_rate: Fraction of citations verified
        avg_confidence: Average confidence score
        category_distribution: Count per category
        match_type_distribution: Count per citation match type
        common_terms: Frequently referenced defined terms
    """
    statute_name: str
    jurisdiction: str = ""
    unique_provisions: list[str] = field(default_factory=list)
    section_structure: list[str] = field(default_factory=list)
    definition_count: int = 0
    requirement_count: int = 0
    verification_rate: float = 0.0
    avg_confidence: float = 0.0
    category_distribution: dict[str, int] = field(default_factory=dict)
    match_type_distribution: dict[str, int] = field(default_factory=dict)
    common_terms: list[str] = field(default_factory=list)


class SessionStore:
    """Persistent store for cross-session statute analysis patterns.

    Data is stored as a JSON file in the working directory.
    """

    DEFAULT_FILENAME = ".techregparser_session.json"

    def __init__(self, store_path: Optional[str] = None):
        """Initialize the session store.

        Args:
            store_path: Path to the JSON store file. Defaults to
                        .techregparser_session.json in the current directory.
        """
        self.store_path = Path(store_path or self.DEFAULT_FILENAME)
        self._data: dict[str, StatutePatterns] = {}
        self._load()

    def _load(self):
        """Load existing session data from disk."""
        if self.store_path.exists():
            try:
                with open(self.store_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for key, value in raw.items():
                    self._data[key] = StatutePatterns(**value)
            except (json.JSONDecodeError, TypeError, KeyError):
                self._data = {}

    def _save(self):
        """Persist session data to disk."""
        raw = {k: asdict(v) for k, v in self._data.items()}
        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2)

    def store(self, patterns: StatutePatterns):
        """Store patterns from an analysis.

        Args:
            patterns: The patterns to store
        """
        self._data[patterns.statute_name] = patterns
        self._save()

    def get(self, statute_name: str) -> Optional[StatutePatterns]:
        """Retrieve patterns for a specific statute.

        Args:
            statute_name: Name of the statute

        Returns:
            StatutePatterns if found, None otherwise
        """
        return self._data.get(statute_name)

    def get_all(self) -> dict[str, StatutePatterns]:
        """Get all stored patterns."""
        return dict(self._data)

    def get_relevant_patterns(self, jurisdiction: str = "") -> list[StatutePatterns]:
        """Get patterns relevant to a given context.

        Args:
            jurisdiction: Filter by jurisdiction if provided

        Returns:
            List of relevant patterns, sorted by requirement count
        """
        results = []
        for patterns in self._data.values():
            if jurisdiction and patterns.jurisdiction and patterns.jurisdiction != jurisdiction:
                continue
            results.append(patterns)

        return sorted(results, key=lambda p: p.requirement_count, reverse=True)

    def build_context_injection(self, jurisdiction: str = "") -> str:
        """Build a context string for injection into Phase 1 prompts.

        Summarizes patterns from previous analyses to help guide new analyses.

        Args:
            jurisdiction: Optional jurisdiction filter

        Returns:
            Formatted string for prompt injection, or empty string if no data
        """
        relevant = self.get_relevant_patterns(jurisdiction)
        if not relevant:
            return ""

        lines = ["PATTERNS FROM PREVIOUS ANALYSES:"]

        for p in relevant[:5]:
            lines.append(f"\n{p.statute_name} ({p.jurisdiction}):")
            lines.append(f"  - {p.requirement_count} requirements, {p.definition_count} definitions")
            lines.append(f"  - Verification rate: {p.verification_rate:.0%}")
            if p.category_distribution:
                dist = ", ".join(f"{k}: {v}" for k, v in p.category_distribution.items())
                lines.append(f"  - Categories: {dist}")
            if p.unique_provisions:
                lines.append(f"  - Unique provisions: {', '.join(p.unique_provisions[:3])}")
            if p.section_structure:
                lines.append(f"  - Section order: {' → '.join(p.section_structure)}")

        return "\n".join(lines)

    @staticmethod
    def from_analysis_result(result) -> StatutePatterns:
        """Create StatutePatterns from an AnalysisResult.

        Args:
            result: An AnalysisResult object

        Returns:
            StatutePatterns extracted from the result
        """
        # Category distribution
        cat_dist = {}
        confidences = []
        for req in result.requirements:
            cat = req.category.value
            cat_dist[cat] = cat_dist.get(cat, 0) + 1
            if req.confidence > 0:
                confidences.append(req.confidence)

        # Verification rate
        verified_count = len([r for r in result.requirements if r.verified])
        total = len(result.requirements)
        verification_rate = verified_count / total if total > 0 else 0.0

        # Average confidence
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        # Section structure
        section_structure = []
        if result.structure:
            section_structure = [s.section_type.value for s in result.structure.sections]

        # Common terms (terms that appear in multiple requirement descriptions)
        common_terms = list(result.definitions.keys()) if result.definitions else []

        return StatutePatterns(
            statute_name=result.statute_name,
            definition_count=len(result.definitions),
            requirement_count=total,
            verification_rate=verification_rate,
            avg_confidence=avg_confidence,
            category_distribution=cat_dist,
            section_structure=section_structure,
            common_terms=common_terms[:20],
        )
