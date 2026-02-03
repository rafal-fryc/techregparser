"""Main entry point for the TechRegParser system.

Usage:
    python -m TechRegParser.main <statute_path> [--output <output_path>] [--format json|markdown]

Example:
    python -m TechRegParser.main texas_privacy_law.txt --output results.json --format json
"""

import asyncio
import argparse
import sys
from pathlib import Path

from .agents import TechRegParserOrchestrator
from .config import OrchestratorConfig


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Parse tech regulation statutes and extract requirements with verified citations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Analyze a statute and output JSON
    python -m TechRegParser.main texas_privacy_law.txt --output results.json

    # Analyze and output markdown
    python -m TechRegParser.main ccpa.txt --output analysis.md --format markdown

    # Skip citation verification (faster but less reliable)
    python -m TechRegParser.main statute.txt --no-verify

    # Resume a previously interrupted analysis
    python -m TechRegParser.main statute.txt --resume --output results.json

    # Run with evaluation metrics
    python -m TechRegParser.main statute.txt --eval --output results.json
        """,
    )

    parser.add_argument(
        "statute_path",
        type=str,
        help="Path to the statute file (text format)",
    )

    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output file path (default: stdout)",
    )

    parser.add_argument(
        "--format", "-f",
        type=str,
        choices=["json", "markdown"],
        default="json",
        help="Output format (default: json)",
    )

    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip citation verification",
    )

    parser.add_argument(
        "--no-classify",
        action="store_true",
        help="Skip requirement classification",
    )

    parser.add_argument(
        "--working-dir", "-w",
        type=str,
        default=".",
        help="Working directory for the agents",
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from a previous partial run (reuses existing JSONL checkpoint)",
    )

    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="Disable session memory (don't load/save patterns from previous runs)",
    )

    parser.add_argument(
        "--eval",
        action="store_true",
        help="Run evaluation metrics after analysis and print quality report",
    )

    parser.add_argument(
        "--eval-gold",
        type=str,
        default=None,
        help="Path to gold-standard JSON file for evaluation comparison",
    )

    return parser.parse_args()


async def main():
    """Main entry point."""
    args = parse_args()

    # Validate input file
    statute_path = Path(args.statute_path)
    if not statute_path.exists():
        print(f"Error: Statute file not found: {statute_path}", file=sys.stderr)
        sys.exit(1)

    # Configure the orchestrator
    config = OrchestratorConfig(
        working_directory=args.working_dir,
        output_format=args.format,
        verify_citations=not args.no_verify,
        classify_requirements=not args.no_classify,
        use_memory=not args.no_memory,
    )

    # Create and run the orchestrator
    orchestrator = TechRegParserOrchestrator(config=config)

    print(f"Analyzing statute: {statute_path}")
    if args.resume:
        print("(Resuming from previous checkpoint)")
    print("-" * 50)

    try:
        result = await orchestrator.analyze_statute(
            statute_path=str(statute_path),
            output_format=args.format,
            resume=args.resume,
        )

        # Generate output
        if args.format == "markdown":
            output = result.to_markdown()
        else:
            import json
            output = json.dumps(result.to_dict(), indent=2)

        # Write output
        if args.output:
            output_path = Path(args.output)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(output)
            print(f"\nResults written to: {output_path}")
        else:
            print("\n" + output)

        # Print summary
        print("\n" + "=" * 50)
        print("Analysis Summary:")
        print(f"  Total Requirements: {len(result.requirements)}")
        print(f"  Verified: {len(result.get_verified_requirements())}")
        print(f"  Unverified: {len(result.unverified_items)}")
        print(f"  Definitions: {len(result.definitions)}")

        # Category breakdown
        print("\nRequirements by Category:")
        for cat in ["disclosure", "operational", "technical", "enforcement"]:
            reqs = result.get_requirements_by_category(cat)
            print(f"  {cat.title()}: {len(reqs)}")

        # P7b: Run evaluation if requested
        if args.eval:
            from .evaluation.eval_framework import EvaluationFramework
            evaluator = EvaluationFramework()

            gold_standard = None
            if args.eval_gold:
                gold_path = Path(args.eval_gold)
                if gold_path.exists():
                    import json as json_mod
                    with open(gold_path, "r", encoding="utf-8") as f:
                        gold_data = json_mod.load(f)
                    from .models import AnalysisResult as AR
                    gold_standard = AR.from_dict(gold_data)
                else:
                    print(f"\nWarning: Gold standard file not found: {gold_path}", file=sys.stderr)

            report = evaluator.evaluate(result, gold_standard=gold_standard)
            print("\n" + "=" * 50)
            print("Evaluation Report:")
            print(evaluator.format_report(report))

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except NotImplementedError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error during analysis: {e}", file=sys.stderr)
        sys.exit(1)


def run():
    """Run the main function."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
