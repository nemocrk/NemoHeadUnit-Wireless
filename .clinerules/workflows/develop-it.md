## Brief overview

This workflow ensures all behavior-changing work in NemoHeadUnit follows documented architectural patterns and is properly verified before claiming completion. The workflow is divided into five sequential phases that must be followed in order.

---

## Workflow Phases

### Phase 1: Pre-Implementation Alignment

**Purpose**: Ensure architectural alignment before any code is written.

**Checklist**:

- [ ] Review `docs/project-vision.md` for design principles
- [ ] Verify architecture rules checklist is satisfied
- [ ] Confirm coding conventions are followed

**When to skip**: Only skip if explicitly documented in `docs/session-handoffs.md` as a known deviation.

---

### Phase 2: Get User Input about what to develop

**Purpose**: Clarify the immediate development goal by soliciting direct user input and pausing for confirmation.

**Checklist**:

- [ ] Ask the user: "What is the next feature or fix to develop?"
- [ ] Wait for a clear response before proceeding to roadmap or code
- [ ] Confirm understanding of the specific requirements and expected behavior

---

### Phase 3: Roadmap Management

**Purpose**: Track feature priorities and sequencing.

**Actions**:

- [ ] Update `docs/roadmap-current.md` when priorities shift
- [ ] Document current priorities
- [ ] Track sequencing of features
- [ ] Update when priorities shift

**Trigger**: Only update when priorities or sequencing change.

---

### Phase 4: Implementation & Coding Style

**Purpose**: Ensure code follows naming conventions and architectural patterns using a surgical modification approach.

**Modification Strategy**:

- [ ] No Full Rewrites: Avoid rewriting entire files. Perform surgical edits on specific sections.
- [ ] Context Awareness: Re-read the file to identify the exact lines or functions to update.
- [ ] Precision: Output only the modified snippets or targeted changes.

**Python Naming Checklist**:

- [ ] Functions/classes: `snake_case`
- [ ] Event handlers: `on_<snake_case>` (e.g., `on_media_channel_setup_request`)

---

### Phase 5: Build & Test Verification

**Purpose**: Ensure every code change is covered by new or updated tests, and that the entire suite passes.

**Checklist**:

- [ ] Mandatory Coverage: Each component must have at least 80% code coverage.
- [ ] New Tests: Verify that every new snippet or bugfix includes a dedicated test case.
- [ ] Pass/Fail: The build is considered FAILED if the coverage threshold is not met.

**Python Build Commands**:

```bash
cd /home/nemo/NemoHeadUnit-Wireless
python -c "from app.main import main; print('Python build OK')"
python -m pytest tests/ --cov=app --cov-report=term-missing --cov-fail-under=80 -v
timeout 5 python -m app.main
```

---

### Phase 6: Handoff Documentation

**Purpose**: Document changes for team handoff.

**File**: `docs/session_handoff.md`

**Entry Format**:

```markdown
## [Date] - [Short Description]

**What changed:**
[Describe the specific change]

**Why:**
[Explain the purpose/rationale]

**Status:**
[Completed/In Progress/Broken]

**Next 1-3 steps:**
1. [Next step]
2. [Next step]
3. [Next step]

**Verification commands/results:**
```bash
# Add verification commands here
python -m pytest tests/smoke_tests.py -v
# Output: PASSED
```

**Run Application Command**
```bash
python app/main.py
```

**Trigger**: Always append after successful build and test.

---

## Quick Reference Commands

| Action | Command |
|--------|---------|
| Run Python build | `python -c "from app.main import main; print('OK')"` |
| Run Python tests | `python -m pytest tests/**** -v` |
| Create handoff entry | Append to `docs/session_handoff.md` |
| Check vision alignment | `grep -A 100 "# Project Vision: NemoHeadUnit-Wireless" docs/project-vision.md` |

---

## Phase Completion Checklist

**Before claiming completion, verify**:
- [ ] All Phase 1-6 steps completed
- [ ] All verification commands pass
- [ ] Handoff entry appended to `docs/session_handoff.md`
