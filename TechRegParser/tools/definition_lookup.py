"""Definition lookup tool for statute interpretation."""

import re
from dataclasses import dataclass
from typing import Optional

from ..models.statute_structure import Definition


@dataclass
class LookupResult:
    """Result of a definition lookup.

    Attributes:
        found: Whether the term was found
        term: The term that was searched
        definition: The definition if found
        section: Section where the definition appears
        related_terms: Related terms that might be relevant
        notes: Any notes about the definition
    """
    found: bool
    term: str
    definition: Optional[str] = None
    section: Optional[str] = None
    related_terms: list[str] = None
    notes: str = ""

    def __post_init__(self):
        if self.related_terms is None:
            self.related_terms = []

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "found": self.found,
            "term": self.term,
            "definition": self.definition,
            "section": self.section,
            "related_terms": self.related_terms,
            "notes": self.notes,
        }


class DefinitionLookup:
    """Looks up term definitions from a statute's definitions section.

    This ensures consistent interpretation of terms as defined by the statute.
    """

    def __init__(self, definitions: dict[str, Definition]):
        """Initialize with extracted definitions.

        Args:
            definitions: Dictionary mapping terms to Definition objects
        """
        self.definitions = definitions
        # Create normalized lookup for case-insensitive search
        self.normalized_lookup = {
            self._normalize(k): v for k, v in definitions.items()
        }

    @staticmethod
    def _normalize(term: str) -> str:
        """Normalize a term for lookup."""
        return term.lower().strip()

    def lookup(self, term: str) -> LookupResult:
        """Look up a term's definition.

        Args:
            term: The term to look up

        Returns:
            LookupResult with the definition if found
        """
        normalized_term = self._normalize(term)

        # Direct lookup
        if normalized_term in self.normalized_lookup:
            defn = self.normalized_lookup[normalized_term]
            return LookupResult(
                found=True,
                term=term,
                definition=defn.definition,
                section=defn.section,
                notes=defn.notes,
            )

        # Try partial matching
        related = []
        for key in self.normalized_lookup:
            if normalized_term in key or key in normalized_term:
                related.append(key)

        if related:
            return LookupResult(
                found=False,
                term=term,
                related_terms=related,
                notes=f"Term not found exactly, but related terms exist: {', '.join(related)}",
            )

        return LookupResult(
            found=False,
            term=term,
            notes="Term not defined in statute's definitions section",
        )

    def get_all_terms(self) -> list[str]:
        """Get all defined terms."""
        return list(self.definitions.keys())

    def search(self, query: str) -> list[LookupResult]:
        """Search for terms containing the query.

        Args:
            query: Search query

        Returns:
            List of matching definitions
        """
        normalized_query = self._normalize(query)
        results = []

        for term, defn in self.definitions.items():
            normalized_term = self._normalize(term)
            if normalized_query in normalized_term or normalized_query in self._normalize(defn.definition):
                results.append(LookupResult(
                    found=True,
                    term=term,
                    definition=defn.definition,
                    section=defn.section,
                    notes=defn.notes,
                ))

        return results


def lookup_definition(
    term: str,
    definitions: dict,
) -> dict:
    """Tool function to look up a term definition.

    This is the function signature expected by the Agent SDK.

    Args:
        term: The term to look up
        definitions: Dictionary of definitions with structure:
            {
                "term_name": {
                    "term": str,
                    "definition": str,
                    "section": str,
                    "notes": str
                }
            }

    Returns:
        Dictionary with lookup results:
            - found: bool
            - term: str
            - definition: str or None
            - section: str or None
            - related_terms: list[str]
            - notes: str
    """
    # Convert dict to Definition objects
    parsed_definitions = {}
    for key, value in definitions.items():
        if isinstance(value, dict):
            parsed_definitions[key] = Definition(
                term=value.get("term", key),
                definition=value.get("definition", ""),
                section=value.get("section", ""),
                notes=value.get("notes", ""),
            )
        elif isinstance(value, Definition):
            parsed_definitions[key] = value
        else:
            # Assume it's a string definition
            parsed_definitions[key] = Definition(
                term=key,
                definition=str(value),
            )

    lookup = DefinitionLookup(parsed_definitions)
    result = lookup.lookup(term)

    return result.to_dict()


def extract_definitions_from_text(text: str) -> dict[str, Definition]:
    """Extract definitions from statute text.

    Looks for common definition patterns in statute text.

    Args:
        text: The definitions section text

    Returns:
        Dictionary of extracted definitions
    """
    definitions = {}

    # Common patterns for definitions in statutes
    patterns = [
        # Pattern: "Term" means ...
        r'"([^"]+)"\s+means?\s+([^.]+\.)',
        # Pattern: (1) "Term" means ...
        r'\(\d+\)\s*"([^"]+)"\s+means?\s+([^.]+\.)',
        # Pattern: "Term." followed by definition
        r'"([^"]+)\."\s*([A-Z][^.]+\.)',
        # Pattern: Term - definition
        r'([A-Z][a-z]+(?:\s+[a-z]+)*)\s*[-–—]\s*([^.]+\.)',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text, re.MULTILINE | re.IGNORECASE)
        for match in matches:
            if len(match) >= 2:
                term = match[0].strip().strip('"')
                definition = match[1].strip()
                if term and definition and len(term) < 100:  # Sanity check
                    definitions[term] = Definition(
                        term=term,
                        definition=definition,
                    )

    return definitions
