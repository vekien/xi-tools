"""Convert existing PNG icon exports to WebP and update all manifest JSON files.

Usage:
    python scripts/convert_icons_webp.py
    python scripts/convert_icons_webp.py --assets exports/assets --quality 90 --keep-png
    python scripts/convert_icons_webp.py --dry-run

Scans <assets>/png/**/*.png, converts each to <assets>/webp/**/*.webp, then rewrites
every manifest*.json in <assets> so "file" paths change from  png/.../<hash>.png
to  webp/.../<hash>.webp  and the top-level "size" field is updated if --size is given.

The original PNGs are deleted by default (use --keep-png to skip deletion).
"""

import argparse
import json
import sys
from pathlib import Path


def convert(assets_dir: Path, quality: int, keep_png: bool, dry_run: bool, size: int | None) -> None:
    png_root  = assets_dir / "png"
    webp_root = assets_dir / "webp"

    if not png_root.exists():
        sys.exit(f"ERROR: {png_root} does not exist — nothing to convert.")

    try:
        from PIL import Image
    except ImportError:
        sys.exit("ERROR: Pillow is required. Install with:  pip install pillow")

    pngs = sorted(png_root.rglob("*.png"))
    if not pngs:
        print(f"No PNGs found under {png_root}.")
        return

    print(f"Found {len(pngs)} PNG(s) under {png_root}")
    if dry_run:
        print("[dry-run] No files will be written or deleted.")

    converted = skipped = failed = 0
    for src in pngs:
        rel      = src.relative_to(png_root)          # e.g.  object/1c611b0b.png
        dst      = webp_root / rel.with_suffix(".webp")

        if dst.exists():
            skipped += 1
            continue

        if dry_run:
            print(f"  [dry] {rel} -> {dst.relative_to(assets_dir)}")
            converted += 1
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            with Image.open(src) as im:
                target_size = (size, size) if size else im.size
                img = im.convert("RGBA")
                if img.size != target_size:
                    img = img.resize(target_size, Image.LANCZOS)
                img.save(dst, format="WEBP", quality=quality, method=6)
            if not keep_png:
                src.unlink()
            converted += 1
        except Exception as e:
            print(f"  FAILED {src.name}: {e}", file=sys.stderr)
            failed += 1

    print(f"Converted: {converted}  Skipped (already exist): {skipped}  Failed: {failed}")

    # ── update manifest JSON files ────────────────────────────────────────────
    manifests = sorted(assets_dir.glob("manifest*.json"))
    if not manifests:
        print("No manifest*.json files found — skipping manifest update.")
        return

    for mf in manifests:
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  Could not read {mf.name}: {e}", file=sys.stderr)
            continue

        changed = False

        if size and data.get("size") != size:
            if not dry_run:
                data["size"] = size
            changed = True

        icons = data.get("icons", {})
        for rec in icons.values():
            old_file = rec.get("file", "")
            if old_file.startswith("png/") and old_file.endswith(".png"):
                new_file = "webp/" + old_file[4:-4] + ".webp"
                if not dry_run:
                    rec["file"] = new_file
                changed = True

        if changed:
            if dry_run:
                print(f"  [dry] would update {mf.name}")
            else:
                mf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"  Updated {mf.name}")
        else:
            print(f"  {mf.name} — no changes needed")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--assets", default="exports/assets",
                    help="Path to the assets root directory (default: exports/assets)")
    ap.add_argument("--quality", type=int, default=90,
                    help="WebP quality 1-100 (default: 90)")
    ap.add_argument("--size", type=int, default=None,
                    help="Resize icons to NxN px during conversion (default: keep original size)")
    ap.add_argument("--keep-png", action="store_true",
                    help="Keep the original PNG files after conversion")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would happen without writing anything")
    args = ap.parse_args()

    assets_dir = Path(args.assets).resolve()
    if not assets_dir.exists():
        sys.exit(f"ERROR: assets dir not found: {assets_dir}")

    convert(assets_dir, quality=args.quality, keep_png=args.keep_png,
            dry_run=args.dry_run, size=args.size)


if __name__ == "__main__":
    main()
