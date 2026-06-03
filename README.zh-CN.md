# PGM MiSTer FPGA 社区维护与资料保存项目

PGM MiSTer FPGA Core 最初由 Eizo Chiu 开发（https://github.com/eziochiu/）。

本仓库基于截至 2026 年 6 月 2 日最后一次公开发布的版本建立。

本仓库的目的是保存该核心最后公开可获取的版本，并汇总社区测试结果、兼容性报告、错误报告以及技术反馈，以便在 Eizo Chiu 未来选择继续开发该核心时，能够为其提供参考和支持。

PGM MiSTer FPGA Core 的所有原创开发成果及相关贡献均归 Eizo Chiu 所有。本仓库仅作为社区资料保存与反馈收集的平台。


## Eizo Chiu 原始 README

以下内容保留自 PGM MiSTer FPGA Core 最后一次公开发布版本的 README，仅供历史记录与技术参考之用。


## 保存说明

本仓库旨在保存项目文档、测试结果以及此前已公开发布的相关资料。

如果 Eizo Chiu 提出修改请求、署名更新请求、内容移除请求，或希望接管本仓库的维护工作，现任维护者将本着善意与尊重原创作者意愿的原则进行处理。

PGM MiSTer FPGA Core 原始项目的开发权及原创归属始终属于 Eizo Chiu。


# PGM for MiSTer FPGA（PGM 街机核心）

[English](README.md) | **简体中文**

