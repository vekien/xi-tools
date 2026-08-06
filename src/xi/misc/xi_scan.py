"""Discovery: walk every zone_id 0..N and classify against LSB.

Produces a per-zone classification:

  live          - DAT exists, client name present, LSB has a real name
  cut_named     - DAT exists, client has a name, LSB row is placeholder/missing
                  (high-value recovery candidate: SE shipped it, LSB ignores it)
  cut_unnamed   - DAT exists, client name is empty/'none'/'?' (beta/dev zone)
  lsb_only      - LSB row exists but client has no resolvable DAT
                  (instance-only zones, or server-side virtual zones)

Inverts `get_zone_entries`'s filter: keeps zones with empty/placeholder names
because those are exactly the cut content we want to recover.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import click

from xi.ftable.xi_core import load_all_tables, scan_file_ids
from xi.xi_config import FFXI_DIR
from xi.zone.xi_list import ZONE_NAME_DAT, parse_dmsg, zone_file_id

from xi.misc.xi_lsb import (
    PLACEHOLDER_NAMES,
    load_lsb_zone_enum,
    load_lsb_zones,
)


STATUS_LIVE        = "live"
STATUS_CUT_NAMED   = "cut_named"
STATUS_CUT_UNNAMED = "cut_unnamed"
STATUS_LSB_ONLY    = "lsb_only"


@dataclass(slots=True)
class ZoneRecord:
    zone_id: int
    file_id: int
    client_name: str
    dat_path: str | None
    lsb_name: str | None
    lsb_enum: str | None
    status: str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def classify(
    zone_id: int,
    client_name: str,
    dat_path: str | None,
    lsb_name: str | None,
    lsb_enum: str | None,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    has_dat = dat_path is not None
    cn = (client_name or "").strip()
    client_named = bool(cn) and cn.lower() not in PLACEHOLDER_NAMES
    lsb_named = lsb_name is not None and lsb_name.strip().lower() not in PLACEHOLDER_NAMES

    if not has_dat and lsb_name is not None:
        return STATUS_LSB_ONLY, ["LSB defines this zone but no client DAT resolves"]

    if has_dat and client_named and lsb_named:
        if cn.lower() != lsb_name.lower().replace("_", " "):
            notes.append(f"name drift: client={cn!r} lsb={lsb_name!r}")
        return STATUS_LIVE, notes

    if has_dat and client_named and not lsb_named:
        notes.append("SE shipped a named zone, LSB has it as a placeholder/no row")
        return STATUS_CUT_NAMED, notes

    if has_dat and not client_named:
        notes.append("client name table has no name for this zone_id")
        if lsb_enum:
            notes.append(f"LSB enum has {lsb_enum} for this id")
        return STATUS_CUT_UNNAMED, notes

    return STATUS_LSB_ONLY, ["no DAT, no recognizable status"]


def scan() -> list[ZoneRecord]:
    name_path = Path(FFXI_DIR) / ZONE_NAME_DAT
    if not name_path.exists():
        raise FileNotFoundError(
            f"Zone name table not found at {name_path}. Set FFXI_DIR to a real client."
        )

    names = parse_dmsg(name_path.read_bytes())
    tables = load_all_tables()
    lsb_zones = load_lsb_zones()
    lsb_enum  = load_lsb_zone_enum()

    seen_zone_ids: set[int] = set()
    records: list[ZoneRecord] = []

    # Walk every zone_id the client knows by name (most are 0..1023ish; the
    # parse returns one string per d_msg slot).
    for zone_id, raw_name in enumerate(names):
        seen_zone_ids.add(zone_id)
        client_name = raw_name.strip()
        hits = scan_file_ids([zone_file_id(zone_id)], tables)
        dat_path = None
        if hits:
            candidate = hits[0]["dat"]
            if (Path(FFXI_DIR) / candidate).is_file():
                dat_path = candidate

        lsb = lsb_zones.get(zone_id)
        enum_name = lsb_enum.get(zone_id)
        status, notes = classify(
            zone_id, client_name, dat_path,
            lsb.name if lsb else None,
            enum_name,
        )
        if status == STATUS_LSB_ONLY and dat_path is None and lsb is None:
            continue
        records.append(ZoneRecord(
            zone_id=zone_id,
            file_id=zone_file_id(zone_id),
            client_name=client_name,
            dat_path=dat_path,
            lsb_name=lsb.name if lsb else None,
            lsb_enum=enum_name,
            status=status,
            notes=notes,
        ))

    # Cover LSB-defined zones the name table didn't cover (zone_id past the
    # name table length).
    for zid, lsb in lsb_zones.items():
        if zid in seen_zone_ids:
            continue
        hits = scan_file_ids([zone_file_id(zid)], tables)
        dat_path = None
        if hits:
            candidate = hits[0]["dat"]
            if (Path(FFXI_DIR) / candidate).is_file():
                dat_path = candidate
        status, notes = classify(
            zid, "", dat_path, lsb.name, lsb_enum.get(zid),
        )
        records.append(ZoneRecord(
            zone_id=zid,
            file_id=zone_file_id(zid),
            client_name="",
            dat_path=dat_path,
            lsb_name=lsb.name,
            lsb_enum=lsb_enum.get(zid),
            status=status,
            notes=notes,
        ))

    records.sort(key=lambda r: (r.status, r.zone_id))
    return records


# ── CLI ──────────────────────────────────────────────────────────────────────

STATUS_COLORS = {
    STATUS_LIVE:        "green",
    STATUS_CUT_NAMED:   "yellow",
    STATUS_CUT_UNNAMED: "magenta",
    STATUS_LSB_ONLY:    "blue",
}


@click.command("scan")
@click.option(
    "--status", "filter_status",
    type=click.Choice([STATUS_LIVE, STATUS_CUT_NAMED, STATUS_CUT_UNNAMED, STATUS_LSB_ONLY]),
    multiple=True,
    help="Only show zones with these statuses. Repeatable.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of a table.")
@click.option("--output", "-o", type=click.Path(), help="Write to file instead of stdout.")
@click.option("--summary", is_flag=True, help="Just print per-status counts and exit.")
def cmd(filter_status, as_json, output, summary):
    """Scan every zone_id and classify against LSB.

    Default output is a table. The interesting buckets are `cut_named` (LSB
    shipped a placeholder for a zone the client has named geometry for) and
    `cut_unnamed` (beta/dev content with no SE-given name).
    """
    records = scan()
    if filter_status:
        wanted = set(filter_status)
        records = [r for r in records if r.status in wanted]

    if summary:
        counts: dict[str, int] = {}
        for r in records:
            counts[r.status] = counts.get(r.status, 0) + 1
        for status in (STATUS_LIVE, STATUS_CUT_NAMED, STATUS_CUT_UNNAMED, STATUS_LSB_ONLY):
            n = counts.get(status, 0)
            click.echo(click.style(f"{status:14s} {n:4d}", fg=STATUS_COLORS.get(status)))
        return

    if as_json:
        payload = json.dumps([r.to_dict() for r in records], ensure_ascii=False, indent=2)
        if output:
            Path(output).write_text(payload, encoding="utf-8")
            click.echo(f"Wrote {len(records)} records -> {output}")
        else:
            click.echo(payload)
        return

    header = f"{'id':>4}  {'status':12s}  {'dat':30s}  {'lsb':28s}  client / notes"
    lines = [header, "-" * 110]
    for r in records:
        status_col = click.style(f"{r.status:12s}", fg=STATUS_COLORS.get(r.status))
        dat = r.dat_path or "-"
        lsb = (r.lsb_name or "-")[:28]
        suffix = r.client_name or "-"
        if r.notes:
            suffix = f"{suffix}   # {'; '.join(r.notes)}"
        lines.append(f"{r.zone_id:>4}  {status_col}  {dat:30s}  {lsb:28s}  {suffix}")

    out_text = "\n".join(lines)
    if output:
        # Strip ANSI when writing to file.
        import re
        Path(output).write_text(re.sub(r"\x1b\[[0-9;]*m", "", out_text), encoding="utf-8")
        click.echo(f"Wrote {len(records)} records -> {output}")
    else:
        click.echo(out_text)
