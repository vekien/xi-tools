# Key Item Categories

This page documents how the retail client files key items into UI categories, how to add
custom key items to a chosen category, and what the current live category layout looks like.

The important discovery from the mount work: key-item category membership is based on
physical row order in the key-item `d_msg` DAT, not on a clean numeric key-item id range and
not on a hardcoded `FFXiMain.dll` range for the normal `0-4095` key-item collection UI.

## Files

| Language | File id | DAT | Format |
|---|---:|---|---|
| EN | `0x0D999` | `ROM/175/35.DAT` | `d_msg`, XOR `0xFF` |
| JP | `0x0D921` | `ROM/175/34.DAT` | `d_msg`, XOR `0xFF` |

Each key-item block is keyed by `sub[0].marker`, which is the actual key-item id. The table
is sparse: a key item with id `3375` does not need 3375 earlier rows.

Text slots differ by language:

| Language | Name | Plural | Description |
|---|---:|---:|---:|
| EN | `sub[4]` | `sub[5]` | `sub[6]` |
| JP | `sub[1]` | none | `sub[2]` |

## Category Model

Category separators are normal table rows whose `sub[0].marker` is `0` and whose displayed
text starts with `-`, such as `-Mounts` or `-Abyssea`.

The separator acts as a footer for the rows before it:

```text
[key-item rows in category]
[marker-0 row named "-Category"]
[key-item rows in next category]
[marker-0 row named "-Next Category"]
```

To place a key item in a category, insert its block before that category's separator row. Do
not append custom key items to the end of the file unless they intentionally belong after the
final category separator.

Category placement only controls where the key item appears in the Key Items UI. It does not
implement gameplay behavior. Claim slips, Mog Garden effects, Active Effects, map behavior,
temporary removal, and quest progression still need content-specific server/client logic.

## Current EN Layout

These row positions are from the current live EN key-item DAT after the Cyakko mount fix. Use
them for reference, not as hardcoded constants. Custom insertions shift later row numbers.
Production tooling should find category separators by label.

| Category | Entries | Entry Rows | Separator Row | Numeric Id Min | Numeric Id Max |
|---|---:|---:|---:|---:|---:|
| Temporary Key Items | 1625 | `1-1625` | `1626` | `1` | `3374` |
| Permanent Key Items | 388 | `1627-2014` | `2015` | `8` | `3366` |
| Abyssea | 412 | `2016-2427` | `2428` | `1271` | `1766` |
| Voidwatch | 117 | `2429-2545` | `2546` | `1539` | `2064` |
| Geas Fete | 102 | `2547-2648` | `2649` | `2839` | `2922` |
| Mounts | 41 | `2650-2690` | `2691` | `3055` | `3122` |
| Mog Garden | 127 | `2692-2818` | `2819` | `2390` | `3201` |
| Magical Maps | 134 | `2820-2953` | `2954` | `385` | `2309` |
| Claim Slips | 89 | `2955-3043` | `3044` | `654` | `1972` |
| Active Effects | 192 | `3045-3236` | `3237` | `512` | `3326` |

Row `0` in the EN table is a non-category marker-0 filler named `in-text name`; the first real
category content starts at row `1`.

JP mostly follows the same category order, but the labels are localized and one section is
split differently near the end: JP has a job-specific attire-related separator before the
final active-effects section. Do not assume EN row numbers match JP forever. Find the intended
JP separator by localized label or by a maintained EN-to-JP category map.

## Adding Key Items To Categories

Generic process:

1. Choose an unused key-item id.
2. Clone a compatible key-item block template.
3. Set `sub[0].marker` to the chosen key-item id.
4. Set EN text slots `4/5/6` and JP text slots `1/2`.
5. Find the target category separator row in EN and JP.
6. Insert the new block immediately before that separator.
7. Update server-side enums/scripts so the same id can be granted and checked.
8. Implement any actual behavior in content-specific code.

Recommended custom id space from the current client is `3375-4095`. The live table currently
uses ids through `3374`, with `3361` also unused. The client ownership bitmap path supports
`0-4095`. Retail updates could consume more ids, so keep custom allocations documented.

## Category-Specific Notes

### Active Effects

Insert before the `-Active Effects` separator.

This only files the item under Active Effects. It does not apply a buff, cheer, moghancement,
or modifier. Those effects need separate logic. The current Active Effects section contains a
mix of shards, souls, moghancements, moglifications, and cheers, so numeric ids are not a
category contract.

### Claim Slips

Insert before the `-Claim Slips` separator.

This only makes the key item display under Claim Slips. Porter Moogle storage, claim/redeem
rules, and equipment-set behavior must be implemented separately in server/content logic.

### Mog Garden

Insert before the `-Mog Garden` separator.

This is useful for mementos, garden permits, invitations, and similar UI organization. It does
not automatically unlock garden systems or apply cheers.

### Abyssea

Insert before the `-Abyssea` separator if the content really belongs there.

Since this project has no Abyssea content, this category can also be renamed and repurposed.
Renaming changes only the UI category label; the rows inside that section remain in the same
section.

### Temporary Key Items

Insert before the `-Temporary Key Items` separator.

Do not treat Temporary as a numeric id range. The current EN section contains 1625 entries,
but their numeric ids range from `1` to `3374`. Temporary behavior is server/content behavior:
the server decides when the key item is granted, consumed, or removed.

### Permanent Key Items

Insert before the `-Permanent Key Items` separator.

Do not treat Permanent as a numeric id range. The current EN section contains 388 entries,
but their numeric ids range from `8` to `3366`. Permanence is server/content behavior, not a
client-enforced property of the category.

## Renaming Categories

Category labels can be renamed by editing the separator row text.

Rules:

1. Keep `sub[0].marker = 0`.
2. Keep the leading `-` in the displayed text.
3. Rename both EN and JP labels.
4. Do not move the separator unless you intentionally want to change category boundaries.

Example: renaming `-Abyssea` to `-Custom Records` would make the existing Abyssea-section key
items appear under Custom Records. It would not change their ids or behavior.

## Tooling Recommendation

Avoid hardcoding row positions in content tools. Add a generic helper that works by category
label:

```python
set_key_item_in_category(
    category="mog_garden",
    key_item=3375,
    name_en="Custom Garden Permit",
    desc_en="Allows access to custom garden content.",
    name_jp="Custom Garden Permit",
    desc_jp="Allows access to custom garden content.",
)
```

That helper should:

1. Map `category` to EN and JP separator labels.
2. Load the EN and JP key-item DATs.
3. Remove any existing block with the same key-item id.
4. Insert the rebuilt block before the target separator.
5. Preserve all unrelated blocks byte-for-byte.

Mounts now follow this model in `xi.mount.xi_core.set_key_item()`: mount key items are kept
inside the Mounts section instead of being appended to the file tail.
