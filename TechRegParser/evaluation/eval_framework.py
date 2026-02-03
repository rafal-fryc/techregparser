"""Evaluation framework for measuring TechRegParser analysis quality.

Tracks verification rate, average confidence, category distribution,
match type distribution, and compares against gold-standard analyses.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EvaluationReport:
    """Evaluation metrics for an analysis run.

    Attributes:
        total_requirements: Total requirements extracted
        verified_count: Number that passed citation verification
        verification_rate: Fraction verified
        avg_confidence: Average confidence across all requirements
        category_distribution: Count per requirement category
        match_type_distribution: Count per citation match type
        unclassified_count: Requirements without a category
        gold_comparison: Comparison metrics if gold standard provided
    """
    total_requirements: int = 0
    verified_count: int = 0
    verification_rate: float = 0.0
    avg_confidence: float = 0.0
    category_distribution: dict[str, int] = field(default_factory=dict)
    match_type_distribution: dict[str, int] = field(default_factory=dict)
    unclassified_count: int = 0
    gold_comparison: Optional[dict] = None


@dataclass
class GoldComparison:
    """Comparison between analysis output and gold standard.

    Attributes:
        precision: Fraction of extracted requirements that match gold
        recall: Fraction of gold requirements found in extraction
        f1: Harmonic mean of precision and recall
        missing_from_extraction: Gold requirements not found
        extra_in_extraction: Extracted requirements not in gold
        category_agreement: Fraction with matching categories
    """
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    missing_from_extraction: int = 0
    extra_in_extraction: int = 0
    category_agreement: float = 0.0


class EvaluationFramework:
    """Evaluates analysis quality and optionally compares to gold standard."""

    def evaluate(
        self,
        result,
        gold_standard=None,
    ) -> EvaluationReport:
        """Evaluate an analysis result.

        Args:
            result: An AnalysisResult object
            gold_standard: Optional AnalysisResult to compare against

        Returns:
            EvaluationReport with computed metrics
        """
        requirements = result.requirements
        total = len(requirements)

        # Verification metrics
        verified = [r for r in requirements if r.verified]
        verification_rate = len(verified) / total if total > 0 else 0.0

        # Confidence metrics
        confidences = [r.confidence for r in requirements if r.confidence > 0]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        # Category distribution
        cat_dist = {}
        unclassified = 0
        for req in requirements:
            cat = req.category.value
            cat_dist[cat] = cat_dist.get(cat, 0) + 1
            if cat == "unclassified":
                unclassified += 1

        # Match type distribution (from citation verification)
        match_dist = {}
        for req in requirements:
            mt = getattr(req.citation, "match_type", "none")
            if mt and mt != "none":
                match_dist[mt] = match_dist.get(mt, 0) + 1
            elif req.verified:
                match_dist["verified"] = match_dist.get("verified", 0) + 1
            else:
                match_dist["unverified"] = match_dist.get("unverified", 0) + 1

        report = EvaluationReport(
            total_requirements=total,
            verified_count=len(verified),
            verification_rate=verification_rate,
            avg_confidence=avg_confidence,
            category_distribution=cat_dist,
            match_type_distribution=match_dist,
            unclassified_count=unclassified,
        )

        # Compare to gold standard if provided
        if gold_standard is not None:
            report.gold_comparison = self._compare_to_gold(result, gold_standard)

        return report

    def _compare_to_gold(self, result, gold) -> dict:
        """Compare extraction results to a gold standard.

        Uses citation section + description overlap to match requirements.

        Args:
            result: The analysis result to evaluate
            gold: The gold standard AnalysisResult

        Returns:
            Dictionary with comparison metrics
        """
        extracted_descs = {r.description.lower().strip() for r in result.requirements}
        gold_descs = {r.description.lower().strip() for r in gold.requirements}

        # Simple string-overlap matching
        matched = 0
        for ed in extracted_descs:
            for gd in gold_descs:
                if self._descriptions_match(ed, gd):
                    matched += 1
                    break

        precision = matched / len(extracted_descs) if extracted_descs else 0.0
        recall = matched / len(gold_descs) if gold_descs else 0.0
        f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

        # Category agreement (for matched requirements)
        cat_matches = 0
        cat_total = 0
        for er in result.requirements:
            for gr in gold.requirements:
                if self._descriptions_match(
                    er.description.lower().strip(),
                    gr.description.lower().strip(),
                ):
                    cat_total += 1
                    if er.category == gr.category:
                        cat_matches += 1
                    break

        category_agreement = cat_matches / cat_total if cat_total > 0 else 0.0

        return {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "missing_from_extraction": len(gold_descs) - matched,
            "extra_in_extraction": len(extracted_descs) - matched,
            "category_agreement": category_agreement,
            "gold_count": len(gold.requirements),
            "extracted_count": len(result.requirements),
        }

    @staticmethod
    def _descriptions_match(desc1: str, desc2: str, threshold: float = 0.6) -> bool:
        """Check if two descriptions refer to the same requirement.

        Uses word overlap ratio as a simple similarity metric.

        Args:
            desc1: First description
            desc2: Second description
            threshold: Minimum overlap ratio to consider a match

        Returns:
            True if descriptions match above threshold
        """
        words1 = set(desc1.split())
        words2 = set(desc2.split())

        if not words1 or not words2:
            return False

        intersection = len(words1 & words2)
        min_size = min(len(words1), len(words2))

        return (intersection / min_size) >= threshold if min_size > 0 else False

    @staticmethod
    def format_report(report: EvaluationReport) -> str:
        """Format an evaluation report as a human-readable string.

        Args:
            report: The evaluation report to format

        Returns:
            Formatted string
        """
        lines = []
        lines.append(f"  Total Requirements: {report.total_requirements}")
        lines.append(f"  Verified:           {report.verified_count} ({report.verification_rate:.0%})")
        lines.append(f"  Avg Confidence:     {report.avg_confidence:.2f}")
        lines.append(f"  Unclassified:       {report.unclassified_count}")

        lines.append("\n  Category Distribution:")
        for cat, count in sorted(report.category_distribution.items()):
            pct = count / report.total_requirements * 100 if report.total_requirements > 0 else 0
            lines.append(f"    {cat:15s}: {count:3d} ({pct:.0f}%)")

        lines.append("\n  Match Type Distribution:")
        for mt, count in sorted(report.match_type_distribution.items()):
            lines.append(f"    {mt:15s}: {count:3d}")

        if report.gold_comparison:
            gc = report.gold_comparison
            lines.append("\n  Gold Standard Comparison:")
            lines.append(f"    Gold count:      {gc['gold_count']}")
            lines.append(f"    Extracted count:  {gc['extracted_count']}")
            lines.append(f"    Precision:        {gc['precision']:.2%}")
            lines.append(f"    Recall:           {gc['recall']:.2%}")
            lines.append(f"    F1 Score:         {gc['f1']:.2%}")
            lines.append(f"    Category Match:   {gc['category_agreement']:.2%}")
            lines.append(f"    Missing:          {gc['missing_from_extraction']}")
            lines.append(f"    Extra:            {gc['extra_in_extraction']}")

        return "\n".join(lines)
