# Changelog — v6.5

A focused fix for redundant, token-wasting "corrections."

## The problem

On a note that had no garbled words — just a clean run-on sentence missing
punctuation — the agent "corrected" it by adding capitalization, periods, and
flow, then logged the ENTIRE note as a single correction: the whole original on
the left, the whole rewrite on the right. This was:

- **Redundant:** the original is already in the note and the rewrite is already
  in "Enriched Note." The correction repeated both, telling the reader nothing
  new.
- **Wasteful:** the model regenerated the full note text a second time in the
  corrections field, roughly doubling output tokens on every note — expensive on
  a device where each note already takes 30–45s.

A "correction" should only flag discrete, surprising, word-level substitutions
("teran" → "team") — the things the two visible versions can't show you at a
glance. Punctuation, casing, grammar, and reflow belong only in the rewritten
body.

## The fix (defense in depth)

1. **Prompt:** `corrections` is now explicitly defined as discrete WORD/short-
   PHRASE substitutions only. Punctuation, capitalization, spacing, grammar, and
   reflow must NEVER be logged. Each `from` must be the short changed span — never
   a whole sentence, paragraph, or the entire note. If the only changes were
   formatting/grammar, corrections is [].

2. **Code guard** (`engine._normalize`), so a misbehaving model can never render
   the junk even if it tries:
   - Drops any correction whose `from` exceeds 15 words (a rewrite, not a
     substitution).
   - Drops no-ops: entries where `from` and `to` differ only by punctuation,
     case, or spacing.
   - Genuine word/phrase swaps are preserved untouched.

## Effect on the reported note

No Corrections block at all (there were no word-level changes). The Enriched
Note stands on its own, and output is leaner.

No new config keys. (Cutoff is 15 words, matching the agreed threshold.)
