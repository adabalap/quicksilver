"""Small text helpers shared by the daemon modules."""


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        s = line.strip().lstrip("#").strip()
        if s:
            return _clip(s, 60)
    return "(untitled)"


def _clip(s: str, n: int) -> str:
    """Trim to n chars on a word boundary, adding an ellipsis if shortened."""
    s = s.strip()
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0].rstrip(",.;:")
    return f"{cut}…"
