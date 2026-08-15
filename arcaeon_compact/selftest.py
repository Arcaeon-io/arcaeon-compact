"""Self-test: golden receipt-digest vectors + the planted-drop fixture.

    python -m arcaeon_compact.selftest

Ships in the package rather than living in CI, so a stranger runs it on THEIR
machine and trusts their own output, not ours:

1. Golden vectors. The receipt digest is only a reliable signal if every
   environment computes the same one from the same content. The v1 vectors
   were frozen when the v1 schema was frozen (0.1.0, 2026-08-14) and NEVER
   change — `seal()` no longer produces v1 rows (see HIGH-1 below), but a
   stranger's environment must still reproduce the frozen v1 digest from the
   v1-shaped core, because that's what makes an EXISTING v1 ledger row
   trustworthy forever. The v2 vectors were frozen at the v2 schema freeze
   (0.1.2, 2026-08-15) alongside them. If your Python, your platform, or a
   future release computes anything else here, this command fails loudly —
   do not trust receipts it produces.

2. The planted drop. The whole product claim is "a compactor cannot claim
   nothing was dropped while dropping something." So the load-bearing test
   plants exactly that lie — record_kept(everything) while the shipped
   survivor is missing an item — and verify_receipt MUST catch it. The honest
   receipt over the same drop MUST pass. Both run here, every time.

3. HIGH-1 (2026-08-15): v1 never recorded `introduced.bytes`, so a receipt
   claiming an introduction (every real summarizer) left `post.bytes` only
   lower-bounded by self-consistency — an understated `dropped.bytes` could
   hide behind a claimed introduction and still self-verify with no content
   held. v2 records `introduced.bytes` for real (computed from the actual
   post-content given to `record_kept()`) so verify can pin `post.bytes`
   exactly, always. This section proves both halves: a v2 receipt catches
   the understatement-behind-an-introduction attack that v1 missed, AND a
   hand-built v1-shaped row (standing in for an already-sealed row from a
   pre-0.1.2 ledger) still verifies correctly under the v1 rule, honestly
   labeled `schema:"v1"`, `understatement_check:"truncation-only"` — old
   receipts are not orphaned by the fix.

Exit code 0 = every check passed.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from collections import Counter

from . import (CompactionReceipt, verify_receipt, _digest_item,
               _digest_content, _core_body, SCHEMA_V1, SCHEMA_V2)
from arcaeon_ledger import digest_json

# The fixture content. Covers all three item types (str, dict, bytes) so the
# frozen type rule (str -> UTF-8 raw-bytes, dict -> json-c14n, bytes -> raw)
# is enforced, not just documented.
PRE = ["the quick brown fox",
       {"role": "user", "text": "hello"},
       b"\x00\x01binary"]
POST = ["the quick brown fox", "summary: user said hello"]

# Frozen at the v1 schema freeze (arcaeon-compact:receipt:v1, 0.1.0,
# 2026-08-14). NEVER changes -- this is what an already-sealed v1 ledger
# row must still recompute to, forever, even though current code no longer
# MINTS v1 rows (see the legacy-row section of run(), below).
GOLDEN_RECEIPT_DIGEST_V1 = ("sha256:json-c14n:v1:"
    "9a0f4bb37b78f8433f52ae14603d20f1fc2413265e434beb9ad15613fc5947a4")
# Frozen at the v2 schema freeze (arcaeon-compact:receipt:v2, 0.1.2,
# 2026-08-15). What `seal()` actually produces now for this fixture.
GOLDEN_RECEIPT_DIGEST_V2 = ("sha256:json-c14n:v1:"
    "04f2e0c2b409212325e5aecbda8481119da9a5ffb6f3ede4d99ac8075f11203f")
GOLDEN_PRE_DIGEST = ("sha256:json-c14n:v1:"
    "0fa1adc5dd156a66d78b1e32a52632ce8b98fabc7dfe9d25813375e75ddc82a7")
GOLDEN_ITEMS = [
    ("str item (raw-bytes of UTF-8)", PRE[0], "sha256:raw-bytes:v1:"
     "9ecb36561341d18eb65484e833efea61edc74b84cf5e6ae1b81c63533e25fc8f"),
    ("dict item (json-c14n)", PRE[1], "sha256:json-c14n:v1:"
     "09a32d6caf7737200a34ee38c65c82446831be896aa5d5aa6b858729ee13beb1"),
    ("bytes item (raw-bytes)", PRE[2], "sha256:raw-bytes:v1:"
     "f8c1ccc7df7243da4740c5b7875f88b70e3f935d99c7b37a5f7239d152d994e6"),
]


def _build_legacy_v1_row(pre, post, *, compactor: str, method: str) -> dict:
    """Reconstruct exactly the row shape `seal()` produced pre-0.1.2: same
    fields, but `introduced` has no `bytes` key and `schema` is v1. Stands in
    for a row already sitting in someone's ledger from before this release --
    proves the fix doesn't strand it."""
    pre_digests, pre_sizes, pre_whole, pre_bytes = _digest_content(pre)
    post_digests, post_sizes, post_whole, post_bytes = _digest_content(post)
    remaining = Counter(post_digests)
    dropped, dropped_bytes = [], 0
    for d, n in zip(pre_digests, pre_sizes):
        if remaining[d] > 0:
            remaining[d] -= 1
        else:
            dropped.append(d)
            dropped_bytes += n
    introduced = sum(remaining.values())
    body = {
        "schema": SCHEMA_V1,
        "pre": {"count": len(pre_digests), "bytes": pre_bytes, "digest": pre_whole},
        "post": {"count": len(post_digests), "bytes": post_bytes, "digest": post_whole},
        "dropped": {"count": len(dropped), "bytes": dropped_bytes, "items": dropped},
        "introduced": {"count": introduced},  # v1 shape: no "bytes" key
        "compactor": compactor,
        "method": method,
    }
    row = dict(body)
    row["receipt_digest"] = digest_json(body)
    return row


