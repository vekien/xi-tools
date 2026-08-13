"""xi dll ffximain sig-gen / sig-apply — signature-based (version-resilient) patching.

`sig-gen` turns an absolute-address `.patch` + the build it targets into a
`.sigpatch` whose edits are located by masked code signatures (address operands
wildcarded). `sig-apply` scans *any* build for those signatures and applies them,
so an edit follows its function across a client rebuild. Sites whose code was
genuinely rewritten fail loud (reported, never written). See
`docs/dll/sig-patch.md` and `docs/ffximain/inventory.md` §10.
"""
from __future__ import annotations

from pathlib import Path

import click

from xi.dll import sig


@click.command("sig-gen", help="Generate a .sigpatch (code-signature edits) from an address .patch + its build.")
@click.option("--unpacked", required=True, type=click.Path(exists=True, path_type=Path),
              help="The UNPACKED build the .patch was authored against (its `expect` bytes must match).")
@click.option("--patch", "patch_file", required=True, type=click.Path(exists=True, path_type=Path),
              help="Address .patch to convert (e.g. docs/ffximain/ffximain_inventory.patch).")
@click.option("--output", required=True, type=click.Path(path_type=Path), help="Output .sigpatch (JSON).")
def cmd_gen(unpacked: Path, patch_file: Path, output: Path) -> None:
    sp = sig.generate(unpacked, patch_file.read_text())
    sig.save_sigpatch(output, sp)
    m = sp["meta"]
    s = m["stats"]
    click.echo(f"Source          : {unpacked}")
    click.echo(f"Address edits   : {m['edits_in']}")
    click.echo(f"Signature entries: {m['entries']}  (unique={s['unique']} multi={s['multi']})")
    click.echo(f"Address-fallback : {s['unsafe']}  (no safe signature — pinned to va)")
    for u in m["unsafe"]:
        click.echo(f"    fallback: {u}")
    click.echo(f"Wrote           : {output}")
    click.echo("Verify it reproduces your build:  sig-apply --unpacked <same build> --sig <this> --dry-run")


@click.command("sig-apply", help="Apply/dry-run a .sigpatch to any UNPACKED build (locates edits by signature).")
@click.option("--unpacked", required=True, type=click.Path(exists=True, path_type=Path),
              help="Unpacked DLL to patch.")
@click.option("--sig", "sig_file", required=True, type=click.Path(exists=True, path_type=Path),
              help="The .sigpatch produced by sig-gen.")
@click.option("--output", type=click.Path(path_type=Path), default=None,
              help="Output DLL [default: overwrite --unpacked].")
@click.option("--dry-run", is_flag=True, help="Report only; write nothing.")
def cmd_apply(unpacked: Path, sig_file: Path, output: Path | None, dry_run: bool) -> None:
    sp = sig.load_sigpatch(sig_file)
    r = sig.apply(unpacked, sp, output, dry_run=dry_run)
    click.echo(f"Sigpatch        : {sig_file}")
    click.echo(f"Target          : {unpacked}")
    click.echo(f"Entries         : {r['entries']}  (address-fallback: {r.get('addr_fallback', 0)})")
    click.echo(f"  would-apply    : {r['applied']}" if dry_run else f"  applied        : {r['applied']}")
    click.echo(f"  already-patched: {r['already']}")
    click.echo(f"  MISSING (rewritten / not found): {len(r['missing'])}")
    click.echo(f"  ambiguous      : {len(r['ambiguous'])}")
    for note, info in r["missing"][:40]:
        click.echo(f"    MISSING  {note}")
    for item in r["ambiguous"][:40]:
        click.echo(f"    AMBIG    {item}")
    if len(r["missing"]) + len(r["ambiguous"]):
        click.echo("Unresolved sites need re-deriving against this build (their code changed). "
                   "See docs/ffximain/inventory.md §10.")
    if dry_run:
        click.echo("Dry-run: no file written.")
    elif r.get("written"):
        click.echo(f"Wrote           : {r['written']}")
