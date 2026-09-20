# xi server — the local LandSandBoat server

Commands that read (and, for an ability build, write to) your own server: its database and
its source checkout. Everything is configured in xi-tools' `.env` — the model viewer's
**Settings › Local Server** edits the same lines:

| Key | What |
|---|---|
| `XI_SERVER_DIR` | The server checkout (the folder with `scripts/actions`, `src/map`, `settings/`). |
| `XI_DB_HOST`, `XI_DB_PORT`, `XI_DB_USER`, `XI_DB_PASSWORD`, `XI_DB_NAME` | The database login. A key left out uses the default (`127.0.0.1`, `3306`, `root`, an empty password, `xidb`). |

The server's `settings/network.lua` is never read for credentials. A database counts as
configured once `XI_DB_HOST`, `XI_DB_USER` or `XI_DB_NAME` is set. No command prints the
password or puts it in JSON, and none takes it as an argument.

```bash
uv run xi server check [--json] [--no-db] [--no-binary]   # what is set up, read-only
uv run xi server ws-widen [--out DIR] [--print] [--json]  # the C++ patch + SQL for weapon skills above 255
uv run xi server db "SELECT …"                            # run a query (json/csv/table)
uv run xi server status                                   # which server processes are running
```

The ability build's server steps — `xi dats build --apply-db / --menu-record / --lua-stub`
— are in [../ability/mixer.md](../ability/mixer.md#database-update-client-menu-record-lua-stub).

## `xi server check`

Read-only. It reports the server folder (and whether it holds `scripts/actions`), where
each database field comes from (`env` or `default`), whether it connects (version,
`sql_mode`), the `animation` column of `spell_list`, `abilities` and `weapon_skills`, and
the weapon-skill state below.

```text
Local server
  folder:        D:\cexi-server\catseyexi  (scripts/actions found)
  database:      xidb@127.0.0.1:3306 as xi  (all from xi-tools .env)
  connected:     10.11.6-MariaDB · sql_mode STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION
  tables:        spell_list.animation smallint(5) unsigned · abilities.animation smallint(5) unsigned · weapon_skills.animation tinyint(3) unsigned
  weapon skills: stock — 0–255 only (xi server ws-widen writes the C++ patch and SQL)
```

`--no-db` makes no connection attempt, `--no-binary` skips the `xi_map.pdb` probe and the
process check. `--json` prints `xi.server-check.v1`
([schema](../../schema/server_check.json)) and always exits 0; so does the text form.

## `xi server ws-widen`

Custom weapon skills use animation numbers 264–527 (the cexislots plugin reads 272–527).
The server can't carry those yet: `weapon_skills.animation` only stores 0–255, and xi_map
reads it as an 8-bit number in four places in its C++. So a published weapon skill would
play its number minus 256 — 300 plays as 44, a retail motion. The patch makes those four
spots 16-bit and the SQL widens the column; retail weapon skills (all below 256) are
unaffected.

`ws-widen` reads the three source files under `XI_SERVER_DIR` and writes, into `--out`
(default `projects/patches/ws_animation_16bit/`):

- `900-xitools-WsAnimation16.patch` — the diff from the stock lines to the 16-bit ones, made
  from your own checkout (from its stock form when it is already widened), so it applies
  with `git apply`;
- `ws_animation_16bit.sql` — the idempotent `ALTER TABLE weapon_skills MODIFY COLUMN
  animation smallint(5) unsigned NOT NULL DEFAULT '0'`;
- `README.md` — the why and the steps below.

It never edits the checkout, never connects to the database, and never commits, builds or
restarts anything; a second run rewrites nothing (`already`). With no server folder, or a
source it doesn't recognise (half widened, or a line moved), the patch is `refused` and the
four target lines are listed for a hand edit; the SQL and README are still written.
`--print` writes nothing and returns the texts. `--json` prints `xi.server-ws-widen.v1`
([schema](../../schema/server_ws_widen.json)) and always exits 0; the text form exits 1
when the patch was refused. The model viewer's **Settings › Local Server › Weapon skills ›
Get the C++ patch** runs it and opens the folder.

Then:

1. `git -C <server> apply <out>/900-xitools-WsAnimation16.patch` (or keep it with your
   module's patches for dbtool to apply);
2. run `ws_animation_16bit.sql` (a dbtool update recreates the column as `tinyint`, so run
   it again after one, or put it in a module's SQL);
3. rebuild xi_map (`xi server check` prints the `cmake --build … --target xi_map` line it
   found) and restart it.

`xi server check` shows each part: the source (`stock` / `widened`), the column, and
whether xi_map was rebuilt — read from `xi_map.pdb` (`m_AnimationId` as `uint16`), or,
without a PDB, from the exe being newer than the sources. A weapon-skill Database Update
above 255 goes ahead only when the column is wide and xi_map reads 16 bits (with no PDB it
goes ahead with a warning).

The patch, for the current LandSandBoat source:

```diff
diff --git a/src/map/utils/battleutils.cpp b/src/map/utils/battleutils.cpp
--- a/src/map/utils/battleutils.cpp
+++ b/src/map/utils/battleutils.cpp
@@ -184,7 +184,7 @@
         PWeaponSkill->setType(rset->get<uint8>("type"));
         PWeaponSkill->setSkillLevel(rset->get<uint16>("skilllevel"));
         PWeaponSkill->setElement(rset->get<uint8>("element"));
-        PWeaponSkill->setAnimationId(rset->get<uint8>("animation"));
+        PWeaponSkill->setAnimationId(rset->get<uint16>("animation"));
         PWeaponSkill->setAnimationTime(std::chrono::milliseconds(rset->get<uint32>("animationTime")));
         PWeaponSkill->setRange(rset->get<uint8>("range"));
         PWeaponSkill->setAoe(rset->get<uint8>("aoe"));
diff --git a/src/map/weapon_skill.cpp b/src/map/weapon_skill.cpp
--- a/src/map/weapon_skill.cpp
+++ b/src/map/weapon_skill.cpp
@@ -111,7 +111,7 @@
     m_name = name;
 }
 
-void CWeaponSkill::setAnimationId(const uint8 id)
+void CWeaponSkill::setAnimationId(const uint16 id)
 {
     m_AnimationId = id;
 }
diff --git a/src/map/weapon_skill.h b/src/map/weapon_skill.h
--- a/src/map/weapon_skill.h
+++ b/src/map/weapon_skill.h
@@ -61,7 +61,7 @@
     void setTertiarySkillchain(uint8 skillchain);
     void setAoe(uint8 aoe);
     void setRadius(uint8 radius);
-    void setAnimationId(uint8 id);
+    void setAnimationId(uint16 id);
     void setAnimationTime(timer::duration time);
     void setType(uint8 type);
     void setMainOnly(uint8 main);
@@ -79,7 +79,7 @@
     uint8                          m_TypeID;
     std::array<uint8, MAX_JOBTYPE> m_Job{};
     uint16                         m_Skilllevel;
-    uint8                          m_AnimationId;
+    uint16                         m_AnimationId;
     timer::duration                m_AnimationTime{};
     uint8                          m_Element;
     uint8                          m_PrimarySkillchain;
```

A weapon skill that can't wait for a rebuild can instead be tried as a humanoid mob skill
(`mob_skills.mob_anim_id` is already 16-bit); the publish folder's SQL shows the row.
