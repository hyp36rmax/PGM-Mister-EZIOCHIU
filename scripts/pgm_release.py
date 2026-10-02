"""Evaluate and package preservation Git objects without accessing an artifact source."""
import argparse
from datetime import date, datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
import xml.etree.ElementTree as ET
import zipfile


class ReleaseError(Exception):
    pass


GROUPS = {'Primary MRA': ('_PGM/', '.mra', False),
          'Alternative MRA': ('_PGM/_alternatives/', '.mra', True),
          'Cores': ('_PGM/cores/', '.rbf', False)}
REPOSITORY = 'hyp36rmax/PGM-Mister-EZIOCHIU'
THRESHOLD = 5


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL)


def release_date(tag):
    if not re.fullmatch(r'beta-\d{4}-\d{2}-\d{2}', tag):
        return None
    try:
        return date.fromisoformat(tag[5:])
    except ValueError:
        return None


def latest_eligible(releases):
    eligible = [r for r in releases if not r.get('draft', True) and r.get('published_at')
                and release_date(r.get('tag_name', '')) is not None]
    return max(eligible, key=lambda r: release_date(r['tag_name'])) if eligible else None


def safe_path(path):
    return (bool(path) and not re.search(r'[\\\x00-\x1f\x7f:]', path)
            and not re.search(r'(?i)[0-9a-f]{40}', path)
            and all(p and not p.startswith('.') for p in path.split('/')))


def group_for(path):
    if not safe_path(path):
        return None
    for group, (prefix, suffix, recursive) in GROUPS.items():
        relative = path.removeprefix(prefix)
        if path.startswith(prefix) and relative.endswith(suffix) and (recursive or '/' not in relative):
            return group
    return None


def inventory(root, commit):
    items = {group: {} for group in GROUPS}
    for entry in filter(None, git(root, 'ls-tree', '-rz', commit, '--', '_PGM').split(b'\0')):
        metadata, raw_path = entry.split(b'\t', 1)
        mode, kind, blob = metadata.decode('ascii').split()
        try:
            path = raw_path.decode('utf-8')
        except UnicodeDecodeError:
            raise ReleaseError('Artifact path validation failed.') from None
        # Git symlinks are never followed, including links to Alternative directories.
        if mode == '120000' and (path in {'_PGM', '_PGM/cores', '_PGM/_alternatives'}
                                or path.startswith('_PGM/_alternatives/') or group_for(path)):
            raise ReleaseError('Artifact symlink requires maintainer review.')
        group = group_for(path)
        # Unsafe candidates fail closed rather than silently falling out of the inventory.
        for prefix, suffix, recursive in GROUPS.values():
            if path.startswith(prefix) and path.endswith(suffix):
                relative = path[len(prefix):]
                if (recursive or '/' not in relative) and not any(p.startswith('.') for p in relative.split('/')):
                    if not safe_path(path):
                        raise ReleaseError('Artifact path validation failed.')
        if group is None:
            continue
        if mode not in {'100644', '100755'} or kind != 'blob':
            raise ReleaseError('Artifacts must be regular files.')
        content = git(root, 'cat-file', 'blob', blob)
        if not content:
            raise ReleaseError('An active artifact is empty.')
        if group != 'Cores':
            try:
                if ET.fromstring(content).tag != 'misterromdescription':
                    raise ValueError()
            except (ET.ParseError, ValueError):
                raise ReleaseError('MRA validation failed.') from None
        items[group][path] = content
    if not items['Primary MRA'] or not items['Cores']:
        raise ReleaseError('The active distribution requires primary MRAs and cores.')
    return items


def compare(previous, current):
    return {group: {'Added': sorted(current[group].keys() - previous[group].keys()),
                    'Updated': sorted(p for p in current[group].keys() & previous[group].keys()
                                      if current[group][p] != previous[group][p]),
                    'Removed': sorted(previous[group].keys() - current[group].keys())}
            for group in GROUPS}


def significance(changes):
    reasons = []
    if any(changes['Cores'].values()):
        reasons.append('Compiled core changes')
    for group in ('Primary MRA', 'Alternative MRA'):
        if changes[group]['Added'] or changes[group]['Removed']:
            reasons.append(group + ' additions or removals')
    updates = sum(len(changes[g]['Updated']) for g in ('Primary MRA', 'Alternative MRA'))
    if updates >= THRESHOLD:
        reasons.append('Accumulated MRA updates')
    return bool(reasons), reasons, updates


