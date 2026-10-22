# Validation — 22 October 2026

Environment: Windows, Python 3.12, Node.js JavaScript syntax check, Codex in-app Chromium browser. Synthetic equipment and faults only. No external Python dependencies installed.

## Automated verification

`python -m unittest discover -s tests -v`: **13 tests passed**.

- Create equipment, reject duplicate IDs regardless of case, edit location without losing history.
- Save faults, transition Open → In progress → Resolved → Open and retain every note.
- Reopen SQLite in a new Store instance and verify persisted data.
- Reject stale updates from another tab, missing records, blank notes, invalid priorities and impossible dates.
- Validate UTF-8/Unicode and multiline CSV; reject malformed headers/rows and roll back duplicate imports atomically.
- Export/restore complete JSON; reject nonempty target, orphan references, missing/inconsistent history, invalid records and oversized integer IDs without partial writes.
- Neutralize formula-like CSV cells while retaining original database text.
- Exercise real HTTP equipment → fault → resolved → CSV/JSON exports.
- Reject foreign origins, unexpected hosts and missing/incorrect session tokens; restrict static files to the three public frontend assets.
- Reject malformed JSON, wrong content types and unknown routes.

`node --check web/app.js`: passed.

Clean-download check: created a ZIP from the committed files, extracted into a fresh folder with no database or dependencies, ran all 13 tests again successfully, and started the extracted app from a different working directory. Verified empty initial records, HTML response and creation of its default database beside the extracted source. Runtime databases/backups were absent from the ZIP.

The first sandboxed attempt could not access the system temp directory / localhost sockets. Re-ran with normal local access; all tests passed. These were environment restrictions, not skipped tests.

## Browser checks performed

- Empty first run: zero equipment/faults and useful empty state.
- Added `LAB2-PC07` through the actual equipment form and verified the saved card.
- Logged a High-priority network fault with a past due date; active and overdue counts became one.
- Opened details, marked the fault Resolved with a troubleshooting note; active/overdue counts became zero.
- Filtered to Resolved and verified the matching fault; refreshed and confirmed persistence.
- Verified weekly summary showed one new fault and one fault marked resolved.
- Selected the supplied example CSV containing the already existing asset; import reported cancellation and equipment count stayed one.
- Downloaded full JSON backup through the browser, started a second empty database on port 8769, selected that downloaded file and restored it through the UI. Equipment count, resolved fault and weekly summary recovered correctly.
- Browser console error snapshot was empty after recovery.
- Desktop layout inspected visually at the browser's actual 1280px viewport.

The browser's viewport override did not change its actual 1280px width, so **phone-width rendering is not independently verified**. Responsive CSS is included. Print dialog/PDF output and other operating systems are not independently verified in this local run.

The Windows/Linux and Python 3.10/3.12/3.13 CI matrix was submitted, but GitHub did not start its jobs: the authenticated check annotations say the owner's account is locked due to a billing issue. This is a runner/account blocker, not a failed test assertion. Local and clean-ZIP tests passed; the remote matrix is **not verified**. [Affected run](https://github.com/ismailiqbal2773/LabDesk/actions/runs/39719362747). No billing settings or purchases were changed.

## Release policy

Exclude runtime databases, backups, credentials, dependency caches and private school data. README documents exact startup, features, backup/restore, import format, limitations and troubleshooting. No claim of universal bug-free operation or school deployment is made.
