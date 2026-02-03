"""Main orchestrator agent for the TechRegParser system."""

import asyncio
import json
import re
import tempfile
from pathlib import Path
from typing import Optional, AsyncIterator, Any

from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition

from ..config import (
    AGENT_CONFIGS,
    OrchestratorConfig,
    STATUTORY_INTERPRETATION_PRINCIPLES,
    MODEL_OPUS,
    MODEL_SONNET,
    MODEL_HAIKU,
)
from ..models import (
    AnalysisResult,
    StatuteStructure,
    Definition,
    Requirement,
    Citation,
    RequirementCategory,
)
from ..tools.citation_verify import CitationVerifier
from ..tools.definition_lookup import DefinitionLookup


# PDF parsing imports (optional)
try:
    import pdfplumber
    PDF_SUPPORT = True
except ImportError:
    try:
        from pypdf import PdfReader
        PDF_SUPPORT = True
    except ImportError:
        PDF_SUPPORT = False


# Default retry settings
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BASE_DELAY = 2.0


class TechRegParserOrchestrator:
    """Main orchestrator for parsing statutes and extracting requirements.

    This orchestrator coordinates multiple specialized subagents to:
    1. Parse statute structure (definitions, sections)
    2. Extract requirements from each section
    3. Verify all citations
    4. Classify requirements by type
    """

    MEMORY_FILE = ".techregparser_memory.json"

    def __init__(self, config: Optional[OrchestratorConfig] = None):
        """Initialize the orchestrator.

        Args:
            config: Configuration options for the orchestrator
        """
        self.config = config or OrchestratorConfig()
        self._setup_agents()
        self._memory: dict[str, Any] = {}

    def _setup_agents(self):
        """Set up the agent definitions for the SDK.

        All subagents use Sonnet or Haiku, while the orchestrator uses Opus.
        """
        self.agents = {}

        for name, agent_config in AGENT_CONFIGS.items():
            self.agents[name] = AgentDefinition(
                description=agent_config.description,
                prompt=agent_config.prompt,
                tools=agent_config.tools,
                model=agent_config.model,
            )

    def _load_memory(self) -> dict[str, Any]:
        """Load session memory from disk.

        Returns:
            Dictionary of stored patterns, or empty dict if none.
        """
        memory_path = Path(self.config.working_directory) / self.MEMORY_FILE
        if memory_path.exists():
            try:
                with open(memory_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save_memory(self, result) -> None:
        """Save patterns from analysis to session memory.

        Stores category distribution, common section types, and
        verification patterns for use in future runs.

        Args:
            result: AnalysisResult from the current run
        """
        memory_path = Path(self.config.working_directory) / self.MEMORY_FILE
        existing = self._load_memory()

        # Build patterns from this run
        cat_dist = {}
        match_types = {}
        for req in result.requirements:
            cat = req.category.value
            cat_dist[cat] = cat_dist.get(cat, 0) + 1
            mt = req.citation.match_type
            if mt and mt != "none":
                match_types[mt] = match_types.get(mt, 0) + 1

        run_entry = {
            "statute_name": result.statute_name,
            "total_requirements": len(result.requirements),
            "category_distribution": cat_dist,
            "match_type_distribution": match_types,
            "defined_terms": list(result.definitions.keys()) if result.definitions else [],
        }

        # Append to run history (keep last 10)
        runs = existing.get("runs", [])
        runs.append(run_entry)
        existing["runs"] = runs[-10:]

        # Aggregate patterns across runs
        all_cats = {}
        for run in existing["runs"]:
            for cat, count in run.get("category_distribution", {}).items():
                all_cats[cat] = all_cats.get(cat, 0) + count
        existing["aggregate_category_distribution"] = all_cats

        try:
            with open(memory_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)
        except IOError:
            pass

    def _build_memory_context(self) -> str:
        """Build context string from session memory for injection into prompts.

        Returns:
            Formatted string with patterns from previous runs, or empty string.
        """
        if not self._memory or not self._memory.get("runs"):
            return ""

        runs = self._memory["runs"]
        agg_cats = self._memory.get("aggregate_category_distribution", {})

        lines = ["\nPATTERNS FROM PREVIOUS ANALYSES:"]
        lines.append(f"  Previous statutes analyzed: {len(runs)}")

        if agg_cats:
            lines.append("  Typical category distribution:")
            total = sum(agg_cats.values())
            for cat, count in sorted(agg_cats.items(), key=lambda x: -x[1]):
                pct = count / total * 100 if total > 0 else 0
                lines.append(f"    {cat}: {pct:.0f}%")

        # Show last run's defined terms as hints
        last_run = runs[-1]
        terms = last_run.get("defined_terms", [])
        if terms:
            lines.append(f"  Terms from last statute: {', '.join(terms[:15])}")

        return "\n".join(lines) + "\n"

    def _get_subagent_options(self, agent_name: str) -> ClaudeAgentOptions:
        """Get ClaudeAgentOptions for a specific subagent.

        Uses the agent's own config (model, tools) instead of orchestrator-level
        settings. This ensures subagents run on Sonnet/Haiku rather than Opus.

        Args:
            agent_name: Name of the agent in AGENT_CONFIGS

        Returns:
            ClaudeAgentOptions configured for the subagent
        """
        agent_config = AGENT_CONFIGS[agent_name]
        return ClaudeAgentOptions(
            allowed_tools=agent_config.tools,
            model=agent_config.model,
            setting_sources=["project"],
            cwd=self.config.working_directory,
            permission_mode="bypassPermissions",
        )

    def _get_orchestrator_options(self) -> ClaudeAgentOptions:
        """Get the ClaudeAgentOptions for the orchestrator.

        The orchestrator uses Opus for complex coordination tasks.
        """
        return ClaudeAgentOptions(
            allowed_tools=["Read", "Grep", "Glob", "Task", "Write"],
            agents=self.agents,
            setting_sources=["project"],
            permission_mode="bypassPermissions",
            cwd=self.config.working_directory,
            model=self.config.model,
        )

    async def analyze_statute(
        self,
        statute_path: str,
        output_format: str = "json",
        resume: bool = False,
    ) -> AnalysisResult:
        """Analyze a statute and extract all requirements.

        Args:
            statute_path: Path to the statute file (PDF or text)
            output_format: Output format ("json" or "markdown")
            resume: If True, resume from a previous partial run

        Returns:
            AnalysisResult with all extracted and verified requirements
        """
        # Read the statute
        statute_text = self._read_statute(statute_path)
        statute_name = Path(statute_path).stem

        # Load session memory if enabled
        if self.config.use_memory:
            self._memory = self._load_memory()
            if self._memory.get("runs"):
                print(f"Session memory: loaded patterns from {len(self._memory['runs'])} previous run(s)")

        # Phase 1: Structure Analysis
        print(f"Phase 1: Analyzing statute structure...")
        structure = await self._run_with_retry(
            self._analyze_structure, statute_text, statute_path
        )

        # Phase 2: Extract Requirements
        print(f"Phase 2: Extracting requirements...")
        requirements = await self._run_with_retry(
            self._extract_requirements, statute_text, structure, statute_path, resume
        )

        # Build extraction metadata for Phase 3/4 context
        extraction_meta = self._build_extraction_metadata(requirements, structure)

        # Phase 3 & 4: Verify Citations and Classify Requirements (in parallel if both enabled)
        unverified = []
        if self.config.verify_citations and self.config.classify_requirements:
            print(f"Phase 3+4: Verifying citations and classifying requirements (parallel)...")
            (requirements, unverified), category_map = await asyncio.gather(
                self._run_with_retry(
                    self._verify_citations, requirements, statute_text
                ),
                self._run_with_retry(
                    self._classify_requirements, requirements, extraction_meta
                ),
            )
            # Apply categories from parallel classification using ID-based mapping
            for req in requirements:
                if req.id in category_map:
                    req.category = category_map[req.id]
        elif self.config.verify_citations:
            print(f"Phase 3: Verifying citations...")
            requirements, unverified = await self._run_with_retry(
                self._verify_citations, requirements, statute_text
            )
        elif self.config.classify_requirements:
            print(f"Phase 4: Classifying requirements...")
            category_map = await self._run_with_retry(
                self._classify_requirements, requirements, extraction_meta
            )
            for req in requirements:
                if req.id in category_map:
                    req.category = category_map[req.id]

        # Build result
        result = AnalysisResult(
            statute_name=statute_name,
            effective_date=structure.effective_date if structure else "",
            definitions=structure.definitions if structure else {},
            requirements=requirements,
            structure=structure,
            unverified_items=unverified,
            metadata={
                "source_file": statute_path,
                "total_requirements": len(requirements),
                "verified_count": len([r for r in requirements if r.verified]),
                "output_format": output_format,
                **extraction_meta,
            },
        )

        # Save session memory if enabled
        if self.config.use_memory:
            self._save_memory(result)

        return result

    def _read_statute(self, path: str) -> str:
        """Read the statute file.

        Supports both text files and PDFs (if pypdf or pdfplumber is installed).

        Args:
            path: Path to the statute file

        Returns:
            The text content of the statute

        Raises:
            FileNotFoundError: If the file doesn't exist
            NotImplementedError: If PDF support is needed but not installed
        """
        path_obj = Path(path)

        if not path_obj.exists():
            raise FileNotFoundError(f"Statute file not found: {path}")

        if path_obj.suffix.lower() == ".pdf":
            return self._read_pdf(path_obj)

        # Text file
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def _read_pdf(self, path: Path) -> str:
        """Read and extract text from a PDF file.

        Tries pdfplumber first (better for complex layouts), falls back to pypdf.

        Args:
            path: Path to the PDF file

        Returns:
            Extracted text from the PDF

        Raises:
            NotImplementedError: If no PDF library is installed
        """
        if not PDF_SUPPORT:
            raise NotImplementedError(
                "PDF parsing requires 'pdfplumber' or 'pypdf'. "
                "Install with: pip install pdfplumber  OR  pip install pypdf"
            )

        text_parts = []

        # Try pdfplumber first (better text extraction for complex layouts)
        try:
            import pdfplumber

            with pdfplumber.open(path) as pdf:
                for page_num, page in enumerate(pdf.pages, start=1):
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(f"--- Page {page_num} ---\n{page_text}")

            if text_parts:
                return "\n\n".join(text_parts)

        except ImportError:
            pass
        except Exception as e:
            print(f"Warning: pdfplumber failed ({e}), trying pypdf...")

        # Fall back to pypdf
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            for page_num, page in enumerate(reader.pages, start=1):
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(f"--- Page {page_num} ---\n{page_text}")

            if text_parts:
                return "\n\n".join(text_parts)

        except ImportError:
            raise NotImplementedError(
                "PDF parsing requires 'pdfplumber' or 'pypdf'. "
                "Install with: pip install pdfplumber  OR  pip install pypdf"
            )
        except Exception as e:
            raise ValueError(f"Failed to read PDF: {e}")

        return ""

    async def _analyze_structure(
        self,
        statute_text: str,
        statute_path: str,
    ) -> Optional[StatuteStructure]:
        """Use the statute-reader subagent to analyze structure.

        Args:
            statute_text: The full text of the statute
            statute_path: Path to the statute file

        Returns:
            StatuteStructure with parsed sections and definitions
        """
        # For large statutes, write to a temp file and let the agent Read on demand
        # instead of embedding the full text in the prompt
        statute_len = len(statute_text)
        use_file_context = statute_len > 50000

        prompt = f"""Analyze the structure of this statute.

The statute is located at: {statute_path}

Please:
1. Use the Read tool to access the statute text at the path above
2. Identify and parse the statute structure
3. Extract all definitions from the definitions section
4. Return the results in JSON format

Return your analysis as a JSON object with:
- name: statute name
- citation: official citation if found
- effective_date: effective date if found
- sections: list of section objects, each with:
  - id: section identifier
  - title: section title
  - section_type: one of (definitions, applicability, consumer_rights, controller_duties, processor_duties, exemptions, enforcement, general, preamble, other)
  - content: first 200 characters of section content (preview)
  - start_line: starting line number
  - end_line: ending line number
- definitions: dictionary where each key is the defined term and each value is an object with:
  - term: the defined term
  - definition: the exact statutory definition text
  - section: the section number where this definition appears (e.g., "22601")

{STATUTORY_INTERPRETATION_PRINCIPLES}
"""

        # Inject session memory context if available
        memory_context = self._build_memory_context()
        if memory_context:
            prompt += memory_context

        options = self._get_subagent_options("statute-reader")

        structure_data = None
        async for message in query(prompt=prompt, options=options):
            if hasattr(message, "result"):
                try:
                    result_text = message.result
                    json_match = self._extract_json(result_text)
                    if json_match:
                        structure_data = json.loads(json_match)
                except (json.JSONDecodeError, ValueError):
                    pass

        if structure_data:
            return StatuteStructure.from_dict(structure_data)

        return None

    async def _extract_requirements(
        self,
        statute_text: str,
        structure: Optional[StatuteStructure],
        statute_path: str,
        resume: bool = False,
    ) -> list[Requirement]:
        """Use the section-analyzer subagent to extract requirements.

        The agent writes requirements incrementally to a JSONL file as it
        processes each section, keeping its context window small.

        Args:
            statute_text: The full text of the statute
            structure: The parsed statute structure
            statute_path: Path to the statute file for agent Read access
            resume: If True, resume from existing partial output

        Returns:
            List of extracted requirements
        """
        # Create a temp file for the agent to write results into
        output_dir = Path(self.config.working_directory)
        output_file = output_dir / ".techregparser_requirements.jsonl"

        # P6a: Checkpoint/resume support
        already_processed_sections = set()
        if resume and output_file.exists():
            existing_reqs = self._read_requirements_file(output_file)
            already_processed_sections = {
                r.source_section for r in existing_reqs if r.source_section
            }
            if already_processed_sections:
                print(f"  Resuming: {len(existing_reqs)} requirements from {len(already_processed_sections)} sections already processed")

        prompt = f"""Extract legal requirements from this statute.

The statute is located at: {statute_path}
Use the Read tool to access the statute text. Work section by section.

IMPORTANT - INCREMENTAL OUTPUT:
As you finish extracting requirements from each section, immediately write them
to the output file using the Write tool. Do NOT accumulate all requirements in
your response.

Output file: {output_file}

Write one JSON object per line (JSONL format). Each line should be a complete
requirement object. Append to the file — do not overwrite previous lines.

Each requirement JSON object must contain:
- description: human-readable description
- citation: {{ "section": "...", "quoted_text": "..." }}
- applies_to: who it applies to
- conditions: list of conditions
- category: disclosure/operational/technical/enforcement
- source_section: the section ID this requirement came from

CRITICAL: Every requirement MUST have a direct quote from the statute.

CONSOLIDATION RULES:
- When a statute lists multiple items under a single subsection (e.g., "may not
  do any of the following: (1)... (2)... (3)..."), extract ONE requirement that
  covers the entire list, not separate requirements per sub-item.
- For the quoted_text field: quote the parent clause plus the full enumerated
  list. If the list is very long, quote the parent clause and a representative
  excerpt with "..." to indicate continuation.
- Skip cross-references: if a section says "must comply with Section X" and you
  already extracted Section X's requirements, do NOT create a separate
  requirement for the cross-reference.
- Aim for roughly one requirement per statutory subsection, not one per
  sub-clause.
- Target: 8-15 requirements for a typical statute. If you have 25+, you are
  likely too granular — look for opportunities to group related items.

After processing all sections, write the string "DONE" as the final line of the
output file to signal completion.
"""

        # P5a: Richer Phase 1 to Phase 2 handoff
        if structure:
            definitions_context = {}
            for k, v in structure.definitions.items():
                definitions_context[k] = {
                    "text": v.definition,
                    "section": v.section,
                }

            sections_context = []
            for s in structure.sections:
                section_info = {
                    "id": s.id,
                    "title": s.title,
                    "section_type": s.section_type.value,
                    "content_preview": s.content[:200] if s.content else "",
                    "start_line": s.start_line,
                    "end_line": s.end_line,
                }
                sections_context.append(section_info)

            structure_summary = json.dumps({
                "definitions": definitions_context,
                "sections": sections_context,
            }, indent=2)
            prompt += f"\n\nPHASE 1 ANALYSIS (definitions and structure already extracted):\n{structure_summary}\n"

            # P4: Inject definitions for the section-analyzer to reference
            if structure.definitions:
                def_text = "\n".join(
                    f'  - "{k}": {v.definition}'
                    for k, v in structure.definitions.items()
                )
                prompt += f"\n\nSTATUTORY DEFINITIONS (use these for consistent interpretation):\n{def_text}\n"

        # P6a: Tell agent which sections to skip if resuming
        if already_processed_sections:
            prompt += f"\n\nALREADY PROCESSED (skip these sections): {', '.join(already_processed_sections)}\n"
            prompt += "Append new requirements to the existing output file. Do NOT overwrite it.\n"

        # Clear output file only if not resuming
        if not resume and output_file.exists():
            output_file.unlink()

        options = self._get_subagent_options("section-analyzer")

        # P6b: Progress monitoring
        monitor_task = asyncio.create_task(
            self._monitor_progress(output_file)
        )

        try:
            # Run the agent — it writes results to the file as it goes
            async for message in query(prompt=prompt, options=options):
                pass
        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass

        # Read requirements from the JSONL output file
        requirements = self._read_requirements_file(output_file)

        # Clean up
        if output_file.exists():
            output_file.unlink()

        return requirements

    async def _monitor_progress(self, output_file: Path, interval: float = 10.0):
        """Monitor JSONL file growth and report progress.

        Args:
            output_file: Path to the JSONL output file
            interval: Seconds between checks
        """
        last_count = 0
        while True:
            await asyncio.sleep(interval)
            if output_file.exists():
                try:
                    with open(output_file, "r", encoding="utf-8") as f:
                        lines = [
                            l for l in f.readlines()
                            if l.strip() and l.strip() != "DONE"
                        ]
                    current_count = len(lines)
                    if current_count > last_count:
                        print(f"  Progress: {current_count} requirements extracted...")
                        last_count = current_count
                except (IOError, OSError):
                    pass

    def _read_requirements_file(self, path: Path) -> list[Requirement]:
        """Read requirements from a JSONL file written by the section-analyzer.

        Args:
            path: Path to the JSONL file

        Returns:
            List of parsed requirements
        """
        requirements = []

        if not path.exists():
            return requirements

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line == "DONE":
                    continue
                try:
                    data = json.loads(line)
                    req = self._parse_requirement(data)
                    if req:
                        requirements.append(req)
                except json.JSONDecodeError:
                    continue

        return requirements

    async def _verify_citations(
        self,
        requirements: list[Requirement],
        statute_text: str,
    ) -> tuple[list[Requirement], list[dict]]:
        """Verify all citations against the statute text.

        This uses the Python CitationVerifier directly (not an LLM agent)
        since citation matching is deterministic work.

        Args:
            requirements: List of requirements to verify
            statute_text: The original statute text

        Returns:
            Tuple of (verified requirements, unverified items)
        """
        verifier = CitationVerifier(statute_text)
        unverified = []

        for req in requirements:
            result = verifier.verify(req.citation)

            req.verified = result.valid
            req.confidence = result.confidence
            req.citation.verified = result.valid
            req.citation.confidence = result.confidence
            req.citation.match_type = result.match_type

            if result.line_numbers:
                req.citation.line_numbers = result.line_numbers

            if not result.valid:
                unverified.append({
                    "requirement_id": req.id,
                    "description": req.description,
                    "citation": req.citation.to_dict(),
                    "error": result.error,
                })

        return requirements, unverified

    async def _classify_requirements(
        self,
        requirements: list[Requirement],
        extraction_meta: Optional[dict] = None,
    ) -> dict[str, RequirementCategory]:
        """Use the requirement-classifier subagent to classify requirements.

        Returns a dict mapping requirement ID to category instead of mutating
        in place, so this method is safe to run in parallel with verification.

        Args:
            requirements: List of requirements to classify
            extraction_meta: Optional metadata from extraction phase for context

        Returns:
            Dict mapping requirement ID to RequirementCategory
        """
        # Build a prompt with the requirements to classify, keyed by ID
        req_list = []
        for req in requirements:
            req_list.append({
                "id": req.id,
                "description": req.description,
                "citation": req.citation.section,
                "applies_to": req.applies_to,
            })

        prompt = f"""Classify these requirements into categories.

Requirements to classify:
{json.dumps(req_list, indent=2)}

Categories:
- DISCLOSURE: Must be stated in privacy policy/notice
- OPERATIONAL: Internal compliance process
- TECHNICAL: System and UI implementation requirements (includes website/app design)
- ENFORCEMENT: Penalties, prohibited conduct, AG authority, cure periods

Return a JSON array of classifications:
[
  {{ "id": "<requirement_id>", "category": "disclosure" }},
  ...
]
"""

        # P5c: Add extraction context if available
        if extraction_meta:
            prompt += f"\n\nExtraction context:\n{json.dumps(extraction_meta, indent=2)}\n"

        options = self._get_subagent_options("requirement-classifier")

        # Default: preserve existing categories
        categories: dict[str, RequirementCategory] = {
            req.id: req.category for req in requirements
        }

        async for message in query(prompt=prompt, options=options):
            if hasattr(message, "result"):
                try:
                    result_text = message.result
                    json_match = self._extract_json(result_text)
                    if json_match:
                        classifications = json.loads(json_match)
                        if isinstance(classifications, list):
                            for item in classifications:
                                req_id = item.get("id")
                                cat = item.get("category", "unclassified")
                                if req_id and req_id in categories:
                                    try:
                                        categories[req_id] = RequirementCategory(cat.lower())
                                    except ValueError:
                                        categories[req_id] = RequirementCategory.UNCLASSIFIED
                except (json.JSONDecodeError, ValueError):
                    pass

        return categories

    def _build_extraction_metadata(
        self,
        requirements: list[Requirement],
        structure: Optional[StatuteStructure],
    ) -> dict:
        """Build metadata about the extraction phase for downstream context.

        Args:
            requirements: Extracted requirements
            structure: Parsed statute structure

        Returns:
            Dictionary with extraction metadata
        """
        # Count requirements per section
        section_counts = {}
        for req in requirements:
            section = req.source_section or req.citation.section
            section_counts[section] = section_counts.get(section, 0) + 1

        # Identify sections that were processed
        sections_processed = list(section_counts.keys())

        # Gather ambiguous terms (terms used but possibly undefined)
        meta = {
            "sections_processed": sections_processed,
            "requirements_per_section": section_counts,
            "total_extracted": len(requirements),
        }

        if structure:
            meta["defined_terms"] = list(structure.definitions.keys())
            meta["section_types"] = {
                s.id: s.section_type.value for s in structure.sections
            }

        return meta

    async def _run_with_retry(self, func, *args, **kwargs):
        """Run an async function with exponential backoff retry.

        Args:
            func: Async function to call
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            The function's return value

        Raises:
            The last exception if all retries fail
        """
        last_error = None
        for attempt in range(DEFAULT_MAX_RETRIES):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < DEFAULT_MAX_RETRIES - 1:
                    delay = DEFAULT_RETRY_BASE_DELAY * (2 ** attempt)
                    print(f"  Retry {attempt + 1}/{DEFAULT_MAX_RETRIES - 1} after error: {e}")
                    print(f"  Waiting {delay:.0f}s before retry...")
                    await asyncio.sleep(delay)
        raise last_error

    def _parse_requirement(self, data: dict) -> Optional[Requirement]:
        """Parse a requirement from dictionary data."""
        try:
            citation_data = data.get("citation", {})
            if isinstance(citation_data, str):
                citation = Citation(
                    section=citation_data,
                    quoted_text=data.get("quoted_text", ""),
                )
            else:
                citation = Citation(
                    section=citation_data.get("section", ""),
                    quoted_text=citation_data.get("quoted_text", ""),
                    context=citation_data.get("context", ""),
                )

            # Parse category
            cat_str = data.get("category", "unclassified").lower()
            try:
                category = RequirementCategory(cat_str)
            except ValueError:
                category = RequirementCategory.UNCLASSIFIED

            return Requirement(
                description=data.get("description", ""),
                citation=citation,
                category=category,
                applies_to=data.get("applies_to", "controller"),
                conditions=data.get("conditions", []),
                source_section=data.get("source_section", ""),
            )
        except Exception:
            return None

    def _extract_json(self, text: str) -> Optional[str]:
        """Extract JSON from text that might contain other content."""
        # First try to extract from markdown code blocks
        code_block_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if code_block_match:
            candidate = code_block_match.group(1).strip()
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass

        # Find the first [ or { and try to parse from there
        for start_char, end_char in [('[', ']'), ('{', '}')]:
            start_idx = text.find(start_char)
            if start_idx == -1:
                continue

            # Find matching end bracket using stack
            depth = 0
            in_string = False
            escape_next = False
            for i, char in enumerate(text[start_idx:], start_idx):
                if escape_next:
                    escape_next = False
                    continue
                if char == '\\' and in_string:
                    escape_next = True
                    continue
                if char == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if char == start_char:
                    depth += 1
                elif char == end_char:
                    depth -= 1
                    if depth == 0:
                        candidate = text[start_idx:i + 1]
                        try:
                            json.loads(candidate)
                            return candidate
                        except json.JSONDecodeError:
                            break

        return None

    async def export_results(
        self,
        result: AnalysisResult,
        output_path: str,
        format: str = "json",
    ) -> None:
        """Export analysis results to a file.

        Args:
            result: The analysis result to export
            output_path: Path to write the output
            format: Output format ("json" or "markdown")
        """
        if format == "markdown":
            content = result.to_markdown()
        else:
            content = json.dumps(result.to_dict(), indent=2)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
