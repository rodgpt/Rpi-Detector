# How to Research Packages for Integration

**Purpose:** This guide provides a systematic process for evaluating third-party packages/libraries before integrating them into the project. Follow this process to ensure consistency, quality, and informed decision-making.

---

## Table of Contents

1. [When to Use This Process](#when-to-use-this-process)
2. [Research Process Overview](#research-process-overview)
   - [Step 1: Preparation](#step-1-preparation-5-minutes)
   - [Step 2: Package Discovery](#step-2-package-discovery-10-15-minutes)
   - [Step 3: Deep Dive Research](#step-3-deep-dive-research-30-45-minutes)
   - [Step 4: Document Your Findings](#step-4-document-your-findings-30-60-minutes)
   - [Step 5: Review & Share](#step-5-review--share-10-minutes)
3. [Master Prompt for AI Assistants](#master-prompt-for-ai-assistants)
4. [Research Checklist](#research-checklist)
5. [Tips for Effective Research](#tips-for-effective-research)

---

## When to Use This Process

Use this research process when considering:
- New UI component libraries (Gantt charts, calendars, rich text editors, etc.)
- State management libraries
- Backend packages (ORM extensions, authentication libraries, etc.)
- Major dependencies that affect architecture
- Any package that will be used across multiple features

**Skip this for:**
- Utility packages with narrow scope (e.g., date-fns, lodash)
- Official framework packages
- Packages already used in the project

---

## Research Process Overview

### Step 1: Preparation (5 minutes)

Read these project documents to understand current context:

**Required Reading:**
1. `README.md` - Tech stack, setup, architecture
2. `docs/PROGRESS.md` - Current phase, what's built, what's next
3. `docs/TODO.md` - Pending features and tasks
4. Frontend dependency file (e.g., `package.json`)
5. Backend dependency file (e.g., `pyproject.toml`, `requirements.txt`, `Cargo.toml`)

**Key things to note:**
- Current tech stack (see README.md)
- Current project phase (see PROGRESS.md)
- Existing dependencies and their versions
- Requirements related to the feature you're researching

---

### Step 2: Package Discovery (10-15 minutes)

**Find the package:**
1. Start with official documentation
2. Check npm/PyPI/crates.io page for package info
3. Review GitHub repository (stars, issues, last commit, license)
4. Search for comparison articles ("best [type] libraries for [framework] [year]")

**Key Questions:**
- Is it actively maintained? (Recent commits, issues handled)
- What's the license? (MIT, Apache, Commercial?)
- How popular is it? (Downloads, GitHub stars)
- Does it have TypeScript support? (If applicable)
- Is there good documentation?

---

### Step 3: Deep Dive Research (30-45 minutes)

Research the following areas systematically:

#### 3.1 Technical Compatibility
- **Framework Version:** Compatible with current version?
- **Type Safety:** Has type definitions?
- **Build Tool:** Compatible with current build tool?
- **UI Library:** Works with/alongside current UI framework?
- **State Management:** Integrates with current state management?
- **Backend:** Compatible with current backend stack?

#### 3.2 Features & Capabilities
- What does it do? (Core features)
- What editions exist? (Open source vs PRO/Commercial)
- Does it cover project requirements? (Check PROGRESS.md)
- What's missing? (Gaps in functionality)

#### 3.3 Installation & Setup
- How to install?
- What dependencies does it pull in?
- Basic usage example (copy from docs)
- Configuration complexity (simple vs complex)

#### 3.4 Integration Approach
- How does it integrate with existing code?
- Does it require backend changes?
- Does it require new models/endpoints?
- Data structure requirements (what format does it expect?)

#### 3.5 Customization & Extensibility
- Can it be styled to match the project's UI?
- Can it be extended with custom logic?
- Does it support plugins/extensions?

#### 3.6 Alternatives
- What are 2-3 alternative packages?
- Quick comparison (features, license, cost, complexity)

#### 3.7 Effort Estimate
- Backend work needed (models, API, endpoints) - days
- Frontend work needed (components, integration, styling) - days
- Total estimate for integration

---

### Step 4: Document Your Findings (30-60 minutes)

Use the [RESEARCH_TEMPLATE.md](./RESEARCH_TEMPLATE.md) to structure your findings.

**Create a new research document:**
```bash
# Create file in docs/research/
touch docs/research/[package-name]-analysis.md
```

**Fill in all sections from the template:**
1. Executive Summary & Recommendation
2. Library Overview
3. Technical Compatibility
4. Installation & Setup (with code examples)
5. Data Structure Requirements
6. Integration with Existing Stack
7. Requirements Coverage
8. Pros and Cons
9. Alternatives Comparison
10. Development Effort Estimate
11. Packages to Install
12. Integration Approach
13. Will It Make Our Lives Easier?
14. Recommendation & Next Steps
15. Additional Resources

**Tips:**
- Be specific (include code examples, version numbers, file paths)
- Be honest about cons and limitations
- Quantify effort estimates (days/weeks)
- Include links to documentation, npm, GitHub
- Use tables for comparisons
- Use checkmarks for quick scanning

---

### Step 5: Review & Share (10 minutes)

**Before sharing:**
- [ ] Proofread for typos and clarity
- [ ] Verify all links work
- [ ] Ensure recommendation is clear (YES/NO/CONDITIONAL)
- [ ] Check that code examples are accurate
- [ ] Confirm effort estimates are realistic

**Share with team:**
1. Commit the research document to the repo
2. Update `docs/research/RESEARCH.md` with a link to your analysis
3. Notify team in chat/email
4. Discuss in next standup/meeting

---

## Master Prompt for AI Assistants

**Copy this prompt when asking an AI to research a package:**

```
I need you to research [PACKAGE_NAME] for potential integration into our project.

## Context
Read these files first to understand our project:
- README.md - Tech stack overview
- docs/PROGRESS.md - Current phase and progress
- docs/TODO.md - Future features
- Frontend dependency file (package.json / Cargo.toml / etc.)
- Backend dependency file (pyproject.toml / requirements.txt / etc.)

## Your Task
1. Research [PACKAGE_NAME] thoroughly (official docs, registry, GitHub, comparisons)
2. Evaluate it against our tech stack (see README.md, check dependency versions)
3. Determine if it meets our requirements for [FEATURE_NAME] (see PROGRESS.md)
4. Follow the structure in docs/research/RESEARCH_TEMPLATE.md

## Evaluation Criteria
- Technical compatibility (check versions against dependency files)
- License (prefer MIT/Apache open source)
- Features vs requirements coverage (compare against PROGRESS.md)
- Installation complexity
- Integration effort estimate
- Pros and cons
- Comparison with 2-3 alternatives

## Deliverable
Create a comprehensive research document at:
docs/research/[package-name]-analysis.md

Follow the structure in docs/research/RESEARCH_TEMPLATE.md. Include:
- Clear YES/NO/CONDITIONAL recommendation
- Code examples for installation and basic usage
- Effort estimate in days/weeks
- Pros/cons list
- Alternatives comparison table
- Links to documentation and resources

Be thorough, specific, and honest about limitations.
```

---

## Research Checklist

Use this checklist to ensure complete research:

### Discovery
- [ ] Found official documentation
- [ ] Checked registry page (downloads, version, license)
- [ ] Reviewed GitHub (stars, issues, last commit, contributors)
- [ ] Searched for comparison articles

### Compatibility
- [ ] Verified framework version compatibility
- [ ] Checked type safety support
- [ ] Confirmed build tool compatibility
- [ ] Tested integration with UI library (if UI component)
- [ ] Verified state management compatibility (if state-heavy)

### Functionality
- [ ] Listed all core features
- [ ] Identified open source vs commercial features
- [ ] Mapped features to project requirements
- [ ] Noted missing features/limitations

### Integration
- [ ] Documented installation steps
- [ ] Created basic usage example
- [ ] Identified backend changes needed (models, API)
- [ ] Identified frontend changes needed (components, routes)
- [ ] Estimated integration effort

### Evaluation
- [ ] Listed 3+ pros
- [ ] Listed 3+ cons
- [ ] Compared with 2-3 alternatives
- [ ] Made clear recommendation (YES/NO/CONDITIONAL)

### Documentation
- [ ] Created research doc in docs/research/
- [ ] Followed RESEARCH_TEMPLATE.md structure
- [ ] Included code examples
- [ ] Added links to resources
- [ ] Proofread and reviewed

---

## Tips for Effective Research

### Do:
- Be thorough but concise
- Use tables for comparisons
- Include code examples
- Quantify effort estimates
- Link to primary sources
- Be honest about cons
- Consider long-term maintenance

### Don't:
- Skip compatibility checks
- Ignore licensing issues
- Underestimate integration effort
- Forget to compare alternatives
- Make recommendations without evidence
- Copy-paste marketing fluff

---

## Questions?

If you're unsure about:
- **Requirements:** Check PROGRESS.md or ask project lead
- **Tech Stack:** Check README.md or dependency files
- **Existing Patterns:** Grep the codebase for similar implementations
- **Effort Estimates:** Compare to similar past integrations

---

**Last Updated:** {{DATE}}
**Maintained By:** Project Team
