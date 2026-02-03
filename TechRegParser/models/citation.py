"""Citation model for statute references."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Citation:
    """Represents a citation to a specific part of a statute.

    Attributes:
        section: The section reference (e.g., "541.001(a)(1)")
        quoted_text: The exact text from the statute
        context: Surrounding context for the quote
        line_numbers: Start and end line numbers in the source file
        verified: Whether this citation has been verified to exist
        confidence: Confidence score from verification (0.0 to 1.0)
        match_type: How the citation was matched (exact, fuzzy_multiline, ngram, etc.)
    """
    section: str
    quoted_text: str
    context: str = ""
    line_numbers: Optional[tuple[int, int]] = None
    verified: bool = False
    confidence: float = 0.0
    match_type: str = "none"

    def to_dict(self) -> dict:
        """Convert to dictionary representation, omitting empty fields."""
        result = {
            "section": self.section,
            "quoted_text": self.quoted_text,
            "verified": self.verified,
            "confidence": self.confidence,
        }
        if self.context:
            result["context"] = self.context
        if self.line_numbers is not None:
            result["line_numbers"] = list(self.line_numbers)
        if self.match_type != "none":
            result["match_type"] = self.match_type
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "Citation":
        """Create Citation from dictionary."""
        line_nums = data.get("line_numbers")
        return cls(
            section=data["section"],
            quoted_text=data["quoted_text"],
            context=data.get("context", ""),
            line_numbers=tuple(line_nums) if line_nums else None,
            verified=data.get("verified", False),
            confidence=data.get("confidence", 0.0),
            match_type=data.get("match_type", "none"),
        )

    def __str__(self) -> str:
        """Return a formatted citation string."""
        return f'{self.section}: "{self.quoted_text[:50]}..."' if len(self.quoted_text) > 50 else f'{self.section}: "{self.quoted_text}"'
