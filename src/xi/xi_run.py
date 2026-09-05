r"""`xi run FILE` — replay a file of xi commands one line at a time.

Blank lines, `#` comments, batch `rem` / `::` comments and markdown ``` fences are
skipped, so a .md / .bat / .txt of pasted commands runs as-is. `set NAME=value`
defines a variable that `%NAME%` (or `${NAME}`) expands in later lines. A line
ending in `\` or `^` continues on the next one. Each command runs in-process
through the same Click tree as typing it at the shell.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
import traceback
from pathlib import Path

import click

_VAR_RE = re.compile(r"%(\w+)%|\$\{(\w+)\}")
_SET_RE = re.compile(r"^(?:set|export)\s+(.+)$", re.IGNORECASE)
_COMMENT_PREFIXES = ("#", "::", "```")
_PREFIXES = (("uv", "run", "xi"), ("python", "-m", "xi"), ("xi",))


def _tokenize(line: str) -> list[str]:
    """shlex split that keeps Windows backslashes (no escape char) but honours quotes."""
    lex = shlex.shlex(line, posix=True)
    lex.whitespace_split = True
    lex.escape = ""
    lex.commenters = ""
    return list(lex)


def _quote(tok: str) -> str:
    return f'"{tok}"' if (" " in tok or not tok) else tok


def _expand(text: str, variables: dict[str, str], lineno: int) -> str:
    def repl(m: re.Match) -> str:
        name = m.group(1) or m.group(2)
        if name in variables:
            return variables[name]
        if name in os.environ:
            return os.environ[name]
        raise click.ClickException(f"line {lineno}: undefined variable {m.group(0)}")
    return _VAR_RE.sub(repl, text)


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _read_steps(path: Path) -> list[tuple[int, str]]:
    """(line number, logical line) pairs with blanks, comments and fences dropped.

    Continuation lines are joined onto the line that started them; the reported
    number is the first physical line."""
    steps: list[tuple[int, str]] = []
    pending: str | None = None
    pending_no = 0
    for no, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if pending is not None:
            line = pending + " " + line
            no = pending_no
            pending = None
        if line.endswith(("\\", "^")):
            pending, pending_no = line[:-1].rstrip(), no
            continue
        if not line or line.startswith(_COMMENT_PREFIXES) or line.lower().startswith("rem "):
            continue
        steps.append((no, line))
    if pending:
        steps.append((pending_no, pending))
    return steps


def _to_args(tokens: list[str]) -> list[str]:
    lowered = [t.lower() for t in tokens]
    for prefix in _PREFIXES:
        if tuple(lowered[: len(prefix)]) == prefix:
            return tokens[len(prefix):]
    return tokens


def _dispatch(args: list[str]) -> int:
    """Run one command through the root group; 0 on success, else an exit code."""
    from xi.xi_cli import cli
    try:
        rv = cli.main(args=args, prog_name="xi", standalone_mode=False)
    except click.ClickException as e:
        e.show()
        return e.exit_code or 1
    except SystemExit as e:
        code = e.code
        return code if isinstance(code, int) else (0 if code is None else 1)
    except Exception:
        traceback.print_exc()
        return 1
    return rv if isinstance(rv, int) else 0


@click.command("run")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--dry-run", is_flag=True, help="Print the expanded commands without running them.")
@click.option("-k", "--keep-going", is_flag=True, help="Continue past a failing line (default: stop).")
@click.option("--start", type=int, default=1, show_default=True, metavar="LINE",
              help="Skip commands before this line number (variables are still collected).")
def cmd(file: Path, dry_run: bool, keep_going: bool, start: int) -> None:
    """Run every xi command in FILE, one line at a time.

    Blank lines, `#` comments and ``` fences are skipped. `set NAME=value` defines
    `%NAME%` for later lines. Lines may start with `xi`, `uv run xi` or the bare group.

    \b
      xi run docs/title/custom_title_screen.md
      xi run steps.txt --dry-run
      xi run steps.txt --start 12        # resume after a failure
    """
    variables: dict[str, str] = {}
    ran = failed = 0
    failed_lines: list[int] = []
    for lineno, line in _read_steps(file):
        m = _SET_RE.match(line)
        if m:
            body = _strip_quotes(m.group(1))
            name, sep, value = body.partition("=")
            if not sep or not name.strip():
                raise click.ClickException(f"line {lineno}: expected `set NAME=value`, got: {line}")
            variables[name.strip()] = _expand(_strip_quotes(value), variables, lineno)
            continue
        if lineno < start:
            continue
        args = _to_args(_tokenize(_expand(line, variables, lineno)))
        if not args:
            raise click.ClickException(f"line {lineno}: nothing to run after `xi`")
        click.secho(f"[{lineno}] xi {' '.join(_quote(a) for a in args)}", fg="cyan", bold=True)
        if dry_run:
            continue
        try:
            rc = _dispatch(args)
        except (click.Abort, KeyboardInterrupt):
            click.secho(f"Aborted at line {lineno}.", fg="red", err=True)
            sys.exit(130)
        ran += 1
        if rc:
            failed += 1
            failed_lines.append(lineno)
            click.secho(f"line {lineno} failed (exit {rc})", fg="red", err=True)
            if not keep_going:
                click.secho(f"Stopped. Resume with: xi run {file} --start {lineno}", fg="yellow", err=True)
                sys.exit(rc)
        click.echo()
    if dry_run:
        return
    colour = "red" if failed else "green"
    summary = f"{ran} command{'s' if ran != 1 else ''} run, {failed} failed"
    if failed_lines:
        summary += f" (lines {', '.join(map(str, failed_lines))})"
    click.secho(summary, fg=colour, bold=True)
    if failed:
        sys.exit(1)
