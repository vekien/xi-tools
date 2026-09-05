"""``xi event sweep``: run every event of a zone through the decompiler, optionally round-trip each one.

    xi event sweep 243                       # decompile only: which events still carry a raw step
    xi event sweep 243 --check               # + recompile each event and compare it with retail, then decode our bytes again
    xi event sweep 243 --check --jobs 8      # the same over 8 worker processes
    xi event sweep 230 231 232 --check --summary sweep.tsv
    xi event sweep 243 --check --only 0x010F3007:58,0x010F30A6:10046

What the counters mean:

* ``raw``      an opcode the decompiler carries as bytes (``{"op": "raw"}``) because no typed form exists for it yet
* ``clean``    the recompiled event matches retail opcode for opcode, operand for operand, string byte for string byte
* ``mism``     the recompiled event differs; the first difference is printed
* ``stable``   a second decompile of OUR bytes gives the same JSON as the first decompile (decode -> encode -> decode is a fixed point)

Every worker loads the item-name tables once, so ``--jobs`` pays off from a few hundred events up.
"""
from __future__ import annotations

import collections
import time
from pathlib import Path

import click

_root: Path | None = None


def _init(root: str) -> None:
    global _root
    _root = Path(root)


def _raw_ops(steps, here) -> None:
    for s in steps:
        if s["op"] == "raw" and s.get("note") == "unmodelled":
            key = s["hex"][:2]
            if key in ("9d", "b4", "71", "d4", "cc"):
                key += "." + s["hex"][3:5]
            here[key] += 1
        if s["op"] == "sub":
            _raw_ops(s["steps"], here)


def one(job):
    """(zone, actor, event, check) -> result dict; runs in a worker process."""
    zone, actor, ev, check = job
    from xi.event import xi_decompile as D
    out = {"actor": actor, "event": ev, "ops": 0, "raw": 0, "here": {}, "error": None,
           "mism": None, "first": None, "stable": None, "stable_diff": None}
    try:
        cs, ctx = D.decompile_event(_root, zone, actor, ev)
        out["ops"], out["raw"] = ctx.total_ops, ctx.raw_ops
        here = collections.Counter()
        _raw_ops(cs["steps"], here)
        out["here"] = dict(here)
        if check:
            r = D.check_roundtrip(_root, zone, actor, ev, cs)
            out["mism"] = r["mismatches"]
            out["first"] = r["first_mismatches"][:1]
            out["stable"] = r.get("stable", True)
            out["stable_diff"] = r.get("stable_diff")
    except Exception as e:  # noqa: BLE001 - one broken event must not stop the zone
        out["error"] = f"{type(e).__name__}: {str(e)[:100]}"
    return out


def sweep(root: Path, zone: int, check: bool, only, skip, jobs: int, echo=print) -> dict:
    from xi.event import xi_explain as ex
    zf = ex.zone_files(root, [zone])[0]
    actors, blobs, names = ex.load_zone(zf)
    todo = []
    for a in actors:
        for ev in a.event_ids:
            if ev in (0xFFFE, 0xFFFF) or (a.actor_id, ev) in skip:
                continue
            if only is not None and (a.actor_id, ev) not in only:
                continue
            todo.append((zone, a.actor_id, ev, check))
    t0 = time.time()
    if jobs > 1 and len(todo) > 1:
        import multiprocessing as mp
        with mp.Pool(min(jobs, len(todo)), initializer=_init, initargs=(str(root),)) as pool:
            results = pool.map(one, todo, chunksize=4)
    else:
        _init(str(root))
        results = [one(j) for j in todo]
    tot = len(results)
    err = sum(1 for r in results if r["error"])
    ops = sum(r["ops"] for r in results)
    raws = sum(r["raw"] for r in results)
    rawc = collections.Counter()
    errs = collections.Counter()
    for r in results:
        rawc.update(r["here"])
        if r["error"]:
            errs[r["error"][:80]] += 1
    raw_events = [(r["actor"], r["event"], r["ops"], r["here"]) for r in results if r["here"]]
    clean = sum(1 for r in results if r["mism"] == 0)
    mism = sum(1 for r in results if r["mism"])
    unstable = [(r["actor"], r["event"], r["stable_diff"]) for r in results if r["stable"] is False]
    for r in results:
        if r["error"]:
            echo(f"ERROR 0x{r['actor']:08X} {r['event']}: {r['error']}")
    line = (f"zone {zone}: {tot} events; opcodes {ops}, raw {raws} ({100 * (ops - raws) // max(1, ops)}% modelled); "
            f"{len(raw_events)} events with raw, {err} errors")
    if check:
        line += f"; check: {clean} clean, {mism} mismatched; second decode: {clean + mism - len(unstable)} stable, {len(unstable)} unstable"
    echo(line + f"; {time.time() - t0:.0f} s")
    echo("raw opcodes: " + str(rawc.most_common(16)))
    for aid, ev, n, here in sorted(raw_events, key=lambda x: -sum(x[3].values()))[:25]:
        echo(f"  raw  0x{aid:08X} {ev:6d}  {n:4d} ops  {names.get(aid, '')[:18]:18s} {here}")
    for r in [r for r in results if r["mism"]][:25]:
        echo(f"  mism 0x{r['actor']:08X} {r['event']:6d}  {r['mism']:4d}  {r['first']}")
    for aid, ev, d in unstable[:25]:
        echo(f"  unstable 0x{aid:08X} {ev:6d}  {d}")
    for k, v in errs.most_common(8):
        echo(f"  {v:4d}  {k}")
    return {"zone": zone, "events": tot, "errors": err, "raw_events": len(raw_events), "clean": clean,
            "mism": mism, "unstable": len(unstable), "seconds": round(time.time() - t0)}


def _parse_pairs(spec: str) -> set[tuple[int, int]]:
    out = set()
    for tok in (spec or "").split(","):
        tok = tok.strip()
        if tok:
            a, e = tok.split(":")
            out.add((int(a, 0), int(e)))
    return out


@click.command("sweep")
@click.argument("zones", nargs=-1, required=True)
@click.option("--check", "check", is_flag=True, help="Recompile every event and compare it with retail, then decode our bytes a second time.")
@click.option("--jobs", "jobs", type=int, default=1, show_default=True, help="Worker processes.")
@click.option("--only", "only", default="", help="Comma list of actor:event to sweep (e.g. 0x010F3007:58,0x010F30A6:10046).")
@click.option("--skip", "skip", default="", help="Comma list of actor:event to leave out (your own appended events).")
@click.option("--summary", "summary", type=click.Path(dir_okay=False), help="Append one tab-separated line per zone to this file.")
def sweep_cmd(zones, check, jobs, only, skip, summary):
    """Decompile every event of one or more zones; with --check, prove each one round-trips.

    \b
      xi event sweep 243 --check --jobs 8
      xi event sweep 230 231 232 --check --jobs 8 --summary sweep.tsv
    """
    import sys
    from xi import xi_config
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    root = Path(xi_config.FFXI_DIR)
    only_set = _parse_pairs(only) or None
    skip_set = _parse_pairs(skip)
    for z in zones:
        res = sweep(root, int(z), check, only_set, skip_set, jobs, echo=click.echo)
        if summary:
            new = not Path(summary).exists()
            with open(summary, "a", encoding="utf-8") as f:
                if new:
                    f.write("\t".join(res.keys()) + "\n")
                f.write("\t".join(str(v) for v in res.values()) + "\n")
