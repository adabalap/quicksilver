# Changelog — v6.7

Two small fixes prompted by enabling semantic embeddings in the field. No
behavior change to enrichment or relationships — these are correctness/cleanliness
fixes around the embedding backend and logging.

## 1. Embedding dimension API deprecation

Newer `sentence-transformers` renamed `get_sentence_embedding_dimension()` →
`get_embedding_dimension()`. The old name still works today but emits a
FutureWarning and will be removed eventually (which would have broken embedding
startup on a future `pip` upgrade). The agent now calls the new name when
available and falls back to the old one for older installs — forward- and
backward-compatible.

## 2. Library warnings no longer mislabeled as ERROR

The daemon redirects stdout/stderr into its log so stray library output is
captured. stderr was being logged at ERROR level — but libraries write warnings,
deprecation notices, and progress bars to stderr, not just errors. So benign
messages (the HF "set an HF_TOKEN" nag, the dimension FutureWarning) appeared as
`[ERROR]`, which was alarming and misleading.

stderr redirection is now logged at **WARNING**. Genuine errors in the agent's
own code call `log.error(...)` explicitly and are unaffected — real errors still
show as ERROR; library chatter now shows as WARNING.

## Notes

- Semantic embeddings (`all-MiniLM-L6-v2`) confirmed working in the field:
  startup now reports `relationships: semantic (embeddings)` after the one-time
  model download.
- Watch memory/latency: running the embedding model alongside Gemma adds
  resident memory. Keep an eye on `free -h` (swap especially) and average
  inference latency in `app.py status`. Revert to lexical anytime with
  `embed_model: ""`.

No new config keys.