def run() -> int:
    failures = 0

    print("== golden vectors (schema enforcement) ==")
    for name, item, want in GOLDEN_ITEMS:
        got = _digest_item(item)[0]
        ok = got == want
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            print(f"        want {want}\n        got  {got}")

    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / "receipts.jsonl"
        r = CompactionReceipt.open(PRE)
        r.record_kept(POST)
        row = r.seal(led, compactor="golden-fixture", method="test:v1")
        for name, got, want in [
                ("pre.digest vector", row["pre"]["digest"], GOLDEN_PRE_DIGEST),
                ("receipt_digest vector (v2 -- what seal() mints now)",
                 row["receipt_digest"], GOLDEN_RECEIPT_DIGEST_V2)]:
            ok = got == want
            failures += 0 if ok else 1
            print(f"  {'PASS' if ok else 'FAIL'}  {name}")
            if not ok:
                print(f"        want {want}\n        got  {got}")

        legacy_row = _build_legacy_v1_row(
            PRE, POST, compactor="golden-fixture", method="test:v1")
        ok = legacy_row["receipt_digest"] == GOLDEN_RECEIPT_DIGEST_V1
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  receipt_digest vector (v1 legacy -- "
              f"an already-sealed pre-0.1.2 row must still recompute to this, "
              f"forever, even though seal() no longer mints it)")
        if not ok:
            print(f"        want {GOLDEN_RECEIPT_DIGEST_V1}\n        got  {legacy_row['receipt_digest']}")

        print("== the planted drop (the load-bearing lie) ==")
        # Honest branch: the same drop, receipted truthfully, must pass fully.
        v = verify_receipt(row, PRE, POST)
        ok = v["ok"] and v["content"] == "match"
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  honest receipt -> "
              f"ok={v['ok']} content={v['content']!r} (must be True/'match')")

        # The lie: claim the survivor is EVERYTHING (nothing dropped), while
        # the actually-shipped survivor is missing the binary item.
        liar = CompactionReceipt.open(PRE)
        liar.record_kept(PRE)                      # "nothing was dropped"
        lied_row = liar.seal(led, compactor="liar", method="test:v1")
        shipped = PRE[:2]                          # ...something was.
        v = verify_receipt(lied_row, PRE, shipped)
        ok = (not v["ok"]) and v["content"] == "mismatch"
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  planted drop   -> "
              f"ok={v['ok']} content={v['content']!r} (must be False/'mismatch')")

        # And the lying row alone (no content) must still be SELF-consistent:
        # the receipt binds the claim; only content exposes the lie. Stating
        # this precisely is part of the product, so it is asserted, not hoped.
        v = verify_receipt(lied_row)
        ok = v["ok"] and v["content"] == "skipped"
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  lie w/o content -> "
              f"ok={v['ok']} (self-consistency cannot expose it: by design, "
              f"and said out loud)")

        print("== HIGH-1 (2026-08-15): v2 pins post.bytes exactly; v1 rows still verify ==")
        # A legacy (pre-0.1.2) v1 row, hand-built above, must still verify --
        # the fix doesn't strand receipts already sitting in someone's ledger.
        v = verify_receipt(legacy_row)
        ok = (v["ok"] and v["self_consistent"] and v["schema"] == "v1"
              and v["understatement_check"] == "truncation-only")
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  legacy v1 row still verifies -> "
              f"ok={v['ok']} schema={v['schema']!r} "
              f"understatement_check={v['understatement_check']!r}")

        # A real summarizer's honest v2 receipt: drops content, introduces a
        # short summary, and now gets the FULL (not truncation-only) check.
        pre_h1 = ["a" * 100, "b" * 400]          # summarizer drops the 400-byte turn
        post_h1 = ["a" * 100, "sum"]             # ...and introduces a 3-byte summary
        rc = CompactionReceipt.open(pre_h1)
        rc.record_kept(post_h1)
        honest_v2 = rc.seal(led, compactor="summarizer", method="llm-summary")
        v = verify_receipt(honest_v2)
        ok = (v["ok"] and v["schema"] == "v2"
              and v["understatement_check"] == "full"
              and honest_v2["introduced"]["bytes"] == 3)
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  honest v2 summarizer receipt -> "
              f"ok={v['ok']} schema={v['schema']!r} "
              f"understatement_check={v['understatement_check']!r} "
              f"introduced.bytes={honest_v2['introduced']['bytes']}")

        # The report's exact attack, replayed: understate dropped.bytes while
        # leaving introduced.bytes at the value the real record_kept() computed
        # (i.e. NOT solving the new equation to match) -- this is what a v1
        # row's self-consistency check could never see, because it never
        # looked at introduced.bytes at all. v2 catches it with no content.
        forged = json.loads(json.dumps(honest_v2))
        forged["dropped"] = {**forged["dropped"], "bytes": 1}
        forged["receipt_digest"] = digest_json(_core_body(forged))
        v = verify_receipt(forged)
        ok = (not v["ok"]) and (not v["self_consistent"]) and v["schema"] == "v2"
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  understated dropped.bytes behind a "
              f"real introduction is caught by v2 self-consistency (no content "
              f"needed) -> ok={v['ok']} self_consistent={v['self_consistent']}")

    print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
