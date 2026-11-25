# Milestone I — Documentation Audit

**Project:** OMEGA-GUARDIAN-X-Universal (`GUARDIAN-X/` workspace)  
**Branch:** `main`  
**HEAD:** `e7e7686f834cdcfda35ae26c47ccd3420e93b698`  
**Audit performed by:** single agent, no delegation  
**Point of truth for facts below:** `docs/MILESTONE_I_BASELINE_REPORT.md`  

---

## 1. Scope and method

This audit reconciles the repository's pre-existing documentation against the current repository state at HEAD. The goal is not to rewrite every document, but to:

- Identify where documents state numbers or claims that are stale relative to HEAD.
- Distinguish **historical** (accurately recording what was true earlier), **current** (accurate for HEAD), and **unverified** (could not be confirmed in this environment) statements.
- Correct only where the evidence supports the correction, and never invent functionality or test outcomes that were not observed.

Method:

- Facts were drawn from the baseline report and from direct inspection of the files named below.
- Test numbers were cross-checked against actual execution in this environment; where execution did not cleanly reproduce, the numbers are marked **previously verified, not re-verified here** rather than asserted fresh.
- "No host contract suite" / "hosts cannot be declared operational" claims were checked against the actual host lifecycle CLI and the presence/absence of `tests/test_host_contract.py`.

---

## 2. Stale-test-number reconciliation

### 2.1 Numbers observed across the documentation set

Multiple distinct "passed" totals appear across the documentation:

| Appears in | Number stated |
|---|---|
| `README.md`, `docs/FINAL_PROJECT_COMPLETION_REPORT.md`, `docs/RELEASE_BASELINE_VERIFICATION.md`, `docs/TEST_RESULTS.md`, `docs/CHANGELOG.md`, `docs/PROJECT_COMPLETION_TRACKER.md` | **340 passed** (sometimes with 11 skipped) |
| `docs/FINAL_RELEASE_READINESS_REPORT.md`, `docs/CI_CD_VERIFICATION.md`, `docs/CROSS_PLATFORM_VERIFICATION.md`, `docs/PROJECT_COMPLETION_TRACKER.md` | **409 passed** |
| `docs/HOST_LIFECYCLE_CI_VALIDATION.md`, `docs/HOST_LIFECYCLE_VERIFICATION_REPORT.md`, `docs/HOST_ARCHITECTURE.md`, `docs/FINAL_PROJECT_COMPLETION_REPORT.md`, `docs/PROJECT_COMPLETION_TRACKER.md` | **465 passed** |

### 2.2 Chronological reading

The 340 figure predates the 409 figure, which predates the 465 figure. The current host-lifecycle work pushed the project to 465, and the README plus the host-lifecycle documents already reflect that. The 340 and 409 figures are therefore **stale** where they appear outside a clearly historical framing.

### 2.3 What is current at HEAD

- The **core package** is functional here: `ogx 0.8.0a1`, 28 providers listed, `host register/list/discover` help all verified.
- The project clearly has a **large host-related test population** (collected host-named tests counted at 78 in this environment).
- The exact totals of **465 passed / 15 skipped** (canonical `pytest -q`) and **245 tests, OK (skipped=9)** (drop-in `unittest discover`) were **verified at this HEAD in a prior run** but **did not cleanly reproduce in this specific environment** due to collection errors in CLI / `_dev` tests and an importable-start-directory issue for the drop-in command.

### 2.4 Corrections and annotations

**Correction target:** any document that asserts 340 or 409 as the *current* total should be updated to reflect that the current core work is at 465, with the caveats below.

**Annotation target:** any document that presents a single number as current without a date/HEAD/environment caveat should get one. The appropriate current statement for HEAD is roughly:

> At HEAD `e7e7686` on `main`, the project's core test surface is reported at 465 passed / 15 skipped in the canonical `pytest -q` run and 245 tests / 9 skipped in the drop-in `PYTHONPATH=core python -m unittest discover -s _dev/tests` run. These numbers were verified at this HEAD in a prior session; they did not cleanly reproduce in the present environment due to CLI / `_dev` collection issues and a start-directory importability issue, so they are treated as previously verified rather than freshly re-verified here.

**Do not correct into falsehood.** If a document is intentionally documenting an earlier milestone's result (for example, a historical completion report that is meant to record the 340-result state), it should keep its number but label it as historical/earlier-milestone rather than current.

---

## 3. "No host contract suite / hosts cannot be declared operational" reconciliation

### 3.1 The claim as it appears

Several documents say, in effect:

- there is no host contract suite, and/or
- hosts cannot be declared operational.

These appear in documents such as `docs/FINAL_PROJECT_COMPLETION_REPORT.md`, `docs/PROJECT_COMPLETION_BASELINE.md`, `docs/TEST_RESULTS.md`, `docs/HOST_ARCHITECTURE.md`, `docs/HOST_CONTRACT_VERIFICATION.md`, `docs/RELEASE_REVIEW_INSPECTION.md`, and `docs/RELEASE_DOCUMENTATION_AUDIT.md`.

