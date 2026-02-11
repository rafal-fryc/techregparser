# TechRegParser

A multi-agent system for extracting requirements from data privacy and tech regulation statutes using the Anthropic Agent SDK.

## Features

- **Multi-agent architecture**: Specialized agents for different tasks:
  - **Statute Reader**: Parses statute structure (definitions, applicability, rights, duties, exemptions, enforcement)
  - **Section Analyzer**: Extracts specific requirements with exact citations
  - **Citation Verifier**: Validates all citations against the original text
  - **Requirement Classifier**: Categorizes requirements (disclosure, operational, technical, enforcement)

- **Built-in viewer**: Interactive HTML viewer for exploring extracted requirements, filtering by category/confidence, and browsing the statute structure tree

- **Model Configuration**:
  - **Orchestrator**: Uses Opus for complex coordination
  - **Subagents**: Use Sonnet for specialized tasks

- **Anti-hallucination measures**:
  - Every requirement must have a direct quote from the statute
  - Two-pass verification (extract then verify)
  - Confidence scoring for citations
  - Flagging of unverified requirements

- **Diagnostics and logging**:
  - Phase 1 logs section/definition counts on success, or a clear warning on failure
  - Parse failures log the raw agent response details for debugging

- **Statute interpretation skill**: Incorporates statutory interpretation guidance from legal experts

- **PDF Support**: Can parse both text files and PDFs (with pdfplumber or pypdf)

## Installation

```bash
# Install from PyPI
pip install techreg-parser

# With PDF support
pip install techreg-parser[pdf]

# Or install locally for development
pip install -e .
```

## Usage

### Command Line

```bash
# Analyze a statute and output JSON
techreg-parser path/to/statute.txt --output results.json

# Analyze a PDF statute
techreg-parser path/to/statute.pdf --output results.json

# Output markdown report
techreg-parser path/to/statute.txt --output analysis.md --format markdown

# Skip citation verification (faster but less reliable)
techreg-parser path/to/statute.txt --no-verify

# Batch-process a directory of PDFs with 3 concurrent workers
techreg-parser --input-dir "State Privacy Laws/" --parallel 3 --output results/

# Disable Phase 1 structure caching
techreg-parser path/to/statute.txt --no-cache --output results.json

# Open the interactive viewer (then select a JSON results file)
techreg-parser view
```

### Python API

```python
import asyncio
from TechRegParser import TechRegParserOrchestrator, OrchestratorConfig

async def main():
    config = OrchestratorConfig(
        verify_citations=True,
        classify_requirements=True,
    )

    parser = TechRegParserOrchestrator(config=config)

    result = await parser.analyze_statute(
        statute_path="path/to/texas_privacy_law.txt",
        output_format="json"
    )

    # Access results
    for req in result.requirements:
        print(f"Requirement: {req.description}")
        print(f"  Citation: {req.citation.section}")
        print(f"  Category: {req.category.value}")
        print(f"  Verified: {req.verified}")
        print()

    # Export to file
    await parser.export_results(result, "output.json", format="json")

asyncio.run(main())
```

## Architecture

```
                    +-------------------+
                    |   Orchestrator    |
                    |   (Opus Model)    |
                    +--------+----------+
                             |
        +--------------------+--------------------+
        |           |              |              |
+-------v----+ +----v-----+ +-----v------+ +-----v------+
|  Statute   | | Section  | | Citation   | |Requirement |
|  Reader    | | Analyzer | | Verifier   | | Classifier |
| (Haiku)    | | (Sonnet) | | (Python)   | | (Haiku)    |
+------------+ +----------+ +------------+ +------------+
```

## Requirement Categories

- **DISCLOSURE**: Must be stated in privacy policy/notice
- **OPERATIONAL**: Internal compliance processes (response times, procedures)
- **TECHNICAL**: System/UI implementation (GPC signals, security measures, link placement, UI elements)
- **LEGAL FRAMEWORK**: Enforcement mechanisms, penalties, AG authority, cure periods

## Viewer

Run `techreg-parser view` to open the built-in interactive viewer in your browser. Then drag-and-drop or select a JSON results file to explore:

