"""Main entry point for the TechRegParser system.

Usage:
    python -m TechRegParser.main <statute_path> [--output <output_path>] [--format json|markdown]
    python -m TechRegParser.main --input-dir <dir> [--parallel 3] [--output <output_dir>]
    techreg-parser view

Example:
    python -m TechRegParser.main texas_privacy_law.txt --output results.json --format json
    python -m TechRegParser.main --input-dir "State Privacy Laws/" --parallel 3 --output results/
    techreg-parser view
"""

import asyncio
import argparse
import json
import sys
import time
import webbrowser
from pathlib import Path

from .agents import TechRegParserOrchestrator
from .config import OrchestratorConfig


def open_viewer() -> None:
    """Open the viewer HTML in the default browser."""
    # Locate viewer.html relative to this module file — works reliably
    # with editable installs, unlike importlib.resources on some setups.
    viewer_path = Path(__file__).parent / "viewer.html"
    if not viewer_path.exists():
        print(f"Error: viewer.html not found at {viewer_path}", file=sys.stderr)
        sys.exit(1)

    print("Opening viewer...")
    webbrowser.open(f"file:///{viewer_path.resolve().as_posix()}")


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

    # Batch-process a directory of PDFs with 3 concurrent workers
    python -m TechRegParser.main --input-dir "State Privacy Laws/" --parallel 3 --output results/

    # Re-run without using Phase 1 cache
    python -m TechRegParser.main statute.txt --no-cache --output results.json

    # Open the viewer (then drag-and-drop or select a JSON file)
    techreg-parser view
        """,
    )

    parser.add_argument(
        "statute_path",
        type=str,
        nargs="?",
        default=None,
        help="Path to the statute file (text or PDF format), or 'view' to open the viewer",
    )

    parser.add_argument(
        "--input-dir",
        type=str,
        default=None,
        help="Directory containing statute files (*.pdf, *.txt) for batch processing",
    )

    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output file path (single file) or directory (batch mode)",
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
        "--no-cache",
        action="store_true",
        help="Disable Phase 1 structure caching (force re-analysis on every run)",
    )

    parser.add_argument(
        "--parallel", "-p",
        type=int,
        default=1,
        help="Number of statutes to process concurrently in batch mode (default: 1, max: 4)",
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


async def analyze_single(args, config):
    """Analyze a single statute file."""
    statute_path = Path(args.statute_path)
    if not statute_path.exists():
        print(f"Error: Statute file not found: {statute_path}", file=sys.stderr)
        sys.exit(1)

    orchestrator = TechRegParserOrchestrator(config=config)

    print(f"Analyzing statute: {statute_path}")
    if args.resume:
        print("(Resuming from previous checkpoint)")
    print("-" * 50)

    result = await orchestrator.analyze_statute(
        statute_path=str(statute_path),
        output_format=args.format,
        resume=args.resume,
    )

    # Generate output
    if args.format == "markdown":
        output = result.to_markdown()
    else:
        output = json.dumps(result.to_dict(), indent=2)

    # Write output
    if args.output:
        output_path = Path(args.output)
        # If output is a directory, generate filename
        if output_path.is_dir():
            ext = ".md" if args.format == "markdown" else ".json"
            output_path = output_path / f"{statute_path.stem}_v5{ext}"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"\nResults written to: {output_path}")
    else:
        print("\n" + output)

    # Print summary
    print_summary(result)

    # Run evaluation if requested
    if args.eval:
        run_evaluation(args, result)

    return result


async def run_batch(paths, output_dir, config, args, parallel=1):
    """Process multiple statute files concurrently.

    Args:
        paths: List of statute file paths
        output_dir: Directory to write output files
        config: OrchestratorConfig
        args: Parsed CLI arguments
        parallel: Max concurrent workers
    """
    sem = asyncio.Semaphore(parallel)
    results = []
    start_time = time.time()

    print(f"Batch processing {len(paths)} statutes with {parallel} concurrent worker(s)")
    print("=" * 60)

    async def process_one(path):
        async with sem:
            statute_start = time.time()
            name = Path(path).stem
            print(f"\n[START] {name}")
            try:
                orchestrator = TechRegParserOrchestrator(config=config)
                result = await orchestrator.analyze_statute(
                    statute_path=str(path),
                    output_format=args.format,
                )

                # Write output
                ext = ".md" if args.format == "markdown" else ".json"
                out_path = output_dir / f"{name}_v5{ext}"
                if args.format == "markdown":
                    output = result.to_markdown()
                else:
                    output = json.dumps(result.to_dict(), indent=2)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(output)

                elapsed = time.time() - statute_start
                verified = len(result.get_verified_requirements())
                print(f"[DONE] {name}: {len(result.requirements)} reqs, "
                      f"{verified} verified, {len(result.definitions)} defs "
                      f"({elapsed:.0f}s)")
                return name, len(result.requirements), None
            except Exception as e:
                elapsed = time.time() - statute_start
                print(f"[FAIL] {name}: {e} ({elapsed:.0f}s)")
                return name, -1, str(e)

    tasks = [process_one(p) for p in paths]
    for coro in asyncio.as_completed(tasks):
        result = await coro
        results.append(result)

    # Print batch summary
    total_time = time.time() - start_time
    print("\n" + "=" * 60)
    print("BATCH SUMMARY")
    print("=" * 60)
    succeeded = 0
    failed = 0
    total_reqs = 0
    for name, count, error in sorted(results):
        if count >= 0:
            print(f"  {name}: {count} requirements")
            succeeded += 1
            total_reqs += count
        else:
            print(f"  {name}: FAILED ({error})")
            failed += 1
    print(f"\nCompleted: {succeeded}/{len(paths)} statutes "
          f"({total_reqs} total requirements)")
    if failed:
        print(f"Failed: {failed}")
    print(f"Total time: {total_time:.0f}s ({total_time/60:.1f} min)")


def print_summary(result):
    """Print analysis summary for a single result."""
    print("\n" + "=" * 50)
    print("Analysis Summary:")
    print(f"  Total Requirements: {len(result.requirements)}")
    print(f"  Verified: {len(result.get_verified_requirements())}")
    print(f"  Unverified: {len(result.unverified_items)}")
    print(f"  Definitions: {len(result.definitions)}")

    print("\nRequirements by Category:")
    for cat in ["disclosure", "operational", "technical", "legal_framework"]:
        reqs = result.get_requirements_by_category(cat)
        print(f"  {cat.title()}: {len(reqs)}")


def run_evaluation(args, result):
    """Run evaluation metrics if requested."""
    from .evaluation.eval_framework import EvaluationFramework
    evaluator = EvaluationFramework()

    gold_standard = None
    if args.eval_gold:
        gold_path = Path(args.eval_gold)
        if gold_path.exists():
            with open(gold_path, "r", encoding="utf-8") as f:
                gold_data = json.load(f)
            from .models import AnalysisResult as AR
            gold_standard = AR.from_dict(gold_data)
        else:
            print(f"\nWarning: Gold standard file not found: {gold_path}", file=sys.stderr)

    report = evaluator.evaluate(result, gold_standard=gold_standard)
    print("\n" + "=" * 50)
    print("Evaluation Report:")
    print(evaluator.format_report(report))


async def main():
    """Main entry point."""
    args = parse_args()

    # Handle `view` subcommand: techreg-parser view
    if args.statute_path == "view":
        open_viewer()
        return

    # Validate arguments
    if not args.statute_path and not args.input_dir:
        print("Error: Provide either a statute_path or --input-dir", file=sys.stderr)
        sys.exit(1)

    # Clamp parallel to [1, 4]
    parallel = max(1, min(4, args.parallel))

    # Configure the orchestrator
    config = OrchestratorConfig(
        working_directory=args.working_dir,
        output_format=args.format,
        verify_citations=not args.no_verify,
        classify_requirements=not args.no_classify,
        use_memory=not args.no_memory,
        use_cache=not args.no_cache,
    )

    try:
        if args.input_dir:
            # Batch mode: process all PDFs/text files in directory
            input_dir = Path(args.input_dir)
            if not input_dir.is_dir():
                print(f"Error: Input directory not found: {input_dir}", file=sys.stderr)
                sys.exit(1)

            paths = sorted(
                list(input_dir.glob("*.pdf")) + list(input_dir.glob("*.txt"))
            )
            if not paths:
                print(f"Error: No .pdf or .txt files found in {input_dir}", file=sys.stderr)
                sys.exit(1)

            # Set up output directory
            output_dir = Path(args.output) if args.output else input_dir / "output"
            output_dir.mkdir(parents=True, exist_ok=True)

            # Skip already-completed statutes
            remaining = []
            for p in paths:
                ext = ".md" if args.format == "markdown" else ".json"
                out_path = output_dir / f"{p.stem}_v5{ext}"
                if out_path.exists():
                    print(f"SKIP {p.stem} (output already exists)")
                else:
                    remaining.append(p)

            if not remaining:
                print("All statutes already processed.")
            else:
                await run_batch(remaining, output_dir, config, args, parallel=parallel)
        else:
            # Single file mode
            await analyze_single(args, config)

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
