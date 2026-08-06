# xi zone reset

Restore a zone DAT to its **pristine** original, discarding every edit made by the other
`xi zone` commands. The undo button for all zone editing.

---

## Usage

```
uv run xi zone reset <dat> [--dry-run]
```

| Argument / Option | Description |
|---|---|
| `<dat>` | Zone DAT or ROM-relative spec (e.g. `ROM/1/41`) |
| `--dry-run` | Show what would be restored, without doing it |

```bash
uv run xi zone reset ROM/1/41
```

---

## What it does

Restores the DAT from the `<dat>.base` backup that the first edit created.

You're back to the original game data. Because most edit commands *layer*
(re-running stacks more changes), `reset` is the clean way to start over.

---

## Related commands

- **[`object import`](../object/import.md)** / **[`object clone`](../object/clone.md)** — additive edits that `reset` undoes
- **[`zone import-json`](import-json.md)** — apply editor change-sets (also undone by `reset`)
