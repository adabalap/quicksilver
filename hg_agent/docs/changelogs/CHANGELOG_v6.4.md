# Changelog — v6.4

Four items from review: transcription tuning, IST timestamps, a dashboard tag
bug, and semantic (embedding-based) note relationships.

## 1. Transcription — asymmetric by risk (your call: balanced)

The reasoning approach (phonetic + context + parallel-structure) was right; the
thresholds were slightly too trusting on named entities. Now tuned ASYMMETRIC:

- **Ordinary words** (grammar, filler, common words, broken sentences): still
  AGGRESSIVE — fix confidently. "liner er are" → "like they're" just gets fixed.
- **Proper nouns, brands, acronyms, numbers, dates, amounts**: CAUTIOUS — only
  applied silently when very confident (≥0.9) AND context supports it; otherwise
  the original token is kept and the fix is recorded as a correction to confirm.
  A guessed brand like "zsclar" → "Zscaler" is treated as cautious (flagged),
  because mis-correcting a fact is costly while flagging a right guess is cheap.

Rationale: be aggressive where errors are cheap and self-evident, conservative
where they're costly and silent.

## 2. Timestamps now in IST (configurable)

All human-readable timestamps in notes and dashboards now render in the
configured timezone instead of UTC. Default `display_timezone: "Asia/Kolkata"`
→ "2026-06-26 20:29 IST". Uses zoneinfo (correct DST + abbreviation) with a
fixed-offset fallback, so it never fails. Set `display_timezone` to any IANA
name (or "UTC") to change it.

## 3. Agent Dashboard no longer pollutes the review filter

**Bug:** the Agent Dashboard printed the literal `#pending-approval`, which Memos
parsed as a real tag — so the dashboard itself showed up in your
`tag:pending-approval` review filter. **Fix:** the dashboard now describes the
review queue in plain text (and shows the filter as `` `tag:pending-approval` ``,
which has no leading `#` and is not parsed as a tag). It keeps its own
`#agent-dashboard` discovery tag. The footnote is informational text only.

## 4. Note relationships — now SEMANTIC (embeddings) with safe fallback

**The limitation you identified:** relationships were keyword-based, so notes
about the same idea in different words ("VDI latency meeting" vs "Zscaler rollout
slowness") didn't link.

**What changed:** added an optional on-device embedding backend. When enabled,
the agent encodes each note's meaning (title + tags + summary + body) into a
vector and groups by semantic similarity — same-meaning notes link even with no
shared words. The `Corpus` interface is unchanged, so dashboards/links just work.

**Fail-safe by design:** if no embedding model is configured or it can't load,
the agent transparently falls back to the existing lexical (TF-IDF) similarity.
It never breaks; you opt in when ready.

### Enabling semantic relationships
1. Install the backend (once):
   `pip install sentence-transformers`
2. Set in config:
   ```
   "embed_model": "all-MiniLM-L6-v2",     # ~90MB, 384-dim; or a local path
   "embed_cache_dir": "/path/to/model/cache"   # optional
   ```
3. Restart. The log will show `embeddings: ready (384-dim)…` and the corpus line
   will read `[semantic]` instead of `[lexical]`. The "How groups are formed"
   note explainer updates accordingly.

Memory: MiniLM is ~90–130MB resident — modest, but on a tight phone consider
loading it via the shared inference service when that exists. This module is the
drop-in the service can back later with no interface change.

### Thresholds (embedding vs lexical are different scales)
```
related_threshold        0.12   cluster_threshold        0.18   # lexical
embed_related_threshold  0.45   embed_cluster_threshold  0.55   # semantic
```
The daemon auto-selects the right pair based on whether embeddings are active.

## New config keys
```
display_timezone         "Asia/Kolkata"
embed_model              ""        # blank = lexical fallback
embed_cache_dir          ""
embed_related_threshold  0.45
embed_cluster_threshold  0.55
```
