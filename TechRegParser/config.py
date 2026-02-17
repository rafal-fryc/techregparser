"""Configuration and prompts for the Statute Parser system."""

from dataclasses import dataclass, field
from typing import Optional


# Agent prompts
STATUTE_READER_PROMPT = """You are a statute structure expert.

Analyze the provided statute and identify:
1. Definitions section - extract all defined terms with their exact statutory definitions
2. Applicability thresholds - who must comply (consumer volume, revenue thresholds, etc.)
3. Consumer/individual rights sections
4. Controller/business duties sections
5. Exemptions - entity and data type exemptions (federal carve-outs like HIPAA, GLBA, FCRA, FERPA, DPPA)
6. Enforcement provisions (penalties, AG authority, cure periods)
7. Legislative intent - sections that explain the statute's purpose, policy goals, or legislative findings. These may be titled 'Purpose', 'Findings', 'Legislative findings', 'Legislative intent', 'Declaration of policy', or may appear as preamble text. Not all statutes have these sections.

Return a structured breakdown of the statute's architecture in JSON format."""


SECTION_ANALYZER_PROMPT = """You are a legal requirement and provision extractor.

For each statute section provided:
1. Identify requirements (obligations, prohibitions, permissions) and legal framework
   provisions (applicability, exemptions, scope, enforcement)
2. For each item, extract the EXACT text from the statute as a citation
3. Note any conditions or limitations
4. Identify who it applies to
5. Classify as: DISCLOSURE, OPERATIONAL, TECHNICAL, or LEGAL_FRAMEWORK

KEY PRINCIPLES:
- Separate disclosure requirements from operational requirements
- Website UI requirements are technical (implementation), not policy content
- Consent requirements imply disclosure requirements
- Exemptions and applicability ARE extractable provisions (legal_framework)
- Federal law carve-outs (HIPAA, GLBA, etc.) should be captured

CRITICAL: Every item MUST have a direct quote from the statute."""


CITATION_VERIFIER_PROMPT = """You are a citation verification specialist.

For each requirement provided:
1. Search the original statute for the quoted text
2. Verify the section reference is correct
3. Flag any citations that cannot be found verbatim
4. Assign a confidence score:
   - 1.0 = exact match found
   - 0.8-0.99 = minor variations (whitespace, punctuation)
   - 0.6-0.79 = partial match found
   - <0.6 = needs human review

ANTI-HALLUCINATION PROTOCOL:
- If the quoted text cannot be found, mark as UNVERIFIED
- If section reference doesn't exist, mark as INVALID
- Always preserve the original text for audit
- Flag any requirements that may have been fabricated

Return verification results in JSON format."""


REQUIREMENT_CLASSIFIER_PROMPT = """You are a legal requirement classifier.

Categorize each requirement into one of these categories:

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

Return classifications in JSON format."""


# Model constants
MODEL_OPUS = "claude-opus-4-6"
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_HAIKU = "claude-haiku-4-5-20251001"


# Agent definitions for the SDK
@dataclass
class AgentConfig:
    """Configuration for an agent."""
    name: str
    description: str
    prompt: str
    tools: list[str]
    model: str = MODEL_SONNET  # Default to Sonnet for subagents


AGENT_CONFIGS = {
    "statute-reader": AgentConfig(
        name="statute-reader",
        description="Expert statute structure analyzer that identifies definitions, applicability, rights, duties, exemptions, and enforcement sections.",
        prompt=STATUTE_READER_PROMPT,
        tools=["Read", "Grep", "Glob"],
        model=MODEL_HAIKU,
    ),
    "section-analyzer": AgentConfig(
        name="section-analyzer",
        description="Requirement extraction specialist that identifies specific obligations from statute sections with exact citations. Has access to definition lookup.",
        prompt=SECTION_ANALYZER_PROMPT,
        tools=["Read", "Write", "Grep"],
        model=MODEL_SONNET,
    ),
    "requirement-classifier": AgentConfig(
        name="requirement-classifier",
        description="Requirement classification expert that categorizes requirements by type.",
        prompt=REQUIREMENT_CLASSIFIER_PROMPT,
        tools=[],
        model=MODEL_HAIKU,
    ),
}

# Note: citation-verifier is implemented as a Python-only tool (CitationVerifier class
# in tools/citation_verify.py), not as an LLM agent. Citation matching is deterministic
# work that doesn't benefit from an LLM. The CITATION_VERIFIER_PROMPT above documents
# the verification criteria used by the Python implementation.


# Orchestrator configuration
@dataclass
class OrchestratorConfig:
    """Configuration for the main orchestrator."""
    working_directory: str = "."
    output_format: str = "json"  # or "markdown"
    verify_citations: bool = True
    classify_requirements: bool = True
    export_to_law_list_buddy: bool = False
    model: str = MODEL_OPUS  # Orchestrator uses Opus
    use_memory: bool = True  # Store/load patterns from previous runs
    use_cache: bool = True  # Cache Phase 1 structure for re-runs
    reextract_confidence_threshold: float = 0.99  # Re-extract below this
    max_reextract_passes: int = 2                 # Max retry loops
    verify_definitions: bool = True               # Check Phase 1 definitions


# Key statutory interpretation principles from Part 9
STATUTORY_INTERPRETATION_PRINCIPLES = """
## Key Principles for Reading Privacy and Tech Statutes

### Structural Navigation
- Privacy/tech statutes follow predictable architecture: definitions → applicability → rights → duties → exemptions → enforcement
- Start with the definitions section - key terms vary significantly between jurisdictions
- Applicability thresholds determine scope - identify WHO must comply

### Legislative Intent
- Many statutes open with a "Purpose", "Findings", "Legislative intent", or "Declaration of policy" section
- These sections explain what the legislature intended the statute to accomplish and why it was enacted
- Use legislative intent to resolve ambiguity: when a provision could be read broadly or narrowly, the stated purpose guides interpretation
- Intent sections are context, not requirements — do not extract them as obligations, but use them to inform how you read the rest of the statute

### Distinguishing Requirement Types
- Separate disclosure requirements from operational requirements
- Website UI requirements are technical (implementation), not policy content
- Consent requirements imply disclosure requirements

### Handling Exemptions
- Federal law carve-outs (HIPAA, GLBA, FCRA, FERPA, DPPA) are nearly universal
- Read exemption language carefully - some apply to ENTITY, others to DATA

### Recognizing Variations
- Consumer rights are NOT uniform across jurisdictions
- "Sale" definitions vary ("monetary consideration" vs "other valuable consideration")
- Age-based protections have different cutoffs (under 13, 13-15, 13-16, 13-17, under 18)

### Source Document Awareness
- AG summaries vs. statute text serve different purposes - use actual statutory language
- Statutes may be amended by multiple bills - find consolidated version
- Effective dates and phase-in periods matter

### Pattern Recognition
- States cluster into regulatory philosophies (consumer-protective vs. business-friendly)
- Watch for unique provisions in each state
- Work section by section, not requirement by requirement
- Cross-reference consumer rights with controller duties
"""
