"""One server-side step's outcome — the ``db:``, ``menu:`` and ``lua:`` lines a
``dats build`` of an ability prints — plus the helpers every step shares.

The console form is fixed (the model viewer parses it; design2 §1.2.2):

    <key>: [would ]<op>[ <text>]

For ``skip``, ``refused`` and ``error`` the text is ``— <reason>`` (U+2014) and all
of it is the reason; for the other ops the reason, if any, follows the first `` — ``.
Every name inside ``'…'`` goes through :func:`q`, which escapes ``\\`` and ``'``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

#: The reason separator. Never U+2013 or " - ".
DASH = "—"
#: Ops whose whole text is the reason.
WHOLE_REASON = frozenset({"skip", "refused", "error"})


def q(name) -> str:
    """``name`` quoted for a ``db:``/``menu:`` line: ``'…'`` with ``\\`` → ``\\\\`` and
    ``'`` → ``\\'`` (the viewer's ``unquote`` reverses it)."""
    s = str(name).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"


def now_iso() -> str:
    """UTC now as ``2026-09-19T14:02:11Z`` (the ``at`` of a recorded result)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def current_server_dir() -> str | None:
    """``XI_SERVER_DIR`` as :mod:`xi.xi_config` holds it now (read at call time: the
    zone editor's setup hot-reloads it, and tests patch it). ``None`` when unset."""
    from xi import xi_config
    return (getattr(xi_config, "XI_SERVER_DIR", None) or None)


@dataclass
class Step:
    """What one step did (or would do). ``text`` follows the op on the console line;
    ``warnings`` print as ``⚠ <key>: <warning>`` after every step's line."""
    op: str
    text: str = ""
    would: bool = False
    warnings: list = field(default_factory=list)

    @classmethod
    def skip(cls, reason: str, **kw) -> "Step":
        return cls("skip", f"{DASH} {reason}", **kw)

    @classmethod
    def refused(cls, reason: str, **kw) -> "Step":
        return cls("refused", f"{DASH} {reason}", **kw)

    @classmethod
    def error(cls, reason: str, **kw) -> "Step":
        return cls("error", f"{DASH} {reason}", **kw)

    @property
    def reason(self) -> str | None:
        """The reason, as the viewer's ``stepReason`` reads it."""
        if self.op in WHOLE_REASON:
            r = self.text
            if r.startswith(DASH):
                r = r[len(DASH):].lstrip()
            return r or None
        i = self.text.find(f" {DASH} ")
        return self.text[i + 3:] if i >= 0 else None

    def line(self, key: str) -> str:
        """``db: would update …`` — the console line, without the 5-space indent."""
        return f"{key}: {'would ' if self.would else ''}{self.op}" + (f" {self.text}" if self.text else "")
