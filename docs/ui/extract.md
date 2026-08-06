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
