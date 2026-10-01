"""Fail-closed, artifact-only synchronization. No source provenance is persisted."""
import argparse
import html
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
          'Alternative MRA': ('_PGM/_alternatives', '.mra', 'legacy/mra/_alternatives'),
          'Cores': ('_PGM/cores', '.rbf', 'legacy/cores')}
SHARED_HOSTS = {'github.com', 'gitlab.com', 'bitbucket.org', 'gitee.com'}


def allowed_path(name):
    if not safe_relative(name):
        return False
    # Keep publication ownership independent of the inventory/plan configuration.
    return any(managed_name(name, directory, suffix, recursive)
               for directory, suffix, recursive in
               [('_PGM', '.mra', False), ('_PGM/cores', '.rbf', False),
                ('_PGM/_alternatives', '.mra', True), ('legacy/mra', '.mra', False),
                ('legacy/cores', '.rbf', False), ('legacy/mra/_alternatives', '.mra', True)])


def safe_relative(name):
    return (bool(name) and not re.search(r'[\\\x00-\x1f\x7f:]', name)
            and all(part and not part.startswith('.') for part in name.split('/')))


def managed_name(path, directory, suffix, recursive=False):
    if not path.startswith(directory + '/'):
        return False
    relative = path[len(directory) + 1:]
    return (safe_relative(relative) and relative.endswith(suffix)
            and (recursive or '/' not in relative))


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
    if not owner or not repo or any(c in owner + repo for c in '/\\\r\n\x00'):
        raise SyncError('Invalid source configuration.')
    # Public multi-tenant hosts alone do not identify a source repository.
    tokens = [url, owner, repo, owner + '/' + repo]
    if parsed.hostname.lower() not in SHARED_HOSTS:
        tokens.append(parsed.hostname)
    return tokens


def disclosed(data, tokens):
    text = unquote(data.decode('utf-8', errors='replace')).casefold()
    return any(re.search(r'(?<![\w-])' + re.escape(t.casefold()) + r'(?![\w-])', text)
               for t in tokens)


def checked_path(root, relative):
    if not safe_relative(relative.as_posix() if isinstance(relative, Path) else relative):
        raise SyncError('Unsafe artifact path.')
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
        if require and label != 'Alternative MRA' and not folder.is_dir():
            raise SyncError('Source artifact layout validation failed.')
        items = {}
        if folder.exists():
            if not folder.is_dir():
                raise SyncError('Invalid artifact directory.')
            def collect(directory_path):
                for p in sorted(directory_path.iterdir()):
                    if p.is_symlink() and label == 'Alternative MRA':
                        raise SyncError('Symlink encountered; maintainer review required.')
                    if p.name.startswith('.'):
                        continue
                    if label == 'Alternative MRA' and p.is_dir():
                        checked_path(root, p.relative_to(root))
                        collect(p)
                    elif p.name.endswith(suffix):
                        checked_path(root, p.relative_to(root))
                        if not p.is_file() or p.stat().st_size == 0:
                            raise SyncError('Invalid or empty artifact.')
                        items[p.relative_to(folder).as_posix()] = p.read_bytes()
            collect(folder)
        if require and label != 'Alternative MRA' and not items:
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
    # Validate every candidate before comparing or classifying any removals.
    for label, items in current.items():
        for name, content in items.items():
            if disclosed(name.encode(), tokens) or any(s in name for s in provenance):
                raise SyncError('Candidate artifact name requires disclosure review.')
            if GROUPS[label][1] == '.mra':
                if disclosed(content, tokens) or any(s.encode() in content for s in provenance):
                    raise SyncError('Candidate MRA requires disclosure review: ' + ascii(name))
                try:
                    if ET.fromstring(content).tag != 'misterromdescription':
                        raise ValueError()
                except (ET.ParseError, ValueError):
                    raise SyncError('Candidate MRA validation failed: ' + ascii(name)) from None
    previous = inventory(root)
    if previous['Alternative MRA'] and not current['Alternative MRA']:
        raise SyncError('Alternative MRA source collection is missing or empty; maintainer review required.')
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
        stats = dict.fromkeys(['NEW', 'UPDATED', 'UNCHANGED', 'REMOVED'], 0)
        for name, content in new.items():
            state = 'NEW' if name not in old else ('UNCHANGED' if old[name] == content else 'UPDATED')
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
    validate_operations(root, changes, current, baseline)
    return changes, counts, current, baseline


