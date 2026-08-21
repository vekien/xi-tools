# xi ui extract → removed

There is **no** `xi ui extract` command. Texture export is:

```
uv run xi ui tex export <DAT_FILE> [OPTIONS]
```

Shortcuts (registered names only):

```
uv run xi ui tex sx <DAT_FILE>   # export DDS + convert to PNG
uv run xi ui tex si <DAT_FILE>   # PNG → DDS + import
```

See **[export.md](export.md)** for full usage, inventories, and format notes.

---

## Alpha brightening

`sx` brightens each texture's alpha to full range on export and records the factor in
`alpha-scale.json`, because FFXI stores UI alpha at half scale (`0x80` = opaque) and a
raw export looks ~50% transparent in an editor. `si` reads the sidecar and restores the
original values. Pass `--raw-alpha` to export untouched alpha instead.
See [export.md](export.md#alpha-is-stored-at-half-scale).
