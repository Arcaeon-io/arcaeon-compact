# arcaeon-compact

**Your summarizer says it kept what mattered. `arcaeon-compact` makes it prove
what it dropped.**

Every agent compacts context — summarizes the conversation, prunes memory,
truncates history — and today that step is a black hole: content goes in,
a survivor comes out, and nothing attests to the difference. A
**CompactionReceipt** is a tamper-evident record of exactly that difference:
digests of the full pre-compaction content and the post-compaction survivor,
plus a drop-manifest naming every dropped item by digest, chained onto an
[`arcaeon-ledger`](https://pypi.org/project/arcaeon-ledger/) log so the receipt
itself can't be quietly edited later.

```
pip install arcaeon-compact     # brings arcaeon-ledger; nothing else
```

```python
from arcaeon_compact import CompactionReceipt, verify_receipt

pre  = conversation_turns                  # list of str | bytes | dict
post = summarize(pre)                      # your compactor, any compactor

receipt = CompactionReceipt.open(pre)      # digest every item + the whole
receipt.record_kept(post)                  # dropped = pre minus kept, by digest
row = receipt.seal("receipts.jsonl",       # one chained row on an arcaeon-ledger
                   compactor="summarizer-v2", method="llm-summary")

verify_receipt(row)                        # self-consistency, always
verify_receipt(row, pre, post)             # recompute from content and compare
```

Three calls in, one call out. That's the whole API.

## What it does NOT prove — read this before the features

Being precise about the boundary is the product, not a disclaimer.

**1. It proves WHAT was dropped, never that dropping was wise.** The receipt
has no opinion on salience. A compactor that keeps the small talk and drops the
wire-transfer instructions gets a perfectly valid receipt saying exactly that.
The receipt turns "trust me, nothing important was lost" into a checkable
claim — judging the loss is still your job.

**2. It proves the compactor's claim about its inputs, not that the inputs
were complete.** The gateway problem: if content was withheld before `open()`
ever saw it, the receipt faithfully notarizes the partial view. The receipt
binds what crossed the gate, not what existed behind it. Closing that gap means
receipting the *producing* side too (the ledger's artefact-binding is the tool
for that) — a layer you add, stated here, not implied away.

**3. Digests only means dropped content is NOT recoverable from the receipt.**
This is privacy by construction — a receipt can be published, shipped to an
auditor, or held by a counterparty without leaking one byte of the
conversation. It is also a real limitation, stated plainly: you can prove an
item you still *hold* was dropped (hash it, find it in the manifest); you
cannot resurrect an item you lost. The receipt is a witness, not a backup.

And, inherited honestly from the chain underneath: the ledger proves the
receipt row wasn't altered *in place* — for truncation-resistance you pin the
ledger head externally, exactly as `arcaeon-ledger`'s docs describe.

## The receipt, anatomically

```json
{
  "schema": "arcaeon-compact:receipt:v1",
  "pre":     {"count": 4, "bytes": 121, "digest": "sha256:json-c14n:v1:0fa1…"},
  "post":    {"count": 2, "bytes": 49,  "digest": "sha256:json-c14n:v1:2623…"},
  "dropped": {"count": 3, "bytes": 84,  "items": ["sha256:raw-bytes:v1:9ecb…", "…"]},
  "introduced": {"count": 1},
  "compactor": "summarizer-v2",
  "method": "llm-summary",
  "opened_at": "2026-08-14T17:40:00Z",
  "receipt_digest": "sha256:json-c14n:v1:9a0f…",
  "ts": "…", "chain": "…"
}
```

- Every digest is **self-describing** (`sha256:<recipe>:<ver>:<hex>`), carrying
  its own pinned canonicalization recipe from `arcaeon-ledger` — never a bare
  hex hash a stranger can't reproduce. The per-item type rule is frozen into
  the v1 schema: `bytes` are hashed raw, `str` as UTF-8, everything else
  through the pinned `json-c14n` recipe.
- The **whole-content digests** are a digest over the ordered per-item digests,
  so they recompute from content alone — content is never stored.
- **`introduced`** counts survivor items that were never in the pre-content:
  the summary text itself, typically. It closes the arithmetic
  (`pre = kept + dropped`, `post = kept + introduced`) so the counts can't be
  fudged independently.
- **`receipt_digest`** covers the deterministic core, so an edited row is
  caught even when it's been copied *out* of its ledger. Inside the ledger,
  the chain catches the same edit; this check travels with the row.
- Duplicates are counted as a **multiset**: keeping one copy of a twice-seen
  item still drops the other, and the manifest says so.

## Verification, honestly scoped

```python
verify_receipt(row)
# {"ok": True, "self_consistent": True, "content": "skipped", "notes": []}

verify_receipt(row, pre_content=pre, post_content=post)
# {"ok": True, "self_consistent": True, "content": "match", "notes": []}
```

Self-consistency (no content needed) checks the schema, every digest's shape,
the count arithmetic, and the `receipt_digest`. With content provided, every
digest is recomputed and compared — and with **both** sides provided, the drop
set itself is recomputed (pre minus post, by digest) and held against the
manifest. That last comparison is the point of the whole library:

```python
# the compactor claims nothing was dropped...
receipt = CompactionReceipt.open(pre)
receipt.record_kept(pre)                       # "kept everything"
row = receipt.seal("receipts.jsonl", compactor="liar", method="llm-summary")

# ...but what it actually shipped is missing an item
verify_receipt(row, pre, shipped_post)
# {"ok": False, "content": "mismatch",
#  "notes": ["post.digest: recomputed … != claimed …",
#            "drop-manifest disagrees with content: recomputed 1 dropped
#             item(s), manifest claims 0"]}
```

Stated with equal honesty: the lying row **alone** is self-consistent — a
receipt binds the claim; only content exposes the lie. The self-test asserts
this out loud rather than letting you discover it. What the receipt guarantees
is that the claim is *frozen*: the compactor committed to specific digests at
seal time, and anyone who ever holds the content can check that commitment.

`record_dropped(items)` is optional — the drop set is inferred as
pre-minus-kept by digest. If you do record it explicitly, `seal()` reconciles
the claim against the inference and **refuses to seal a receipt that disagrees
with itself**, so an internally inconsistent receipt never exists to be
believed.

## Prove your own install

```
python -m arcaeon_compact.selftest
```

Golden digest vectors frozen at the v1 schema freeze — if your environment
computes anything else, the command fails loudly and you should not trust
receipts it produces — plus the planted-drop fixture above, run for real in a
temp dir every time. The negative test ships in the package because "trust our
CI" is exactly the posture this library exists to replace.

## Built on arcaeon-ledger

Receipts append to a standard [`arcaeon-ledger`](https://pypi.org/project/arcaeon-ledger/)
chain, so everything the ledger gives you composes for free: `verify` names
the exact tampered line, `head()` pins close the truncation gap, and a
`WitnessStore` gives you an external record a re-minter cannot advance. A
receipts file is just a ledger file; the receipt is just a row with a schema.

## Status

v0.1.0. Library + packaged self-test, tested against the planted-drop lie,
in-row edits, multiset duplicates, byte totals, and lifecycle misuse
(`test_compact.py`). Extracted from the context-compaction flow of a
long-running agent that wanted receipts for its own memory pruning before
selling them to anyone else.

MIT.
