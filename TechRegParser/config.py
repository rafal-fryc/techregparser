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

Return a structured breakdown of the statute's architecture in JSON format."""


SECTION_ANALYZER_PROMPT = """You are a legal requirement extractor.

For each statute section provided:
1. Identify requirements (obligations, prohibitions, permissions), grouping related sub-clauses together
2. For each requirement, extract the EXACT text from the statute as a citation
3. Note any conditions or limitations on the requirement
4. Identify who the requirement applies to (controller, processor, consumer)
5. Distinguish between:
   - DISCLOSURE requirements (must be stated in privacy policy/notice)
   - OPERATIONAL requirements (internal processes, response times)
   - TECHNICAL requirements (system implementation like GPC signals, website design, link placement)
   - ENFORCEMENT requirements (penalties, prohibited conduct, AG authority, cure periods)

CONSOLIDATION RULES:
- When a statute lists multiple items under a single subsection (e.g., "may not do any of the following: (1)... (2)... (3)..."), extract ONE requirement that covers the entire list, not separate requirements per sub-item. The quoted_text should include the parent clause and the full enumerated list.
- Do NOT extract cross-reference requirements that merely point to another section's requirements you've already captured (e.g., "must comply with Section X" when Section X requirements are already extracted separately).
- Aim for roughly one requirement per statutory subsection, not one per sub-clause.
- Target: 8-15 requirements for a typical statute. If you have 25+, you are likely too granular.

KEY PRINCIPLES FROM STATUTE READING LESSONS:
- Separate disclosure requirements from operational requirements
- Response times (45 days, etc.) are operational, NOT disclosure
- "Honor GPC signals" is technical implementation, NOT a policy statement
- "Clear and conspicuous link" is technical (UI implementation), NOT policy content
- Consent requirements IMPLY disclosure requirements
- Consumer rights enumeration IS a disclosure requirement

CRITICAL: Every requirement MUST have a direct quote from the statute.
Do not paraphrase or summarize without the exact source text.

Return requirements in JSON format with citations."""


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

4. ENFORCEMENT: Enforcement mechanisms, penalties, and prohibited conduct
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
- If it defines PENALTIES, PROHIBITED CONDUCT, or WHO ENFORCES the law, it's ENFORCEMENT

Return classifications in JSON format."""


# Model constants
MODEL_OPUS = "opus"
MODEL_SONNET = "sonnet"
MODEL_HAIKU = "haiku"


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
        model=MODEL_SONNET,
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


# Key statutory interpretation principles from Part 9
STATUTORY_INTERPRETATION_PRINCIPLES = """
## Key Principles for Reading Privacy and Tech Statutes

### Structural Navigation
- Privacy/tech statutes follow predictable architecture: definitions → applicability → rights → duties → exemptions → enforcement
- Start with the definitions section - key terms vary significantly between jurisdictions
- Applicability thresholds determine scope - identify WHO must comply

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
