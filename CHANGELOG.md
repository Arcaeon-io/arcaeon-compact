# Changelog — arcaeon-compact

## 0.1.2 — 2026-08-15

Fix from the 2026-08-15 adversarial scrutiny pass
(`projects/online_business/SCRUTINY_COMPACT_2026-08-15.md` in the Velouria repo).

- **Fixed (HIGH-1) — the content-free byte-understatement catch was void
  whenever an introduction was claimed.** v1 never recorded
  `introduced.bytes`, so a receipt claiming an introduction (every real
  `method="llm-summary"` summarizer, the primary advertised use case) left
  `post.bytes` only lower-bounded by `verify_receipt`'s self-consistency
  check — a receipt could claim it dropped 1 byte out of 500 (really 500)
  and still self-verify with zero content held. Reproduced from the report
  and now caught:
  ```
  liar (dropped.bytes=1, post.bytes inflated to 599):
  {'ok': False, 'self_consistent': False, ...,
   'notes': ['bytes do not reconcile: pre.bytes - dropped.bytes + '
             'introduced.bytes = 502, got post.bytes 599']}
  ```
  **Mechanics — schema v2:** `CompactionReceipt.seal()` now computes and
  records `introduced.bytes` (built from a size-by-digest lookup over the
  real post-content given to `record_kept()` — not a number a caller can
  hand-wave) and writes `schema: "arcaeon-compact:receipt:v2"`.
  `verify_receipt` asserts `post.bytes == pre.bytes - dropped.bytes +
  introduced.bytes` **exactly, unconditionally** for v2 rows (previously
  exact only when `introduced.count == 0`).

- **v1 compatibility, by design, not by accident.** `verify_receipt` reads
  both schemas. A receipt sealed before 0.1.2 (no `introduced.bytes`) still
  verifies — same digests, same counts, nothing stranded — but the result
  now says which rule applied: `out["schema"]` is `"v1"` or `"v2"`, and
  `out["understatement_check"]` is `"truncation-only"` (v1 — pinned only
  when nothing was introduced, the honest residual) or `"full"` (v2 —
  pinned always). A hand-built legacy v1 row still recomputes to the exact
  frozen 0.1.0 golden `receipt_digest` vector — proven in
  `arcaeon_compact/selftest.py`, not just asserted.

- **README updated** ("Verification, honestly scoped" + the anatomized
  receipt example) to state the v2 guarantee and the v1 residual plainly,
  including that self-consistency — v1 or v2 — still can't *prove* a claim
  without content; v2 narrows the forgeable range, it doesn't eliminate it.

- **New regression tests** (`test_compact.py`): `seal()` mints v2 with real
  `introduced.bytes`; the report's exact liar reproduction is caught under
  v2 with no content; a hand-built legacy v1 row still verifies labeled
  `truncation-only`; the v1 residual (understated `dropped.bytes` +
  inflated `post.bytes` behind an introduction) is documented as still
  passing v1 self-consistency, honestly, on purpose. `arcaeon_compact
  /selftest.py` gained a matching golden-vector split (v1 legacy digest,
  frozen forever + a new v2 digest) and a live HIGH-1 demonstration section.
  Full suite: 17/17 (`test_compact.py`, was 13), selftest `ALL CHECKS PASSED`.

- No change to LOW-1 (dict-shape crashes on `open()`) or LOW-2 (str/bytes
  digest collision) — both were explicitly deferred in the source audit to
  keep that pass minimal, and this pass's brief scoped to HIGH-1 only.

## 0.1.1 and earlier

Pre-CHANGELOG history. See git log and the 2026-08-15 audit for the
byte-arithmetic self-consistency additions and the README digest-privacy
wording pass that preceded this release.
