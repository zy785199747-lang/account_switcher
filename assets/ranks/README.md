# Rank badge images

[src/ui/rank_icon.py](../../src/ui/rank_icon.py) looks here first when
rendering a card. If a file matching one of the supported filenames is
present it gets loaded and scaled; otherwise the app draws a procedural
tier-colored circle as a fallback.

## Supported filenames

For each tier the loader tries these names in order — the first one that
exists wins:

| Tier        | Candidates                                                          |
| ----------- | ------------------------------------------------------------------- |
| Iron        | `iron.png`        / `Iron.png`        / `Season_2023_-_Iron.png`        |
| Bronze      | `bronze.png`      / `Bronze.png`      / `Season_2023_-_Bronze.png`      |
| Silver      | `silver.png`      / `Silver.png`      / `Season_2023_-_Silver.png`      |
| Gold        | `gold.png`        / `Gold.png`        / `Season_2023_-_Gold.png`        |
| Platinum    | `platinum.png`    / `Platinum.png`    / `Season_2023_-_Platinum.png`    |
| Emerald     | `emerald.png`     / `Emerald.png`     / `Season_2023_-_Emerald.png`     |
| Diamond     | `diamond.png`     / `Diamond.png`     / `Season_2023_-_Diamond.png`     |
| Master      | `master.png`      / `Master.png`      / `Season_2023_-_Master.png`      |
| Grandmaster | `grandmaster.png` / `Grandmaster.png` / `Season_2023_-_Grandmaster.png` |
| Challenger  | `challenger.png`  / `Challenger.png`  / `Season_2023_-_Challenger.png`  |
| Unranked    | `unranked.png`    / `Unranked.png`    / `Season_2023_-_Unranked.png`    |

The `Season_2023_-_*.png` set ships in this repo and corresponds to Riot's
official Ranked Emblems 2023 pack. Drop in a different year's pack and
rename — or extend `TIER_FILES` in `rank_icon.py` to recognise a new pattern.

Square images (e.g. 256×256) work best — the loader scales with
`KeepAspectRatio` so non-square images letterbox.

## PyInstaller bundling

`RiotAccountSwitcher.spec` includes `assets/` in `datas`, so every PNG in
this folder ships inside the distributed `.exe`. Rebuild via `build.ps1` to
pick up new icons.