def validate_operations(root, changes, expected, baseline):
    # Reconstruct the only permitted operations independently of the proposed map.
    permitted = {}
    for label, (directory, suffix, archive) in GROUPS.items():
        old = {name[len(directory) + 1:]: entry[1] for name, entry in baseline.items()
               if managed_name(name, directory, suffix, label == 'Alternative MRA')
               and entry[0] == 'file'}
        for name, content in expected[label].items():
            if name not in old or old[name] != content:
                permitted[directory + '/' + name] = content
        for name in old.keys() - expected[label].keys():
            destination = archive + '/' + name
            if checked_path(root, destination).exists():
                raise SyncError('Legacy collision; maintainer review required.')
            permitted[destination] = old[name]
            permitted[directory + '/' + name] = None
    if changes != permitted or any(not allowed_path(name) for name in changes):
        raise SyncError('Synchronization boundary check failed; no changes applied.')
    for name in changes:
        checked_path(root, name)


def verify_final(root, changes, expected, baseline):
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
        elif after[name][0] != 'file' or after[name][1] != content:
            raise SyncError('Artifact byte verification failed.')


def apply(root, changes, expected, baseline):
    if snapshot(root) != baseline:
        raise SyncError('Target changed during planning.')
    validate_operations(root, changes, expected, baseline)
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
        verify_final(root, changes, expected, baseline)
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
    # Only prune ancestors of removed alternatives, and only when truly empty.
    alternative_root = root / '_PGM/_alternatives'
    for name, content in changes.items():
        if content is None and name.startswith('_PGM/_alternatives/'):
            parent = (root / name).parent
            while parent != alternative_root:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent


def summary(counts, preview=False):
    lines = ['PGM Artifact Sync', '']
    for label, stats in counts.items():
        lines += [label] + [f'{display}: {stats[state]}' for state, display in
                           [('NEW', 'Added'), ('UPDATED', 'Updated'),
                            ('REMOVED', 'Removed'), ('UNCHANGED', 'Unchanged')]] + ['']
    lines.append(('Legacy to preserve: ' if preview else 'Legacy Preserved: ') +
                 str(sum(s['REMOVED'] for s in counts.values())))
    return '\n'.join(lines)


def preview_summary(counts, changes, baseline, tokens, provenance=()):
    filenames = {label: {state: [] for state in ('NEW', 'UPDATED', 'REMOVED')}
                 for label in GROUPS}
    # Check all names before rendering any output, including last-known removed names.
    for path, content in changes.items():
        if (not allowed_path(path) or disclosed(path.encode(), tokens)
                or any(sha in path for sha in provenance)):
            raise SyncError('Artifact filename requires review; preview withheld.')
        for label, (directory, suffix, _) in GROUPS.items():
            if managed_name(path, directory, suffix, label == 'Alternative MRA'):
                state = 'REMOVED' if content is None else ('UPDATED' if path in baseline else 'NEW')
                filenames[label][state].append(path[len(directory) + 1:])
    lines = [summary(counts, preview=True), '']
    for label, states in filenames.items():
        lines += [label, '']
        for state, heading in [('NEW', 'Added'), ('UPDATED', 'Updated'),
                               ('REMOVED', 'Removed → Legacy')]:
            lines.append(heading + ':')
            for name in sorted(states[state]):
                # Preserve literal filenames in the Markdown summary without active markup.
                display = re.sub(r'([\\`*_{}\[\]])', r'\\\1', html.escape(name))
                lines.append('- ' + display)
            if not states[state]:
                lines.append('(none)')
            lines.append('')
    return '\n'.join(lines).rstrip()


def removal_guard(counts, scheduled, approved):
    if scheduled:
        for stats in counts.values():
            previous_count = stats['UPDATED'] + stats['UNCHANGED'] + stats['REMOVED']
            if stats['REMOVED'] * 5 > previous_count:
                raise SyncError('Removal count exceeds 20% of an active collection. Preview and review manually.')
    elif any(approved[label] != stats['REMOVED'] for label, stats in counts.items()):
        raise SyncError('Removal counts differ from the reviewed counts. Run a new preview before applying.')


def staged_paths(root):
    return set(filter(None, git(root, 'diff', '--cached', '--no-renames',
                               '--name-only', '-z').decode().split('\0')))