- Filter requirements by category, verification status, and confidence threshold
- Full-text search across descriptions and citations
- Browse the statute structure tree with section types and line ranges
- Expand individual requirements to see quoted text, conditions, and metadata

## Output

The analysis produces:
- **Requirements**: List of all extracted requirements with citations
- **Definitions**: All defined terms from the statute
- **Structure**: Full statute section tree (IDs, types, titles, line ranges) — included by default in JSON export for the viewer's Structure tab
- **Verification**: Status of citation verification
- **Classification**: Category for each requirement

## Key Principles

Based on lessons from analyzing tech regulation statutes:

1. Start with definitions sections to anchor interpretation — defined terms control meaning throughout
2. Separate disclosure requirements from operational and technical requirements
3. Tech regulation statutes follow predictable architecture (definitions, scope, rights, duties, exemptions, enforcement)
4. Obligations and defined terms vary across jurisdictions and regulatory domains — never assume uniformity
5. Work section by section, not requirement by requirement — structure drives accurate extraction
6. Every extracted requirement must trace back to a specific statutory provision with a verbatim quote

## Prerequisites

Before installing TechRegParser, you need the following set up on your computer. If you're not sure whether you have these, follow the steps below.

### 1. Python (version 3.11 or later)

Python is the programming language this tool runs on. Download it from [python.org](https://www.python.org/downloads/). During installation on Windows, make sure to check **"Add Python to PATH"**.

To verify it's installed, open a terminal and run:
```bash
python --version
```

### 2. Claude Code

TechRegParser uses Anthropic's AI models to read and analyze statutes. Claude Code is the program that connects to those models.

- Install Claude Code by following the [official setup guide](https://docs.anthropic.com/en/docs/claude-code)
- You will need an **Anthropic API key** — this is what allows the tool to communicate with the AI. You can get one from [console.anthropic.com](https://console.anthropic.com/)
- API usage is billed by Anthropic based on how much text is processed. Analyzing a single statute typically costs a few dollars

### 3. Git for Windows (Windows only)

Claude Code requires Git Bash to run on Windows. Download and install [Git for Windows](https://gitforwindows.org/). Use the default installation options.

To verify it's installed:
```bash
git --version
```

### 4. PDF support (optional)

If you want to analyze statutes in PDF format (rather than plain text), install PDF support:
```bash
pip install techreg-parser[pdf]
```

## Technical Requirements

For developers and CI environments:

- Python 3.11+
- Claude Code with a configured Anthropic API key
- Git for Windows (Windows only)
- Anthropic Agent SDK (`claude-agent-sdk`)
- Pydantic 2.0+
- Optional: `pdfplumber` or `pypdf` for PDF support

## Session Memory (Claude Code)

When developing TechRegParser with Claude Code, the assistant maintains a persistent memory file at `.claude/projects/.../memory/MEMORY.md`. This memory carries context across separate conversations so the assistant doesn't re-learn the same things each session.

### What it stores

- Project architecture (phase pipeline, model assignments, orchestrator location)
- Performance optimizations already implemented (caching, early classification, parallel safety)
- CLI flags and their behavior
- Key code patterns (return types, backfill logic, temp file naming)
- Files that should not be deleted (e.g. `viewer.html`)

### When it helps

- **Resuming work across sessions** — the assistant already knows the codebase layout, model assignments, and design decisions without needing to re-explore
- **Avoiding regressions** — recorded patterns (like the backfill duplication or temp file hashing) prevent the assistant from accidentally breaking established behavior
- **Consistent style** — remembering conventions means new code matches existing patterns

### When it should NOT be used

- **One-off questions** — if you're just asking about Python syntax or a general concept, memory adds no value
- **New/unrelated projects** — the memory is scoped to this project directory; it won't interfere with other work, but it also won't help
- **Speculative or unverified info** — memory should only contain patterns confirmed across multiple interactions or explicitly requested by the user, not guesses from reading a single file

### Managing memory

- Ask the assistant to "remember X across sessions" to add something
- Ask the assistant to "forget X" or "stop remembering X" to remove an entry
- The memory file is plain markdown — you can edit it directly if needed

## License

MIT