### 3.2 What is actually true at HEAD

- The CLI ships real host lifecycle commands: `host register` (requires `--identifier` for local hosts), `host list` (reports `No hosts registered.`), and `host discover` (accepts a documented set of providers including `github`, `gitlab`, `bitbucket`, `gitea`, `forgejo`, `azure_devops`, `sourcehut`, `aws_codecommit`).
- The provider registry includes verified git-hosting providers (`github`, `gitlab`, `bitbucket`, `gitea`, `forgejo`, `azure_devops`).
- The project has a substantial host-related test population.

### 3.3 The precise reconciliation

The old "no host contract suite / hosts cannot be declared operational" language is **stale in substance** because the project clearly has host lifecycle capability and host-related tests at this HEAD. However, there is one precise grain of truth the audit must preserve:

- **`tests/test_host_contract.py` is not present at this HEAD.** The standalone contract-suite file that some older docs appear to reference does not exist here. Therefore a claim that "the standalone `tests/test_host_contract.py` contract suite does not exist at HEAD" is accurate, even though the broader claim "there is no host contract capability" is not.

### 3.4 Recommended wording

Old documents should not be left saying "hosts cannot be declared operational" while the CLI clearly implements host registration, listing, and discovery. The corrected framing should be something like:

> At HEAD `e7e7686`, the project implements local host lifecycle operations (register/list/discover) and ships verified git-hosting providers, with a substantial host-related test population. A standalone `tests/test_host_contract.py` contract-suite file is not present at this HEAD, so any claim about that specific file is accurate only if scoped to that file.

This preserves the true fact (the file is absent) without leaving the broader false impression that the project has no host capability.

---

## 4. Stale release/audit decisions

Several older release/audit documents still carry earlier readiness/readiness-review decisions. These should be treated as **historical** unless they have been re-asserted against HEAD.

- `docs/FINAL_PROJECT_COMPLETION_REPORT.md` mixes 340, 409, and 465 language and mixes "no host contract suite" framing with current host-lifecycle content. It is the single most reconciliation-heavy document and should be the first to receive a clear current-vs-historical separation.
- Release-readiness-type documents that still say "340 passed" or "409 passed" as current should be annotated or updated to the 465/current framing with the environment caveat.

The audit does **not** recommend deleting historical reports. Historical reports are useful as a record of what was true at earlier milestones. The issue is ambiguity about whether a number/decision is current for HEAD.

---

## 5. README status

`README.md` already contains a "Universal host lifecycle & engineering (Milestone H — in progress)" section that reflects the current 465/245/host-lifecycle numbers and an "Only local hosts are implemented" statement. It was **not** rewritten during this Milestone I pass. It is therefore currently one of the more accurate documents for HEAD, and the audit recommends keeping it that way and using it as a reference point when updating the stale documents.

---

## 6. Documents that look current (no change required on numbers alone)

These already reflect the current host-lifecycle reality at HEAD:

- `docs/HOST_LIFECYCLE_VERIFICATION_REPORT.md` — already reflects the host lifecycle work and already carries a `READY_FOR_EXTERNAL_VALIDATION`-style decision.
- `docs/HOST_LIFECYCLE_CI_VALIDATION.md` — already reflects 465 language.
- `README.md` — already reflects the current host-lifecycle section and numbers.

These are not "finished" documents, but they are not the source of the stale 340/409 / "no host contract suite" inconsistency.

---

## 7. Documentation audit conclusions

1. The repository's documentation set is large and mostly well-structured; the main defect is **inconsistent current numbers and stale capability framing**, not missing documents.
2. The **340/409** figures are stale where presented as current; the current core figure is **465**, with a reproduction caveat for this environment.
3. The **"no host contract suite / hosts cannot be declared operational"** framing is stale in substance; the precise true statement is that `tests/test_host_contract.py` is absent at HEAD while host lifecycle CLI and host-related tests are present.
4. Historical documents should be retained but clearly labeled as historical, not silently copied forward as current.
5. Corrections should be minimal and evidence-bound: update the stale numbers, update the stale capability framing, and add current-vs-historical labels. Do not invent new functionality, test outcomes, or remote-CI results.

---

## 8. Verification notes and limitations

- Test numbers cited as "current at HEAD" are **previously verified at this HEAD** and **could not be freshly re-verified in this environment**. They are not asserted as freshly re-run here.
- `tests/test_host_contract.py` is confirmed absent by `ls` in this environment; this is a real observation, not an inference.
- The provider list, version, and host-CLI help are freshly verified in this environment.
- No document was rewritten during this audit; this report records what should be reconciled and how, and leaves the actual edits to the final-report step if authorized.

---

*This audit separates historical, current, and unverified statements. It does not itself modify documents, and it does not claim any remote CI outcome.*
