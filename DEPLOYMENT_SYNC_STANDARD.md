# ENERGREX Deployment Sync Standard

This standard governs when a change counts as "done." It applies to every
change in this repo, not just the single-stock report.

## The rule: three-place sync, no exceptions

A change is only complete when all three of these are true:

1. **Local files** — the working tree has the change, verified working
   (tests pass, live-tested in the browser where applicable).
2. **Git** — committed and pushed to `origin/master`
   (`github.com/evolxd/energrex-multilens`).
3. **Deployed server** — the actual running instance the user or anyone
   else would hit (Render, or whatever replaces it) is serving the new
   code, not just an older snapshot.

**A change that only satisfies (1) and (2) is not done.** "I pushed it" is
not the same as "it's live." Don't report a task as complete, and don't
let a session end, without checking all three — or explicitly telling the
user which ones are outstanding and why.

## Why this exists

Local edits and git pushes happened constantly and reliably throughout the
2026-07-11 session (see `HANDOFF.md` §5 for the full commit history). The
Render deployment step, by contrast, was started (`render.yaml` added in
commit `1fc5f55`) but **never confirmed to have actually finished** —
the session moved on to other things (custom domain research, print bugs,
the editorial redesign, sidebar nav) without closing that loop. As of the
last update to this file, it is still unknown whether a live Render
instance exists and is serving current code. Assume it is **stale or
absent** until directly verified — do not assume "we pushed a bunch of
commits" means the deployed site reflects them.

## Verification checklist before calling anything "deployed" or "live"

- [ ] Confirm the actual deployment target exists (Render dashboard, or
  wherever this ends up) and is not in a failed/crashed state.
- [ ] Confirm the deployed commit hash matches (or is newer than) the
  latest commit you expect to be live — Render shows this in its deploy
  log; don't just assume the last push auto-deployed successfully.
- [ ] Actually load the deployed URL and spot-check it (not just localhost)
  before telling the user a feature is "live" for anyone but them running
  it locally.
- [ ] If the deployment target doesn't exist yet or is broken, say so
  explicitly — "committed and pushed, but not yet deployed" is a valid and
  honest status. Don't imply broader availability than actually exists.

## Cross-reference

See `HANDOFF.md` §4 ("Deployment status") for the current best-known state
of the Render setup, and §9 for it as a standing open TODO. Update both
this file's "why this exists" section and `HANDOFF.md` §4 together if the
deployment situation changes — don't let them drift out of sync with each
other the same way the three sync targets themselves can drift.
