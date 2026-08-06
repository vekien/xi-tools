# FFXI Zone DATs by Zone Name

Map of FINAL FANTASY XI zones to their DAT files, sorted alphabetically by zone name.
~302 zone slots (IDs 0–301). Original data extracted by Devi Ltti.

Source: https://www.reddit.com/r/ffximodding/comments/9ndg2d/complete_list_of_zone_dats_by_zone_id/
(fetched via Wayback Machine snapshot 2025-02-09)

**Cross-checked against the CatsEyeXI client** (2026-06) using xim's resolution method —
see [Verification](#verification-against-the-client-filetable) below. Zone **names** are
from the client's name table `ROM/165/84.DAT`; the **Model** column is resolved from the
merged FileTable (`FTABLE*.DAT`/`VTABLE*.DAT`). Dialog/NPC/Event are the retail convention
and are **not** verifiable by this method (see notes).

## Generate Current Zone JSON

`xi` can generate the current client-backed zone list directly from `ROM/165/84.DAT`
and the merged FTABLE/VTABLE data:

```bash
uv run xi zone json
```

This emits the level-editor-compatible JSON shape:

```json
[
  { "id": 245, "name": "Lower Jeuno", "path": "game/ROM/1/41.DAT" }
]
```

To refresh the web level editor manifest:

```bash
uv run xi zone json -o web/leveleditor/zones.json
```

As of the 2026-06 CatsEyeXI client this resolves 294 valid named/model zones. This is the
preferred source for tooling because it follows the live client file tables instead of a
static copied list.

## Columns

| Column | Contents |
|--------|----------|
| **ID** | Zone ID number |
| **Name** | Zone name (blank = unused/unnamed slot) |
| **Model** | Zone geometry / map DAT |
| **Dialog** | Dialog (XISTRING) DAT |
| **NPCs** | NPC placement DAT |
| **Events** | Event script DAT |

## Zones (alphabetical)

| ID | Name | Model | Dialog | NPCs | Events |
|---:|------|-------|--------|------|--------|
| 44 | Abdhaljs Isle-Purgonorgo | ROM3/5/7.DAT | ROM3/2/54.DAT | ROM3/3/26.DAT | ROM3/0/110.DAT |
| 218 | Abyssea - Altepa | ROM/258/110.DAT | ROM/25/27.DAT | ROM/27/27.DAT | ROM/21/27.DAT |
| 215 | Abyssea - Attohwa | ROM/254/7.DAT | ROM/25/24.DAT | ROM/27/24.DAT | ROM/21/24.DAT |
| 255 | Abyssea - Empyreal Paradox | ROM/258/113.DAT | ROM/25/64.DAT | ROM/27/64.DAT | ROM/21/64.DAT |
| 254 | Abyssea - Grauberg | ROM/258/112.DAT | ROM/25/63.DAT | ROM/27/63.DAT | ROM/21/63.DAT |
| 15 | Abyssea - Konschtat | ROM/240/106.DAT | ROM/23/80.DAT | ROM/25/80.DAT | ROM/19/80.DAT |
| 132 | Abyssea - La Theine | ROM/240/108.DAT | ROM/24/69.DAT | ROM/26/69.DAT | ROM/20/69.DAT |
| 216 | Abyssea - Misareaux | ROM/254/8.DAT | ROM/25/25.DAT | ROM/27/25.DAT | ROM/21/25.DAT |
| 45 | Abyssea - Tahrongi | ROM/240/107.DAT | ROM/23/110.DAT | ROM/25/110.DAT | ROM/19/110.DAT |
| 253 | Abyssea - Uleguerand | ROM/258/111.DAT | ROM/25/62.DAT | ROM/27/62.DAT | ROM/21/62.DAT |
| 217 | Abyssea - Vunkerl | ROM/254/9.DAT | ROM/25/26.DAT | ROM/27/26.DAT | ROM/21/26.DAT |
| 50 | Aht Urhgan Whitegate | ROM4/0/3.DAT | ROM4/0/123.DAT | ROM4/1/49.DAT | ROM4/0/55.DAT |
| 48 | Al Zahbi | ROM4/0/2.DAT | ROM4/0/121.DAT | ROM4/1/47.DAT | ROM4/0/53.DAT |
| 33 | Al'Taieu | ROM3/0/32.DAT | ROM3/2/43.DAT | ROM3/3/15.DAT | ROM3/0/99.DAT |
| 152 | Altar Room | ROM/0/64.DAT | ROM/24/89.DAT | ROM/26/89.DAT | ROM/20/89.DAT |
| 72 | Alzadaal Undersea Ruins | ROM4/0/25.DAT | ROM4/1/17.DAT | ROM4/1/71.DAT | ROM4/0/77.DAT |
| 38 | Apollyon | ROM3/5/1.DAT | ROM3/2/48.DAT | ROM3/3/20.DAT | ROM3/0/104.DAT |
| 54 | Arrapago Reef | ROM4/0/7.DAT | ROM4/0/127.DAT | ROM4/1/53.DAT | ROM4/0/59.DAT |
| 74 | Arrapago Remnants | ROM4/0/27.DAT | ROM4/1/19.DAT | ROM4/1/73.DAT | ROM4/0/79.DAT |
| 7 | Attohwa Chasm | ROM3/0/6.DAT | ROM3/2/17.DAT | ROM3/2/117.DAT | ROM3/0/73.DAT |
| 68 | Aydeewa Subterrane | ROM4/0/21.DAT | ROM4/1/13.DAT | ROM4/1/67.DAT | ROM4/0/73.DAT |
| 146 | Balga's Dais | ROM/0/105.DAT | ROM/24/83.DAT | ROM/26/83.DAT | ROM/20/83.DAT |
| 235 | Bastok Markets | ROM/1/35.DAT | ROM/25/44.DAT | ROM/27/44.DAT | ROM/21/44.DAT |
| 87 | Bastok Markets [S] | ROM5/0/7.DAT | ROM/24/24.DAT | ROM/26/24.DAT | ROM/20/24.DAT |
| 234 | Bastok Mines | ROM/1/34.DAT | ROM/25/43.DAT | ROM/27/43.DAT | ROM/21/43.DAT |
| 224 | Bastok-Jeuno Airship | ROM/1/27.DAT | ROM/25/33.DAT | ROM/27/33.DAT | ROM/21/33.DAT |
| 105 | Batallia Downs | ROM/0/122.DAT | ROM/24/42.DAT | ROM/26/42.DAT | ROM/20/42.DAT |
| 84 | Batallia Downs [S] | ROM5/0/4.DAT | ROM/24/21.DAT | ROM/26/21.DAT | ROM/20/21.DAT |
| 147 | Beadeaux | ROM/0/61.DAT | ROM/24/84.DAT | ROM/26/84.DAT | ROM/20/84.DAT |
| 92 | Beadeaux [S] | ROM5/0/12.DAT | ROM/24/29.DAT | ROM/26/29.DAT | ROM/20/29.DAT |
| 6 | Bearclaw Pinnacle | ROM3/0/5.DAT | ROM3/2/16.DAT | ROM3/2/116.DAT | ROM3/0/72.DAT |
| 111 | Beaucedine Glacier | ROM/0/72.DAT | ROM/24/48.DAT | ROM/26/48.DAT | ROM/20/48.DAT |
| 136 | Beaucedine Glacier [S] | ROM5/0/21.DAT | ROM/24/73.DAT | ROM/26/73.DAT | ROM/20/73.DAT |
| 127 | Behemoth's Dominion | ROM/1/3.DAT | ROM/24/64.DAT | ROM/26/64.DAT | ROM/20/64.DAT |
| 75 | Bhaflau Remnants | ROM4/0/28.DAT | ROM4/1/20.DAT | ROM4/1/74.DAT | ROM4/0/80.DAT |
| 52 | Bhaflau Thickets | ROM4/7/55.DAT | ROM4/0/125.DAT | ROM4/1/51.DAT | ROM4/0/57.DAT |
| 4 | Bibiki Bay | ROM3/0/3.DAT | ROM3/2/14.DAT | ROM3/2/114.DAT | ROM3/0/70.DAT |
| 8 | Boneyard Gully | ROM3/0/7.DAT | ROM3/2/18.DAT | ROM3/2/118.DAT | ROM3/0/74.DAT |
| 167 | Bostaunieux Oubliette | ROM/0/108.DAT | ROM/24/104.DAT | ROM/26/104.DAT | ROM/20/104.DAT |
| 118 | Buburimu Peninsula | ROM/0/71.DAT | ROM/24/55.DAT | ROM/26/55.DAT | ROM/20/55.DAT |
| 79 | Caedarva Mire | ROM4/0/32.DAT | ROM4/1/24.DAT | ROM4/1/78.DAT | ROM4/0/84.DAT |
| 113 | Cape Teriggan | ROM2/0/0.DAT | ROM2/17/46.DAT | ROM2/13/95.DAT | ROM2/13/5.DAT |
| 2 | Carpenters' Landing | ROM3/0/1.DAT | ROM3/2/12.DAT | ROM3/2/112.DAT | ROM3/0/68.DAT |
| 151 | Castle Oztroja | ROM/0/63.DAT | ROM/24/88.DAT | ROM/26/88.DAT | ROM/20/88.DAT |
| 99 | Castle Oztroja [S] | ROM5/0/19.DAT | ROM/24/36.DAT | ROM/26/36.DAT | ROM/20/36.DAT |
| 161 | Castle Zvahl Baileys | ROM/0/60.DAT | ROM/24/98.DAT | ROM/26/98.DAT | ROM/20/98.DAT |
| 138 | Castle Zvahl Baileys [S] | ROM5/0/23.DAT | ROM/24/75.DAT | ROM/26/75.DAT | ROM/20/75.DAT |
| 162 | Castle Zvahl Keep | ROM/0/73.DAT | ROM/24/99.DAT | ROM/26/99.DAT | ROM/20/99.DAT |
| 155 | Castle Zvahl Keep [S] | ROM5/0/24.DAT | ROM/24/92.DAT | ROM/26/92.DAT | ROM/20/92.DAT |
| 261 | Ceizak Battlegrounds | ROM9/0/8.DAT | ROM9/5/106.DAT | ROM9/6/50.DAT | ROM9/5/58.DAT |
| 284 | Celennia Memorial Library | ROM/303/26.DAT | ROM/303/33.DAT | ROM/303/37.DAT | ROM/303/29.DAT |
| 168 | Chamber of Oracles | ROM2/0/11.DAT | ROM2/17/101.DAT | ROM2/13/111.DAT | ROM2/13/21.DAT |
| 233 | Chateau d'Oraguille | ROM/1/33.DAT | ROM/25/42.DAT | ROM/27/42.DAT | ROM/21/42.DAT |
| 70 | Chocobo Circuit | ROM4/5/106.DAT | ROM4/1/15.DAT | ROM4/1/69.DAT | ROM4/0/75.DAT |
| 270 | Cirdas Caverns | ROM9/0/17.DAT | ROM9/5/115.DAT | ROM9/6/59.DAT | ROM9/5/67.DAT |
| 271 | Cirdas Caverns [U] | ROM9/0/18.DAT | ROM9/5/116.DAT | ROM9/6/60.DAT | ROM9/5/68.DAT |
| 207 | Cloister of Flames | ROM2/0/21.DAT | ROM2/18/12.DAT | ROM2/14/1.DAT | ROM2/13/39.DAT |
| 203 | Cloister of Frost | ROM2/0/19.DAT | ROM2/18/8.DAT | ROM2/13/127.DAT | ROM2/13/37.DAT |
| 201 | Cloister of Gales | ROM2/0/17.DAT | ROM2/18/6.DAT | ROM2/13/125.DAT | ROM2/13/35.DAT |
| 202 | Cloister of Storms | ROM2/0/18.DAT | ROM2/18/7.DAT | ROM2/13/126.DAT | ROM2/13/36.DAT |
| 211 | Cloister of Tides | ROM2/0/23.DAT | ROM2/18/16.DAT | ROM2/14/4.DAT | ROM2/13/42.DAT |
| 209 | Cloister of Tremors | ROM2/0/22.DAT | ROM2/18/14.DAT | ROM2/14/3.DAT | ROM2/13/41.DAT |
| 197 | Crawlers' Nest | ROM/0/76.DAT | ROM/25/6.DAT | ROM/27/6.DAT | ROM/21/6.DAT |
| 171 | Crawlers' Nest [S] | ROM5/0/27.DAT | ROM/24/108.DAT | ROM/26/108.DAT | ROM/20/108.DAT |
| 191 | Dangruf Wadi | ROM/0/52.DAT | ROM/25/0.DAT | ROM/27/0.DAT | ROM/21/0.DAT |
| 149 | Davoi | ROM/0/99.DAT | ROM/24/86.DAT | ROM/26/86.DAT | ROM/20/86.DAT |
| 160 | Den of Rancor | ROM2/12/110.DAT | ROM2/17/93.DAT | ROM2/13/109.DAT | ROM2/13/19.DAT |
| 290 | Desuetia - Empyreal Paradox | ROM/342/72.DAT | ROM/342/84.DAT | ROM/342/93.DAT | ROM/342/78.DAT |
| 272 | Dho Gates | ROM9/0/19.DAT | ROM9/5/117.DAT | ROM9/6/61.DAT | ROM9/5/69.DAT |
| 43 | Diorama Abdhaljs-Ghelsba | ROM3/5/6.DAT | ROM3/2/53.DAT | ROM3/3/25.DAT | ROM3/0/109.DAT |
| 154 | Dragon's Aery | ROM2/0/9.DAT | ROM2/17/87.DAT | ROM2/13/107.DAT | ROM2/13/17.DAT |
| 186 | Dynamis - Bastok | ROM2/12/117.DAT | ROM2/17/119.DAT | ROM2/13/122.DAT | ROM2/13/32.DAT |
| 295 | Dynamis - Bastok [D] | ROM/355/3.DAT | ROM/355/8.DAT | ROM/355/5.DAT | ROM/355/6.DAT |
| 134 | Dynamis - Beaucedine | ROM2/12/108.DAT | ROM2/17/67.DAT | ROM2/13/104.DAT | ROM2/13/14.DAT |
| 40 | Dynamis - Buburimu | ROM3/5/3.DAT | ROM3/2/50.DAT | ROM3/3/22.DAT | ROM3/0/106.DAT |
| 188 | Dynamis - Jeuno | ROM2/12/119.DAT | ROM2/17/121.DAT | ROM2/13/124.DAT | ROM2/13/34.DAT |
| 297 | Dynamis - Jeuno [D] | ROM/355/47.DAT | ROM/355/51.DAT | ROM/355/54.DAT | ROM/355/49.DAT |
| 41 | Dynamis - Qufim | ROM3/5/4.DAT | ROM3/2/51.DAT | ROM3/3/23.DAT | ROM3/0/107.DAT |
| 185 | Dynamis - San d'Oria | ROM2/12/116.DAT | ROM2/17/118.DAT | ROM2/13/121.DAT | ROM2/13/31.DAT |
| 294 | Dynamis - San d'Oria [D] | ROM/354/109.DAT | ROM/354/113.DAT | ROM/354/116.DAT | ROM/354/111.DAT |
| 42 | Dynamis - Tavnazia | ROM3/5/5.DAT | ROM3/2/52.DAT | ROM3/3/24.DAT | ROM3/0/108.DAT |
| 39 | Dynamis - Valkurm | ROM3/5/2.DAT | ROM3/2/49.DAT | ROM3/3/21.DAT | ROM3/0/105.DAT |
| 187 | Dynamis - Windurst | ROM2/12/118.DAT | ROM2/17/120.DAT | ROM2/13/123.DAT | ROM2/13/33.DAT |
| 296 | Dynamis - Windurst [D] | ROM/355/32.DAT | ROM/355/36.DAT | ROM/355/39.DAT | ROM/355/34.DAT |
| 135 | Dynamis - Xarcabard | ROM2/12/109.DAT | ROM2/17/68.DAT | ROM2/13/105.DAT | ROM2/13/15.DAT |
| 101 | East Ronfaure | ROM/0/121.DAT | ROM/24/38.DAT | ROM/26/38.DAT | ROM/20/38.DAT |
| 81 | East Ronfaure [S] | ROM5/0/1.DAT | ROM/24/18.DAT | ROM/26/18.DAT | ROM/20/18.DAT |
| 116 | East Sarutabaruta | ROM/1/0.DAT | ROM/24/53.DAT | ROM/26/53.DAT | ROM/20/53.DAT |
| 257 | Eastern Adoulin | ROM9/0/4.DAT | ROM9/5/102.DAT | ROM9/6/46.DAT | ROM9/5/54.DAT |
| 114 | Eastern Altepa Desert | ROM2/0/1.DAT | ROM2/17/47.DAT | ROM2/13/96.DAT | ROM2/13/6.DAT |
| 36 | Empyreal Paradox | ROM3/0/35.DAT | ROM3/2/46.DAT | ROM3/3/18.DAT | ROM3/0/102.DAT |
| 289 | Escha - Ru'Aun | ROM/337/59.DAT | ROM/337/63.DAT | ROM/337/66.DAT | ROM/337/61.DAT |
| 288 | Escha - Zi'Tah | ROM/332/99.DAT | ROM/332/106.DAT | ROM/332/109.DAT | ROM/332/104.DAT |
| 86 | Everbloom Hollow | ROM5/0/6.DAT | ROM/24/23.DAT | ROM/26/23.DAT | ROM/20/23.DAT |
| 204 | Fei'Yin | ROM/1/18.DAT | ROM/25/13.DAT | ROM/27/13.DAT | ROM/21/13.DAT |
| 285 | Feretory | ROM/306/54.DAT | ROM/306/58.DAT | ROM/306/61.DAT | ROM/306/56.DAT |
| 262 | Foret de Hennetiel | ROM9/0/9.DAT | ROM9/5/107.DAT | ROM9/6/51.DAT | ROM9/5/59.DAT |
| 141 | Fort Ghelsba | ROM/1/7.DAT | ROM/24/78.DAT | ROM/26/78.DAT | ROM/20/78.DAT |
| 96 | Fort Karugo-Narugo [S] | ROM5/0/16.DAT | ROM/24/33.DAT | ROM/26/33.DAT | ROM/20/33.DAT |
| 170 | Full Moon Fountain | ROM2/0/12.DAT | ROM2/17/103.DAT | ROM2/13/112.DAT | ROM2/13/22.DAT |
| 200 | Garlaige Citadel | ROM/1/17.DAT | ROM/25/9.DAT | ROM/27/9.DAT | ROM/21/9.DAT |
| 164 | Garlaige Citadel [S] | ROM5/0/26.DAT | ROM/24/101.DAT | ROM/26/101.DAT | ROM/20/101.DAT |
| 140 | Ghelsba Outpost | ROM/0/95.DAT | ROM/24/77.DAT | ROM/26/77.DAT | ROM/20/77.DAT |
| 129 | Ghoyu's Reverie | ROM5/0/20.DAT | ROM/24/66.DAT | ROM/26/66.DAT | ROM/20/66.DAT |
| 145 | Giddeus | ROM/0/104.DAT | ROM/24/82.DAT | ROM/26/82.DAT | ROM/20/82.DAT |
| 34 | Grand Palace of Hu'Xzoi | ROM3/0/33.DAT | ROM3/2/44.DAT | ROM3/3/16.DAT | ROM3/0/100.DAT |
| 89 | Grauberg [S] | ROM5/0/9.DAT | ROM/24/26.DAT | ROM/26/26.DAT | ROM/20/26.DAT |
| 196 | Gusgen Mines | ROM/1/16.DAT | ROM/25/5.DAT | ROM/27/5.DAT | ROM/21/5.DAT |
| 212 | Gustav Tunnel | ROM2/12/121.DAT | ROM2/18/17.DAT | ROM2/14/5.DAT | ROM2/13/43.DAT |
| 251 | Hall of the Gods | ROM2/21/114.DAT | ROM2/18/56.DAT | ROM2/14/10.DAT | ROM2/13/48.DAT |
| 14 | Hall of Transference | ROM3/0/13.DAT | ROM3/2/24.DAT | ROM3/2/124.DAT | ROM3/0/80.DAT |
| 62 | Halvung | ROM4/0/15.DAT | ROM4/1/7.DAT | ROM4/1/61.DAT | ROM4/0/67.DAT |
| 78 | Hazhalm Testing Grounds | ROM4/0/31.DAT | ROM4/1/23.DAT | ROM4/1/77.DAT | ROM4/0/83.DAT |
| 242 | Heavens Tower | ROM/1/38.DAT | ROM/25/51.DAT | ROM/27/51.DAT | ROM/21/51.DAT |
| 139 | Horlais Peak | ROM/1/6.DAT | ROM/24/76.DAT | ROM/26/76.DAT | ROM/20/76.DAT |
| 205 | Ifrit's Cauldron | ROM2/0/20.DAT | ROM2/18/10.DAT | ROM2/14/0.DAT | ROM2/13/38.DAT |
| 55 | Ilrusi Atoll | ROM4/0/8.DAT | ROM4/1/0.DAT | ROM4/1/54.DAT | ROM4/0/60.DAT |
| 192 | Inner Horutoto Ruins | ROM/0/112.DAT | ROM/25/1.DAT | ROM/27/1.DAT | ROM/21/1.DAT |
| 67 | Jade Sepulcher | ROM4/0/20.DAT | ROM4/1/12.DAT | ROM4/1/66.DAT | ROM4/0/72.DAT |
| 104 | Jugner Forest | ROM/0/114.DAT | ROM/24/41.DAT | ROM/26/41.DAT | ROM/20/41.DAT |
| 82 | Jugner Forest [S] | ROM5/0/2.DAT | ROM/24/19.DAT | ROM/26/19.DAT | ROM/20/19.DAT |
| 267 | Kamihr Drifts | ROM9/0/14.DAT | ROM9/5/112.DAT | ROM9/6/56.DAT | ROM9/5/64.DAT |
| 250 | Kazham | ROM2/0/25.DAT | ROM2/18/55.DAT | ROM2/14/9.DAT | ROM2/13/47.DAT |
| 226 | Kazham-Jeuno Airship | ROM2/0/24.DAT | ROM2/18/31.DAT | ROM2/14/7.DAT | ROM2/13/45.DAT |
| 190 | King Ranperre's Tomb | ROM/1/14.DAT | ROM/24/127.DAT | ROM/26/127.DAT | ROM/20/127.DAT |
| 108 | Konschtat Highlands | ROM/0/90.DAT | ROM/24/45.DAT | ROM/26/45.DAT | ROM/20/45.DAT |
| 173 | Korroloka Tunnel | ROM2/0/13.DAT | ROM2/17/106.DAT | ROM2/13/113.DAT | ROM2/13/23.DAT |
| 174 | Kuftal Tunnel | ROM2/0/14.DAT | ROM2/17/107.DAT | ROM2/13/114.DAT | ROM2/13/24.DAT |
| 102 | La Theine Plateau | ROM/0/115.DAT | ROM/24/39.DAT | ROM/26/39.DAT | ROM/20/39.DAT |
| 85 | La Vaule [S] | ROM5/0/5.DAT | ROM/24/22.DAT | ROM/26/22.DAT | ROM/20/22.DAT |
| 180 | La'Loff Amphitheater | ROM2/12/114.DAT | ROM2/17/113.DAT | ROM2/13/119.DAT | ROM2/13/29.DAT |
| 213 | Labyrinth of Onzozo | ROM2/12/122.DAT | ROM2/18/18.DAT | ROM2/14/6.DAT | ROM2/13/44.DAT |
| 281 | Leafallia | ROM9/8/10.DAT | ROM/315/108.DAT | ROM/315/114.DAT | ROM/315/104.DAT |
| 63 | Lebros Cavern | ROM4/0/16.DAT | ROM4/1/8.DAT | ROM4/1/62.DAT | ROM4/0/68.DAT |
| 69 | Leujaoam Sanctum | ROM4/0/22.DAT | ROM4/1/14.DAT | ROM4/1/68.DAT | ROM4/0/74.DAT |
| 184 | Lower Delkfutt's Tower | ROM/1/13.DAT | ROM/24/121.DAT | ROM/26/121.DAT | ROM/20/121.DAT |
| 245 | Lower Jeuno | ROM/1/41.DAT | ROM/25/54.DAT | ROM/27/54.DAT | ROM/21/54.DAT |
| 24 | Lufaise Meadows | ROM3/0/23.DAT | ROM3/2/34.DAT | ROM3/3/6.DAT | ROM3/0/90.DAT |
| 65 | Mamook | ROM4/0/18.DAT | ROM4/1/10.DAT | ROM4/1/64.DAT | ROM4/0/70.DAT |
| 66 | Mamool Ja Training Grounds | ROM4/0/19.DAT | ROM4/1/11.DAT | ROM4/1/65.DAT | ROM4/0/71.DAT |
| 3 | Manaclipper | ROM3/0/2.DAT | ROM3/2/13.DAT | ROM3/2/113.DAT | ROM3/0/69.DAT |
| 183 | Maquette Abdhaljs-LegionA | ROM/1/12.DAT | ROM/24/120.DAT | ROM/26/120.DAT | ROM/20/120.DAT |
| 266 | Marjami Ravine | ROM9/0/13.DAT | ROM9/5/111.DAT | ROM9/6/55.DAT | ROM9/5/63.DAT |
| 198 | Maze of Shakhrami | ROM/0/75.DAT | ROM/25/7.DAT | ROM/27/7.DAT | ROM/21/7.DAT |
| 119 | Meriphataud Mountains | ROM/0/101.DAT | ROM/24/56.DAT | ROM/26/56.DAT | ROM/20/56.DAT |
| 97 | Meriphataud Mountains [S] | ROM5/0/17.DAT | ROM/24/34.DAT | ROM/26/34.DAT | ROM/20/34.DAT |
| 237 | Metalworks | ROM/1/37.DAT | ROM/25/46.DAT | ROM/27/46.DAT | ROM/21/46.DAT |
| 249 | Mhaura | ROM/1/44.DAT | ROM/25/58.DAT | ROM/27/58.DAT | ROM/21/58.DAT |
| 157 | Middle Delkfutt's Tower | ROM/0/56.DAT | ROM/24/94.DAT | ROM/26/94.DAT | ROM/20/94.DAT |
| 13 | Mine Shaft #2716 | ROM3/0/12.DAT | ROM3/2/23.DAT | ROM3/2/123.DAT | ROM3/0/79.DAT |
| 25 | Misareaux Coast | ROM3/0/24.DAT | ROM3/2/35.DAT | ROM3/3/7.DAT | ROM3/0/91.DAT |
| 280 | Mog Garden | ROM/309/10.DAT | ROM/303/32.DAT | ROM/303/36.DAT | ROM/303/28.DAT |
| 269 | Moh Gates | ROM9/0/16.DAT | ROM9/5/114.DAT | ROM9/6/58.DAT | ROM9/5/66.DAT |
| 31 | Monarch Linn | ROM3/0/30.DAT | ROM3/2/41.DAT | ROM3/3/13.DAT | ROM3/0/97.DAT |
| 150 | Monastic Cavern | ROM/0/100.DAT | ROM/24/87.DAT | ROM/26/87.DAT | ROM/20/87.DAT |
| 131 | Mordion Gaol | ROM/1/4.DAT | ROM/24/68.DAT | ROM/26/68.DAT | ROM/20/68.DAT |
| 265 | Morimar Basalt Fields | ROM9/0/12.DAT | ROM9/5/110.DAT | ROM9/6/54.DAT | ROM9/5/62.DAT |
| 282 | Mount Kamihr | ROM/315/102.DAT | ROM/315/109.DAT | ROM/315/115.DAT | ROM/315/105.DAT |
| 61 | Mount Zhayolm | ROM4/0/14.DAT | ROM4/1/6.DAT | ROM4/1/60.DAT | ROM4/0/66.DAT |
| 53 | Nashmau | ROM4/0/6.DAT | ROM4/0/126.DAT | ROM4/1/52.DAT | ROM4/0/58.DAT |
| 64 | Navukgo Execution Chamber | ROM4/0/17.DAT | ROM4/1/9.DAT | ROM4/1/63.DAT | ROM4/0/69.DAT |
| 12 | Newton Movalpolos | ROM3/0/11.DAT | ROM3/2/22.DAT | ROM3/2/122.DAT | ROM3/0/78.DAT |
| 49 | none | ROM/0/40.DAT | — | — | — |
| 252 | Norg | ROM2/0/27.DAT | ROM2/18/57.DAT | ROM2/14/11.DAT | ROM2/13/49.DAT |
| 106 | North Gustaberg | ROM/0/123.DAT | ROM/24/43.DAT | ROM/26/43.DAT | ROM/20/43.DAT |
| 88 | North Gustaberg [S] | ROM5/0/8.DAT | ROM/24/25.DAT | ROM/26/25.DAT | ROM/20/25.DAT |
| 231 | Northern San d'Oria | ROM/1/32.DAT | ROM/25/40.DAT | ROM/27/40.DAT | ROM/21/40.DAT |
| 77 | Nyzul Isle | ROM4/0/30.DAT | ROM4/1/22.DAT | ROM4/1/76.DAT | ROM4/0/82.DAT |
| 11 | Oldton Movalpolos | ROM3/0/10.DAT | ROM3/2/21.DAT | ROM3/2/121.DAT | ROM3/0/77.DAT |
| 46 | Open sea route to Al Zahbi | ROM4/0/0.DAT | ROM4/0/119.DAT | ROM4/1/45.DAT | ROM4/0/51.DAT |
| 47 | Open sea route to Mhaura | ROM4/0/1.DAT | ROM4/0/120.DAT | ROM4/1/46.DAT | ROM4/0/52.DAT |
| 193 | Ordelle's Caves | ROM/0/92.DAT | ROM/25/2.DAT | ROM/27/2.DAT | ROM/21/2.DAT |
| 194 | Outer Horutoto Ruins | ROM/1/15.DAT | ROM/25/3.DAT | ROM/27/3.DAT | ROM/21/3.DAT |
| 274 | Outer Ra'Kaznar | ROM9/0/21.DAT | ROM9/5/119.DAT | ROM9/6/63.DAT | ROM9/5/71.DAT |
| 275 | Outer Ra'Kaznar [U1] | ROM9/0/22.DAT | ROM9/5/120.DAT | ROM9/6/64.DAT | ROM9/5/72.DAT |
| 143 | Palborough Mines | ROM/0/88.DAT | ROM/24/80.DAT | ROM/26/80.DAT | ROM/20/80.DAT |
| 109 | Pashhow Marshlands | ROM/0/125.DAT | ROM/24/46.DAT | ROM/26/46.DAT | ROM/20/46.DAT |
| 90 | Pashhow Marshlands [S] | ROM5/0/10.DAT | ROM/24/27.DAT | ROM/26/27.DAT | ROM/20/27.DAT |
| 56 | Periqia | ROM4/0/9.DAT | ROM4/1/1.DAT | ROM4/1/55.DAT | ROM4/0/61.DAT |
| 1 | Phanauet Channel | ROM3/0/0.DAT | ROM3/2/11.DAT | ROM3/2/111.DAT | ROM3/0/67.DAT |
| 27 | Phomiuna Aqueducts | ROM3/0/26.DAT | ROM3/2/37.DAT | ROM3/3/9.DAT | ROM3/0/93.DAT |
| 236 | Port Bastok | ROM/1/36.DAT | ROM/25/45.DAT | ROM/27/45.DAT | ROM/21/45.DAT |
| 246 | Port Jeuno | ROM/1/42.DAT | ROM/25/55.DAT | ROM/27/55.DAT | ROM/21/55.DAT |
| 232 | Port San d'Oria | ROM/0/113.DAT | ROM/25/41.DAT | ROM/27/41.DAT | ROM/21/41.DAT |
| 240 | Port Windurst | ROM/0/80.DAT | ROM/25/49.DAT | ROM/27/49.DAT | ROM/21/49.DAT |
| 18 | Promyvion - Dem | ROM3/0/17.DAT | ROM3/2/28.DAT | ROM3/3/0.DAT | ROM3/0/84.DAT |
| 16 | Promyvion - Holla | ROM3/0/15.DAT | ROM3/2/26.DAT | ROM3/2/126.DAT | ROM3/0/82.DAT |
| 20 | Promyvion - Mea | ROM3/0/19.DAT | ROM3/2/30.DAT | ROM3/3/2.DAT | ROM3/0/86.DAT |
| 22 | Promyvion - Vahzl | ROM3/0/21.DAT | ROM3/2/32.DAT | ROM3/3/4.DAT | ROM3/0/88.DAT |
| 222 | Provenance | ROM/280/2.DAT | ROM/25/31.DAT | ROM/27/31.DAT | ROM/21/31.DAT |
| 9 | Pso'Xja | ROM3/0/8.DAT | ROM3/2/19.DAT | ROM3/2/119.DAT | ROM3/0/75.DAT |
| 206 | Qu'Bia Arena | ROM/1/19.DAT | ROM/25/15.DAT | ROM/27/15.DAT | ROM/21/15.DAT |
| 126 | Qufim Island | ROM/0/58.DAT | ROM/24/63.DAT | ROM/26/63.DAT | ROM/20/63.DAT |
| 208 | Quicksand Caves | ROM2/12/120.DAT | ROM2/18/13.DAT | ROM2/14/2.DAT | ROM2/13/40.DAT |
| 148 | Qulun Dome | ROM/0/62.DAT | ROM/24/85.DAT | ROM/26/85.DAT | ROM/20/85.DAT |
| 276 | Ra'Kaznar Inner Court | ROM9/0/23.DAT | ROM9/5/121.DAT | ROM9/6/65.DAT | ROM9/5/73.DAT |
| 277 | Ra'Kaznar Turris | ROM9/0/24.DAT | ROM9/5/122.DAT | ROM9/6/66.DAT | ROM9/5/74.DAT |
| 247 | Rabao | ROM2/12/123.DAT | ROM2/18/52.DAT | ROM2/14/8.DAT | ROM2/13/46.DAT |
| 258 | Rala Waterways | ROM9/0/5.DAT | ROM9/5/103.DAT | ROM9/6/47.DAT | ROM9/5/55.DAT |
| 259 | Rala Waterways [U] | ROM9/0/6.DAT | ROM9/5/104.DAT | ROM9/6/48.DAT | ROM9/5/56.DAT |
| 166 | Ranguemont Pass | ROM/1/10.DAT | ROM/24/103.DAT | ROM/26/103.DAT | ROM/20/103.DAT |
| 291 | Reisenjima | ROM/342/73.DAT | ROM/342/85.DAT | ROM/342/94.DAT | ROM/342/79.DAT |
| 292 | Reisenjima Henge | ROM/351/58.DAT | ROM/353/58.DAT | ROM/353/61.DAT | ROM/353/56.DAT |
| 293 | Reisenjima Sanctorium | ROM/342/74.DAT | ROM/342/86.DAT | ROM/342/95.DAT | ROM/342/80.DAT |
| 30 | Riverne - Site #A01 | ROM3/0/29.DAT | ROM3/2/40.DAT | ROM3/3/12.DAT | ROM3/0/96.DAT |
| 29 | Riverne - Site #B01 | ROM3/0/28.DAT | ROM3/2/39.DAT | ROM3/3/11.DAT | ROM3/0/95.DAT |
| 122 | Ro'Maeve | ROM2/0/3.DAT | ROM2/17/55.DAT | ROM2/13/98.DAT | ROM2/13/8.DAT |
| 110 | Rolanberry Fields | ROM/0/126.DAT | ROM/24/47.DAT | ROM/26/47.DAT | ROM/20/47.DAT |
| 91 | Rolanberry Fields [S] | ROM5/0/11.DAT | ROM/24/28.DAT | ROM/26/28.DAT | ROM/20/28.DAT |
| 130 | Ru'Aun Gardens | ROM2/12/107.DAT | ROM2/17/63.DAT | ROM2/13/103.DAT | ROM2/13/13.DAT |
| 243 | Ru'Lude Gardens | ROM/1/39.DAT | ROM/25/52.DAT | ROM/27/52.DAT | ROM/21/52.DAT |
| 93 | Ruhotz Silvermines | ROM5/0/13.DAT | ROM/24/30.DAT | ROM/26/30.DAT | ROM/20/30.DAT |
| 28 | Sacrarium | ROM3/0/27.DAT | ROM3/2/38.DAT | ROM3/3/10.DAT | ROM3/0/94.DAT |
| 163 | Sacrificial Chamber | ROM2/12/111.DAT | ROM2/17/96.DAT | ROM2/13/110.DAT | ROM2/13/20.DAT |
| 223 | San d'Oria-Jeuno Airship | ROM/1/26.DAT | ROM/25/32.DAT | ROM/27/32.DAT | ROM/21/32.DAT |
| 120 | Sauromugue Champaign | ROM/1/2.DAT | ROM/24/57.DAT | ROM/26/57.DAT | ROM/20/57.DAT |
| 98 | Sauromugue Champaign [S] | ROM5/0/18.DAT | ROM/24/35.DAT | ROM/26/35.DAT | ROM/20/35.DAT |
| 176 | Sea Serpent Grotto | ROM2/0/15.DAT | ROM2/17/109.DAT | ROM2/13/115.DAT | ROM2/13/25.DAT |
| 32 | Sealion's Den | ROM3/0/31.DAT | ROM3/2/42.DAT | ROM3/3/14.DAT | ROM3/0/98.DAT |
| 248 | Selbina | ROM/1/43.DAT | ROM/25/57.DAT | ROM/27/57.DAT | ROM/21/57.DAT |
| 221 | Ship bound for Mhaura | ROM/1/25.DAT | ROM/25/30.DAT | ROM/27/30.DAT | ROM/21/30.DAT |
| 228 | Ship bound for Mhaura | ROM/1/30.DAT | ROM/25/37.DAT | ROM/27/37.DAT | ROM/21/37.DAT |
| 220 | Ship bound for Selbina | ROM/1/24.DAT | ROM/25/29.DAT | ROM/27/29.DAT | ROM/21/29.DAT |
| 227 | Ship bound for Selbina | ROM/1/29.DAT | ROM/25/36.DAT | ROM/27/36.DAT | ROM/21/36.DAT |
| 268 | Sih Gates | ROM9/0/15.DAT | ROM9/5/113.DAT | ROM9/6/57.DAT | ROM9/5/65.DAT |
| 283 | Silver Knife | ROM/374/94.DAT | — | — | — |
| 76 | Silver Sea Remnants | ROM4/0/29.DAT | ROM4/1/21.DAT | ROM4/1/75.DAT | ROM4/0/81.DAT |
| 59 | Silver Sea route to Al Zahbi | ROM4/0/12.DAT | ROM4/1/4.DAT | ROM4/1/58.DAT | ROM4/0/64.DAT |
| 58 | Silver Sea route to Nashmau | ROM4/0/11.DAT | ROM4/1/3.DAT | ROM4/1/57.DAT | ROM4/0/63.DAT |
| 107 | South Gustaberg | ROM/0/124.DAT | ROM/24/44.DAT | ROM/26/44.DAT | ROM/20/44.DAT |
| 230 | Southern San d'Oria | ROM/1/31.DAT | ROM/25/39.DAT | ROM/27/39.DAT | ROM/21/39.DAT |
| 80 | Southern San d'Oria [S] | ROM5/0/0.DAT | ROM/24/17.DAT | ROM/26/17.DAT | ROM/20/17.DAT |
| 19 | Spire of Dem | ROM3/0/18.DAT | ROM3/2/29.DAT | ROM3/3/1.DAT | ROM3/0/85.DAT |
| 17 | Spire of Holla | ROM3/0/16.DAT | ROM3/2/27.DAT | ROM3/2/127.DAT | ROM3/0/83.DAT |
| 21 | Spire of Mea | ROM3/0/20.DAT | ROM3/2/31.DAT | ROM3/3/3.DAT | ROM3/0/87.DAT |
| 23 | Spire of Vahzl | ROM3/0/22.DAT | ROM3/2/33.DAT | ROM3/3/5.DAT | ROM3/0/89.DAT |
| 179 | Stellar Fulcrum | ROM2/0/16.DAT | ROM2/17/112.DAT | ROM2/13/118.DAT | ROM2/13/28.DAT |
| 117 | Tahrongi Canyon | ROM/1/1.DAT | ROM/24/54.DAT | ROM/26/54.DAT | ROM/20/54.DAT |
| 57 | Talacca Cove | ROM4/0/10.DAT | ROM4/1/2.DAT | ROM4/1/56.DAT | ROM4/0/62.DAT |
| 26 | Tavnazian Safehold | ROM3/0/25.DAT | ROM3/2/36.DAT | ROM3/3/8.DAT | ROM3/0/92.DAT |
| 37 | Temenos | ROM3/3/82.DAT | ROM3/2/47.DAT | ROM3/3/19.DAT | ROM3/0/103.DAT |
| 159 | Temple of Uggalepih | ROM2/0/10.DAT | ROM2/17/92.DAT | ROM2/13/108.DAT | ROM2/13/18.DAT |
| 60 | The Ashu Talif | ROM4/0/13.DAT | ROM4/1/5.DAT | ROM4/1/59.DAT | ROM4/0/65.DAT |
| 153 | The Boyahda Tree | ROM2/0/8.DAT | ROM2/17/86.DAT | ROM2/13/106.DAT | ROM2/13/16.DAT |
| 181 | The Celestial Nexus | ROM2/12/115.DAT | ROM2/17/114.DAT | ROM2/13/120.DAT | ROM2/13/30.DAT |
| 71 | The Colosseum | ROM4/0/24.DAT | ROM4/1/16.DAT | ROM4/1/70.DAT | ROM4/0/76.DAT |
| 195 | The Eldieme Necropolis | ROM/0/77.DAT | ROM/25/4.DAT | ROM/27/4.DAT | ROM/21/4.DAT |
| 175 | The Eldieme Necropolis [S] | ROM5/0/28.DAT | ROM/24/112.DAT | ROM/26/112.DAT | ROM/20/112.DAT |
| 35 | The Garden of Ru'Hmet | ROM3/0/34.DAT | ROM3/2/45.DAT | ROM3/3/17.DAT | ROM3/0/101.DAT |
| 121 | The Sanctuary of Zi'Tah | ROM2/0/2.DAT | ROM2/17/54.DAT | ROM2/13/97.DAT | ROM2/13/7.DAT |
| 178 | The Shrine of Ru'Avitau | ROM2/12/113.DAT | ROM2/17/111.DAT | ROM2/13/117.DAT | ROM2/13/27.DAT |
| 10 | The Shrouded Maw | ROM3/0/9.DAT | ROM3/2/20.DAT | ROM3/2/120.DAT | ROM3/0/76.DAT |
| 165 | Throne Room | ROM/0/74.DAT | ROM/24/102.DAT | ROM/26/102.DAT | ROM/20/102.DAT |
| 156 | Throne Room [S] | ROM5/0/25.DAT | ROM/24/93.DAT | ROM/26/93.DAT | ROM/20/93.DAT |
| 169 | Toraimarai Canal | ROM/0/65.DAT | ROM/24/106.DAT | ROM/26/106.DAT | ROM/20/106.DAT |
| 5 | Uleguerand Range | ROM3/0/4.DAT | ROM3/2/15.DAT | ROM3/2/115.DAT | ROM3/0/71.DAT |
| 0 | unknown | ROM/0/28.DAT | — | — | — |
| 158 | Upper Delkfutt's Tower | ROM/1/9.DAT | ROM/24/95.DAT | ROM/26/95.DAT | ROM/20/95.DAT |
| 244 | Upper Jeuno | ROM/1/40.DAT | ROM/25/53.DAT | ROM/27/53.DAT | ROM/21/53.DAT |
| 103 | Valkurm Dunes | ROM/0/102.DAT | ROM/24/40.DAT | ROM/26/40.DAT | ROM/20/40.DAT |
| 128 | Valley of Sorrows | ROM2/0/7.DAT | ROM2/17/61.DAT | ROM2/13/102.DAT | ROM2/13/12.DAT |
| 177 | Ve'Lugannon Palace | ROM2/12/112.DAT | ROM2/17/110.DAT | ROM2/13/116.DAT | ROM2/13/26.DAT |
| 83 | Vunkerl Inlet [S] | ROM5/0/3.DAT | ROM/24/20.DAT | ROM/26/20.DAT | ROM/20/20.DAT |
| 51 | Wajaom Woodlands | ROM4/1/99.DAT | ROM4/0/124.DAT | ROM4/1/50.DAT | ROM4/0/56.DAT |
| 182 | Walk of Echoes | ROM5/0/29.DAT | ROM/24/119.DAT | ROM/26/119.DAT | ROM/20/119.DAT |
| 144 | Waughroon Shrine | ROM/0/89.DAT | ROM/24/81.DAT | ROM/26/81.DAT | ROM/20/81.DAT |
| 100 | West Ronfaure | ROM/0/120.DAT | ROM/24/37.DAT | ROM/26/37.DAT | ROM/20/37.DAT |
| 115 | West Sarutabaruta | ROM/0/127.DAT | ROM/24/52.DAT | ROM/26/52.DAT | ROM/20/52.DAT |
| 95 | West Sarutabaruta [S] | ROM5/0/15.DAT | ROM/24/32.DAT | ROM/26/32.DAT | ROM/20/32.DAT |
| 256 | Western Adoulin | ROM9/0/3.DAT | ROM9/5/101.DAT | ROM9/6/45.DAT | ROM9/5/53.DAT |
| 125 | Western Altepa Desert | ROM2/0/6.DAT | ROM2/17/58.DAT | ROM2/13/101.DAT | ROM2/13/11.DAT |
| 239 | Windurst Walls | ROM/0/79.DAT | ROM/25/48.DAT | ROM/27/48.DAT | ROM/21/48.DAT |
| 238 | Windurst Waters | ROM/0/78.DAT | ROM/25/47.DAT | ROM/27/47.DAT | ROM/21/47.DAT |
| 94 | Windurst Waters [S] | ROM5/0/14.DAT | ROM/24/31.DAT | ROM/26/31.DAT | ROM/20/31.DAT |
| 241 | Windurst Woods | ROM/0/81.DAT | ROM/25/50.DAT | ROM/27/50.DAT | ROM/21/50.DAT |
| 225 | Windurst-Jeuno Airship | ROM/1/28.DAT | ROM/25/34.DAT | ROM/27/34.DAT | ROM/21/34.DAT |
| 273 | Woh Gates | ROM9/0/20.DAT | ROM9/5/118.DAT | ROM9/6/62.DAT | ROM9/5/70.DAT |
| 112 | Xarcabard | ROM/0/57.DAT | ROM/24/49.DAT | ROM/26/49.DAT | ROM/20/49.DAT |
| 137 | Xarcabard [S] | ROM5/0/22.DAT | ROM/24/74.DAT | ROM/26/74.DAT | ROM/20/74.DAT |
| 260 | Yahse Hunting Grounds | ROM9/0/7.DAT | ROM9/5/105.DAT | ROM9/6/49.DAT | ROM9/5/57.DAT |
| 124 | Yhoator Jungle | ROM2/0/5.DAT | ROM2/17/57.DAT | ROM2/13/100.DAT | ROM2/13/10.DAT |
| 263 | Yorcia Weald | ROM9/0/10.DAT | ROM9/5/108.DAT | ROM9/6/52.DAT | ROM9/5/60.DAT |
| 264 | Yorcia Weald [U] | ROM9/0/11.DAT | ROM9/5/109.DAT | ROM9/6/53.DAT | ROM9/5/61.DAT |
| 142 | Yughott Grotto | ROM/1/8.DAT | ROM/24/79.DAT | ROM/26/79.DAT | ROM/20/79.DAT |
| 123 | Yuhtunga Jungle | ROM2/0/4.DAT | ROM2/17/56.DAT | ROM2/13/99.DAT | ROM2/13/9.DAT |
| 172 | Zeruhn Mines | ROM/1/11.DAT | ROM/24/109.DAT | ROM/26/109.DAT | ROM/20/109.DAT |
| 73 | Zhayolm Remnants | ROM4/0/26.DAT | ROM4/1/18.DAT | ROM4/1/72.DAT | ROM4/0/78.DAT |
| 133 | Outer Ra'Kaznar [U2] | ROM9/0/22.DAT | ? | ? | ? |
| 189 | Outer Ra'Kaznar [U3] | ROM9/0/22.DAT | ? | ? | ? |
| 199 | — | ROM5/0/31.DAT | ROM/25/8.DAT | ROM/27/8.DAT | ROM/21/8.DAT |
| 210 | — | ROM/0/40.DAT | ROM/25/19.DAT | ROM/27/19.DAT | ROM/21/19.DAT |
| 214 | — | ROM4/0/33.DAT | ROM/25/23.DAT | ROM/27/23.DAT | ROM/21/23.DAT |
| 219 | — | ROM5/0/32.DAT | ROM/25/28.DAT | ROM/27/28.DAT | ROM/21/28.DAT |
| 229 | Throne Room [V] | ROM/373/34.DAT | ? | ? | ? |
| 278 | Gwora - Corridor | ROM/375/123.DAT | ? | ? | ? |
| 279 | Walk of Echoes [P2] | ROM/361/85.DAT | ? | ? | ? |
| 286 | — | — | — | — | — |
| 287 | Maquette Abdhaljs-LegionB | ROM/362/18.DAT | ? | ? | ? |
| 298 | Walk of Echoes [P1] | ROM/361/85.DAT | ? | ? | ? |
| 299 | Gwora - Throne Room | ROM/378/99.DAT | ? | ? | ? |
| 300 | — | ROM/1/5.DAT | ? | ? | ? |
| 301 | — | ROM5/0/30.DAT | ? | ? | ? |

## Notes

- **0** (`unknown`) and **49** (`none`): placeholder slots with only a model DAT.
- **199, 210, 214, 219, 286**: still unnamed in the client name table (genuine blank slots).
- **133, 189, 229, 278, 279, 287, 298, 299**: named/remapped by SE or CatsEyeXI to newer or
  custom content (the original Reddit snapshot left them blank or pointed at stale slots).
  Their Dialog/NPC/Event DATs are marked `?` — not derivable by this method (see below).
- `[S]` = Crystal War (Campaign) past versions; `[U]`/`[U1..U3]` = upper/alternate; `[D]` = Dynamis
  (Divergence); `[V]`, `[P1]/[P2]`, `Gwora ...`, `...LegionA/B` = newer/custom CatsEyeXI variants.
- Paths are relative to the FFXI install ROM folders (`ROM/`, `ROM2/` … `ROM9/`, plus custom
  `ROM/36x`–`ROM/37x` used by CatsEyeXI for added zones).

## Verification against the client FileTable

Re-derived from the **CatsEyeXI client** (2026-06) using the same method as `xim`
(`FileTableManager`, `ZoneIdToResourceId`, `StringTableParser`):

- **Names** — read from the zone-name string table `ROM/165/84.DAT` (a `d_msg` table,
  XOR mask `0xFF`), indexed by zone ID. The table has 300 entries (0–299). Format documented
  in [../dats/ROM_165_84.md](../dats/ROM_165_84.md).
- **Model** — the FileTable. All nine `FTABLE*.DAT`/`VTABLE*.DAT` pairs are consulted,
  each entry gated by its VTABLE version byte (xim models this as one OR-combined global
  index; external byte evidence says the real client is volume-direct with overlay entries
  shadowing the base — same resolution result for registered ids). The main-area path is
  `FileTable[0x64 + zoneId]` (zoneId < 0x100) or `FileTable[0x147B3 + (zoneId − 0x100)]`.
  **The `≥ 0x100` branch is byte-verified (2026-08)**: for zones 256–301 it resolves every
  named zone to the correct era volume (SoA → ROM9, Mog Garden → ROM/309, Escha → ROM/33x…),
  with [U]-variant zones landing on same-size sibling DATs and Walk of Echoes [P1]/[P2]
  resolving to the *same* DAT — a competing decompile-derived formula (`+0x144F7`,
  threshold 600) resolves nothing in that band.
- **Coverage** — every zone ID 0–301 resolves to a model that exists on disk. The table now
  lists all of them (added **298–301**, which the retail snapshot was missing).

**Model paths that differed from the original Reddit snapshot** (FileTable value is authoritative
for the actual client — SE/CatsEyeXI reused old empty slots and added custom content):

| ID | Name | Reddit model | Client FileTable |
|---:|------|--------------|------------------|
| 0 | unknown | ROM/1/20.DAT | ROM/0/28.DAT |
| 133 | Outer Ra'Kaznar [U2] | ROM/1/5.DAT | ROM9/0/22.DAT |
| 189 | Outer Ra'Kaznar [U3] | ROM5/0/30.DAT | ROM9/0/22.DAT |
| 229 | Throne Room [V] | ROM/0/119.DAT | ROM/373/34.DAT |
| 278 | Gwora - Corridor | ROM9/0/25.DAT | ROM/375/123.DAT |
| 279 | Walk of Echoes [P2] | ROM9/0/26.DAT | ROM/361/85.DAT |
| 283 | Silver Knife | (none) | ROM/374/94.DAT |
| 287 | Maquette Abdhaljs-LegionB | (none) | ROM/362/18.DAT |
| 298 | Walk of Echoes [P1] | (absent) | ROM/361/85.DAT |
| 299 | Gwora - Throne Room | (absent) | ROM/378/99.DAT |
| 300 | — | (absent) | ROM/1/5.DAT |
| 301 | — | (absent) | ROM5/0/30.DAT |

**Caveats**
- **Dialog / NPC / Event** are **not** verified here. xim is a renderer and resolves only the
  main-area model from the FileTable; it does not derive these from the zone ID. The values in
  those columns are the retail LSB/community convention and are kept as best-effort reference
  (`?` where the slot was repurposed and the original values are stale).
- A few high/custom IDs resolve to the same model (e.g. 133/189 → ROM9/0/22,
  279/298 → ROM/361/85). For 279/298 this is legitimately shared geometry (two phases of
  Walk of Echoes); for the custom slots it may be a table collision.
- **Client-variant caveat:** these tables were read from a **CatsEyeXI** install. Rows that
  reflect CatsEyeXI-remapped slots (e.g. 133/189/229) and the custom `ROM/36x–37x` ranges are
  client-variant — they may not hold on a clean retail install.
- `283` (`Silver Knife`) does have a model (`ROM/374/94.DAT`); xim disables its bump-map only.