def verify_staged(root, changes):
    staged = staged_paths(root)
    # The allowlist is independent of the plan, and rejects nested lookalike paths.
    if any(not allowed_path(name) for name in staged) or staged != set(changes):
        raise SyncError('Synchronization boundary check failed: staged paths are not approved artifacts.')
    deleted = set(filter(None, git(root, 'diff', '--cached', '--no-renames', '--diff-filter=D',
                                   '--name-only', '-z').decode().split('\0')))
    if deleted != {name for name, content in changes.items() if content is None}:
        raise SyncError('Staged removal verification failed.')
    entries = git(root, 'ls-files', '--stage', '-z', '--', *changes.keys()).decode().split('\0')
    for entry in filter(None, entries):
        metadata, _ = entry.split('\t', 1)
        mode, _, stage = metadata.split()
        if mode not in {'100644', '100755'} or stage != '0':
            raise SyncError('Staged artifact must be a regular file with no merge conflict.')
    for name, content in changes.items():
        if content is not None and git(root, 'show', ':' + name) != content:
            raise SyncError('Staged artifact bytes differ from the approved artifact.')


FALLBACK_SUBJECT = 'Update PGM beta artifacts'


def commit_subject(message, changes, tokens, provenance=(), identity=()):
    """Recognize a small safe vocabulary; never publish arbitrary source text."""
    try:
        subject = message.decode('utf-8').rstrip('\n')
    except UnicodeDecodeError:
        return FALLBACK_SUBJECT
    protected = tokens + [value for value in identity if value]
    if (not subject or len(subject) > 120 or not re.fullmatch(r'[A-Za-z0-9 ,&-]+', subject)
            or disclosed(subject.encode(), protected)
            or any(sha.casefold() in subject.casefold() for sha in provenance)
            or re.search(r'\b[0-9a-fA-F]{40}\b', subject)):
        return FALLBACK_SUBJECT
    words = re.findall(r'[a-z0-9-]+', subject.casefold())
    vocabulary = {'update', 'fix', 'add', 'remove', 'refresh', 'pgm', 'core', 'cores',
                  'rbf', 'implementation', 'mra', 'mras', 'file', 'files', 'primary',
                  'alternate', 'alternative', 'alternatives', 'definition', 'definitions',
                  'game', 'games', 'and', 'pgm-027a', 'kov2'}
    if not words or words[0] not in {'update', 'fix', 'add', 'remove', 'refresh'} or set(words) - vocabulary:
        return FALLBACK_SUBJECT
    actions = [word for word in words if word in {'update', 'fix', 'add', 'remove', 'refresh'}]
    if len(actions) > 1 and not re.fullmatch(r'fix (?:kov2 )?mras? and update (?:pgm )?core', subject.casefold()):
        return FALLBACK_SUBJECT
    if any(words.count(word) > 1 for word in {'core', 'cores', 'mra', 'mras', 'definition', 'definitions'}):
        return FALLBACK_SUBJECT
    mentioned = set()
    alternative = bool(set(words) & {'alternate', 'alternative', 'alternatives'})
    if alternative and set(words) & {'mra', 'mras', 'definition', 'definitions'}:
        mentioned.add('Alternative MRA')
    if set(words) & {'mra', 'mras'} and (not alternative or 'primary' in words):
        mentioned.add('MRA')
    if set(words) & {'core', 'cores', 'rbf', 'implementation'}:
        mentioned.add('Cores')
    actual = {label: [path.rsplit('/', 1)[-1] for path in changes
                     if managed_name(path, directory, suffix, label == 'Alternative MRA')]
              for label, (directory, suffix, _) in GROUPS.items()}
    if mentioned != {label for label, names in actual.items() if names}:
        return FALLBACK_SUBJECT
    if words[0] == 'remove' and any(content is not None for path, content in changes.items()
                                  if any(managed_name(path, directory, suffix, label == 'Alternative MRA')
                                         for label, (directory, suffix, _) in GROUPS.items())):
        return FALLBACK_SUBJECT
    core, mra = 'PGM core', 'MRA files'
    if 'pgm-027a' in words:
        if not actual['Cores'] or not all('PGM-027A' in name.upper() for name in actual['Cores']):
            return FALLBACK_SUBJECT
        core = 'PGM-027A core'
    if 'kov2' in words:
        if not actual['MRA'] or not all(re.search(r'kov2|knights of valour 2', name, re.I)
                                        for name in actual['MRA']):
            return FALLBACK_SUBJECT
        mra = 'KOV2 MRA files'
    descriptions = [description for label, description in
                    [('MRA', mra), ('Alternative MRA', 'alternative MRA files'), ('Cores', core)]
                    if actual[label]]
    if not descriptions:
        return FALLBACK_SUBJECT
    description = ' and '.join(descriptions) if len(descriptions) < 3 else ', '.join(descriptions[:-1]) + ' and ' + descriptions[-1]
    target = 'Update ' + description
    # A verbatim source subject is never reused, even when it resembles our wording.
    if target.casefold() == subject.casefold() or disclosed(target.encode(), protected):
        return FALLBACK_SUBJECT
    return target


