# TODO — cross-cutting

Items that touch both codebases, or neither, or the engagement itself. Domain-specific items go in `../raspberry-pi/docs/TODO.md` or `https://github.com/rodgpt/Dashboard-Detector/blob/main/docs/TODO.md`.

Add TODOs the moment they occur. Context compression eats undocumented ones.

Categories: `[Contract]` `[Sec]` `[Ops]` `[Client]` `[Docs]` `[Data]`

---

## Pending

### Blocking, send today

- [ ] **[Ops] Azure access request** — Contributor on the resource group plus Storage Blob Data Owner, or Owner. Phase 4 is 24 to 28 hours of the engagement and none of it can start without this. Put in writing that the delivery date moves with the grant date.

- [ ] **[Sec] Twilio console access, then rotate** — The token is exposed in `legacy/build-artifacts/` twice and on a git remote (it is out of `raspberry-pi/src/` since Phase 1). *Client decision 2026-08-13: keep using the exposed token until console access arrives.* It lives in the gitignored `raspberry-pi/oceankind.env`, flagged for rotation in the file itself. On rotation: replace both values there, redeploy the env file, delete `legacy/build-artifacts/`. Still the single most urgent action in the project.

- [ ] **[Ops] Three field questions** — Does the power chart render (answers D-002, F-16). Which user runs the service (answers D-010, sets F-03 urgency). Which ADC is installed (answers D-009). All one-line answers from whoever can reach the unit. All three change what gets built first.

### Client-facing

- [ ] **[Client] Correct the write-SAS claim in `SYSTEM_REVIEW.md`** — §4.3 says the dashboard stores a write-capable SAS in `localStorage`. It does not; the constant is dead. See X-01. Correct it before the client's side finds it, because one falsified claim discounts the twelve that are true.

- [ ] **[Client] Reconcile the deaf-window contradiction** — `IMPROVEMENT_REPORT.md` §3.1 says 30 to 60 percent of blasts may be missed. `SYSTEM_REVIEW.md` §3.1 says the miss rate is low. Both were delivered. Phase 1 instrumentation produces the real number; send it and retire both claims.

- [ ] **[Client] Tell them the historical counts undercount** — Once D-008 lands, the manifest becomes a truthful record and the numbers change. Any blast-frequency figure derived from the current history is low, and worst during the episodes that matter most. Better said by us now than discovered by them later.

- [ ] **[Client] Cap meetings in writing** — "Las reuniones y correcciones son parte de las horas declaradas" is unbounded against a small total. Propose four hours included, further sessions billed. Protects roughly 15 percent of the engagement without changing the price.

### Structural

- [ ] **[Contract] `schema_version` on every blob** — Cheapest possible insurance. Without it, the Phase 4 layout migration is a flag day; with it, the dashboard can warn instead of break. Do it in Phase 1 while the contract is still simple.

- [ ] **[Data] Nothing archives the manifest** — 5000-entry cap, truncated from the tail, no backup. Detection history is being destroyed on a rolling basis. For a conservation record that is worse than a bug. Even a periodic copy to a dated blob would do until the backend exists.

- [ ] **[Data] Classifier range is unvalidated** — `IMPROVEMENT_REPORT.md` §2.5 asks whether the model detects distant, attenuated blasts at all, and nobody has tested it against labelled distant recordings. Arguably the largest open question in the project, not contracted, and it needs data that may not exist. Raise it with the client rather than leaving it in a report nobody re-reads.

- [ ] **[Docs] Settle the documentation language** — D-012. Internal docs are English, the interface and client documents are Spanish, and the current mix is the least defensible option because nobody knows which to write next. Cheap to decide, expensive to keep deferring.

- [ ] **[Ops] No monitoring of the monitor** — If the Pi stops, the only signal is the dashboard going stale after three minutes, and only if someone is looking. An uptime check against `status.json` freshness with an alert would be a small amount of work for a large amount of confidence, and it does not need the backend.

---

## Done

- [x] **[Docs] Restructure the repository** DONE (2026-08-02) — Four folders, per-domain roots, reversal manifest in `MOVES.md`.
- [x] **[Docs] Consolidate the audits into a verified register** DONE (2026-08-02) — `FINDINGS.md`, twenty entries, each checked against source.
- [x] **[Contract] Extract the blob schemas** DONE (2026-08-02) — `DATA-CONTRACT.md`. The seam was previously undocumented.
