"""Parsing of a model's Ask answer: citation markers -> cited note ids."""
import re



_ASK_MARK = re.compile(r"\[\s*(\d+(?:\s*[,;]\s*\d+)*)\s*\]")


def _parse_ask_answer(raw: str, excerpts: list, limit: int | None = None) -> tuple[str, list]:
    """Split the model's raw Ask output into (answer_text, cited_note_ids).

    Citations come from two places, merged in order of first appearance:
      1. inline "[n]" markers in the answer (n = the number the note was given
         in the prompt). Markers naming a note the model never saw (n out of
         range, or beyond `limit`, the count that fit the context) are removed
         from the text — an invented citation is worse than none.
      2. the trailing "SOURCES:" line — numbers, or (older prompt / model
         habit) exact titles.
    If the model gave neither, no citations — never "everything we handed it":
    an unverified citation is worse than an honest none."""
    raw = (raw or "").strip()
    m = re.search(r"\n?\s*SOURCES:\s*(.*)\Z", raw, re.IGNORECASE | re.DOTALL)
    body = raw[:m.start()].strip() if m else raw
    src_text = m.group(1).strip() if m else ""
    n_max = min(limit if limit is not None else len(excerpts), len(excerpts))
    order: list[int] = []
    dropped = [False]

    def _fix(mo):
        nums = [int(x) for x in re.split(r"[,;]", mo.group(1))]
        keep = [n for n in nums if 1 <= n <= n_max]
        if len(keep) != len(nums):
            dropped[0] = True
        for n in keep:
            if n not in order:
                order.append(n)
        return "".join(f"[{n}]" for n in keep)

    answer = _ASK_MARK.sub(_fix, body)
    if dropped[0]:                         # tidy gaps left by removed markers
        answer = re.sub(r"[ \t]+([.,;:!?])", r"\1", answer)
        answer = re.sub(r"[ \t]{2,}", " ", answer).strip()

    if src_text and not src_text.lower().startswith("none"):
        by_title = {(ex.get("title") or "").strip().lower(): i + 1
                    for i, ex in enumerate(excerpts) if ex.get("id")}
        for tok in (t.strip() for t in src_text.split(",")):
            if not tok:
                continue
            num = tok.strip("[]. ")
            if num.isdigit():
                n = int(num)
                n = n if 1 <= n <= n_max else None
            else:
                n = by_title.get(tok.lower())
            if n and n not in order:
                order.append(n)
    cited = [excerpts[n - 1]["id"] for n in order if excerpts[n - 1].get("id")]
    return answer, cited
