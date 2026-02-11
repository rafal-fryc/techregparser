"""Main orchestrator agent for the TechRegParser system."""

import asyncio
import hashlib
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
    LegislativeIntent,
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

    def _get_structure_cache_path(self, statute_path: str) -> Path:
        """Get the cache file path for a statute's Phase 1 structure.

        The cache key is based on the file content hash, so re-runs of the
        same statute file will hit cache even if the path changes.

        Args:
            statute_path: Path to the statute file

        Returns:
            Path to the cache file
        """
        content_hash = hashlib.md5(Path(statute_path).read_bytes()).hexdigest()[:12]
        cache_dir = Path(self.config.working_directory) / ".techregparser_cache"
        cache_dir.mkdir(exist_ok=True)
        return cache_dir / f"{content_hash}_structure.json"

    def _load_cached_structure(self, statute_path: str) -> Optional[StatuteStructure]:
        """Load Phase 1 structure from cache if available.

        Args:
            statute_path: Path to the statute file

        Returns:
            Cached StatuteStructure, or None if no cache hit
        """
        if not self.config.use_cache:
            return None
        cache_path = self._get_structure_cache_path(statute_path)
        if not cache_path.exists():
            return None
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return StatuteStructure.from_dict(data)
        except (json.JSONDecodeError, IOError, KeyError):
            return None

    def _save_structure_cache(self, statute_path: str, structure: StatuteStructure) -> None:
        """Save Phase 1 structure to cache for future re-runs.

        Args:
            statute_path: Path to the statute file
            structure: The parsed statute structure to cache
        """
        if not self.config.use_cache:
            return
        cache_path = self._get_structure_cache_path(statute_path)
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(structure.to_dict(), f, indent=2)
        except (IOError, TypeError):
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

        # Note: defined_terms from previous runs are intentionally excluded
        # here to avoid cross-statute contamination (e.g., Minnesota terms
        # leaking into a Nebraska run when Phase 1 fails).

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
            max_buffer_size=20 * 1024 * 1024,  # 20 MB for large PDF statutes
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
            max_buffer_size=20 * 1024 * 1024,  # 20 MB for large PDF statutes
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

        # Phase 1: Structure Analysis (with optional caching)
        cached_structure = self._load_cached_structure(statute_path)
        if cached_structure is not None:
            print(f"Phase 1: Loaded structure from cache (skipping LLM call)")
            structure = cached_structure
            print(f"  Found {len(structure.sections)} sections, {len(structure.definitions)} definitions")
        else:
            print(f"Phase 1: Analyzing statute structure...")
            structure = await self._run_with_retry(
                self._analyze_structure, statute_text, statute_path
            )
            if structure is None:
                print("  WARNING: Phase 1 returned no structure. Definitions, section types,")
                print("           and structure tree will be missing from output.")
                print("           Phase 2 will proceed without section context.")
            elif structure.sections:
                print(f"  Found {len(structure.sections)} sections, {len(structure.definitions)} definitions")
                self._save_structure_cache(statute_path, structure)

        # Phase 2: Extract Requirements
        print(f"Phase 2: Extracting requirements...")
        last_error = None
        output_file = None
        for attempt in range(DEFAULT_MAX_RETRIES):
            try:
                requirements, output_file = await self._extract_requirements(
                    statute_text, structure, statute_path,
                    resume=(resume or attempt > 0),  # Auto-resume on retry
                )
                break
            except Exception as e:
                last_error = e
                if attempt < DEFAULT_MAX_RETRIES - 1:
                    delay = DEFAULT_RETRY_BASE_DELAY * (2 ** attempt)
                    print(f"  Retry {attempt + 1}/{DEFAULT_MAX_RETRIES - 1} after error: {e}")
                    print(f"  Waiting {delay:.0f}s before retry...")
                    await asyncio.sleep(delay)
                else:
                    raise last_error

        # Build extraction metadata for Phase 3/4 context
        extraction_meta = self._build_extraction_metadata(requirements, structure)

        # Determine if backfill is needed
        needs_backfill = False
        if structure and structure.sections:
            skip_types = {"definitions", "preamble", "legislative_intent"}
            expected = {s.id for s in structure.sections if s.section_type.value not in skip_types}
            covered = set()
            for req in requirements:
                norm = self._normalize_section_id(req.source_section)
                for exp_id in expected:
                    if self._normalize_section_id(exp_id) == norm:
                        covered.add(exp_id)
            missed = expected - covered
            needs_backfill = len(missed) >= 3 and len(missed) >= 0.2 * len(expected)

        # Early Phase 4: Start classification on initial requirements while backfill runs
        classification_task = None
        initial_req_ids = {req.id for req in requirements}
        if self.config.classify_requirements and needs_backfill:
            print(f"Phase 4: Starting early classification (overlapping with backfill)...")
            classification_task = asyncio.create_task(
                self._run_with_retry(
                    self._classify_requirements, requirements, extraction_meta
                )
            )

        # Backfill missed sections (if needed)
        if needs_backfill and output_file:
            requirements = await self._check_completeness_and_backfill(
                requirements, structure, statute_path, output_file,
            )
            # Re-apply caps after backfill
            requirements = self._enforce_section_caps(requirements, structure)

        # Clean up output file
        if output_file and output_file.exists():
            output_file.unlink()

        # Phase 3 & 4: Verify Citations and Classify Requirements
        unverified = []

        if classification_task is not None:
            # Early classification was started — await it and handle backfill additions
            category_map = await classification_task

            # Classify any new requirements added by backfill
            new_reqs = [r for r in requirements if r.id not in initial_req_ids]
            if new_reqs:
                print(f"  Classifying {len(new_reqs)} new requirements from backfill...")
                new_meta = self._build_extraction_metadata(new_reqs, structure)
                supplementary_map = await self._run_with_retry(
                    self._classify_requirements, new_reqs, new_meta
                )
                category_map.update(supplementary_map)

            # Apply categories
            for req in requirements:
                if req.id in category_map:
                    req.category = category_map[req.id]

            # Run Phase 3 verification
            if self.config.verify_citations:
                print(f"Phase 3: Verifying citations...")
                requirements, unverified = await self._run_with_retry(
                    self._verify_citations, requirements, statute_text
                )
        elif self.config.verify_citations and self.config.classify_requirements:
            # No backfill needed — run Phase 3 and 4 in parallel as before
            print(f"Phase 3+4: Verifying citations and classifying requirements (parallel)...")
            (requirements, unverified), category_map = await asyncio.gather(
                self._run_with_retry(
                    self._verify_citations, requirements, statute_text
                ),
                self._run_with_retry(
                    self._classify_requirements, requirements, extraction_meta
                ),
            )
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

        # Rebuild extraction metadata with final requirement list
        final_extraction_meta = self._build_extraction_metadata(requirements, structure)

        # Build result
        result = AnalysisResult(
            statute_name=statute_name,
            effective_date=structure.effective_date if structure else "",
            definitions=structure.definitions if structure else {},
            requirements=requirements,
            structure=structure,
            legislative_intent=structure.legislative_intent if structure else None,
            unverified_items=unverified,
            metadata={
                "source_file": statute_path,
                "total_requirements": len(requirements),
                "verified_count": len([r for r in requirements if r.verified]),
                "output_format": output_format,
                **final_extraction_meta,
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
                print(f"  PDF parsed with pdfplumber ({len(pdf.pages)} pages)")
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
                print(f"  PDF parsed with pypdf ({len(reader.pages)} pages)")
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
        # Write statute text to a temp file so the agent reads the same text
        # that Phase 3 (citation verifier) will verify against.
        output_dir = Path(self.config.working_directory)
        statute_hash = hashlib.md5(statute_path.encode()).hexdigest()[:8]
        statute_text_file = output_dir / f".techregparser_statute_{statute_hash}.txt"
        statute_text_file.write_text(statute_text, encoding="utf-8")

        prompt = f"""Analyze the structure of this statute.

The statute text has been extracted and saved to: {statute_text_file.absolute()}

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
  - id: the section number exactly as it appears in the statute text headings and cross-references. Use the statute's own internal numbering, not any external code or title numbering. For example, if the statute text says "Section 5" or "§ 5", use "5"; if it says "§ 12.3-101", use "12.3-101".
  - title: section title
  - section_type: one of (definitions, applicability, consumer_rights, controller_duties, processor_duties, exemptions, enforcement, general, preamble, legislative_intent, other)
  - content: first 200 characters of section content (preview)
  - start_line: starting line number
  - end_line: ending line number
- definitions: dictionary where each key is the defined term and each value is an object with:
  - term: the defined term
  - definition: the exact statutory definition text
  - section: the section number where this definition appears
- legislative_intent: (optional, include only if the statute has purpose/findings/intent sections) an object with:
  - purpose: the stated purpose or policy goal (plain text summary)
  - findings: list of individual legislative findings (each as a string)
  - source_sections: list of section IDs where intent was found
  - raw_text: the original text of the intent/purpose/findings sections

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
                    else:
                        print(f"  Phase 1: No JSON found in agent response ({len(result_text)} chars)")
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"  Phase 1: JSON parse error: {e}")

        # Clean up statute text temp file
        if statute_text_file.exists():
            statute_text_file.unlink()

        if structure_data:
            return StatuteStructure.from_dict(structure_data)

        return None

    async def _extract_requirements(
        self,
        statute_text: str,
        structure: Optional[StatuteStructure],
        statute_path: str,
        resume: bool = False,
    ) -> tuple[list[Requirement], Path]:
        """Use the section-analyzer subagent to extract requirements.

        The agent writes requirements incrementally to a JSONL file as it
        processes each section, keeping its context window small.

        Args:
            statute_text: The full text of the statute
            structure: The parsed statute structure
            statute_path: Path to the statute file for agent Read access
            resume: If True, resume from existing partial output

        Returns:
            Tuple of (extracted requirements, output file path for backfill)
        """
        # Create a temp file for the agent to write results into
        # Use statute-specific hash to prevent collisions during parallel runs
        output_dir = Path(self.config.working_directory)
        statute_hash = hashlib.md5(statute_path.encode()).hexdigest()[:8]
        output_file = output_dir / f".techregparser_requirements_{statute_hash}.jsonl"

        # P6a: Checkpoint/resume support
        already_processed_sections = set()
        if resume and output_file.exists():
            existing_reqs = self._read_requirements_file(output_file)
            already_processed_sections = {
                r.source_section for r in existing_reqs if r.source_section
            }
            if already_processed_sections:
                print(f"  Resuming: {len(existing_reqs)} requirements from {len(already_processed_sections)} sections already processed")

        # Write statute text to a temp file so the agent reads the same text
        # that Phase 3 (citation verifier) will verify against.
        statute_text_file = output_dir / f".techregparser_statute_{statute_hash}.txt"
        statute_text_file.write_text(statute_text, encoding="utf-8")

        prompt = f"""Extract legal requirements AND legal framework provisions from this statute.

The statute text has been extracted and saved to: {statute_text_file.absolute()}
Use the Read tool to access the statute text at that path. Work section by section.

IMPORTANT - INCREMENTAL OUTPUT:
As you finish extracting items from each section, immediately save them
to the output file. Do NOT accumulate all items in your response.

Output file: {output_file}

Format: One JSON object per line (JSONL format). Each line is a complete item.

CRITICAL APPEND PROCEDURE (the Write tool OVERWRITES, it does not append):
1. FIRST: Read the current contents of the output file using the Read tool
2. THEN: Write the OLD contents PLUS your new JSON lines using the Write tool
This ensures previous items are preserved. Never write only new lines.
If the file doesn't exist yet or is empty, just write your new lines.

Each JSON object must contain:
- description: human-readable description
- citation: {{ "section": "...", "quoted_text": "..." }}
- applies_to: who it applies to
- conditions: list of conditions
- category: one of the following (see definitions below)
- source_section: the section ID this item came from
- obligation_type: "shall" | "shall_not" | "may" | "must" | "may_not" (the verb form)

Categories:
- disclosure: Must appear in privacy policy/notice text (data categories, purposes,
  rights, third parties, retention, contact info)
- operational: Internal compliance processes (response deadlines, free request limits,
  appeal timelines, consent revocation processing, assessment requirements)
- technical: System/UI implementation (GPC signals, security measures, opt-out buttons,
  age verification, dark pattern prohibitions, link placements)
- legal_framework: Applicability thresholds, entity/data exemptions, scope rules,
  penalties, AG authority, cure periods, private right of action provisions

CRITICAL: Every item MUST have a direct quote from the statute.

WHAT TO EXTRACT:
1. REQUIREMENTS (category: disclosure/operational/technical) — affirmative
   obligations, duties, and mandates imposed on covered entities.
2. LEGAL FRAMEWORK PROVISIONS (category: legal_framework) — these are NOT
   optional. You MUST extract from EVERY section typed as applicability,
   exemptions, scope, or enforcement. Specifically:
   - APPLICABILITY: Who the statute applies to, threshold criteria, scope
   - EXEMPTIONS: Entity-level exemptions (e.g., nonprofits, government,
     HIPAA-covered entities) and data-level exemptions (e.g., employee data,
     publicly available info, de-identified data). Consolidate each exemption
     section into one item with the full list in quoted_text.
   - ENFORCEMENT: Penalties, AG authority, cure periods, private right of action
     (or lack thereof)

CONSOLIDATION RULES:
- When a statute lists multiple items under a single subsection (e.g., "may not
  do any of the following: (1)... (2)... (3)..."), extract ONE item that
  covers the entire list, not separate items per sub-item.
- For the quoted_text field: quote the parent clause plus the full enumerated
  list. If the list is very long, quote the parent clause and a representative
  excerpt with "..." to indicate continuation.
- Skip cross-references: if a section says "must comply with Section X" and you
  already extracted Section X's content, do NOT create a separate item for the
  cross-reference.
- Aim for roughly one item per statutory subsection, not one per sub-clause.

COVERAGE IS MANDATORY:
- Every substantive section MUST produce at least one item. Do NOT skip any section.
- HARD CAP: Maximum 5 items per section. If a section has more than 5 subsections,
  consolidate related subsections into fewer items. Example: group all "controller
  data handling duties" from subsections (a)-(g) into 1-2 items, rather than one
  per subsection.
- Full coverage across all sections is more important than depth in any one section.

After processing all sections, write the string "DONE" as the final line of the
output file to signal completion.
"""

        # P5a: Richer Phase 1 to Phase 2 handoff
        # Write context to a temp file to avoid Windows command-line length limits (8191 chars)
        # Large statutes like Delaware/Indiana can have 8-12KB of definitions and section metadata
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
                    "content_preview": s.content[:500] if s.content else "",
                    "start_line": s.start_line,
                    "end_line": s.end_line,
                }
                sections_context.append(section_info)

            # Write Phase 1 context to a file instead of embedding in prompt
            context_file = output_dir / f".phase1_context_{statute_hash}.json"
            context_data = {
                "definitions": definitions_context,
                "sections": sections_context,
            }
            if structure.legislative_intent:
                context_data["legislative_intent"] = structure.legislative_intent.to_dict()
            context_file.write_text(json.dumps(context_data, indent=2), encoding="utf-8")

            prompt += f"\n\nPHASE 1 ANALYSIS: The definitions and structure have been extracted."
            prompt += f"\nRead the context file at: {context_file.absolute()}"
            prompt += "\nUse the Read tool to load this JSON file before processing sections."
            prompt += "\nThe file contains 'definitions' (term -> text/section), 'sections' (id, title, type, preview), and optionally 'legislative_intent' (purpose, findings) for interpretive context."

            prompt += """

SECTION IDs: Use the section IDs from the Phase 1 context as your starting reference.
However, VERIFY them against the actual statute text. If the statute headings and
cross-references use different section numbers than Phase 1 provided, use the numbers
from the statute text. The citation.section field must always match what appears in the
statute. Do NOT invent section numbers that appear in neither Phase 1 nor the statute text.

Use the section_type field to guide extraction:
- "definitions": Skip (already extracted in Phase 1)
- "applicability": MUST extract as legal_framework (who must comply, thresholds)
- "exemptions": MUST extract as legal_framework (entity and data exemptions)
- "enforcement": MUST extract as legal_framework (penalties, AG authority, cure periods)
- "consumer_rights": Extract as operational (rights to exercise) or disclosure (rights to disclose)
- "controller_duties" / "processor_duties": Extract as operational, technical, or disclosure
- "preamble" / "legislative_intent": Skip (used as interpretive context only)
- "general" / "other": Extract if substantive content exists
"""

            # Add explicit section checklist to reduce backfill triggers
            if structure.sections:
                checklist = []
                for s in structure.sections:
                    if s.section_type.value not in ("definitions", "preamble", "legislative_intent"):
                        checklist.append(f"  [ ] Section {s.id}: {s.title}")
                if checklist:
                    prompt += "\nSECTION CHECKLIST — extract from every section below:\n"
                    prompt += "\n".join(checklist)
                    prompt += "\nMark each section done as you process it. Do NOT stop until all are done.\n"

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

        if not requirements:
            print("  Warning: No requirements parsed from output file. "
                  "The section-analyzer may have overwritten earlier results.")

        # Fix 1: Enforce per-section hard cap
        requirements = self._enforce_section_caps(requirements, structure)

        # Clean up context file and statute text file (output_file kept for potential backfill)
        context_cleanup = output_dir / f".phase1_context_{statute_hash}.json"
        if context_cleanup.exists():
            context_cleanup.unlink()
        if statute_text_file.exists():
            statute_text_file.unlink()

        return requirements, output_file

    @staticmethod
    def _normalize_section_id(section_id: str) -> str:
        """Normalize a section ID to its top-level root for grouping.

        Strips prefixes like 'Section '/'Sec. ', subdivision suffixes,
        and trailing parenthesized content to get the root section number.

        Examples:
            '325O.05, Subd. 1(a)' -> '325O.05'
            '541.002(a)' -> '541.002'
            '9(6)' -> '9'
            'RSA 507-H:3(I)(a)' -> 'RSA 507-H:3'
            'Section 5' -> '5'
        """
        s = section_id.strip()
        # Strip common prefixes
        for prefix in ["Section ", "Sec. ", "§ ", "§"]:
            if s.startswith(prefix):
                s = s[len(prefix):].strip()
        # Split on subdivision markers and take the left part
        for marker in [", Subd.", ", Subdivision", ",Subd.", ",Subdivision"]:
            if marker.lower() in s.lower():
                idx = s.lower().index(marker.lower())
                s = s[:idx].strip()
        # Strip trailing parenthesized content: extract root up to first '('
        paren_match = re.match(r'^([^(]+)', s)
        if paren_match:
            s = paren_match.group(1).strip()
        return s

    def _enforce_section_caps(
        self,
        requirements: list[Requirement],
        structure: Optional[StatuteStructure],
        max_per_section: int = 5,
    ) -> list[Requirement]:
        """Enforce a hard cap on requirements per top-level section.

        Groups requirements by normalized section ID and keeps only the
        best N per group, scoring by quote length and obligation strength.

        Args:
            requirements: All extracted requirements
            structure: Parsed statute structure (for exact-match scoring)
            max_per_section: Maximum requirements per top-level section

        Returns:
            Capped list of requirements preserving original order
        """
        if not requirements:
            return requirements

        # Build set of Phase 1 section IDs for exact-match bonus
        phase1_ids = set()
        if structure and structure.sections:
            phase1_ids = {s.id for s in structure.sections}

        # Group by normalized section ID
        from collections import defaultdict
        groups: dict[str, list[int]] = defaultdict(list)
        for idx, req in enumerate(requirements):
            norm = self._normalize_section_id(req.source_section)
            groups[norm].append(idx)

        # Identify which indices to keep
        keep_indices: set[int] = set()
        for norm_id, indices in groups.items():
            if len(indices) <= max_per_section:
                keep_indices.update(indices)
                continue

            # Score each requirement in the group
            scored = []
            for idx in indices:
                req = requirements[idx]
                score = 0.0
                # Prefer longer quoted_text (more comprehensive)
                quote_len = len(req.citation.quoted_text) if req.citation.quoted_text else 0
                score += min(quote_len / 200.0, 3.0)  # Cap at 3 points
                # Prefer shall/must over may
                obl = (req.obligation_type or "").lower()
                if obl in ("shall", "must"):
                    score += 2.0
                elif obl in ("shall_not", "must_not"):
                    score += 1.5
                elif obl in ("may_not",):
                    score += 1.0
                # Prefer exact Phase 1 section match
                if req.source_section in phase1_ids:
                    score += 1.0
                scored.append((score, idx))

            # Sort by score descending, keep top N
            scored.sort(key=lambda x: -x[0])
            kept = [idx for _, idx in scored[:max_per_section]]
            keep_indices.update(kept)

            print(f"  Section cap: {norm_id} had {len(indices)} items, capped to {max_per_section}")

        # Return in original order
        return [req for idx, req in enumerate(requirements) if idx in keep_indices]

    def _write_requirements_to_jsonl(
        self,
        requirements: list[Requirement],
        output_file: Path,
    ) -> None:
        """Serialize current requirements back to JSONL for the resume mechanism.

        Args:
            requirements: List of requirements to write
            output_file: Path to the JSONL output file
        """
        with open(output_file, "w", encoding="utf-8") as f:
            for req in requirements:
                data = {
                    "description": req.description,
                    "citation": {
                        "section": req.citation.section,
                        "quoted_text": req.citation.quoted_text,
                        "context": req.citation.context,
                    },
                    "category": req.category.value,
                    "applies_to": req.applies_to,
                    "conditions": req.conditions,
                    "source_section": req.source_section,
                    "obligation_type": req.obligation_type,
                }
                f.write(json.dumps(data) + "\n")

    async def _check_completeness_and_backfill(
        self,
        requirements: list[Requirement],
        structure: StatuteStructure,
        statute_path: str,
        output_file: Path,
        max_passes: int = 2,
    ) -> list[Requirement]:
        """Check for missed sections and run backfill passes to cover them.

        Compares Phase 1 sections against extracted requirements and re-runs
        the section-analyzer for any missed sections.

        Args:
            requirements: Currently extracted requirements
            structure: Parsed statute structure from Phase 1
            statute_path: Path to the statute file
            output_file: Path to the JSONL output file
            max_passes: Maximum number of backfill passes

        Returns:
            Updated list of requirements including backfilled items
        """
        # Skip types that don't produce requirements
        skip_types = {"definitions", "preamble", "legislative_intent"}

        # Build expected set from Phase 1 sections
        expected: dict[str, "StatuteSection"] = {}
        for section in structure.sections:
            if section.section_type.value not in skip_types:
                expected[section.id] = section

        if not expected:
            return requirements

        # Build covered set from requirements
        covered = set()
        for req in requirements:
            norm = self._normalize_section_id(req.source_section)
            for exp_id in expected:
                if self._normalize_section_id(exp_id) == norm:
                    covered.add(exp_id)

        missed = set(expected.keys()) - covered

        # Trigger threshold: >= 3 missed AND >= 20% of expected
        if len(missed) < 3 or len(missed) < 0.2 * len(expected):
            if missed:
                print(f"  Completeness check: {len(missed)} section(s) uncovered "
                      f"(below backfill threshold of 3 and 20%)")
            return requirements

        print(f"  Completeness check: {len(missed)}/{len(expected)} sections uncovered, "
              f"starting backfill...")

        # Write statute text to a temp file so the backfill agent reads the same
        # text that Phase 3 (citation verifier) will verify against.
        output_dir = Path(self.config.working_directory)
        statute_hash = hashlib.md5(statute_path.encode()).hexdigest()[:8]
        statute_text_file = output_dir / f".techregparser_statute_{statute_hash}.txt"
        # Only write if not already present (e.g. from _extract_requirements)
        if not statute_text_file.exists():
            statute_text = self._read_statute(statute_path)
            statute_text_file.write_text(statute_text, encoding="utf-8")

        for pass_num in range(1, max_passes + 1):
            print(f"  Backfill pass {pass_num}/{max_passes}: targeting {len(missed)} sections")

            # Write current requirements to JSONL for the agent to read
            self._write_requirements_to_jsonl(requirements, output_file)

            # Build focused prompt listing only missed sections
            section_lines = []
            for sec_id in sorted(missed):
                sec = expected[sec_id]
                line_range = f"lines {sec.start_line}-{sec.end_line}" if sec.start_line else "lines unknown"
                section_lines.append(
                    f"- Section {sec_id} ({sec.section_type.value}, {line_range}): \"{sec.title}\""
                )
            sections_listing = "\n".join(section_lines)

            backfill_prompt = f"""You are completing an extraction that was interrupted.
The statute text has been extracted and saved to: {statute_text_file.absolute()}
Output file: {output_file}

Read the output file FIRST using the Read tool, then APPEND new items.

CRITICAL APPEND PROCEDURE (the Write tool OVERWRITES, it does not append):
1. FIRST: Read the current contents of the output file using the Read tool
2. THEN: Write the OLD contents PLUS your new JSON lines using the Write tool
This ensures previous items are preserved. Never write only new lines.

Extract from ONLY these sections:
{sections_listing}

Each JSON object (one per line) must contain:
- description: human-readable description
- citation: {{ "section": "...", "quoted_text": "..." }}
- applies_to: who it applies to
- conditions: list of conditions
- category: one of (disclosure, operational, technical, legal_framework)
- source_section: the section ID this item came from
- obligation_type: "shall" | "shall_not" | "may" | "must" | "may_not"

CONSOLIDATION RULES:
- One item per statutory subsection, NOT one per sub-clause.
- Maximum 5 items per section. Consolidate related provisions.
- Every item MUST have a direct quote from the statute in quoted_text.

Use the Read tool to access the statute text at {statute_text_file.absolute()}.
Write "DONE" as the final line when finished.
"""

            options = self._get_subagent_options("section-analyzer")

            # Run backfill agent
            async for message in query(prompt=backfill_prompt, options=options):
                pass

            # Read updated requirements
            new_requirements = self._read_requirements_file(output_file)

            # Deduplicate by (normalized section, description prefix)
            seen = set()
            deduped = []
            for req in new_requirements:
                key = (
                    self._normalize_section_id(req.source_section),
                    req.description[:80].lower(),
                )
                if key not in seen:
                    seen.add(key)
                    deduped.append(req)
            new_requirements = deduped

            # Check new coverage
            new_covered = set()
            for req in new_requirements:
                norm = self._normalize_section_id(req.source_section)
                for exp_id in expected:
                    if self._normalize_section_id(exp_id) == norm:
                        new_covered.add(exp_id)

            newly_filled = new_covered - covered
            print(f"  Backfill pass {pass_num}: covered {len(newly_filled)} new section(s) "
                  f"({len(new_requirements)} total requirements)")

            if not newly_filled:
                # No progress — stop to prevent infinite loops
                print(f"  Backfill stopping: no new sections covered in pass {pass_num}")
                break

            requirements = new_requirements
            covered = new_covered
            missed = set(expected.keys()) - covered

            if len(missed) < 3 or len(missed) < 0.2 * len(expected):
                print(f"  Backfill complete: {len(missed)} section(s) remain uncovered "
                      f"(below threshold)")
                break

        # Clean up statute text temp file
        if statute_text_file.exists():
            statute_text_file.unlink()

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
        skipped = 0

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
                    skipped += 1
                    continue

        if skipped > 0:
            print(f"  Warning: Skipped {skipped} malformed JSON line(s) in output file.")

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

1. DISCLOSURE: Requirements that must be stated in a privacy policy or notice
   - Categories of data collected
   - Purposes for processing
   - Consumer rights available
   - Third party sharing
   - Sale of data statement
   - Retention periods
   - Contact information

2. OPERATIONAL: Internal compliance processes (NOT in policy)
   - Response timeframes (45 days, 90 days, etc.)
   - Free request limits per year
   - Appeal process deadlines
   - Consent revocation processing time
   - Internal assessment requirements

3. TECHNICAL: System, UI, and website implementation requirements
   - GPC/Universal opt-out signal recognition
   - Security measure implementations
   - Data deletion technical processes
   - Age verification systems
   - "Clear and conspicuous link" placements
   - Opt-out button locations
   - Cookie banner requirements
   - Dark pattern prohibitions
   - Any website/app design or UX requirements

4. LEGAL_FRAMEWORK: Applicability, exemptions, scope, enforcement, and regulatory administration
   - Applicability thresholds (consumer volume, revenue, data volume)
   - Entity exemptions (nonprofits, government, small businesses)
   - Data-type exemptions (HIPAA, GLBA, FCRA, FERPA, DPPA carve-outs)
   - Scope limitations (what data types are covered, geographic reach)
   - Civil penalties and fine amounts
   - Criminal prohibitions ("it shall be unlawful...")
   - Attorney General authority and enforcement powers
   - Cure periods and right-to-cure provisions
   - Private right of action (or lack thereof)
   - Penalty multipliers or aggravating factors
   - Injunctive relief provisions

CLASSIFICATION RULES:
- If it must appear in the privacy policy TEXT, it's DISCLOSURE
- If it's about HOW QUICKLY to respond, it's OPERATIONAL
- If it's about SYSTEM BEHAVIOR, UI DESIGN, or WHERE TO PLACE something on a website, it's TECHNICAL
- If it defines WHO MUST COMPLY, WHAT IS EXEMPT, PENALTIES, PROHIBITED CONDUCT, or WHO ENFORCES the law, it's LEGAL_FRAMEWORK

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
                obligation_type=data.get("obligation_type", ""),
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
