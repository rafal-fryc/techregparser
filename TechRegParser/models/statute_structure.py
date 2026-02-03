"""Statute structure models for parsing and organizing statute content."""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

from .requirement import Requirement
from .citation import Citation


class SectionType(str, Enum):
    """Types of sections found in privacy/tech statutes."""
    DEFINITIONS = "definitions"
    APPLICABILITY = "applicability"
    CONSUMER_RIGHTS = "consumer_rights"
    CONTROLLER_DUTIES = "controller_duties"
    PROCESSOR_DUTIES = "processor_duties"
    EXEMPTIONS = "exemptions"
    ENFORCEMENT = "enforcement"
    GENERAL = "general"
    PREAMBLE = "preamble"
    OTHER = "other"


@dataclass
class Definition:
    """A term definition from the statute's definitions section.

    Attributes:
        term: The term being defined
        definition: The statutory definition text
        section: The section where this definition appears
        notes: Any additional notes about the definition
    """
    term: str
    definition: str
    section: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "term": self.term,
            "definition": self.definition,
            "section": self.section,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Definition":
        """Create Definition from dictionary."""
        return cls(
            term=data.get("term", ""),
            definition=data.get("definition", ""),
            section=data.get("section", ""),
            notes=data.get("notes", ""),
        )


@dataclass
class StatuteSection:
    """Represents a section of the statute.

    Attributes:
        id: Section identifier (e.g., "541.001")
        title: Section title
        section_type: Type classification of the section
        content: Raw text content of the section
        start_line: Starting line number in source
        end_line: Ending line number in source
        subsections: Any nested subsections
    """
    id: str
    title: str
    section_type: SectionType
    content: str
    start_line: int = 0
    end_line: int = 0
    subsections: list["StatuteSection"] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "id": self.id,
            "title": self.title,
            "section_type": self.section_type.value,
            "content": self.content,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "subsections": [s.to_dict() for s in self.subsections],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StatuteSection":
        """Create StatuteSection from dictionary."""
        # Handle section_type - could be string or already in correct format
        section_type_val = data.get("section_type", "general")
        try:
            section_type = SectionType(section_type_val.lower() if isinstance(section_type_val, str) else section_type_val)
        except ValueError:
            section_type = SectionType.OTHER

        return cls(
            id=data.get("id", data.get("section", "")),  # Allow 'section' as fallback key
            title=data.get("title", data.get("name", "")),  # Allow 'name' as fallback key
            section_type=section_type,
            content=data.get("content", data.get("text", "")),  # Allow 'text' as fallback key
            start_line=data.get("start_line", 0),
            end_line=data.get("end_line", 0),
            subsections=[cls.from_dict(s) for s in data.get("subsections", [])],
        )


