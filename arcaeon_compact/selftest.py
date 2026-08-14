"""Self-test: golden receipt-digest vectors + the planted-drop fixture.

    python -m arcaeon_compact.selftest

Ships in the package rather than living in CI, so a stranger runs it on THEIR
machine and trusts their own output, not ours:

1. Golden vectors. The receipt digest is only a reliable signal if every
   environment computes the same one from the same content. These constants
   were frozen when the v1 schema was frozen (0.1.0, 2026-08-14). If your
   Python, your platform, or a future release computes anything else, this
   command fails loudly — do not trust receipts it produces.

2. The planted drop. The whole product claim is "a compactor cannot claim
   nothing was dropped while dropping something." So the load-bearing test
   plants exactly that lie — record_kept(everything) while the shipped
   survivor is missing an item — and verify_receipt MUST catch it. The honest
   receipt over the same drop MUST pass. Both run here, every time.

Exit code 0 = every check passed.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from . import CompactionReceipt, verify_receipt, _digest_item

# The fixture content. Covers all three item types (str, dict, bytes) so the
# frozen type rule (str -> UTF-8 raw-bytes, dict -> json-c14n, bytes -> raw)
# is enforced, not just documented.
PRE = ["the quick brown fox",
       {"role": "user", "text": "hello"},
       b"\x00\x01binary"]
POST = ["the quick brown fox", "summary: user said hello"]

# Frozen at schema freeze (arcaeon-compact:receipt:v1, 0.1.0, 2026-08-14).
# These never change. A v2 schema gets NEW vectors alongside these.
GOLDEN_RECEIPT_DIGEST = ("sha256:json-c14n:v1:"
    "9a0f4bb37b78f8433f52ae14603d20f1fc2413265e434beb9ad15613fc5947a4")
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
                ("receipt_digest vector", row["receipt_digest"],
                 GOLDEN_RECEIPT_DIGEST)]:
            ok = got == want
            failures += 0 if ok else 1
            print(f"  {'PASS' if ok else 'FAIL'}  {name}")
            if not ok:
                print(f"        want {want}\n        got  {got}")

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

    print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
