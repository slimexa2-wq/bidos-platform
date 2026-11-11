# Synthetic UX regressions

These tests exercise real React pages against in-memory demonstration responses. They never import the API, start schedulers, read application data, or open real Excel/NAS files. Both servers bind only to loopback. Fixture identity is checked directly and through the UI proxy before mutations.

Install the frontend's locked dependencies in `apps/web` and provide an existing Playwright module and Chromium executable. No new production dependency is required. Run each server in its own terminal from the repository root:

```sh
node tests/frontend/fixture-server.cjs
node apps/web/node_modules/vite/bin/vite.js --config tests/frontend/vite.fixture.config.mjs --configLoader native
```

Then run:

```sh
PLAYWRIGHT_MODULE=/path/to/playwright CHROMIUM_PATH=/path/to/chromium node tests/frontend/ux-regression.cjs
PLAYWRIGHT_MODULE=/path/to/playwright CHROMIUM_PATH=/path/to/chromium node tests/frontend/draft-save-races.cjs
PLAYWRIGHT_MODULE=/path/to/playwright CHROMIUM_PATH=/path/to/chromium node tests/frontend/draft-storage-failure.cjs
PLAYWRIGHT_MODULE=/path/to/playwright CHROMIUM_PATH=/path/to/chromium node tests/frontend/historical-project-links.cjs
PLAYWRIGHT_MODULE=/path/to/playwright CHROMIUM_PATH=/path/to/chromium node tests/frontend/drawer-route-drafts.cjs
```

The test exits nonzero on failure. It covers project/route/reload draft recovery, conflict retention, filtered selection, follow-up completion/history/rescheduling without dismissing bond reminders, drawer focus/Escape/discard protection, error/loading/empty/stale recovery, mobile automation navigation and filter return, and first-viewport task visibility at 1440×900 and 390×844. A stale snapshot check waits for the existing 30-second read interval.

The separate draft-save race test delays save responses across project switches and verifies that newer work/material drafts survive, successful completion updates a remounted editor, concurrent owner/note edits retain the completion history, and preserved edits can be saved with the updated version. It also checks material text entered while a save is pending. The fixture enforces work version/digest conflicts.

The storage-failure test rejects draft writes and verifies that native unload protection remains active after leaving the editor and until all unpersisted drafts are discarded.

The historical-project test verifies explicit historical links, history-toggle explanation, manual filtering, and draft recovery.

The drawer-route test verifies blind and refund drafts across browser Back/Forward and reload, explicit discard, a blind save confined to its selected synthetic object, a delayed save updating the current drawer after Back/Forward, and an older save response preserving a newer save to another project. Refund drafts resume when “已退回” is reopened.

Screenshots and JSON results go to the system temporary directory (`bidos-ux-evidence`) unless `BIDOS_EVIDENCE_DIR` is set. Request logs go to the system temporary directory unless `BIDOS_FIXTURE_LOG` is set. No runtime evidence belongs in source control.