def safe_context(subjects, changes):
    """Recognize fixed preservation wording; never reproduce arbitrary Git subjects."""
    context = []
    cores = changes['Cores']['Updated']
    if cores and all('PGM-027A' in p.upper() for p in cores):
        if 'Update PGM-027A core' in subjects or 'Update MRA files and PGM-027A core' in subjects:
            context.append('Updated PGM-027A core artifacts')
    mras = changes['Primary MRA']['Updated']
    if mras and all(re.search(r'kov2|knights of valour 2', p, re.I) for p in mras):
        if 'Update KOV2 MRA files' in subjects or 'Update KOV2 MRA files and PGM core' in subjects:
            context.append('Updated Knights of Valour 2 game definitions')
    return context


def display_path(path, group):
    relative = path[len(GROUPS[group][0]):]
    return re.sub(r'([\\`*_{}\[\]])', r'\\\1', html.escape(relative))


def notes(day, baseline, changes, current, subjects=()):
    significant, reasons, updates = significance(changes)
    lines = ['## PGM MiSTer FPGA Public Beta', '',
             'PGM FPGA core originally developed by Eizo Chiu.', '']
    if baseline is None:
        lines += ['Initial preservation baseline preview. Maintainer approval is required.', '']
    else:
        lines += ['Changes since ' + baseline + '.', '', '### Highlights', '']
        for group, states in changes.items():
            for action, paths in states.items():
                if paths:
                    noun = {'Cores': 'compiled core', 'Primary MRA': 'primary MRA file',
                            'Alternative MRA': 'alternative MRA file'}[group]
                    if len(paths) != 1:
                        noun += 's'
                    lines.append(f'- {action} {len(paths)} {noun}')
        lines += ['- ' + text for text in safe_context(subjects, changes)]
        if not any(any(states.values()) for states in changes.values()):
            lines.append('- No active artifact changes')
        lines += ['', '### Changes Since Previous Release', '']
        for group, states in changes.items():
            lines += [group, ''] + [f'- {action}: {len(paths)}' for action, paths in states.items()] + ['']
            for action in ('Added', 'Removed'):
                paths = states[action]
                if paths:
                    lines += [action + ' ' + group + ' artifacts:', '']
                    lines += ['- ' + display_path(p, group) for p in paths[:20]]
                    if len(paths) > 20:
                        lines.append(f'- {len(paths) - 20} more; see the artifact diff')
                    lines.append('')
    lines += ['### Package', ''] + [f'- {group}: {len(items)}' for group, items in current.items()]
    return '\n'.join(lines) + '\n'


def evaluation_report(day, baseline, changes, current, subjects=()):
    significant, reasons, updates = significance(changes)
    lines = ['PGM Release Evaluation', '', 'Baseline: ' + (baseline or '(none)'), '',
             'Significant change: ' + ('YES' if significant and baseline else 'NO'), '']
    if baseline is None:
        lines += ['No eligible automated release baseline found.',
                  'Initial preservation baseline requires maintainer approval.', '',
                  'Current active distribution:']
        lines += [f'{group}: {len(items)}' for group, items in current.items()]
    else:
        lines.append('Since previous release:')
        for group, states in changes.items():
            lines += ['', group] + [f'{action}: {len(paths)}' for action, paths in states.items()]
        if significant:
            lines += ['', 'Release triggered by:'] + ['- ' + reason for reason in reasons]
        else:
            lines += ['', 'No significant PGM release changes detected.',
                      f'{updates} MRA updates remain accumulated since the previous release.',
                      'No release published.']
    if significant or baseline is None:
        lines += ['', 'Proposed title: PGM MiSTer FPGA Public Beta - ' + day.isoformat(),
                  'Proposed tag: beta-' + day.isoformat(),
                  'Package: PGM-MiSTer-' + day.isoformat() + '.zip', '',
                  notes(day, baseline, changes, current, subjects)]
    return '\n'.join(lines).rstrip() + '\n'


def flat_inventory(items):
    return {path: content for group in items.values() for path, content in group.items()}


def validate_package(path, current):
    expected = flat_inventory(current)
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) != len(expected) or {e.filename for e in entries} != set(expected):
            raise ReleaseError('Package inventory validation failed.')
        for entry in entries:
            if (group_for(entry.filename) is None or entry.is_dir()
                    or (entry.external_attr >> 16) & 0o170000 != 0o100000
                    or archive.read(entry) != expected[entry.filename]):
                raise ReleaseError('Package artifact validation failed.')


