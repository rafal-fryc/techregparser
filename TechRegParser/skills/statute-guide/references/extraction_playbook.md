# Extraction Playbook

Concise checklist for extracting requirements from privacy/tech statutes.

## Pre-Extraction

- [ ] Read definitions section first — anchor all interpretation on statutory definitions
- [ ] Identify applicability thresholds (conjunctive AND vs disjunctive OR)
- [ ] Note effective dates and phase-in periods
- [ ] Check for consolidated/amended versions

## Section-by-Section Extraction

- [ ] Work section by section, not requirement by requirement
- [ ] One requirement per statutory subsection (not per sub-clause)
- [ ] Target 8-15 requirements for a typical statute; 25+ means too granular

## Citation Rules

- [ ] Every requirement MUST have a direct `quoted_text` from the statute
- [ ] Quote parent clause + full enumerated list for multi-item subsections
- [ ] Use `"..."` for very long lists — quote parent clause + representative excerpt
- [ ] Record `source_section` for each requirement

## Classification (four categories)

| Category      | Test                                                        | Examples                                          |
|---------------|-------------------------------------------------------------|---------------------------------------------------|
| DISCLOSURE    | Must appear in privacy policy/notice text                   | Data categories, purposes, rights list, retention |
| OPERATIONAL   | Internal process / timeline                                 | 45-day response, annual assessments, appeal deadlines |
| TECHNICAL     | System behavior, UI placement, or website/app design        | GPC signals, clear link placement, dark patterns  |
| ENFORCEMENT   | Penalties, prohibited conduct, or who enforces the law      | Civil fines, AG authority, cure periods, criminal prohibitions |

### Quick Decision Tree

1. Does it describe **what the policy must say**? -> DISCLOSURE
2. Does it describe **how fast or how often** to do something? -> OPERATIONAL
3. Does it describe **system behavior** or **where/how to display** something? -> TECHNICAL
4. Does it define **who must comply**, **what is exempt**, **penalties**, **prohibited conduct**, or **enforcement authority**? -> LEGAL_FRAMEWORK

## Consolidation Rules

- [ ] Group enumerated sub-items under a single parent requirement
- [ ] Skip cross-references to sections already extracted
- [ ] Consent requirements imply a disclosure requirement — capture both
- [ ] Cross-reference consumer rights with controller duties for completeness

## Common Pitfalls

- Splitting enumerated lists into separate requirements (over-extraction)
- Classifying response times as DISCLOSURE (they are OPERATIONAL)
- Classifying "clear and conspicuous link" as DISCLOSURE (it is TECHNICAL)
- Missing implied disclosure requirements from consent provisions
- Paraphrasing instead of quoting the statute verbatim
