# PGM MiSTer FPGA Community Preservation Project

PGM MiSTer FPGA Core originally developed by Eizo Chiu (https://github.com/eziochiu/).

This repository is based on the latest publicly released version available as of June 2, 2026.

The purpose of this repository is to preserve the latest publicly available release and aggregate community testing, compatibility reports, bug reports, and technical feedback should Eizo Chiu choose to continue development of the core in the future.

All original credit for the PGM MiSTer FPGA Core belongs to Eizo Chiu. This repository is intended solely as a community preservation and feedback resource.

# PGM for MiSTer FPGA

**English** | [简体中文](README.zh-CN.md)

> ## ⚖️ Statement of Originality
>
> **This repository is NOT a fork of [wickerwaka](https://github.com/wickerwaka/Arcade-IGSPGM_MiSTer)'s PGM FPGA code.** Specifically:
>
> 1. **IGS027A ARM core (Type 1 / Type 2 / Type 3 / LPC)** — the VHDL for every IGS027A ARM type was written entirely by me, from scratch.
> 2. **ICS2115 sound** — wickerwaka's ICS2115 implementation is *incorrect*, so there was nothing worth copying. I work from my own complete, correct ICS2115 C++ implementation.
> 3. **IGS023 video** — wickerwaka's IGS023 implementation is itself *incomplete*, so there was nothing worth copying. My IGS023 emulation was ported in full from my own arcade emulator code, **not** from his.
> 4. **IGS025 / IGS027 protection & game logic** (`kov`, `oldsplus`, `py2k2`, `ddp3`, `puzlstar`, `puzzli2`, `olds`, `killbld`, …) — developed by me with AI assistance.
> 5. **LPC2132 (`kovgsyxjqb` / `kovzscs`)** — the MAME LPC2132 ARM emulation used by these sets was also implemented by me. I submitted it to MAME, but it was not accepted upstream; the implementation here is entirely my own.
>
> In short, there is **no "fork" relationship** with wickerwaka's project.

An FPGA implementation of IGS's **PolyGame Master (PGM)** arcade system for the
[MiSTer FPGA](https://github.com/MiSTer-devel/Wiki_MiSTer/wiki) platform.

PGM (PolyGameMaster, 鈊象 IGS, 1997) is a cartridge-based arcade board that
hosted many beloved Taiwanese/Chinese arcade classics — the *Knights of Valour*
(三国战纪) beat 'em ups, the *Oriental Legend* (西游释厄传) series, fighters such as
*Martial Masters* and *The Gladiator*, and a handful of Cave shoot 'em ups.

> 🔔 **Updating from an earlier release?** The core has been updated, so the
> `.mra` files need to be updated to match.

---

## 💛 Non-Commercial

> **This core involves no commercial activity of any kind — it is purely a personal
> hobby project, freely shared with the community.**

---

## Hardware Requirement — Single SDRAM

> **This core now runs on a single SDRAM module — dual SDRAM is no longer required.**

PGM is a memory-hungry system, but the core now fits within a **single SDRAM module**,
so a standard MiSTer SDRAM setup is enough to run it.

> **Recommended firmware:** run this core on the MiSTer main firmware **v2.10**.

---

## Installation

1. Create a **`_PGM`** folder in the **root** of your MiSTer SD card
   (`/_PGM/`).
2. Inside `_PGM`, place the `.mra` files and the `cores/` folder **side by side**,
   with the `.rbf` core inside `cores/`. This mirrors the layout of this
   repository, so you can simply copy the whole `_PGM` folder over:

   ```
   /_PGM/
   ├── cores/
   │   ├── PGM.rbf
   │   └── PGM-KOVGSYX.rbf
   ├── kov.mra
   ├── kov2.mra
   └── ...            (all .mra files)
   ```
3. Place the required MAME ROM zips into `/games/mame/`.
   Every game needs the shared BIOS `pgm.zip` plus its own romset (the exact zip
   names are listed inside each `.mra`).
4. On MiSTer, open **`PGM`** from the main menu and pick a game.

> The two `kov*` variants that use the dedicated `PGM-KOVGSYX.rbf` core are wired
> up automatically by their `.mra` files — just make sure both `.rbf` files are
> present in `cores/`.

> **Region switching:** most games expose a **Region** DIP switch (China, Taiwan,
> Japan, Korea, Hong Kong, World, …) in the MiSTer OSD — change it and reset the
> core to boot the game in the selected region.

---

## Supported Games

A total of **32** game sets are supported.

### Knights of Valour series — 三国战纪 (horizontal, beat 'em up)

| MRA | Title |
| --- | --- |
| `kov` | Knights of Valour |
| `kovplus` | Knights of Valour Plus |
| `kovsh` | Knights of Valour Super Heroes |
| `kovshp` | Knights of Valour Super Heroes Plus |
| `kov2` | Knights of Valour 2 |
| `kov2p` | Knights of Valour 2 Plus – Nine Dragons |
| `kovgsyxjqb` | Knights of Valour – Gai Shi Ying Xiong Jia Qiang Ban *(bootleg)* |
| `kovzscs` | Knights of Valour – Zhan Shen Chuan Shuo Te Bie Ban *(bootleg)* |
| `kovytzy` | Knights of Valour Super Heroes – Yi Tong Zhong Yuan |

### Oriental Legend series — 西游释厄传 (horizontal, beat 'em up)

| MRA | Title |
| --- | --- |
| `orlegend` | Oriental Legend |
| `olds` | Oriental Legend Super |
| `oldsplus` | Oriental Legend Super Plus |

### Fighting / Action (horizontal)

| MRA | Title |
| --- | --- |
| `killbld` | The Killing Blade |
| `killbldp` | The Killing Blade Plus |
| `martmast` | Martial Masters |
| `theglad` | The Gladiator |
| `svg` | S.V.G. – Spectral vs Generation |
| `dmnfrnt` | Demon Front |

### Shoot 'em ups (vertical)

| MRA | Title |
| --- | --- |
| `ddp2` | DoDonPachi II – Bee Storm |
| `ddp3` | DoDonPachi III |
| `ddpdoj` | DoDonPachi Dai-Ou-Jou |
| `ketbl` | Ketsui *(bootleg conversion)* |
| `espgalbl` | EspGaluda *(bootleg conversion)* |

### Puzzle / Quiz / Compilation (early PGM titles)

| MRA | Title |
| --- | --- |
| `drgw2` | Dragon World II |
| `drgw3` | Dragon World 3 |
| `photoy2k` | Photo Y2K *(Real and Fake)* |
| `py2k2` | Photo Y2K2 |
| `puzzli2` | Puzzli 2 *(Pao Pao Yu)* |
| `puzzli2s` | Puzzli 2 Super |
| `puzlstar` | Puzzle Star *(Mohuan Xingzuo)* |
| `happy6` | Happy 6-in-1 |
| `pgm3in1` | Flash 3-in-1 |

---

## Renaming `.mra` files to friendly titles

The `.mra` filename is exactly what MiSTer shows in its game menu. The files ship
named after their MAME set (`kov.mra`, `ddp2.mra`, …). If you'd rather see the full
titles, the helper in [`utils/`](utils/) batch-renames every `.mra` to the `<name>`
stored inside it — e.g. `ddp2.mra` → `DoDonPachi II - Bee Storm (World, ver. 102).mra`.
Characters that are illegal on FAT32/exFAT (`\ / : * ? " < > |`) are replaced with `-`.

```sh
# Preview the changes first, without touching anything (recommended):
python utils/rename_mra.py --dry-run /path/to/your/SD/_PGM

# Apply the rename:
python utils/rename_mra.py /path/to/your/SD/_PGM
```

The directory argument defaults to the current folder, so you can also `cd` into
your `_PGM` folder and just run `python rename_mra.py`. Run it on the **copy** on
your SD card, not on this repository — the repo keeps the set-name filenames on
purpose. Each `.mra` still records its original set in `<setname>`, so only the
display name changes. Requires **Python 3.7+**.

---

## Special Thanks

- **Youzhi** and **Danio** — for providing the MiSTer FPGA hardware. 🙏
- **佬工 (Laogong)** — for fabricating the SDRAM and I/O board PCBs. 🙏

---

## License

This project is licensed under the **GNU General Public License v3.0** — see the
[LICENSE](LICENSE) file for details.

