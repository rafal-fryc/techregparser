"""Requirement model for extracted statutory requirements."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import uuid

from .citation import Citation


class RequirementCategory(str, Enum):
    """Categories for classifying requirements.

    DISCLOSURE: Must be stated in privacy policy/notice
    OPERATIONAL: Internal compliance process (response times, procedures)
    TECHNICAL: System/UI implementation (GPC signals, security measures, link placement, UI elements)
    LEGAL_FRAMEWORK: Applicability, exemptions, scope, enforcement mechanisms, penalties, AG authority, cure periods
    """
    DISCLOSURE = "disclosure"
    OPERATIONAL = "operational"
    TECHNICAL = "technical"
    LEGAL_FRAMEWORK = "legal_framework"
    UNCLASSIFIED = "unclassified"


@dataclass
class Requirement:
    """Represents a requirement extracted from a statute.

    Attributes:
        id: Unique identifier for this requirement
        description: Human-readable description of the requirement
        citation: The citation proving this requirement exists
        category: Classification of the requirement type
        applies_to: Who the requirement applies to
        conditions: Any conditions or limitations on the requirement
        verified: Whether the citation has been verified
        confidence: Confidence score for the extraction (0.0 to 1.0)
        source_section: The section of the statute this came from
        obligation_type: Type of obligation (shall, may, must, etc.)
    """
    description: str
    citation: Citation
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    category: RequirementCategory = RequirementCategory.UNCLASSIFIED
    applies_to: str = "controller"
    conditions: list[str] = field(default_factory=list)
    verified: bool = False
    confidence: float = 0.0
    source_section: str = ""
    obligation_type: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary representation, omitting empty fields."""
        result = {
            "id": self.id,
            "description": self.description,
            "citation": self.citation.to_dict(),
            "category": self.category.value,
            "applies_to": self.applies_to,
            "verified": self.verified,
            "confidence": self.confidence,
        }
        if self.conditions:
            result["conditions"] = self.conditions
        if self.source_section:
            result["source_section"] = self.source_section
        if self.obligation_type:
            result["obligation_type"] = self.obligation_type
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "Requirement":
        """Create Requirement from dictionary."""
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            description=data["description"],
            citation=Citation.from_dict(data["citation"]),
            category=RequirementCategory(
                "technical" if data.get("category") == "ui"
                else "legal_framework" if data.get("category") == "enforcement"
                else data.get("category", "unclassified")
            ),
            applies_to=data.get("applies_to", "controller"),
            conditions=data.get("conditions", []),
            verified=data.get("verified", False),
            confidence=data.get("confidence", 0.0),
            source_section=data.get("source_section", ""),
            obligation_type=data.get("obligation_type", ""),
        )

    def is_disclosure_requirement(self) -> bool:
        """Check if this is a disclosure requirement."""
        return self.category == RequirementCategory.DISCLOSURE

    def is_operational_requirement(self) -> bool:
        """Check if this is an operational requirement."""
        return self.category == RequirementCategory.OPERATIONAL

    def __str__(self) -> str:
        """Return a formatted requirement string."""
        status = "[VERIFIED]" if self.verified else "[UNVERIFIED]"
        return f"{status} [{self.category.value.upper()}] {self.description}"