def source_commit_subject(source, changes, tokens, provenance):
    context = git(source, 'log', '-1', '--format=%B%x00%an%x00%ae%x00%D').rstrip(b'\n')
    fields = context.split(b'\0')
    if len(fields) != 4:
        return FALLBACK_SUBJECT
    message, author, email, refs = fields
    try:
        identities = [author.decode('utf-8'), email.decode('utf-8')]
        for ref in refs.decode('utf-8').split(', '):
            identities.append(ref.removeprefix('HEAD -> ').removeprefix('origin/'))
    except UnicodeDecodeError:
        return FALLBACK_SUBJECT
    return commit_subject(message, changes, tokens, provenance, identities)


def publish(root, changes, expected, baseline, subject=FALLBACK_SUBJECT):
    verify_final(root, changes, expected, baseline)
    git(root, '-c', 'core.autocrlf=false', 'add', '--', *changes.keys())
    verify_staged(root, changes)
    verify_final(root, changes, expected, baseline)
    git(root, '-c', 'user.name=PGM Preservation Bot',
        '-c', 'user.email=pgm-preservation-bot@users.noreply.github.com',
        '-c', 'core.hooksPath=/dev/null', 'commit', '-m', subject)
    # A concurrent target update rejects this ordinary fast-forward push.
    git(root, 'push', 'origin', 'HEAD')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Preview or synchronize PGM artifacts.')
    parser.add_argument('--apply', action='store_true', help='Apply and publish the reviewed changes.')
    parser.add_argument('--scheduled', action='store_true')
    parser.add_argument('--expected-mra-removals', type=int, default=-1)
    parser.add_argument('--expected-alternative-mra-removals', type=int, default=-1)
    parser.add_argument('--expected-core-removals', type=int, default=-1)
    options = parser.parse_args(argv)
    root = Path.cwd()
    try:
        url = os.environ.get('PGM_SOURCE_URL', '')
        if not url:
            raise SyncError('PGM_SOURCE_URL is not configured. Add the repository secret before running.')
        tokens = identifiers(url)
        if git(root, 'status', '--porcelain', '--untracked-files=all').strip():
            raise SyncError('Target working tree must be clean.')
        # Neutral temporary path; no clone errors, remotes or source identifiers are logged.
        with tempfile.TemporaryDirectory(prefix='pgm-artifacts-') as temporary:
            source = Path(temporary) / 'checkout'
            env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1',
                       GIT_CONFIG_GLOBAL=os.devnull)
            try:
                subprocess.run(['git', '-c', 'core.hooksPath=/dev/null',
                                '-c', 'core.autocrlf=false',
                                '-c', 'http.https://github.com/.extraheader=', 'clone',
                                '--quiet', '--depth', '1', '--', url, str(source)],
                               check=True, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, env=env, cwd=temporary, timeout=600)
            except (subprocess.SubprocessError, OSError):
                raise SyncError('Source retrieval failed; no artifact changes applied.') from None
            sha = git(source, 'rev-parse', 'HEAD').decode().strip()
            changes, counts, expected, baseline = plan(root, source, tokens, (sha,))
            subject = (source_commit_subject(source, changes, tokens, (sha,))
                       if options.apply and changes else FALLBACK_SUBJECT)
            shutil.rmtree(source / '.git')
            report = (summary(counts, preview=True) if options.apply else
                      preview_summary(counts, changes, baseline, tokens, (sha,)))
            # A summary path inside the checkout would violate the read-only boundary.
            summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
            if summary_path and Path(summary_path).resolve().is_relative_to(root.resolve()):
                raise SyncError('Summary output must be outside the repository.')
            print(report)
            if summary_path:
                Path(summary_path).write_text(report + '\n', encoding='utf-8')
            if not changes:
                print('No PGM artifact changes detected.')
                return 0
            if not options.apply:
                print('Preview only. No files changed, committed or pushed.')
                return 0
            removal_guard(counts, options.scheduled,
                          {'MRA': options.expected_mra_removals,
                           'Alternative MRA': options.expected_alternative_mra_removals,
                           'Cores': options.expected_core_removals})
            # The source checkout never enters the target, caches, or uploaded artifacts.
            apply(root, changes, expected, baseline)
        publish(root, changes, expected, baseline, subject)
        print(summary(counts))
        if summary_path:
            Path(summary_path).write_text(summary(counts) + '\n', encoding='utf-8')
        return 0
    except SyncError as error:
        print('Synchronization aborted: ' + str(error))
    except Exception:
        print('Synchronization failed; maintainer review required. Nothing further pushed.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
