"""arcaeon_compact — tamper-evident receipts for context compaction.

When an agent compacts context — summarizes a conversation, prunes memory,
truncates history — nothing, today, proves what was dropped. The summary says
"nothing important was lost" and you take its word. A CompactionReceipt fixes
the provable part: a hash-chained record binding

  - a digest of the FULL pre-compaction content,
  - a digest of the post-compaction SURVIVOR,
  - a drop-manifest: per-dropped-item digests + count + byte totals
    (digests only, NEVER content — privacy by construction),
  - the compactor's identity string + method label,
  - a timestamp,

appended as one row to an `arcaeon-ledger` chain, so the receipt itself is
tamper-evident and ordered against every other receipt.

Three calls:

    from arcaeon_compact import CompactionReceipt, verify_receipt

    receipt = CompactionReceipt.open(pre_content)     # list[str | bytes | dict]
    receipt.record_kept(post_content)                 # dropped inferred by digest
    row = receipt.seal("receipts.jsonl", compactor="summarizer-v2",
                       method="llm-summary")

    verify_receipt(row)                               # self-consistency, always
    verify_receipt(row, pre_content, post_content)    # recompute + compare

WHAT IT PROVES, AND WHAT IT DOESN'T — the boundary is the product:

  1. It proves WHAT was dropped, never that dropping was WISE. The receipt has
     no opinion on salience. A compactor that keeps the pleasantries and drops
     the wire-transfer instructions gets a perfectly valid receipt saying
     exactly that.
  2. It proves the compactor's CLAIM about its inputs, not that the inputs were
     complete (the gateway problem). If content was withheld before `open()`
     ever saw it, the receipt faithfully notarizes the partial view. The
     receipt binds what crossed the gate, not what existed behind it.
  3. Digests only means content is NOT recoverable from the receipt. Feature:
     dropped secrets don't leak into the audit trail. Limitation: you can
     prove an item you still HOLD was dropped (hash it, find it in the
     manifest); you cannot resurrect an item you lost.

Stdlib + arcaeon-ledger only. MIT.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from arcaeon_ledger import Ledger, digest_bytes, digest_json

__version__ = "0.1.0"
__all__ = ["CompactionReceipt", "verify_receipt", "SCHEMA"]

# The receipt schema label, frozen at v1. A future shape change mints v2;
# old rows keep v1 forever, so a schema drift never reads history as tampered.
SCHEMA = "arcaeon-compact:receipt:v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest_item(item: Any) -> tuple[str, int]:
    """(self-describing digest, byte length) for one content item.

    The type rule is part of the v1 schema — frozen, so a stranger can
    reproduce any manifest entry from content they hold:

      bytes / bytearray -> raw-bytes digest of the bytes, as-is
      str               -> raw-bytes digest of the UTF-8 encoding
      anything else     -> json-c14n digest of the canonicalized value
                           (arcaeon-ledger's pinned recipe: sorted keys,
                           compact separators, UTF-8, NaN rejected)

    Digests are always self-describing (`sha256:<recipe>:<ver>:<hex>`) —
    never a bare hex hash — so each manifest entry carries its own recipe.
    """
    if isinstance(item, (bytes, bytearray)):
        b = bytes(item)
        return digest_bytes(b), len(b)
    if isinstance(item, str):
        b = item.encode("utf-8")
        return digest_bytes(b), len(b)
    canon = json.dumps(item, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False).encode("utf-8")
    return digest_json(item), len(canon)


def _digest_content(items: Iterable[Any]) -> tuple[list[str], list[int], str, int]:
    """Digest a content list: (per-item digests, per-item bytes, whole, total).

    The WHOLE digest is `digest_json` over the ordered list of per-item digest
    strings — a digest of digests. Deterministic, order-sensitive, and
    recomputable from content alone, without ever storing content.
    """
    digests: list[str] = []
    sizes: list[int] = []
    for it in items:
        d, n = _digest_item(it)
        digests.append(d)
        sizes.append(n)
    return digests, sizes, digest_json(digests), sum(sizes)


def _well_formed(digest: str) -> bool:
    """A self-describing digest has exactly 4 colon-parts with a hex tail."""
    parts = digest.split(":") if isinstance(digest, str) else []
    if len(parts) != 4 or not all(parts):
        return False
    try:
        int(parts[3], 16)
    except ValueError:
        return False
    return True


class CompactionReceipt:
    """One compaction event, opened on the full content, sealed to a ledger.

    Lifecycle: `open(pre)` -> `record_kept(post)` [-> `record_dropped(items)`]
    -> `seal(...)`. After seal the receipt is immutable; recording or sealing
    again raises. `record_dropped` is optional — the drop set is inferred as
    pre-minus-kept by digest (multiset, duplicates counted); if you do record
    it explicitly, seal() reconciles your claim against the inference and
    refuses to notarize a receipt that disagrees with itself.
    """

    def __init__(self, *_a: Any, **_k: Any):
        raise TypeError("use CompactionReceipt.open(pre_content)")

    @classmethod
    def open(cls, pre_content: Sequence[Any]) -> "CompactionReceipt":
        """Digest every pre-compaction item + the whole; start the receipt."""
        self = object.__new__(cls)
        self._pre_digests, self._pre_sizes, self._pre_whole, self._pre_bytes = \
            _digest_content(pre_content)
        self._opened_at = _now_iso()
        self._post: tuple[list[str], list[int], str, int] | None = None
        self._dropped_explicit: list[str] | None = None
        self._sealed_row: dict | None = None
        return self

    def _require_unsealed(self) -> None:
        if self._sealed_row is not None:
            raise RuntimeError("receipt already sealed; open a new one")

    def record_kept(self, post_content: Sequence[Any]) -> None:
        """Digest the post-compaction survivor content."""
        self._require_unsealed()
        self._post = _digest_content(post_content)

    def record_dropped(self, items: Sequence[Any]) -> None:
        """Explicitly claim the dropped items (optional; else inferred).

        seal() checks this claim against pre-minus-kept and raises on any
        disagreement — the receipt never notarizes an inconsistent claim.
        """
        self._require_unsealed()
        self._dropped_explicit = [_digest_item(it)[0] for it in items]

    def seal(self, ledger_path: str | Path, *, compactor: str,
             method: str) -> dict:
        """Append the receipt row to an arcaeon-ledger chain; return the row.

        `compactor` is the identity string of whatever did the compacting
        (an agent id, a model name, a service); `method` labels how
        ("llm-summary", "fifo-truncate", "salience-prune", ...). Both are
        claims, bound tamper-evidently — the chain proves they weren't edited
        after the fact, not that they were true when written.
        """
        self._require_unsealed()
        if self._post is None:
            raise ValueError(
                "seal() before record_kept() — record the survivor first "
                "(an empty list is a valid survivor: everything was dropped)")
        post_digests, _post_sizes, post_whole, post_bytes = self._post

        # Inferred drop set: pre minus kept, as a multiset over digests, in
        # pre order. Duplicates count: keeping one copy of a twice-seen item
        # still drops the other.
        remaining = Counter(post_digests)
        dropped: list[str] = []
        dropped_bytes = 0
        for d, n in zip(self._pre_digests, self._pre_sizes):
            if remaining[d] > 0:
                remaining[d] -= 1
            else:
                dropped.append(d)
                dropped_bytes += n
        # Whatever the survivor holds that pre never did was INTRODUCED by the
        # compactor (the summary text itself, typically).
        introduced = sum(remaining.values())

        if self._dropped_explicit is not None and \
                Counter(self._dropped_explicit) != Counter(dropped):
            raise ValueError(
                "record_dropped() claim disagrees with pre-minus-kept "
                f"(claimed {len(self._dropped_explicit)} item(s), inferred "
                f"{len(dropped)}); refusing to seal an inconsistent receipt")

        body = {
            "schema": SCHEMA,
            "pre": {"count": len(self._pre_digests), "bytes": self._pre_bytes,
                    "digest": self._pre_whole},
            "post": {"count": len(post_digests), "bytes": post_bytes,
                     "digest": post_whole},
            "dropped": {"count": len(dropped), "bytes": dropped_bytes,
                        "items": dropped},
            "introduced": {"count": introduced},
            "compactor": compactor,
            "method": method,
        }
        row = dict(body)
        row["kind"] = "compaction_receipt"
        row["opened_at"] = self._opened_at
        # The receipt digest covers the deterministic core (body) only — not
        # timestamps or the chain — so it is reproducible from content alone
        # and a receipt row stays checkable even copied OUT of its ledger.
        row["receipt_digest"] = digest_json(body)
        row["ts"] = _now_iso()
        row["chain"] = Ledger(ledger_path).append(row)
        self._sealed_row = row
        return row


def _core_body(row: dict) -> dict:
    """The deterministic core the receipt_digest covers."""
    return {k: row.get(k) for k in
            ("schema", "pre", "post", "dropped", "introduced",
             "compactor", "method")}


def verify_receipt(row: dict, pre_content: Sequence[Any] | None = None,
                   post_content: Sequence[Any] | None = None) -> dict:
    """Verify a receipt row. Self-consistency always; recompute if given content.

    Self-consistency (no content needed): schema known, every digest
    well-formed, the counts add up (pre = kept + dropped, post = kept +
    introduced), the manifest length matches its count, and the
    receipt_digest reproduces from the row's own core — so an edited row is
    caught even when it's been copied out of its ledger. (Inside a ledger the
    chain catches the same edit; this check travels with the row.)

    With `pre_content` and/or `post_content`: recompute the digests from the
    content you hold and compare. With BOTH, the drop set itself is
    recomputed (pre minus post, by digest) and compared against the manifest
    — a receipt that claims nothing was dropped while something was, fails
    here by construction.

    Returns:
        {"ok": bool,                       # self_consistent AND content didn't mismatch
         "self_consistent": bool,
         "content": "skipped" | "match" | "mismatch",
         "notes": [<str>, ...]}

    What a pass MEANS, exactly: the row is internally coherent and, if you
    provided content, that content reproduces the claim. It does not mean the
    drop was wise, and it does not mean the pre-content the compactor showed
    the receipt was everything that existed (the gateway problem).
    """
    notes: list[str] = []
    out = {"ok": False, "self_consistent": False, "content": "skipped",
           "notes": notes}

    # --- self-consistency ---------------------------------------------------
    sc = True
    if row.get("schema") != SCHEMA:
        notes.append(f"unknown schema {row.get('schema')!r} — cannot verify")
        return out
    try:
        pre, post, dropped = row["pre"], row["post"], row["dropped"]
        introduced = row["introduced"]
        counts = (pre["count"], post["count"], dropped["count"],
                  introduced["count"], pre["bytes"], post["bytes"],
                  dropped["bytes"])
        manifest = dropped["items"]
    except (KeyError, TypeError) as e:
        notes.append(f"malformed receipt row: missing {e}")
        return out
    if not all(isinstance(c, int) and c >= 0 for c in counts):
        sc = False
        notes.append("counts/bytes must be non-negative integers")
    for label, d in [("pre.digest", pre.get("digest")),
                     ("post.digest", post.get("digest")),
                     ("receipt_digest", row.get("receipt_digest"))] + \
                    [(f"dropped.items[{i}]", d) for i, d in enumerate(manifest)]:
        if not _well_formed(d):
            sc = False
            notes.append(f"{label} is not a well-formed self-describing digest")
    if len(manifest) != dropped["count"]:
        sc = False
        notes.append(f"manifest length {len(manifest)} != dropped.count "
                     f"{dropped['count']}")
    kept = pre["count"] - dropped["count"]
    if kept < 0:
        sc = False
        notes.append("dropped.count exceeds pre.count")
    elif post["count"] - introduced["count"] != kept:
        sc = False
        notes.append("counts do not reconcile: post - introduced != pre - dropped")
    if row.get("receipt_digest") != digest_json(_core_body(row)):
        sc = False
        notes.append("receipt_digest does not reproduce from the row's core "
                     "— the row was altered")
    out["self_consistent"] = sc

    # --- content recomputation ----------------------------------------------
    mismatch = False
    checked = False
    pre_digests = pre_sizes = None
    if pre_content is not None:
        checked = True
        pre_digests, pre_sizes, whole, total = _digest_content(pre_content)
        for label, got, want in [("pre.digest", whole, pre.get("digest")),
                                 ("pre.count", len(pre_digests), pre["count"]),
                                 ("pre.bytes", total, pre["bytes"])]:
            if got != want:
                mismatch = True
                notes.append(f"{label}: recomputed {got!r} != claimed {want!r}")
        # every manifest entry must exist in pre (multiset)
        avail = Counter(pre_digests)
        for d in manifest:
            if avail[d] > 0:
                avail[d] -= 1
            else:
                mismatch = True
                notes.append(f"manifest digest {d[:40]}… not found in "
                             "pre_content — receipt claims a drop of an item "
                             "the pre-content never contained")
    post_digests = None
    if post_content is not None:
        checked = True
        post_digests, _sizes, whole, total = _digest_content(post_content)
        for label, got, want in [("post.digest", whole, post.get("digest")),
                                 ("post.count", len(post_digests), post["count"]),
                                 ("post.bytes", total, post["bytes"])]:
            if got != want:
                mismatch = True
                notes.append(f"{label}: recomputed {got!r} != claimed {want!r}")
    if pre_digests is not None and post_digests is not None:
        # Recompute the drop set from BOTH sides and hold it against the
        # manifest — the planted-drop catch. A compactor that dropped item X
        # while claiming it dropped nothing fails exactly here.
        remaining = Counter(post_digests)
        actual: list[str] = []
        actual_bytes = 0
        for d, n in zip(pre_digests, pre_sizes):
            if remaining[d] > 0:
                remaining[d] -= 1
            else:
                actual.append(d)
                actual_bytes += n
        if Counter(actual) != Counter(manifest):
            mismatch = True
            notes.append(f"drop-manifest disagrees with content: recomputed "
                         f"{len(actual)} dropped item(s), manifest claims "
                         f"{len(manifest)}")
        elif actual_bytes != dropped["bytes"]:
            mismatch = True
            notes.append(f"dropped.bytes: recomputed {actual_bytes} != "
                         f"claimed {dropped['bytes']}")
        if sum(remaining.values()) != introduced["count"]:
            mismatch = True
            notes.append(f"introduced.count: recomputed "
                         f"{sum(remaining.values())} != claimed "
                         f"{introduced['count']}")

    out["content"] = "mismatch" if mismatch else ("match" if checked else "skipped")
    out["ok"] = sc and not mismatch
    return out
