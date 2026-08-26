# docs

The umbrella. Everything here concerns the system as a whole, or the seam between the two codebases. Domain-specific documentation lives in `raspberry-pi/docs/` and `dashboard/docs/`.

The Pi and the dashboard are one system coupled through blob storage, so working on either one means being able to check the state of the other. That is what these files are for.

---

## Working documents

Read in this order on a cold start.

**[FINDINGS.md](FINDINGS.md)** — The defect register: 23 entries verified against source, ranked, most now FIXED with dates. Canonical here; the dashboard repo mirrors it. The audits below are its sources.

**[ARCHITECTURE.md](ARCHITECTURE.md)** — The system as built: device package, private storage, FastAPI/Postgres backend, React frontend, and the signed config path. Failure modes as they behave today.

**[DATA-CONTRACT.md](DATA-CONTRACT.md)** — The v2 contract, normative for both codebases. Enforced: `tools/validate_contract.py`, the device conformance test, and the dashboard's `make contract` all check it. Canonical here; the dashboard mirrors it byte for byte.

**[PROGRESS.md](PROGRESS.md)** — Stack-level phase state. Rolls up both codebases. Phase numbers are shared across all three `PROGRESS.md` files.

**[TODO.md](TODO.md)** — Cross-cutting items, client-facing actions, and the three field questions that are blocking decisions.

**[BACKEND-SCHEME.md](BACKEND-SCHEME.md)** — SUPERSEDED. The backend is built (Dashboard D-019/D-020/D-021). Kept as the spec-versus-built record.

**[RUNBOOK.md](RUNBOOK.md)** — Intentionally empty. Waiting on decisions it would otherwise document wrongly.

**[research/](research/)** — Three-stage pipeline for evaluating libraries and services, plus the queue of decisions needing research.

Decisions live one level up, at [`../DECISIONS.md`](../DECISIONS.md), because they feed downward into both codebases.

---

## Commercial

**[presupuesto.md](presupuesto.md)** — The agreed terms. Two workstreams, effort estimates, rate in UF, 20 working days.

**[ASSESSMENT_AND_PLAN.md](ASSESSMENT_AND_PLAN.md)** — What the scope costs, where the budget and the audits disagree, day-by-day delivery plan. English, internal. Written against the original 36 to 40 hour figure; the budget is now 60.

**[ALCANCE_FASE1_FASE2.md](ALCANCE_FASE1_FASE2.md)** — Client-facing scope note. Phase 1 versus Phase 2, required access, acceptance criteria. Spanish, ready to send.

---

## Source audits

Three passes, chronological. All kept because all three were delivered to the client and their contradictions matter.

**[INITIAL_REPORT.md](INITIAL_REPORT.md)** (6 July 2026) — First full-stack pass. Thirteen issues with a severity table. Still the best description of how the system talks to the internet.

**[IMPROVEMENT_REPORT.md](IMPROVEMENT_REPORT.md)** (July 2026) — Long form. Hardware right-sizing, ML model analysis, the async architecture, the API design, full security exposure inventory.

**[SYSTEM_REVIEW.md](SYSTEM_REVIEW.md)** / **[SYSTEM_REVIEW_ES.md](SYSTEM_REVIEW_ES.md)** — The honest cut, both languages. Claim then counter-argument, sorted into fix now, should fix, can wait. The most useful for deciding what *not* to do.

Two cautions. The reports disagree on the deaf window: `IMPROVEMENT_REPORT.md` §3.1 says 30 to 60 percent of blasts may be missed, `SYSTEM_REVIEW.md` §3.1 says the rate is low. Neither measured it. See F-05. And `SYSTEM_REVIEW.md` §4.3 makes a claim about a write-capable SAS token in browser storage that does not hold against the current dashboard. See X-01.

---

## Generated documents

`MarFutura_System_Review.docx` and `OceanKind_Improvement_Report.docx` are the client-facing versions of two audits above.

`system_review.js` and `oceankind_improvement_report.js` generate them, using the `docx` library with Futurity house styling. Build tooling for the documents, not project code, which is why they sit beside their output. Run with `node <script>.js`.

---

## Housekeeping

**[MOVES.md](MOVES.md)** — Every file moved during the restructure, source and destination. There is no version control here, so this is the record and the reversal instructions.
