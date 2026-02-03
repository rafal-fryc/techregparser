"""Citation verification tool for validating statute citations.

Enhanced with multi-strategy matching to handle:
- PDF line breaks (multi-line joining)
- Incomplete quotes (n-gram and substring matching)
- Missing punctuation (aggressive normalization)
- Variable-length quotes (sliding windows)
"""

import re
from dataclasses import dataclass
from typing import Optional
from difflib import SequenceMatcher

from ..models.citation import Citation


@dataclass
class VerificationResult:
    """Result of a citation verification.

    Attributes:
        valid: Whether the citation was found in the statute
        found_text: The matching text found (if any)
        confidence: Confidence score (0.0 to 1.0)
        error: Error message if verification failed
        line_numbers: Line numbers where the text was found
        match_type: Type of match (exact, fuzzy, partial, etc.)
    """
    valid: bool
    found_text: Optional[str] = None
    confidence: float = 0.0
    error: Optional[str] = None
    line_numbers: Optional[tuple[int, int]] = None
    match_type: str = "none"

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "valid": self.valid,
            "found_text": self.found_text,
            "confidence": self.confidence,
            "error": self.error,
            "line_numbers": list(self.line_numbers) if self.line_numbers else None,
            "match_type": self.match_type,
        }


class CitationVerifier:
    """Verifies citations against statute text using multi-strategy matching.

    This tool checks that quoted text actually exists in the statute,
    preventing hallucinated requirements. Uses tiered matching strategies
    from strict to lenient to maximize verification accuracy.
    """

    def __init__(self, statute_text: str):
        """Initialize with statute text.

        Args:
            statute_text: The full text of the statute
        """
        self.statute_text = statute_text
        self.lines = statute_text.split("\n")

        # Standard normalization
        self.normalized_text = self._normalize(statute_text, level="standard")
        self.normalized_lines = [self._normalize(line, level="standard") for line in self.lines]

        # Aggressive normalization (no punctuation) for fallback
        self.aggressive_text = self._normalize(statute_text, level="aggressive")

        # Multi-line joined blocks for cross-line matching
        self.joined_paragraphs = self._create_joined_blocks()

    @staticmethod
    def _normalize(text: str, level: str = "standard") -> str:
        """Normalize text for comparison.

        Args:
            text: Text to normalize
            level: Normalization level - "minimal", "standard", or "aggressive"

        Returns:
            Normalized text
        """
        # Level 1 (minimal): Just case and whitespace
        text = text.lower().strip()
        text = re.sub(r"\s+", " ", text)

        if level == "minimal":
            return text

        # Level 2 (standard): Quote and dash normalization
        text = re.sub(r'["""]', '"', text)
        text = re.sub(r"[''']", "'", text)
        text = re.sub(r"[—–-]+", "-", text)  # Normalize dashes
        text = re.sub(r"§", "section", text)  # Normalize section symbols

        if level == "standard":
            return text

        # Level 3 (aggressive): Remove all punctuation
        text = re.sub(r'[^\w\s]', ' ', text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _create_joined_blocks(self) -> list[tuple[str, int, int]]:
        """Create joined text blocks for multi-line matching.

        Joins consecutive non-empty lines and creates sliding windows
        of 2-5 lines to handle quotes that span PDF line breaks.

        Returns:
            List of (joined_text, start_line, end_line) tuples
        """
        blocks = []

        # Create sliding windows of consecutive lines
        for window_size in [2, 3, 4, 5, 6, 7, 8]:
            for i in range(len(self.normalized_lines) - window_size + 1):
                window_lines = self.normalized_lines[i:i + window_size]
                # Join non-empty lines
                window_text = " ".join(line.strip() for line in window_lines if line.strip())
                if window_text and len(window_text) > 20:
                    blocks.append((
                        window_text,
                        i + 1,  # 1-indexed
                        i + window_size
                    ))

        # Also add full paragraphs (lines separated by empty lines)
        current_block = []
        start_line = 0

        for i, line in enumerate(self.normalized_lines):
            if not line.strip():
                if current_block:
                    joined = " ".join(current_block)
                    if len(joined) > 20:
                        blocks.append((joined, start_line + 1, i))
                    current_block = []
                start_line = i + 1
            else:
                if not current_block:
                    start_line = i
                current_block.append(line.strip())

        # Don't forget last block
        if current_block:
            joined = " ".join(current_block)
            if len(joined) > 20:
                blocks.append((joined, start_line + 1, len(self.normalized_lines)))

        return blocks

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """Calculate similarity ratio between two texts."""
        return SequenceMatcher(None, text1, text2).ratio()

    def _find_text(self, quoted_text: str) -> Optional[tuple[str, int, int, str, float]]:
        """Find quoted text using tiered matching strategies.

        Tries multiple approaches from strict to lenient:
        1. Exact match
        2. Normalized exact match (aggressive)
        3. Substring match
        4. Multi-line fuzzy match
        5. N-gram overlap matching
        6. Keyword anchor matching

        Returns:
            Tuple of (found_text, start_line, end_line, match_type, confidence) or None
        """
        # Strategy 1: Exact match with standard normalization
        result = self._try_exact_match(quoted_text)
        if result:
            return result

        # Strategy 2: Exact match with aggressive normalization
        result = self._try_normalized_exact_match(quoted_text)
        if result:
            return result

        # Strategy 3: Substring containment
        result = self._try_substring_match(quoted_text)
        if result:
            return result

        # Strategy 4: Multi-line fuzzy match
        result = self._try_multiline_fuzzy_match(quoted_text)
        if result:
            return result

        # Strategy 5: N-gram overlap matching
        result = self._try_ngram_match(quoted_text)
        if result:
            return result

        # Strategy 6: Keyword anchor matching
        result = self._try_keyword_anchor_match(quoted_text)
        if result:
            return result

        return None

    def _try_exact_match(self, quoted_text: str) -> Optional[tuple[str, int, int, str, float]]:
        """Try exact substring match with standard normalization."""
        normalized_quote = self._normalize(quoted_text, level="standard")

        if normalized_quote in self.normalized_text:
            start_line, end_line = self._find_line_numbers(normalized_quote)
            return (quoted_text, start_line, end_line, "exact", 1.0)

        return None

    def _try_normalized_exact_match(self, quoted_text: str) -> Optional[tuple[str, int, int, str, float]]:
        """Try exact match with aggressive normalization (removes punctuation)."""
        aggressive_quote = self._normalize(quoted_text, level="aggressive")

        if len(aggressive_quote) < 15:
            return None  # Too short, might have false positives

        if aggressive_quote in self.aggressive_text:
            start_line, end_line = self._find_line_numbers_aggressive(aggressive_quote)
            return (quoted_text, start_line, end_line, "exact_normalized", 0.95)

        return None

    def _try_substring_match(self, quoted_text: str) -> Optional[tuple[str, int, int, str, float]]:
        """Check if quote is a substring of any multi-line block or vice versa."""
        normalized_quote = self._normalize(quoted_text, level="standard")

        # Check if quote is contained in any block
        for block_text, start_line, end_line in self.joined_paragraphs:
            if normalized_quote in block_text:
                return (quoted_text, start_line, end_line, "substring", 0.98)

        # Check if any substantial portion of a block is in the quote
        # (handles cases where LLM added extra context)
        if len(normalized_quote) > 50:
            for block_text, start_line, end_line in self.joined_paragraphs:
                if len(block_text) > 30 and block_text in normalized_quote:
                    return (block_text, start_line, end_line, "contains", 0.85)

        return None

    def _try_multiline_fuzzy_match(self, quoted_text: str) -> Optional[tuple[str, int, int, str, float]]:
        """Fuzzy match against joined multi-line blocks."""
        normalized_quote = self._normalize(quoted_text, level="standard")
        quote_len = len(normalized_quote)

        if quote_len < 20:
            return None

        best_match = None
        best_score = 0.0
        best_lines = (0, 0)

        for block_text, start_line, end_line in self.joined_paragraphs:
            block_len = len(block_text)

            # Skip blocks that are very different in length
            if block_len < quote_len * 0.4 or block_len > quote_len * 2.5:
                continue

            score = self._calculate_similarity(normalized_quote, block_text)

            if score > best_score:
                best_score = score
                best_match = block_text
                best_lines = (start_line, end_line)

        # Also try sliding window through full text
        window_result = self._sliding_window_fuzzy(normalized_quote)
        if window_result and window_result[1] > best_score:
            best_score = window_result[1]
            best_match = window_result[0]
            best_lines = window_result[2]

        if best_score >= 0.70:  # Lower threshold for multi-line tolerance
            return (best_match, best_lines[0], best_lines[1], "fuzzy_multiline", best_score)

        return None

    def _try_ngram_match(self, quoted_text: str) -> Optional[tuple[str, int, int, str, float]]:
        """N-gram overlap matching for partial/incomplete quotes."""
        normalized_quote = self._normalize(quoted_text, level="aggressive")
        quote_words = normalized_quote.split()

        if len(quote_words) < 5:
            return None  # Too short for n-gram matching

        # Generate 4-grams from the quote
        quote_ngrams = set()
        for i in range(len(quote_words) - 3):
            ngram = " ".join(quote_words[i:i + 4])
            quote_ngrams.add(ngram)

        if not quote_ngrams:
            return None

        best_overlap = 0.0
        best_block = None
        best_lines = (0, 0)

        for block_text, start_line, end_line in self.joined_paragraphs:
            block_normalized = self._normalize(block_text, level="aggressive")
            block_words = block_normalized.split()

            if len(block_words) < 5:
                continue

            # Generate 4-grams from block
            block_ngrams = set()
            for i in range(len(block_words) - 3):
                ngram = " ".join(block_words[i:i + 4])
                block_ngrams.add(ngram)

            if not block_ngrams:
                continue

            # Calculate overlap
            intersection = len(quote_ngrams & block_ngrams)
            min_size = min(len(quote_ngrams), len(block_ngrams))

            if min_size > 0:
                # Use proportion of smaller set that overlaps
                overlap = intersection / min_size
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_block = block_text
                    best_lines = (start_line, end_line)

        if best_overlap >= 0.40:  # At least 40% n-gram overlap
            confidence = 0.55 + (best_overlap * 0.40)  # Scale to 0.55-0.95
            return (best_block, best_lines[0], best_lines[1], "ngram", min(confidence, 0.85))

        return None

    def _try_keyword_anchor_match(self, quoted_text: str) -> Optional[tuple[str, int, int, str, float]]:
        """Find matches based on rare/unique keyword anchors."""
        normalized_quote = self._normalize(quoted_text, level="standard")
        words = normalized_quote.split()

        # Find "anchor" words - longer words that are more unique
        anchor_words = [w for w in words if len(w) >= 7 and w.isalpha()]

        if len(anchor_words) < 2:
            return None

        best_block = None
        best_score = 0.0
        best_lines = (0, 0)

        for block_text, start_line, end_line in self.joined_paragraphs:
            anchor_hits = sum(1 for anchor in anchor_words if anchor in block_text)
            hit_ratio = anchor_hits / len(anchor_words)

            if hit_ratio >= 0.5:  # At least 50% of anchors found
                # Verify with fuzzy match
                similarity = self._calculate_similarity(normalized_quote, block_text)
                combined_score = (hit_ratio * 0.4) + (similarity * 0.6)

                if combined_score > best_score:
                    best_score = combined_score
                    best_block = block_text
                    best_lines = (start_line, end_line)

        if best_score >= 0.50:
            return (best_block, best_lines[0], best_lines[1], "anchor", min(best_score, 0.70))

        return None

    def _sliding_window_fuzzy(self, normalized_quote: str) -> Optional[tuple[str, float, tuple[int, int]]]:
        """Sliding window fuzzy search with variable window sizes."""
        quote_len = len(normalized_quote)

        if quote_len < 30:
            return None

        best_match = None
        best_score = 0.0
        best_position = 0

        # Try windows from 70% to 130% of quote length
        for window_mult in [0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3]:
            window_size = int(quote_len * window_mult)
            if window_size < 25 or window_size > len(self.normalized_text):
                continue

            # Slide through the normalized text (step by characters for accuracy)
            step = max(10, window_size // 10)
            for i in range(0, len(self.normalized_text) - window_size, step):
                window = self.normalized_text[i:i + window_size]
                score = self._calculate_similarity(normalized_quote, window)

                if score > best_score:
                    best_score = score
                    best_match = window
                    best_position = i

        if best_score >= 0.70 and best_match:
            start_line, end_line = self._position_to_lines(best_position, len(best_match))
            return (best_match, best_score, (start_line, end_line))

        return None

    def _position_to_lines(self, start_pos: int, length: int) -> tuple[int, int]:
        """Convert character position in normalized text to line numbers."""
        current_pos = 0
        start_line = 1
        end_line = 1

        for i, line in enumerate(self.normalized_lines):
            line_len = len(line) + 1  # +1 for space that replaced newline

            if current_pos <= start_pos < current_pos + line_len:
                start_line = i + 1

            if current_pos <= start_pos + length <= current_pos + line_len:
                end_line = i + 1
                break

            current_pos += line_len

        return (start_line, max(end_line, start_line))

    def _find_line_numbers(self, normalized_text: str) -> tuple[int, int]:
        """Find line numbers where normalized text appears."""
        # First try single-line match
        for i, line in enumerate(self.normalized_lines):
            if normalized_text in line or line in normalized_text:
                return (i + 1, i + 1)

        # Try in joined blocks
        for block_text, start_line, end_line in self.joined_paragraphs:
            if normalized_text in block_text:
                return (start_line, end_line)

        return (0, 0)

    def _find_line_numbers_aggressive(self, aggressive_text: str) -> tuple[int, int]:
        """Find line numbers using aggressive normalization."""
        for i, line in enumerate(self.lines):
            line_aggressive = self._normalize(line, level="aggressive")
            if aggressive_text in line_aggressive or line_aggressive in aggressive_text:
                return (i + 1, i + 1)

        return (0, 0)

    def _find_closest_match(self, quoted_text: str) -> Optional[tuple[str, float, tuple[int, int]]]:
        """Find the closest match even if below threshold (for error reporting)."""
        normalized_quote = self._normalize(quoted_text, level="standard")
        best_match = None
        best_score = 0.0
        best_lines = (0, 0)

        for block_text, start_line, end_line in self.joined_paragraphs:
            score = self._calculate_similarity(normalized_quote, block_text)
            if score > best_score:
                best_score = score
                best_match = block_text
                best_lines = (start_line, end_line)

        if best_score >= 0.25:  # At least 25% similar for reporting
            return (best_match, best_score, best_lines)

        return None

    def _adjust_confidence(
        self,
        original_quote: str,
        found_text: str,
        match_type: str,
        base_confidence: float
    ) -> float:
        """Adjust confidence based on match characteristics."""
        confidence = base_confidence

        # Bonus for length similarity
        len_ratio = min(len(original_quote), len(found_text)) / max(len(original_quote), len(found_text), 1)
        if len_ratio > 0.85:
            confidence += 0.03

        # Penalty for very short matches (might be coincidental)
        if len(original_quote) < 30:
            confidence -= 0.05

        # Bonus for high-confidence match types
        if match_type in ("exact", "exact_normalized", "substring"):
            confidence = min(confidence + 0.02, 1.0)

        return max(0.0, min(1.0, confidence))

    def verify(self, citation: Citation) -> VerificationResult:
        """Verify a citation exists in the statute.

        Args:
            citation: The citation to verify

        Returns:
            VerificationResult with verification status
        """
        if not citation.quoted_text:
            return VerificationResult(
                valid=False,
                error="Citation has no quoted text",
                confidence=0.0,
            )

        result = self._find_text(citation.quoted_text)

        if result:
            found_text, start_line, end_line, match_type, confidence = result

            # Adjust confidence based on match quality
            confidence = self._adjust_confidence(
                citation.quoted_text,
                found_text,
                match_type,
                confidence
            )

            return VerificationResult(
                valid=confidence >= 0.55,  # Threshold for "valid"
                found_text=found_text,
                confidence=confidence,
                line_numbers=(start_line, end_line),
                match_type=match_type,
            )

        # No match found - provide helpful close match info
        close_match = self._find_closest_match(citation.quoted_text)
        if close_match:
            return VerificationResult(
                valid=False,
                found_text=close_match[0][:200] + "..." if len(close_match[0]) > 200 else close_match[0],
                confidence=close_match[1],
                line_numbers=close_match[2],
                match_type="close_no_match",
                error=f"Best match ({close_match[1]:.0%} similar) found but below threshold",
            )

        return VerificationResult(
            valid=False,
            error=f"Could not find quoted text: '{citation.quoted_text[:50]}...'",
            confidence=0.0,
        )

    def verify_section(self, section_ref: str) -> bool:
        """Check if a section reference exists in the statute.

        Args:
            section_ref: Section reference like "541.001(a)(1)"

        Returns:
            True if the section reference is found
        """
        normalized_ref = section_ref.lower().strip()

        patterns = [
            rf"\b{re.escape(normalized_ref)}\b",
            rf"section\s+{re.escape(normalized_ref)}",
            rf"§\s*{re.escape(normalized_ref)}",
        ]

        for pattern in patterns:
            if re.search(pattern, self.normalized_text, re.IGNORECASE):
                return True

        return False


def verify_citation(
    statute_path: str,
    citation_data: dict,
) -> dict:
    """Tool function to verify a citation exists in a statute.

    This is the function signature expected by the Agent SDK.

    Args:
        statute_path: Path to the statute file
        citation_data: Dictionary with citation details:
            - section: Section reference (e.g., "541.001(a)(1)")
            - quoted_text: Exact text from statute
            - context: (Optional) Surrounding context

    Returns:
        Dictionary with verification results:
            - valid: bool
            - found_text: str or None
            - confidence: float
            - error: str or None
    """
    try:
        with open(statute_path, "r", encoding="utf-8") as f:
            statute_text = f.read()
    except FileNotFoundError:
        return {
            "valid": False,
            "found_text": None,
            "confidence": 0.0,
            "error": f"Statute file not found: {statute_path}",
        }
    except Exception as e:
        return {
            "valid": False,
            "found_text": None,
            "confidence": 0.0,
            "error": f"Error reading statute file: {str(e)}",
        }

    citation = Citation(
        section=citation_data.get("section", ""),
        quoted_text=citation_data.get("quoted_text", ""),
        context=citation_data.get("context", ""),
    )

    verifier = CitationVerifier(statute_text)
    result = verifier.verify(citation)

    return result.to_dict()
