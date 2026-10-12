"""Plain-text formatting for the terminal."""

from __future__ import annotations

MINUS = "−"


def dur(seconds: float) -> str:
    s = int(round(seconds))
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    return f"{h}h{m:02d}m{sec:02d}s" if h else f"{m}m{sec:02d}s"


def dur_delta(d: float) -> str:
    if round(d) == 0:
        return "same"
    return (MINUS if d < 0 else "+") + dur(abs(d))


def grams(g: float) -> str:
    return f"{g:.2f} g"


def grams_delta(d: float) -> str:
    if abs(d) < 0.005:
        return "same"
    return f"{MINUS if d < 0 else '+'}{abs(d):.2f} g"


def pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "-"


def table(rows: list[list[str]], right: set[int] = frozenset(), indent: int = 2) -> str:
    if not rows:
        return ""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    out = []
    for r in rows:
        cells = [c.rjust(w) if i in right else c.ljust(w) for i, (c, w) in enumerate(zip(r, widths))]
        out.append(" " * indent + "   ".join(cells).rstrip())
    return "\n".join(out)
