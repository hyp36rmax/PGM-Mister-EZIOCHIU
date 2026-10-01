"""Fail-closed, artifact-only synchronization. No source provenance is persisted."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET


class SyncError(Exception):
    pass


GROUPS = {'MRA': ('_PGM', '.mra', 'legacy/mra'),
          'Cores': ('_PGM/cores', '.rbf', 'legacy/cores')}


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL)


def identifiers(url):
    parsed = urlsplit(url)
    parts = [unquote(p) for p in parsed.path.strip('/').split('/')]
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or len(parts) != 2):
        raise SyncError('Configure a credential-free HTTPS repository clone URL in the secret.')
    owner, repo = parts
    repo = repo.removesuffix('.git')
    if not owner or not repo or any(c in owner + repo for c in '\\\r\n\x00'):
        raise SyncError('Invalid source configuration.')
    # Public multi-tenant hosts alone do not identify a source repository.
    tokens = [url, owner, repo, owner + '/' + repo]
    if parsed.hostname.lower() not in {'github.com', 'gitlab.com', 'bitbucket.org'}:
        tokens.append(parsed.hostname)
    return tokens


def disclosed(data, tokens):
    text = unquote(data.decode('utf-8', errors='replace')).casefold()
    return any(re.search(r'(?<![\w-])' + re.escape(t.casefold()) + r'(?![\w-])', text)
               for t in tokens)


def checked_path(root, relative):
    p = root / relative
    for component in [p, *p.parents]:
        if component == root.parent:
            break
        if component.is_symlink():
            raise SyncError('Symlink encountered; maintainer review required.')
    if not p.resolve().is_relative_to(root.resolve()):
        raise SyncError('Unsafe artifact path.')
    return p


def inventory(root, require=False):
    result = {}
    for label, (directory, suffix, _) in GROUPS.items():
        folder = checked_path(root, directory)
        if require and not folder.is_dir():
            raise SyncError('Source artifact layout validation failed.')
        items = {}
        if folder.exists():
            if not folder.is_dir():
                raise SyncError('Invalid artifact directory.')
            for p in sorted(folder.iterdir()):
                if p.name.endswith(suffix):
                    checked_path(root, p.relative_to(root))
                    if not p.is_file() or p.stat().st_size == 0:
                        raise SyncError('Invalid or empty artifact.')
                    items[p.name] = p.read_bytes()
        if require and not items:
            raise SyncError('Source artifact inventory is empty; synchronization aborted.')
        result[label] = items
    return result


def snapshot(root):
    result = {}
    for p in root.rglob('*'):
        relative = p.relative_to(root)
        if relative.parts[0] == '.git':
            continue
        if p.is_symlink():
            result[relative.as_posix()] = ('link', os.readlink(p))
        elif p.is_file():
            result[relative.as_posix()] = ('file', p.read_bytes(), p.stat().st_mode)
    return result


def plan(root, source, tokens, provenance=()):
    current = inventory(source, require=True)
    previous = inventory(root)
    baseline = snapshot(root)
    # Audit all project-controlled files, including helpers, workflows and reports.
    for name, entry in baseline.items():
        if (disclosed(name.encode(), tokens) or any(s in name for s in provenance)
                or (entry[0] == 'file' and
                (disclosed(entry[1], tokens) or any(s.encode() in entry[1] for s in provenance)))):
            raise SyncError('Project disclosure audit failed; maintainer review required.')
    changes, counts = {}, {}
    for label, (directory, suffix, archive) in GROUPS.items():
        old, new = previous[label], current[label]
        stats = dict.fromkeys(['NEW', 'CHANGED', 'UNCHANGED', 'REMOVED'], 0)
        for name, content in new.items():
            if disclosed(name.encode(), tokens) or any(s in name for s in provenance):
                raise SyncError('Candidate artifact name requires disclosure review.')
            if suffix == '.mra':
                if disclosed(content, tokens) or any(s.encode() in content for s in provenance):
                    # Avoid echoing identifiers from an unsafe filename or content.
                    raise SyncError('Candidate MRA requires disclosure review: ' + ascii(name))
                try:
                    if ET.fromstring(content).tag != 'misterromdescription':
                        raise ValueError()
                except (ET.ParseError, ValueError):
                    raise SyncError('Candidate MRA validation failed: ' + ascii(name)) from None
            state = 'NEW' if name not in old else ('UNCHANGED' if old[name] == content else 'CHANGED')
            stats[state] += 1
            if state != 'UNCHANGED':
                changes[directory + '/' + name] = content
        for name in old.keys() - new.keys():
            dest = archive + '/' + name
            if checked_path(root, dest).exists():
                raise SyncError('Legacy collision; maintainer review required.')
            stats['REMOVED'] += 1
            changes[dest] = old[name]
            changes[directory + '/' + name] = None
        counts[label] = stats
    for name in changes:
        checked_path(root, name)
    return changes, counts, current, baseline


def apply(root, changes, expected, baseline):
    if snapshot(root) != baseline:
        raise SyncError('Target changed during planning.')
    created_dirs = []
    try:
        for name, content in changes.items():
            p = checked_path(root, name)
            if content is None:
                p.unlink()
            else:
                missing = []
                parent = p.parent
                while not parent.exists():
                    missing.append(parent)
                    parent = parent.parent
                for folder in reversed(missing):
                    folder.mkdir()
                    created_dirs.append(folder)
                p.write_bytes(content)
        after = snapshot(root)
        if inventory(root) != expected:
            raise SyncError('Final active inventory verification failed.')
        if {k: v for k, v in after.items() if k not in changes} != {
                k: v for k, v in baseline.items() if k not in changes}:
            raise SyncError('Unrelated content verification failed.')
        for name, content in changes.items():
            if content is None:
                if name in after:
                    raise SyncError('Removal verification failed.')
            elif after[name][1] != content:
                raise SyncError('Artifact byte verification failed.')
    except Exception:
        for name in changes:
            p = root / name
            if name in baseline:
                p.write_bytes(baseline[name][1])
                p.chmod(baseline[name][2])
            elif p.exists():
                p.unlink()
        for folder in reversed(created_dirs):
            folder.rmdir()
        raise


def summary(counts):
    lines = ['PGM Artifact Sync', '']
    for label, stats in counts.items():
        lines += [label] + [f'{display}: {stats[state]}' for state, display in
                           [('NEW', 'Added'), ('CHANGED', 'Updated'),
                            ('REMOVED', 'Removed'), ('UNCHANGED', 'Unchanged')]] + ['']
    lines.append('Legacy Preserved: ' + str(sum(s['REMOVED'] for s in counts.values())))
    return '\n'.join(lines)


def main():
    root = Path.cwd()
    try:
        url = os.environ.get('PGM_SOURCE_URL', '')
        tokens = identifiers(url)
        if git(root, 'status', '--porcelain', '--untracked-files=all').strip():
            raise SyncError('Target working tree must be clean.')
        # Neutral temporary path; no clone errors, remotes or source identifiers are logged.
        with tempfile.TemporaryDirectory(prefix='pgm-artifacts-') as temporary:
            source = Path(temporary) / 'checkout'
            env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1')
            try:
                subprocess.run(['git', '-c', 'core.hooksPath=/dev/null',
                                '-c', 'http.https://github.com/.extraheader=', 'clone',
                                '--quiet', '--depth', '1', '--', url, str(source)],
                               check=True, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, env=env, timeout=600)
            except (subprocess.SubprocessError, OSError):
                raise SyncError('Source retrieval failed; no artifact changes applied.') from None
            sha = git(source, 'rev-parse', 'HEAD').decode().strip()
            shutil.rmtree(source / '.git')
            changes, counts, expected, baseline = plan(root, source, tokens, (sha,))
            # The source checkout never enters the target, caches, or uploaded artifacts.
            apply(root, changes, expected, baseline)
        report = summary(counts)
        print(report)
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            Path(os.environ['GITHUB_STEP_SUMMARY']).write_text(report + '\n', encoding='utf-8')
        if not changes:
            print('No PGM artifact changes detected.')
            return 0
        git(root, 'add', '--', *changes.keys())
        staged = set(git(root, 'diff', '--cached', '--name-only', '-z').decode().strip('\0').split('\0'))
        if staged != set(changes):
            raise SyncError('Staged path verification failed; nothing pushed.')
        git(root, '-c', 'user.name=PGM Preservation Bot',
            '-c', 'user.email=pgm-preservation-bot@users.noreply.github.com',
            '-c', 'core.hooksPath=/dev/null', 'commit', '-m', 'Update PGM beta artifacts')
        # A concurrent target update rejects this ordinary fast-forward push.
        git(root, 'push', 'origin', 'HEAD')
        return 0
    except SyncError as error:
        print('Synchronization aborted: ' + str(error))
    except Exception:
        print('Synchronization failed; maintainer review required. Nothing further pushed.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
