# TechRegParser

A multi-agent system for extracting requirements from data privacy and tech regulation statutes using the Anthropic Agent SDK.

## Features

- **Multi-agent architecture**: Specialized agents for different tasks:
  - **Statute Reader**: Parses statute structure (definitions, applicability, rights, duties, exemptions, enforcement)
  - **Section Analyzer**: Extracts specific requirements with exact citations
  - **Citation Verifier**: Validates all citations against the original text
  - **Requirement Classifier**: Categorizes requirements (disclosure, operational, technical, enforcement)

- **Model Configuration**:
  - **Orchestrator**: Uses Opus for complex coordination
  - **Subagents**: Use Sonnet for specialized tasks

- **Anti-hallucination measures**:
  - Every requirement must have a direct quote from the statute
  - Two-pass verification (extract then verify)
  - Confidence scoring for citations
  - Flagging of unverified requirements

- **Statute interpretation skill**: Incorporates statutory interpretation guidance from legal experts

- **PDF Support**: Can parse both text files and PDFs (with pdfplumber or pypdf)

## Installation

```bash
# Install from GitHub
pip install git+https://github.com/rafal-fryc/TechRegParser.git

# With PDF support
pip install "techreg-parser[pdf] @ git+https://github.com/rafal-fryc/TechRegParser.git"

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
| (Sonnet)   | | (Sonnet) | | (Python)   | | (Sonnet)   |
+------------+ +----------+ +------------+ +------------+
```

## Requirement Categories

- **DISCLOSURE**: Must be stated in privacy policy/notice
- **OPERATIONAL**: Internal compliance processes (response times, procedures)
- **TECHNICAL**: System/UI implementation (GPC signals, security measures, link placement, UI elements)
- **ENFORCEMENT**: Enforcement mechanisms, penalties, prohibited conduct, AG authority, cure periods

## Output

The analysis produces:
- **Requirements**: List of all extracted requirements with citations
- **Definitions**: All defined terms from the statute
- **Structure**: Parsed statute sections
- **Verification**: Status of citation verification
- **Classification**: Category for each requirement

## Key Principles

Based on lessons from analyzing 19 US state privacy laws:

1. Start with definitions section to anchor interpretation
2. Separate disclosure requirements from operational requirements
3. Recognize that privacy/tech statutes follow predictable architecture
4. Consumer rights are NOT uniform across jurisdictions
5. "Sale" definitions vary between states
6. Work section by section, not requirement by requirement

## Requirements

- Python 3.11+
- Anthropic Agent SDK (`claude-agent-sdk`)
- Pydantic 2.0+
- Optional: pdfplumber or pypdf for PDF support

## License

MIT
