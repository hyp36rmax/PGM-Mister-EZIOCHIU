#!/usr/bin/env python3
"""Batch-rename PGM .mra files to the friendly <name> stored inside each file.

MiSTer shows the .mra filename in its game menu. By default the files are named
after their MAME set (kov.mra, ddp2.mra, ...). This tool renames every .mra in a
directory to its internal <name>, e.g.:

    ddp2.mra -> DoDonPachi II - Bee Storm (World, ver. 102).mra

Characters that are illegal on FAT32/exFAT (the MiSTer SD card) -- \\ / : * ? " < > |
-- are replaced with '-'. The original set name is preserved inside each file's
<setname> element, so the rename only affects the display name.

Usage:
    python rename_mra.py [DIRECTORY] [--dry-run]

DIRECTORY defaults to the current folder. Point it at a *copy* of your SD card's
_PGM folder, not at this repository (the repo keeps the set-name filenames).
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def read_name(path: Path) -> str | None:
    """Return the <name> text of an .mra file, or None if it has none."""
    try:
        name = ET.parse(path).getroot().findtext('name')
        if name and name.strip():
            return name.strip()
    except ET.ParseError:
        pass
    match = re.search(r'<name>(.*?)</name>', path.read_text(encoding='utf-8', errors='replace'), re.DOTALL)
    return html.unescape(match.group(1)).strip() if match else None


def sanitize(name: str) -> str:
    """Turn an arbitrary title into a FAT32/exFAT-safe filename stem."""
    name = ILLEGAL.sub('-', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name.rstrip(' .')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('directory', nargs='?', default='.', help='folder containing the .mra files (default: current folder)')
    parser.add_argument('-n', '--dry-run', action='store_true', help='show what would happen without renaming anything')
    args = parser.parse_args()

    directory = Path(args.directory)
    if not directory.is_dir():
        print(f'error: not a directory: {directory}', file=sys.stderr)
        return 1

    files = sorted(directory.glob('*.mra'))
    if not files:
        print(f'No .mra files found in {directory}')
        return 0

    renamed = skipped = 0
    claimed: dict[str, Path] = {}
    for mra in files:
        name = read_name(mra)
        if not name:
            print(f'skip  {mra.name}: no <name> element')
            skipped += 1
            continue

        target = mra.with_name(sanitize(name) + '.mra')
        if target.name == mra.name:
            skipped += 1
            continue

        key = target.name.lower()
        if key in claimed or (target.exists() and target.resolve() != mra.resolve()):
            other = claimed.get(key, target)
            print(f'skip  {mra.name}: target "{target.name}" already taken by {other.name}')
            skipped += 1
            continue

        print(f'{"would rename" if args.dry_run else "rename"}  {mra.name}  ->  {target.name}')
        if not args.dry_run:
            mra.rename(target)
        claimed[key] = target
        renamed += 1

    verb = 'would rename' if args.dry_run else 'renamed'
    print(f'\n{verb} {renamed}, skipped {skipped}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