> ## ⚖️ 原创声明
>
> **本仓库的代码并非 fork 自 [wickerwaka](https://github.com/wickerwaka/Arcade-IGSPGM_MiSTer) 的 PGM FPGA 代码。** 具体说明如下：
>
> 1. **IGS027A ARM 核心（Type 1 / Type 2 / Type 3 / LPC）** —— 每一种 ARM 类型的 VHDL 代码全部由我本人亲自从零编写。
> 2. **ICS2115 声音** —— wickerwaka 的 ICS2115 代码是*错误*的，根本无需抄袭。我手上有一份完整且正确的 ICS2115 C++ 代码作为依据。
> 3. **IGS023 图像** —— wickerwaka 的 IGS023 代码同样是*不完整*的，根本无需抄袭。我的 IGS023 模拟代码是从我自己的街机模拟器代码完整移植而来，**并非** wickerwaka 的代码。
> 4. **IGS025 / IGS027 加密与游戏逻辑**（`kov`、`oldsplus`、`py2k2`、`ddp3`、`puzlstar`、`puzzli2`, `olds`, `killbld` 等）—— 均由我在 AI 辅助下完成。
> 5. **LPC2132（`kovgsyxjqb` / `kovzscs`）** —— 这两个游戏所用的 MAME LPC2132 ARM 模拟代码同样由我编写，曾提交到 MAME，但未被采纳；本仓库中的实现亦完全由我本人完成。
>
> 总之，本项目与 wickerwaka 的项目**不存在任何 “fork” 关系**。

这是一个面向 [MiSTer FPGA](https://github.com/MiSTer-devel/Wiki_MiSTer/wiki) 平台的
**PGM（PolyGame Master，鈊象电子 IGS）** 街机系统 FPGA 实现。

PGM（PolyGameMaster，IGS 于 1997 年推出）是一块卡带式街机主板，承载了大量经典华语街机游戏——
《三国战纪》系列横版动作、《西游释厄传》系列，以及《形意拳》《神剑伏魔录》等格斗游戏，
还有数款 Cave 移植的弹幕射击游戏。

> 🔔 **重要：** 由于核心已更新，`.mra` 也需要同步更新。

---

## 💛 非商业声明

> **本核心不存在任何商业行为，纯属个人爱好，免费分享给社区。**

---

## 硬件要求 —— 单 SDRAM

> **本核心现在只需单条 SDRAM 即可运行，不再需要双 SDRAM。**

PGM 对内存需求较大，但本核心现已可在**单条 SDRAM 模组**下运行，
因此普通的 MiSTer SDRAM 配置即可运行本核心。

> **推荐固件：** 建议在 MiSTer 主程序固件 **2.10** 版本下运行本核心。

---

## 使用方法

1. 在 MiSTer SD 卡的**根目录**下新建一个 **`_PGM`** 文件夹（`/_PGM/`）。
2. 在 `_PGM` 文件夹内，把 `.mra` 文件与 `cores` 文件夹**放在同一级**，
   `.rbf` 核心则放进 `cores` 文件夹里。这与本仓库的目录结构一致，
   所以直接把整个 `_PGM` 文件夹复制过去即可：

   ```
   /_PGM/
   ├── cores/
   │   ├── PGM.rbf
   │   └── PGM-KOVGSYX.rbf
   ├── kov.mra
   ├── kov2.mra
   └── ...            （所有 .mra 文件）
   ```
3. 将所需的 MAME ROM 压缩包放入 `/games/mame/`。
   每个游戏都需要共用 BIOS `pgm.zip`，外加自身的 romset（具体压缩包名写在各 `.mra` 文件内）。
4. 在 MiSTer 主菜单中打开 **`PGM`**，选择游戏即可。

> 使用专用核心 `PGM-KOVGSYX.rbf` 的那两个 `kov*` 修改版，会由对应的 `.mra` 文件自动指向正确核心——
> 只需确保两个 `.rbf` 都已放入 `cores` 文件夹即可。

> **区域切换：** 大部分游戏都在 MiSTer OSD 中提供 **Region（区域）** DIP 开关
> （China、Taiwan、Japan、Korea、Hong Kong、World 等）——切换后复位核心，
> 即可以所选区域启动游戏。

---

## 支持的游戏

共支持 **32** 款游戏。

### 三国战纪系列（横版动作）

| MRA | 中文名 | English |
| --- | --- | --- |
| `kov` | 三国战纪 | Knights of Valour |
| `kovplus` | 三国战纪 正宗 Plus | Knights of Valour Plus |
| `kovsh` | 三国战纪 风云再起 | Knights of Valour Super Heroes |
| `kovshp` | 三国战纪 乱世枭雄 | Knights of Valour Super Heroes Plus |
| `kov2` | 三国战纪2 | Knights of Valour 2 |
| `kov2p` | 三国战纪2 群雄争霸 | Knights of Valour 2 Plus – Nine Dragons |
| `kovgsyxjqb` | 三国战纪 盖世英雄加强版（盗版） | Knights of Valour – Gai Shi Ying Xiong Jia Qiang Ban |
| `kovzscs` | 三国战纪 战神传说特别版（盗版） | Knights of Valour – Zhan Shen Chuan Shuo Te Bie Ban |
| `kovytzy` | 三国战纪 风云再起 一统中原 | Knights of Valour Super Heroes – Yi Tong Zhong Yuan |

### 西游释厄传系列（横版动作）

| MRA | 中文名 | English |
| --- | --- | --- |
| `orlegend` | 西游释厄传 | Oriental Legend |
| `olds` | 西游释厄传 Super | Oriental Legend Super |
| `oldsplus` | 西游释厄传 群魔乱舞 | Oriental Legend 2 |

### 格斗 / 动作（横版）

| MRA | 中文名 | English |
| --- | --- | --- |
| `killbld` | 傲剑狂刀 | The Killing Blade |
| `killbldp` | 傲剑狂刀 加强版 | The Killing Blade Plus |
| `martmast` | 形意拳 | Martial Masters |
| `theglad` | 神剑伏魔录（神剑风云） | The Gladiator |
| `svg` | 圣魔世纪 | S.V.G. – Spectral vs Generation |
| `dmnfrnt` | 魔域战线（恶魔前线） | Demon Front |

### 弹幕射击（竖版）

| MRA | 中文名 | English |
| --- | --- | --- |
| `ddp2` | 怒首领蜂 II 蜂暴 | DoDonPachi II – Bee Storm |
| `ddp3` | 怒首领蜂 III | DoDonPachi III |
| `ddpdoj` | 怒首领蜂 大往生 | DoDonPachi Dai-Ou-Jou |
| `ketbl` | 决意 ケツイ（盗版移植） | Ketsui *(bootleg conversion)* |
| `espgalbl` | エスプガルーダ EspGaluda（盗版移植） | EspGaluda *(bootleg conversion)* |

### 益智 / 问答 / 合集（PGM 早期作品）

| MRA | 中文名 | English |
| --- | --- | --- |
| `drgw2` | 中国龙 II | Dragon World II |
| `drgw3` | 中国龙 3 | Dragon World 3 |
| `photoy2k` | 大家来找碴（Real and Fake） | Photo Y2K |
| `py2k2` | 大家来找碴 2 | Photo Y2K2 |
| `puzzli2` | 泡泡鱼 2 | Puzzli 2 |
| `puzzli2s` | 泡泡鱼 2 超级版 | Puzzli 2 Super |
| `puzlstar` | 魔幻星座 | Puzzle Star |
| `happy6` | 欢乐六合一 | Happy 6-in-1 |
| `pgm3in1` | 闪亮三合一 | Flash 3-in-1 |

---

## 把 `.mra` 重命名为完整标题

`.mra` 的文件名就是 MiSTer 菜单里显示的名称。仓库里的文件默认按 MAME romset 命名
（`kov.mra`、`ddp2.mra` 等）。如果你想直接看到完整标题，可以用 [`utils/`](utils/)
里的脚本，把每个 `.mra` 批量重命名为它内部 `<name>` 字段的值——
例如 `ddp2.mra` → `DoDonPachi II - Bee Storm (World, ver. 102).mra`。
FAT32/exFAT 不允许的字符（`\ / : * ? " < > |`）会被替换成 `-`。

```sh
# 先预览改动，不实际修改（建议先跑一次）：
python utils/rename_mra.py --dry-run /path/to/your/SD/_PGM

# 确认无误后正式执行：
python utils/rename_mra.py /path/to/your/SD/_PGM
```

目录参数默认是当前文件夹，所以你也可以先 `cd` 进 `_PGM` 文件夹再直接运行
`python rename_mra.py`。请对 SD 卡上的**副本**运行，不要对本仓库运行——
仓库刻意保留 romset 命名。每个 `.mra` 内部的 `<setname>` 仍保留原始 romset，
因此只改显示名、不影响功能。需要 **Python 3.7+**。

---

## 特别鸣谢

- 感谢 **Youzhi** 和 **Danio** 提供 MiSTer FPGA 硬件。🙏
- 感谢 **佬工** 打样 SDRAM 与 I/O board PCB。🙏

---

## 许可证

本项目基于 **GNU General Public License v3.0** 协议开源，详见 [LICENSE](LICENSE) 文件。