@dataclass
class StatuteStructure:
    """Complete structure of a parsed statute.

    Attributes:
        name: Name of the statute
        citation: Official citation
        effective_date: When the statute takes effect
        sections: List of parsed sections
        definitions: Dictionary of term definitions
        raw_text: Original full text
    """
    name: str
    citation: str = ""
    effective_date: str = ""
    sections: list[StatuteSection] = field(default_factory=list)
    definitions: dict[str, Definition] = field(default_factory=dict)
    raw_text: str = ""

    def get_section_by_type(self, section_type: SectionType) -> list[StatuteSection]:
        """Get all sections of a given type."""
        return [s for s in self.sections if s.section_type == section_type]

    def get_definitions_section(self) -> Optional[StatuteSection]:
        """Get the definitions section if it exists."""
        sections = self.get_section_by_type(SectionType.DEFINITIONS)
        return sections[0] if sections else None

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "name": self.name,
            "citation": self.citation,
            "effective_date": self.effective_date,
            "sections": [s.to_dict() for s in self.sections],
            "definitions": {k: v.to_dict() for k, v in self.definitions.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StatuteStructure":
        """Create StatuteStructure from dictionary."""
        # Parse definitions - handle both dict and list formats
        definitions = {}
        defs_data = data.get("definitions", {})
        if isinstance(defs_data, dict):
            for k, v in defs_data.items():
                if isinstance(v, dict):
                    definitions[k] = Definition.from_dict(v)
                elif isinstance(v, str):
                    definitions[k] = Definition(term=k, definition=v)
        elif isinstance(defs_data, list):
            for d in defs_data:
                if isinstance(d, dict):
                    term = d.get("term", "")
                    if term:
                        definitions[term] = Definition.from_dict(d)

        return cls(
            name=data.get("name", data.get("statute_name", "Unknown Statute")),
            citation=data.get("citation", ""),
            effective_date=data.get("effective_date", ""),
            sections=[StatuteSection.from_dict(s) for s in data.get("sections", [])],
            definitions=definitions,
            raw_text=data.get("raw_text", ""),
        )


@dataclass
class AnalysisResult:
    """Complete analysis result from processing a statute.

    Attributes:
        statute_name: Name of the analyzed statute
        effective_date: Effective date of the statute
        definitions: Dictionary of extracted definitions
        requirements: List of all extracted requirements
        structure: The parsed statute structure
        unverified_items: Items that failed verification
        metadata: Additional metadata about the analysis
    """
    statute_name: str
    effective_date: str = ""
    definitions: dict[str, Definition] = field(default_factory=dict)
    requirements: list[Requirement] = field(default_factory=list)
    structure: Optional[StatuteStructure] = None
    unverified_items: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def get_verified_requirements(self) -> list[Requirement]:
        """Get only verified requirements."""
        return [r for r in self.requirements if r.verified]

    def get_requirements_by_category(self, category: str) -> list[Requirement]:
        """Get requirements filtered by category."""
        from .requirement import RequirementCategory
        try:
            cat = RequirementCategory(category)
            return [r for r in self.requirements if r.category == cat]
        except ValueError:
            return []

    def get_disclosure_requirements(self) -> list[Requirement]:
        """Get only disclosure requirements."""
        return [r for r in self.requirements if r.is_disclosure_requirement()]

    def get_operational_requirements(self) -> list[Requirement]:
        """Get only operational requirements."""
        return [r for r in self.requirements if r.is_operational_requirement()]

    def to_dict(self, include_structure: bool = False) -> dict:
        """Convert to dictionary representation.

        Args:
            include_structure: If True, include the full statute structure
                (sections with raw content). Defaults to False to reduce
                output size since structure duplicates the statute text.
        """
        result = {
            "statute_name": self.statute_name,
            "effective_date": self.effective_date,
            "definitions": {k: v.to_dict() for k, v in self.definitions.items()},
            "requirements": [r.to_dict() for r in self.requirements],
            "unverified_items": self.unverified_items,
            "metadata": self.metadata,
        }
        if include_structure:
            result["structure"] = self.structure.to_dict() if self.structure else None
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "AnalysisResult":
        """Create AnalysisResult from dictionary."""
        return cls(
            statute_name=data["statute_name"],
            effective_date=data.get("effective_date", ""),
            definitions={k: Definition.from_dict(v) for k, v in data.get("definitions", {}).items()},
            requirements=[Requirement.from_dict(r) for r in data.get("requirements", [])],
            structure=StatuteStructure.from_dict(data["structure"]) if data.get("structure") else None,
            unverified_items=data.get("unverified_items", []),
            metadata=data.get("metadata", {}),
        )

    def to_markdown(self) -> str:
        """Generate a markdown report of the analysis."""
        lines = [
            f"# Statute Analysis: {self.statute_name}",
            "",
            f"**Effective Date:** {self.effective_date or 'Not specified'}",
            "",
        ]

        # Summary stats
        verified_count = len(self.get_verified_requirements())
        total_count = len(self.requirements)
        lines.extend([
            "## Summary",
            "",
            f"- **Total Requirements Extracted:** {total_count}",
            f"- **Verified Requirements:** {verified_count}",
            f"- **Unverified Items:** {len(self.unverified_items)}",
            f"- **Definitions Extracted:** {len(self.definitions)}",
            "",
        ])

        # Requirements by category
        lines.extend([
            "## Requirements by Category",
            "",
        ])

        from .requirement import RequirementCategory
        for category in RequirementCategory:
            reqs = self.get_requirements_by_category(category.value)
            if reqs:
                lines.append(f"### {category.value.title()} ({len(reqs)})")
                lines.append("")
                for req in reqs:
                    status = "[VERIFIED]" if req.verified else "[UNVERIFIED]"
                    lines.append(f"- {status} {req.description}")
                    lines.append(f"  - *Citation:* {req.citation.section}")
                    if req.conditions:
                        lines.append(f"  - *Conditions:* {', '.join(req.conditions)}")
                lines.append("")

        # Definitions
        if self.definitions:
            lines.extend([
                "## Definitions",
                "",
            ])
            for term, defn in sorted(self.definitions.items()):
                lines.append(f"**{term}**: {defn.definition}")
                lines.append("")

        return "\n".join(lines)
