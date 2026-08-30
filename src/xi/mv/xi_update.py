"""``xi mv update`` — refresh model-viewer list JSONs into ``mv/lists``."""

from __future__ import annotations

from pathlib import Path

import click

from xi.mv.update_lists import ALL_TARGETS, default_lists_dir, run_updates


@click.command("update")
@click.option(
    "--only",
    "only",
    default=None,
    metavar="TARGETS",
    help=(
        "Comma-separated subset of targets "
        f"(default: all). Choices: {', '.join(ALL_TARGETS)}"
    ),
)
@click.option(
    "--lists",
    "lists_dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="Output directory  [default: mv/lists under XI_TOOLS_DIR].",
)
@click.option(
    "--base",
    "base_dir",
    type=click.Path(path_type=Path, file_okay=False, exists=True),
    default=None,
    help=(
        "Seed/read base JSON from this dir when the file is missing under --lists. "
        "Writes still go to --lists."
    ),
)
@click.option(
    "--sql",
    "sql_path",
    type=click.Path(path_type=Path, exists=False),
    default=None,
    help="zone_settings.sql for zone-music  [default: <XI_SERVER_DIR>/sql/zone_settings.sql]",
)
@click.option("--dry-run", is_flag=True, help="Report changes without writing files.")
@click.option(
    "--mid-cap",
    default=1500,
    show_default=True,
    type=int,
    help="Gear: only consider model ids (mid) up to this value.",
)
def cmd(
    only: str | None,
    lists_dir: Path | None,
    base_dir: Path | None,
    sql_path: Path | None,
    dry_run: bool,
    mid_cap: int,
):
    """Append missing gear / music / sfx names; rebuild zone_music from SQL.

    \b
    Writes to mv/lists by default. Examples:
      xi mv update
      xi mv update --only gear,music
      xi mv update --dry-run --only sfx,zone-music

    Gear uses FFXiMain race tables + FTABLE (append NEW - mid N). Music/SFX
    scan the game sound* trees. zone-music rebuilds from zone_settings.sql.
    Effects: spell anim → file_id (0xAF0+anim) → FTABLE DAT; job abilities
    (4412+anim); weapon skills (4912+anim); spell-band orphan scan. Images and
    npcs classify DATs by their section types (textures-only vs skinned mesh)
    and, for npcs, name them from mob_pools / npc_list. file-ids stamps the
    reverse-FTABLE file_id onto every list row.

    floors / zones are not touched (manual).

    Copy curated base lists into mv/lists once; later runs append in place.
    """
    lists_dir = (lists_dir or default_lists_dir()).resolve()

    if only:
        targets = [t.strip().lower() for t in only.split(",") if t.strip()]
        aliases = {"zone_music", "file_ids", "fileids", "gear_sets", "gear_labels"}
        bad = [t for t in targets if t not in ALL_TARGETS and t not in aliases]
        if bad:
            raise click.ClickException(
                f"Unknown target(s): {', '.join(bad)}. "
                f"Choose from: {', '.join(ALL_TARGETS)}"
            )
    else:
        targets = list(ALL_TARGETS)

    click.echo(f"lists: {lists_dir}")
    if base_dir:
        click.echo(f"base:  {base_dir.resolve()}")
    if dry_run:
        click.echo(click.style("dry-run — no files will be written", fg="yellow"))
    click.echo(f"targets: {', '.join(targets)}")
    click.echo()

    # A full run reads ~53k DAT headers and rebuilds five lists, so it streams:
    # each target announces what it is doing, then prints its own result before
    # the next one starts.
    started: set[str] = set()

    def on_step(target: str, message: str) -> None:
        if target not in started:
            started.add(target)
            click.echo(click.style(f"  {target}…", bold=True))
        click.echo(click.style(f"      {message}", fg="bright_black"))

    def on_report(r: dict) -> None:
        started.add(r.get("target", "?"))
        echo_report(r, dry_run=dry_run)

    reports = run_updates(
        targets,
        lists_dir,
        dry_run=dry_run,
        sql_path=sql_path,
        base_dir=base_dir.resolve() if base_dir else None,
        mid_cap=mid_cap,
        on_step=on_step,
        on_report=on_report,
    )

    click.echo()
    if any(r.get("error") for r in reports):
        raise SystemExit(1)
    click.echo(click.style("done.", fg="green"))


def echo_report(r: dict, *, dry_run: bool) -> None:
    """One target's result: counts, the file it wrote, and a few sample rows."""
    t = r.get("target", "?")
    if r.get("error"):
        click.echo(click.style(f"  {t}: ERROR — {r['error']}", fg="red"))
        return
    parts = [f"  {t}:"]
    if "added" in r:
        parts.append(f"+{r['added']}")
    if r.get("corrected"):
        parts.append(f"fixed {r['corrected']}")
    if "folders_added" in r:
        parts.append(f"folders +{r['folders_added']}")
    if "names_added" in r:
        parts.append(f"names +{r['names_added']}")
    if "zones" in r:
        parts.append(f"{r['zones']} zones")
    if "by_race" in r and r["by_race"]:
        detail = ", ".join(f"{k}:{v}" for k, v in r["by_race"].items())
        parts.append(f"({detail})")
    if "by_cat" in r and r["by_cat"]:
        detail = ", ".join(f"{k}:{v}" for k, v in r["by_cat"].items())
        parts.append(f"({detail})")
    if r.get("skipped_missing_file"):
        parts.append(f"skip-missing-file {r['skipped_missing_file']}")
    if r.get("skipped_path_already_listed"):
        parts.append(f"skip-path-dup-mid {r['skipped_path_already_listed']}")
    if r.get("wrote"):
        parts.append(click.style("wrote", fg="green"))
    elif dry_run:
        parts.append("dry-run")
    else:
        parts.append("no change")
    click.echo(" ".join(str(p) for p in parts))
    if r.get("file"):
        click.echo(f"         → {r['file']}")
    for s in r.get("samples") or []:
        click.echo(f"           · {s}")
