"""Tests for arcaeon-compact — the product claim is "a compactor cannot claim
nothing was dropped while dropping something," so the negative tests (the lie
is CAUGHT) are the load-bearing ones.
Run: python test_compact.py
"""
import json
import tempfile
from pathlib import Path

from arcaeon_ledger import Ledger
from arcaeon_compact import CompactionReceipt, verify_receipt, SCHEMA

PRE = ["turn 1: hello", {"role": "assistant", "text": "turn 2"},
       b"turn 3 raw", "turn 4: the important one"]
POST = ["turn 1: hello", "summary of turns 2-3"]


def _sealed(tmp, pre=PRE, post=POST, **kw):
    r = CompactionReceipt.open(pre)
    r.record_kept(post)
    kw.setdefault("compactor", "test-compactor")
    kw.setdefault("method", "unit-test")
    return r.seal(Path(tmp) / "receipts.jsonl", **kw)


def test_happy_path_full_verify():
    with tempfile.TemporaryDirectory() as d:
        row = _sealed(d)
        assert row["schema"] == SCHEMA
        assert row["pre"]["count"] == 4 and row["post"]["count"] == 2
        assert row["dropped"]["count"] == 3      # turns 2, 3, 4
        assert row["introduced"]["count"] == 1   # the summary line
        assert len(row["dropped"]["items"]) == 3
        v = verify_receipt(row, PRE, POST)
        assert v["ok"] and v["self_consistent"] and v["content"] == "match", v
    print("PASS happy path: open/record/seal + full content verify")


def test_row_lands_on_ledger_chain():
    with tempfile.TemporaryDirectory() as d:
        row = _sealed(d)
        led = Ledger(Path(d) / "receipts.jsonl")
        assert led.verify().ok
        (stored,) = list(led)
        assert stored == row, "returned row must equal the chained row"
    print("PASS sealed row equals the chained ledger row; chain verifies")


def test_planted_drop_is_caught():
    # The lie: record_kept(everything), ship a survivor missing item 4.
    with tempfile.TemporaryDirectory() as d:
        row = _sealed(d, post=PRE)                 # claims nothing dropped
        v = verify_receipt(row, PRE, PRE[:3])      # reality: one item gone
        assert not v["ok"] and v["content"] == "mismatch", v
        assert any("drop-manifest disagrees" in n for n in v["notes"]), v["notes"]
    print("PASS planted drop caught (claimed 0 dropped, reality says 1)")


def test_row_edit_caught_without_content():
    # Shave one dropped item + fix the counts to stay arithmetic-consistent:
    # the receipt_digest must still catch the edit, even outside the ledger.
    with tempfile.TemporaryDirectory() as d:
        row = json.loads(json.dumps(_sealed(d)))
        row["dropped"]["items"] = row["dropped"]["items"][:-1]
        row["dropped"]["count"] -= 1
        row["introduced"]["count"] -= 1  # keep post - introduced == pre - dropped
        v = verify_receipt(row)
        assert not v["ok"] and not v["self_consistent"], v
        assert any("receipt_digest" in n for n in v["notes"]), v["notes"]
    print("PASS in-row edit caught by receipt_digest (no content, no ledger)")


def test_explicit_dropped_reconciled():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "r.jsonl"
        # honest explicit claim seals fine
        r = CompactionReceipt.open(PRE)
        r.record_kept(POST)
        r.record_dropped([PRE[1], PRE[2], PRE[3]])
        row = r.seal(p, compactor="c", method="m")
        assert verify_receipt(row, PRE, POST)["ok"]
        # understating the drop refuses to seal
        r = CompactionReceipt.open(PRE)
        r.record_kept(POST)
        r.record_dropped([PRE[1]])                 # claims 1, reality 3
        try:
            r.seal(p, compactor="c", method="m")
            assert False, "inconsistent record_dropped sealed!"
        except ValueError as e:
            assert "disagrees" in str(e)
    print("PASS explicit record_dropped reconciled; inconsistent claim refused")


def test_duplicates_counted_as_multiset():
    pre = ["dup", "dup", "unique"]
    with tempfile.TemporaryDirectory() as d:
        row = _sealed(d, pre=pre, post=["dup"])    # kept ONE copy of two
        assert row["dropped"]["count"] == 2        # the other dup + unique
        v = verify_receipt(row, pre, ["dup"])
        assert v["ok"], v
    print("PASS duplicate items counted as a multiset (kept 1 of 2 drops 1)")


def test_dropped_bytes_totals():
    pre = ["abcd", b"\x00" * 10]
    with tempfile.TemporaryDirectory() as d:
        row = _sealed(d, pre=pre, post=["abcd"])
        assert row["pre"]["bytes"] == 14 and row["dropped"]["bytes"] == 10, row
    print("PASS byte totals: pre=14, dropped=10 for a 4-char str + 10 raw bytes")


def test_seal_requires_record_kept_and_is_final():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "r.jsonl"
        r = CompactionReceipt.open(PRE)
        try:
            r.seal(p, compactor="c", method="m")
            assert False, "sealed without record_kept!"
        except ValueError:
            pass
        r.record_kept([])                          # everything dropped: valid
        row = r.seal(p, compactor="c", method="m")
        assert row["dropped"]["count"] == 4 and row["post"]["count"] == 0
        for fn in (lambda: r.record_kept([]), lambda: r.record_dropped([]),
                   lambda: r.seal(p, compactor="c", method="m")):
            try:
                fn()
                assert False, "mutated a sealed receipt!"
            except RuntimeError:
                pass
    print("PASS seal requires record_kept; sealed receipt is immutable")


def test_unknown_schema_refused():
    with tempfile.TemporaryDirectory() as d:
        row = dict(_sealed(d))
        row["schema"] = "arcaeon-compact:receipt:v99"
        v = verify_receipt(row)
        assert not v["ok"] and any("unknown schema" in n for n in v["notes"])
    print("PASS unknown schema refused, never half-verified")


def test_partial_content_pre_only():
    with tempfile.TemporaryDirectory() as d:
        row = _sealed(d)
        v = verify_receipt(row, pre_content=PRE)
        assert v["ok"] and v["content"] == "match", v
        v = verify_receipt(row, pre_content=PRE[:2])   # wrong pre
        assert not v["ok"] and v["content"] == "mismatch"
    print("PASS pre-only verify works; wrong pre-content mismatches")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED")