def manifest_text(day, current, package, digest):
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ReleaseError('Checksum validation failed.')
    return (f'Release: {day.isoformat()}\nPrimary MRAs: {len(current["Primary MRA"])}\n'
            f'Alternative MRAs: {len(current["Alternative MRA"])}\nCores: {len(current["Cores"])}\n'
            f'Package: {package}\nSHA-256: {digest}\n')


def build_package(output, day, current):
    if day.year < 1980:
        raise ReleaseError('Release date cannot be represented in the package.')
    package = output / ('PGM-MiSTer-' + day.isoformat() + '.zip')
    with zipfile.ZipFile(package, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(flat_inventory(current).items()):
            if group_for(path) is None:
                raise ReleaseError('Package contains an unmanaged path.')
            info = zipfile.ZipInfo(path, (day.year, day.month, day.day, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    validate_package(package, current)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    manifest = output / ('PGM-MiSTer-' + day.isoformat() + '-manifest.txt')
    manifest.write_text(manifest_text(day, current, package.name, digest), encoding='utf-8')
    validate_assets(package, manifest, day, current)
    return package, manifest


def validate_assets(package, manifest, day, current):
    validate_package(package, current)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    if manifest.read_text(encoding='utf-8') != manifest_text(day, current, package.name, digest):
        raise ReleaseError('Manifest validation failed.')


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != 'https':
            raise ReleaseError('Unexpected asset redirect.')
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if redirected and urlsplit(newurl).hostname != urlsplit(request.full_url).hostname:
            redirected.remove_header('Authorization')
        return redirected


class GitHub:
    def __init__(self, token):
        self.token = token
        self.opener = build_opener(SafeRedirect())

    def request(self, path, method='GET', payload=None, raw=False, upload=False):
        host = 'https://uploads.github.com' if upload else 'https://api.github.com'
        headers = {'Accept': 'application/octet-stream' if raw else 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'PGM-preservation-release'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        data = payload if isinstance(payload, bytes) else (json.dumps(payload).encode() if payload is not None else None)
        if data is not None:
            headers['Content-Type'] = 'application/octet-stream' if upload else 'application/json'
        request = Request(host + '/repos/' + REPOSITORY + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=120) as response:
                body = response.read()
        except HTTPError as error:
            if error.code == 404 and method == 'GET':
                return None
            raise ReleaseError('Preservation repository API request failed.') from None
        return body if raw else (json.loads(body) if body else None)

    def releases(self):
        releases = []
        for page in range(1, 1001):
            batch = self.request(f'/releases?per_page=100&page={page}')
            if not isinstance(batch, list):
                raise ReleaseError('Release baseline lookup failed.')
            releases.extend(batch)
            if len(batch) < 100:
                return releases
        raise ReleaseError('Release baseline pagination exceeded its limit.')

    def tag_commit(self, tag):
        reference = self.request('/git/ref/tags/' + quote(tag, safe=''))
        if reference is None:
            return None
        obj = reference['object']
        for _ in range(10):
            if obj['type'] == 'commit' and re.fullmatch(r'[0-9a-f]{40}', obj['sha']):
                return obj['sha']
            if obj['type'] != 'tag':
                break
            obj = self.request('/git/tags/' + obj['sha'])['object']
        raise ReleaseError('Preservation tag resolution failed.')

    def main_commit(self):
        return self.request('/git/ref/heads/main')['object']['sha']


def ensure_available(api, tag):
    if api.tag_commit(tag) is not None or any(r.get('tag_name') == tag for r in api.releases()):
        raise ReleaseError("Today's preservation tag or release already exists; nothing overwritten.")


def verify_remote_assets(api, state, assets):
    expected = {asset.name: asset.read_bytes() for asset in assets}
    if len(state['assets']) != len(expected) or {a['name'] for a in state['assets']} != set(expected):
        raise ReleaseError('Release asset inventory verification failed.')
    for asset in state['assets']:
        if (asset['state'] != 'uploaded' or asset['size'] != len(expected[asset['name']])
                or api.request('/releases/assets/' + str(asset['id']), raw=True) != expected[asset['name']]):
            raise ReleaseError('Release asset byte verification failed.')


def publish(api, root, commit, baseline, day, changes, current, subjects, package, manifest, release_notes):
    """Validate locally, upload to a draft, verify bytes, then make it public."""
    tag = 'beta-' + day.isoformat()
    title = 'PGM MiSTer FPGA Public Beta - ' + day.isoformat()
    if not baseline or not significance(changes)[0] or release_date(tag) != day:
        raise ReleaseError('Publication requires a reviewed baseline and significant changes.')
    if inventory(root, commit) != current or release_notes != notes(day, baseline, changes, current, subjects):
        raise ReleaseError('Final artifact or release-note verification failed.')
    validate_assets(package, manifest, day, current)
    baseline_commit = api.tag_commit(baseline)
    if baseline_commit is None or compare(inventory(root, baseline_commit), current) != changes:
        raise ReleaseError('Baseline artifact comparison changed; run a new preview.')
    latest = latest_eligible(api.releases())
    if not latest or latest['tag_name'] != baseline or api.main_commit() != commit:
        raise ReleaseError('Release baseline or main changed; run a new preview.')
    ensure_available(api, tag)
    draft_id = None
    published = False
    tag_created = False
    try:
        draft = api.request('/releases', 'POST', {'tag_name': tag, 'target_commitish': commit,
                            'name': title, 'body': release_notes, 'draft': True,
                            'prerelease': True, 'make_latest': 'false'})
        draft_id = draft['id']
        for asset in (package, manifest):
            uploaded = api.request(f'/releases/{draft_id}/assets?name=' + quote(asset.name),
                                   'POST', asset.read_bytes(), upload=True)
            if (uploaded['name'] != asset.name or uploaded['state'] != 'uploaded'
                    or uploaded['size'] != asset.stat().st_size
                    or api.request('/releases/assets/' + str(uploaded['id']), raw=True) != asset.read_bytes()):
                raise ReleaseError('Uploaded release asset verification failed.')
        ready = api.request(f'/releases/{draft_id}')
        if not ready['draft'] or ready['tag_name'] != tag or ready['name'] != title or ready['body'] != release_notes:
            raise ReleaseError('Draft release verification failed.')
        verify_remote_assets(api, ready, (package, manifest))
        latest = latest_eligible(api.releases())
        if (not latest or latest['tag_name'] != baseline or api.main_commit() != commit
                or api.tag_commit(baseline) != baseline_commit):
            raise ReleaseError('Release baseline or main changed; draft not published.')
        # Never overwrite a tag that appeared while the assets were uploading.
        if api.tag_commit(tag) is not None:
            raise ReleaseError('Preservation tag appeared during publication; draft not published.')
        # Creating a ref is atomic and never updates an existing tag.
        reference = api.request('/git/refs', 'POST', {'ref': 'refs/tags/' + tag, 'sha': commit})
        tag_created = True
        if (reference['ref'] != 'refs/tags/' + tag or reference['object']['type'] != 'commit'
                or reference['object']['sha'] != commit or api.tag_commit(tag) != commit):
            raise ReleaseError('New preservation tag verification failed.')
        result = api.request(f'/releases/{draft_id}', 'PATCH', {'draft': False})
        published = not result['draft']
        final = api.request(f'/releases/{draft_id}')
        if (final['draft'] or not final.get('published_at') or final['tag_name'] != tag
                or final['name'] != title or final['body'] != release_notes
                or {a['name'] for a in final['assets']} != {package.name, manifest.name}
                or api.tag_commit(tag) != commit):
            raise ReleaseError('Published release verification requires maintainer review.')
        verify_remote_assets(api, final, (package, manifest))
    except Exception:
        if draft_id is not None and not published:
            # If the final response was lost, inspect state before any cleanup.
            try:
                state = api.request(f'/releases/{draft_id}')
                if state and state['draft']:
                    api.request(f'/releases/{draft_id}', 'DELETE')
                    if (tag_created and api.tag_commit(tag) == commit
                            and not any(r.get('tag_name') == tag and not r.get('draft', True)
                                        for r in api.releases())):
                        api.request('/git/refs/tags/' + tag, 'DELETE')
            except Exception:
                pass
        raise


def run(root, output, api, day, do_publish=False, expected_commit=None, expected_baseline=None,
        expected_baseline_commit=None):
    root = root.resolve()
    output = output.resolve()
    if output.is_relative_to(root) or output == root:
        raise ReleaseError('Preview output must be outside the repository.')
    if output.exists() and any(output.iterdir()):
        raise ReleaseError('Use an empty output directory; existing assets are never overwritten.')
    commit = git(root, 'rev-parse', 'HEAD').decode().strip()
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ReleaseError('Current preservation commit validation failed.')
    if expected_commit is not None and expected_commit != commit:
        raise ReleaseError('Current main differs from the evaluated commit.')
    if api.main_commit() != commit:
        raise ReleaseError('Checkout must represent current main; run a new preview.')
    releases = api.releases()
    baseline_release = latest_eligible(releases)
    baseline = baseline_release['tag_name'] if baseline_release else None
    if baseline and release_date(baseline) > day:
        raise ReleaseError('Latest preservation release date is in the future; maintainer review required.')
    if expected_baseline is not None and expected_baseline != (baseline or ''):
        raise ReleaseError('Published baseline changed; run a new preview.')
    ensure_available(api, 'beta-' + day.isoformat())
    current = inventory(root, commit)
    subjects = []
    baseline_commit = ''
    if baseline:
        baseline_commit = api.tag_commit(baseline)
        if baseline_commit is None:
            raise ReleaseError('Published preservation baseline tag is missing.')
        if expected_baseline_commit is not None and expected_baseline_commit != baseline_commit:
            raise ReleaseError('Evaluated baseline tag target changed; run a new preview.')
        subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', baseline_commit, commit],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        previous = inventory(root, baseline_commit)
        subjects = git(root, 'log', '--format=%s', baseline_commit + '..' + commit).decode('utf-8', errors='replace').splitlines()
    else:
        previous = {g: {} for g in GROUPS}
    changes = compare(previous, current)
    significant = bool(baseline) and significance(changes)[0]
    report = evaluation_report(day, baseline, changes, current, subjects)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'evaluation.md').write_text(report, encoding='utf-8')
    if significant or baseline is None:
        release_notes = notes(day, baseline, changes, current, subjects)
        (output / 'release-notes.md').write_text(release_notes, encoding='utf-8')
        (output / 'artifact-diff.json').write_text(json.dumps(changes, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        package, manifest = build_package(output, day, current)
        report += '\n' + manifest.read_text(encoding='utf-8')
        if do_publish and baseline is not None:
            publish(api, root, commit, baseline, day, changes, current, subjects, package, manifest, release_notes)
            report += '\nPreservation release published.\n'
        else:
            report += '\nPreview only. No tag or release created.\n'
    elif do_publish:
        report += '\nNo release published.\n'
    # No eligible baseline can ever be published by this engine.
    (output / 'evaluation.md').write_text(report, encoding='utf-8')
    return report, {'commit': commit, 'baseline': baseline or '', 'baseline_commit': baseline_commit,
                    'date': day.isoformat(),
                    'significant': str(significant).lower()}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Preview the weekly PGM preservation release.')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--expected-commit')
    parser.add_argument('--expected-baseline')
    parser.add_argument('--expected-baseline-commit')
    parser.add_argument('--expected-date')
    options = parser.parse_args(argv)
    try:
        for variable in ('GITHUB_STEP_SUMMARY', 'GITHUB_OUTPUT'):
            destination = os.environ.get(variable)
            if destination and Path(destination).resolve().is_relative_to(Path.cwd().resolve()):
                raise ReleaseError('Workflow output must be outside the repository.')
        day = datetime.now(timezone.utc).date()
        if options.expected_date is not None and options.expected_date != day.isoformat():
            raise ReleaseError('UTC release date changed; run a new preview.')
        report, outputs = run(Path.cwd(), options.output, GitHub(os.environ.get('GH_TOKEN', '')),
                              day, options.publish,
                              options.expected_commit, options.expected_baseline,
                              options.expected_baseline_commit)
        print(report)
        for variable, text in [('GITHUB_STEP_SUMMARY', report),
                               ('GITHUB_OUTPUT', ''.join(f'{k}={v}\n' for k, v in outputs.items()))]:
            destination = os.environ.get(variable)
            if destination:
                path = Path(destination).resolve()
                if path.is_relative_to(Path.cwd().resolve()):
                    raise ReleaseError('Workflow output must be outside the repository.')
                with path.open('a', encoding='utf-8') as stream:
                    stream.write(text)
        return 0
    except ReleaseError as error:
        print('Release evaluation stopped: ' + str(error))
        return 1
    except Exception:
        # Remote errors and Git diagnostics must never become public provenance.
        print('Release evaluation failed; no automatic retry. Maintainer review required.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
