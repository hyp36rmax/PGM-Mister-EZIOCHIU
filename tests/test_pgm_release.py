import copy
from datetime import date
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request
import zipfile

spec = importlib.util.spec_from_file_location('pgm_release', Path(__file__).parents[1] / 'scripts/pgm_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
MRA = b'<misterromdescription><name>Game</name></misterromdescription>'
UPDATED = b'<misterromdescription><name>Updated</name></misterromdescription>'
DAY = date(2026, 10, 9)
BASE_TAG = 'beta-2026-10-02'
TAG = 'beta-2026-10-09'


def published(tag):
    return {'tag_name': tag, 'draft': False, 'published_at': '2026-10-02T18:00:00Z'}


class FakeGitHub:
    def __init__(self, commit):
        self.main = commit
        self.tags = {BASE_TAG: commit}
        self.collection = [published(BASE_TAG)]
        self.calls = []
        self.draft = None
        self.asset_bytes = {}
        self.fail_upload = False
        self.corrupt_upload = False
        self.fail_publish = False
        self.lost_publish_response = False
        self.move_main_on_upload = False

    def releases(self):
        return copy.deepcopy(self.collection + ([self.draft] if self.draft else []))

    def main_commit(self):
        return self.main

    def tag_commit(self, tag):
        return self.tags.get(tag)

    def request(self, path, method='GET', payload=None, raw=False, upload=False):
        self.calls.append((path, method))
        if path == '/git/refs' and method == 'POST':
            tag = payload['ref'].removeprefix('refs/tags/')
            if tag in self.tags:
                raise release.ReleaseError('Fixture duplicate tag')
            self.tags[tag] = payload['sha']
            return {'ref': payload['ref'], 'object': {'type': 'commit', 'sha': payload['sha']}}
        if path.startswith('/git/refs/tags/') and method == 'DELETE':
            del self.tags[path.removeprefix('/git/refs/tags/')]
            return None
        if path == '/releases' and method == 'POST':
            self.draft = dict(payload, id=100, assets=[], published_at=None)
            return copy.deepcopy(self.draft)
        if path.startswith('/releases/100/assets?') and upload:
            if self.fail_upload:
                raise release.ReleaseError('Fixture upload failure')
            name = path.split('name=')[1]
            asset_id = len(self.asset_bytes) + 1
            self.asset_bytes[asset_id] = payload + (b'corruption' if self.corrupt_upload else b'')
            asset = {'id': asset_id, 'name': name, 'state': 'uploaded', 'size': len(payload)}
            self.draft['assets'].append(asset)
            if self.move_main_on_upload:
                self.main = 'f' * 40
            return asset
        if path.startswith('/releases/assets/') and raw:
            return self.asset_bytes[int(path.rsplit('/', 1)[1])]
        if path == '/releases/100':
            if method == 'GET':
                return copy.deepcopy(self.draft)
            if method == 'DELETE':
                self.draft = None
                return None
            if method == 'PATCH':
                if self.fail_publish:
                    raise release.ReleaseError('Fixture publication failure')
                self.draft.update(draft=False, published_at='2026-10-09T18:00:00Z')
                self.tags[self.draft['tag_name']] = self.draft['target_commitish']
                if self.lost_publish_response:
                    raise release.ReleaseError('Fixture response lost')
                return copy.deepcopy(self.draft)
        raise AssertionError('Unexpected fake API operation: ' + path)


class SignificanceTests(unittest.TestCase):
    def changes(self):
        return {g: {s: [] for s in ('Added', 'Updated', 'Removed')} for g in release.GROUPS}

    def test_no_changes(self):
        self.assertEqual(release.significance(self.changes()), (False, [], 0))

    def test_mra_update_threshold(self):
        for count in (1, 4, 5):
            with self.subTest(count=count):
                changes = self.changes()
                changes['Primary MRA']['Updated'] = [f'game{i}.mra' for i in range(count)]
                self.assertEqual(release.significance(changes)[0], count >= 5)

    def test_combined_updates(self):
        changes = self.changes()
        changes['Primary MRA']['Updated'] = ['one', 'two']
        changes['Alternative MRA']['Updated'] = ['three', 'four', 'five']
        self.assertEqual(release.significance(changes)[2], 5)
        self.assertTrue(release.significance(changes)[0])

    def test_valid_calendar_tags(self):
        for tag in ('v1', 'dev', 'test-beta-2026-10-02', 'beta-2026-2-02',
                    'beta-2026-02-30', 'beta-2026-10-02-extra', 'beta-2026-10-02\n'):
            self.assertIsNone(release.release_date(tag), tag)
        self.assertEqual(release.release_date(BASE_TAG), date(2026, 10, 2))

    def test_latest_published_eligible_release(self):
        releases = [published('beta-2026-09-01'), published(BASE_TAG),
                    published('beta-2026-99-01'), published('source-version'),
                    dict(published('beta-2026-12-01'), draft=True),
                    dict(published('beta-2026-11-01'), published_at=None)]
        self.assertEqual(release.latest_eligible(releases)['tag_name'], BASE_TAG)
        self.assertIsNone(release.latest_eligible([published('v1')]))

    def test_misleading_subject_does_not_invent_changes(self):
        changes = self.changes()
        self.assertEqual(release.safe_context(['Update PGM-027A core', 'Update KOV2 MRA files'], changes), [])

    def test_safe_context_requires_matching_artifact(self):
        changes = self.changes()
        changes['Cores']['Updated'] = ['_PGM/cores/PGM-027A.rbf']
        self.assertEqual(release.safe_context(['Update PGM-027A core'], changes), ['Updated PGM-027A core artifacts'])
        changes['Cores']['Updated'] = ['_PGM/cores/unrelated.rbf']
        self.assertEqual(release.safe_context(['Update PGM-027A core'], changes), [])
        changes['Cores']['Updated'] = []
        changes['Cores']['Added'] = ['_PGM/cores/PGM-027A.rbf']
        self.assertEqual(release.safe_context(['Update PGM-027A core'], changes), [])

    def test_arbitrary_source_context_is_not_copied(self):
        changes = self.changes()
        changes['Cores']['Updated'] = ['_PGM/cores/PGM.rbf']
        subjects = ['Update core https://example.invalid/private-owner/private-repository',
                    'Update private-branch', 'Update ' + 'a' * 40, 'Source Author <author@example.invalid>']
        text = release.notes(DAY, BASE_TAG, changes, {g: {} for g in release.GROUPS}, subjects)
        for value in ('private-owner', 'private-repository', 'private-branch', 'author@example', 'a' * 40):
            self.assertNotIn(value, text)
        self.assertIn('Updated 1 compiled core', text)
        self.assertIn('Eizo Chiu', text)

    def test_large_addition_notes_remain_concise(self):
        changes = self.changes()
        changes['Alternative MRA']['Added'] = [f'_PGM/_alternatives/Set/game{i}.mra' for i in range(21)]
        text = release.notes(DAY, BASE_TAG, changes, {g: {} for g in release.GROUPS})
        self.assertIn('Added 21 alternative MRA files', text)
        self.assertIn('1 additional artifacts', text)
        self.assertNotIn('see the artifact diff', text)

    def test_allowlist_excludes_unmanaged_and_unsafe_paths(self):
        for path in ('legacy/mra/game.mra', 'docs/game.mra', 'scripts/x.py', 'tests/x.py',
                     '_PGM/cores/nested/core.rbf', '_PGM/nested/game.mra',
                     '_PGM/_alternatives/../game.mra', '/_PGM/game.mra',
                     '_PGM/_alternatives/.hidden/game.mra', '_PGM/.hidden.mra',
                     '_PGM/_alternatives/Set/game\n.mra', '_PGM/_alternatives/Set/game\\x.mra',
                     '_PGM/_alternatives/Set/' + 'a' * 40 + '.mra'):
            self.assertIsNone(release.group_for(path), path)

    def test_asset_redirect_strips_credentials(self):
        request = Request('https://api.github.com/asset', headers={'Authorization': 'Bearer fixture'})
        result = release.SafeRedirect().redirect_request(request, None, 302, '', {}, 'https://release-assets.githubusercontent.com/asset')
        self.assertIsNone(result.get_header('Authorization'))
        with self.assertRaises(release.ReleaseError):
            release.SafeRedirect().redirect_request(request, None, 302, '', {}, 'http://unsafe.invalid/asset')

    def test_release_pagination(self):
        api = release.GitHub('')
        first = [published('v1')] * 100
        with patch.object(api, 'request', side_effect=[first, [published(BASE_TAG)]]) as request:
            self.assertEqual(len(api.releases()), 101)
            self.assertIn('page=2', request.call_args.args[0])

    def test_annotated_tag_resolution(self):
        api = release.GitHub('')
        with patch.object(api, 'request', side_effect=[{'object': {'type': 'tag', 'sha': 'a' * 40}},
                                                      {'object': {'type': 'commit', 'sha': 'b' * 40}}]):
            self.assertEqual(api.tag_commit(BASE_TAG), 'b' * 40)


def policy_case(group, state):
    def test(self):
        changes = self.changes()
        changes[group][state] = ['artifact']
        self.assertTrue(release.significance(changes)[0])
    return test


for group, states in [('Cores', ('Added', 'Updated', 'Removed')),
                      ('Primary MRA', ('Added', 'Removed')), ('Alternative MRA', ('Added', 'Removed'))]:
    for state in states:
        setattr(SignificanceTests, 'test_' + group.lower().replace(' ', '_') + '_' + state.lower(), policy_case(group, state))


class ReleaseEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'preservation'
        self.root.mkdir()
        self.output = Path(self.temp.name) / 'preview'
        self.git('init', '--initial-branch=main')
        self.git('config', 'user.name', 'Fixture Maintainer')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'core.autocrlf', 'false')
        for i in range(5):
            self.write(f'_PGM/game{i}.mra', MRA)
        for path in ('Set A/game.mra', 'Set B/Region/game.mra'):
            self.write('_PGM/_alternatives/' + path, MRA)
        self.write('_PGM/cores/PGM.rbf', b'core')
        self.write('_PGM/cores/secondary.rbf', b'secondary-core')
        for path in ('README.md', 'LICENSE', 'docs/report.md', 'legacy/mra/old.mra',
                     'legacy/cores/old.rbf', 'scripts/tool.py', 'tests/test_other.py',
                     '.github/workflows/other.yml', '_PGM/_alternatives/Set A/README.md',
                     '_PGM/_alternatives/.hidden/hidden.mra', '_PGM/_alternatives/Set A/.hidden.mra',
                     '_PGM/cores/nested/ignored.rbf', '_PGM/other/ignored.mra'):
            self.write(path, b'unmanaged')
        self.base = self.commit('Fixture baseline')
        self.git('tag', BASE_TAG)
        self.api = FakeGitHub(self.base)

    def git(self, *args):
        return release.git(self.root, *args)

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def commit(self, subject='Fixture artifact change'):
        self.git('add', '.')
        self.git('commit', '-m', subject)
        sha = self.git('rev-parse', 'HEAD').decode().strip()
        if hasattr(self, 'api'):
            self.api.main = sha
        return sha

    def evaluate(self, publish=False, **kwargs):
        return release.run(self.root, self.output, self.api, DAY, publish, **kwargs)

    def significant(self):
        self.write('_PGM/cores/PGM.rbf', b'updated-core')
        self.commit('Update PGM core')

    def package(self):
        return self.output / 'PGM-MiSTer-2026-10-09.zip'

    def test_no_changes_creates_no_assets_or_release(self):
        report, result = self.evaluate(publish=True)
        self.assertEqual(result['significant'], 'false')
        self.assertIn('No significant PGM release changes detected.', report)
        self.assertIn('0 MRA updates remain accumulated', report)
        self.assertFalse(self.package().exists())
        self.assertEqual(self.api.calls, [])

    def test_one_to_four_updates_accumulate(self):
        for i in range(4):
            self.write(f'_PGM/game{i}.mra', UPDATED)
        self.commit()
        report, result = self.evaluate()
        self.assertEqual(result['baseline'], BASE_TAG)
        self.assertEqual(result['significant'], 'false')
        self.assertIn('4 MRA updates remain accumulated', report)
        self.assertFalse(self.package().exists())
        self.assertEqual(self.api.collection, [published(BASE_TAG)])

    def test_accumulation_keeps_last_published_baseline(self):
        for i in range(3):
            self.write(f'_PGM/game{i}.mra', UPDATED)
        self.commit()
        _, first = self.evaluate()
        self.assertEqual(first['significant'], 'false')
        self.output = Path(self.temp.name) / 'second-preview'
        for i in (3, 4):
            self.write(f'_PGM/game{i}.mra', UPDATED)
        self.commit()
        report, second = self.evaluate()
        self.assertEqual(second['baseline'], BASE_TAG)
        self.assertEqual(second['significant'], 'true')
        self.assertIn('Updated: 5', report)

    def test_combined_primary_alternative_threshold_integration(self):
        for i in range(3):
            self.write(f'_PGM/game{i}.mra', UPDATED)
        for path in ('Set A/game.mra', 'Set B/Region/game.mra'):
            self.write('_PGM/_alternatives/' + path, UPDATED)
        self.commit()
        self.assertEqual(self.evaluate()[1]['significant'], 'true')

    def test_docs_workflow_legacy_changes_ignored(self):
        for path in ('README.md', '.github/workflows/other.yml', 'legacy/mra/old.mra'):
            self.write(path, b'edited documentation')
        self.commit()
        self.assertEqual(self.evaluate()[1]['significant'], 'false')

    def test_relocation_is_significant(self):
        old = self.root / '_PGM/_alternatives/Set A/game.mra'
        self.write('_PGM/_alternatives/Set C/game.mra', old.read_bytes())
        old.unlink()
        self.commit()
        report, result = self.evaluate()
        self.assertEqual(result['significant'], 'true')
        self.assertIn('Set C/game.mra', report)
        self.assertIn('Set A/game.mra', report)

    def test_initial_baseline_preview_cannot_publish(self):
        self.api.collection = [published('unrelated')]
        self.api.tags = {}
        report, result = self.evaluate(publish=True)
        self.assertEqual(result['baseline'], '')
        self.assertEqual(result['significant'], 'false')
        self.assertIn('Initial preservation baseline requires maintainer approval.', report)
        self.assertTrue(self.package().exists())
        self.assertEqual(self.api.calls, [])

    def test_duplicate_tag_rejected(self):
        self.significant()
        self.api.tags[TAG] = self.api.main
        with self.assertRaisesRegex(release.ReleaseError, 'already exists'):
            self.evaluate(publish=True)
        self.assertEqual(self.api.calls, [])

    def test_duplicate_draft_release_rejected(self):
        self.significant()
        self.api.collection.append(dict(published(TAG), draft=True))
        with self.assertRaisesRegex(release.ReleaseError, 'already exists'):
            self.evaluate()

    def test_missing_baseline_tag_fails_closed(self):
        self.api.tags.clear()
        with self.assertRaisesRegex(release.ReleaseError, 'tag is missing'):
            self.evaluate(publish=True)
        self.assertEqual(self.api.calls, [])

    def test_future_baseline_date_requires_review(self):
        self.api.collection.append(published('beta-2027-01-01'))
        with self.assertRaisesRegex(release.ReleaseError, 'in the future'):
            self.evaluate(publish=True)
        self.assertEqual(self.api.calls, [])

    def test_unknown_baseline_commit_fails_closed(self):
        self.api.tags[BASE_TAG] = 'a' * 40
        with self.assertRaises(subprocess.CalledProcessError):
            self.evaluate(publish=True)
        self.assertEqual(self.api.calls, [])

    def test_package_exact_scope_hierarchy_and_bytes(self):
        self.significant()
        self.evaluate()
        with zipfile.ZipFile(self.package()) as archive:
            names = set(archive.namelist())
            self.assertEqual(len(names), 9)
            self.assertIn('_PGM/_alternatives/Set A/game.mra', names)
            self.assertIn('_PGM/_alternatives/Set B/Region/game.mra', names)
            self.assertEqual(archive.read('_PGM/cores/PGM.rbf'), b'updated-core')
            self.assertTrue(all(release.group_for(p) for p in names))
        release.validate_package(self.package(), release.inventory(self.root, self.api.main))

    def test_manifest_counts_and_sha256(self):
        self.significant()
        report, _ = self.evaluate()
        text = (self.output / 'PGM-MiSTer-2026-10-09-manifest.txt').read_text(encoding='utf-8')
        self.assertIn('Primary MRAs: 5\nAlternative MRAs: 2\nCores: 2', text)
        self.assertIn(hashlib.sha256(self.package().read_bytes()).hexdigest(), text)
        self.assertIn(text, report)

    def test_package_and_notes_are_deterministic(self):
        self.significant()
        report, _ = self.evaluate()
        original = self.package().read_bytes()
        self.output = Path(self.temp.name) / 'second'
        second, _ = self.evaluate()
        self.assertEqual(original, self.package().read_bytes())
        self.assertEqual(report, second)

    def test_corrupt_manifest_prevents_publication(self):
        self.significant()
        self.evaluate()
        current = release.inventory(self.root, self.api.main)
        manifest = self.output / 'PGM-MiSTer-2026-10-09-manifest.txt'
        manifest.write_text('wrong counts', encoding='utf-8')
        with self.assertRaisesRegex(release.ReleaseError, 'Manifest'):
            release.validate_assets(self.package(), manifest, DAY, current)
        self.assertEqual(self.api.calls, [])

    def test_extra_package_file_rejected(self):
        self.significant()
        self.evaluate()
        with zipfile.ZipFile(self.package(), 'a') as archive:
            archive.writestr('README.md', b'not allowed')
        with self.assertRaises(release.ReleaseError):
            release.validate_package(self.package(), release.inventory(self.root, self.api.main))

    def test_corrupt_package_bytes_rejected(self):
        self.significant()
        self.evaluate()
        with zipfile.ZipFile(self.package(), 'a') as archive:
            archive.writestr('_PGM/cores/PGM.rbf', b'corruption')
        with self.assertRaises(release.ReleaseError):
            release.validate_package(self.package(), release.inventory(self.root, self.api.main))

    def test_empty_core_prevents_publication(self):
        self.write('_PGM/cores/PGM.rbf', b'')
        self.commit()
        with self.assertRaisesRegex(release.ReleaseError, 'empty'):
            self.evaluate(publish=True)
        self.assertEqual(self.api.calls, [])

    def test_malformed_nested_mra_prevents_publication(self):
        self.write('_PGM/_alternatives/Set A/game.mra', b'bad XML')
        self.commit()
        with self.assertRaisesRegex(release.ReleaseError, 'MRA validation'):
            self.evaluate(publish=True)
        self.assertEqual(self.api.calls, [])

    def test_git_symlink_rejected(self):
        path = self.root / '_PGM/_alternatives/Set A/link.mra'
        try:
            path.symlink_to(self.root / '_PGM/game0.mra')
        except OSError:
            self.skipTest('Symlink creation unavailable on this host')
        self.commit()
        with self.assertRaisesRegex(release.ReleaseError, 'symlink'):
            self.evaluate(publish=True)
        self.assertEqual(self.api.calls, [])

    def test_git_symlink_directory_rejected(self):
        path = self.root / '_PGM/_alternatives/link'
        try:
            path.symlink_to(self.root / '_PGM', target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation unavailable on this host')
        self.commit()
        with self.assertRaisesRegex(release.ReleaseError, 'symlink'):
            self.evaluate()

    def test_preview_readonly_git_and_files(self):
        self.significant()
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        tags = self.git('show-ref', '--tags')
        head = self.git('rev-parse', 'HEAD')
        self.evaluate()
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(tags, self.git('show-ref', '--tags'))
        self.assertEqual(head, self.git('rev-parse', 'HEAD'))
        self.assertEqual(self.api.collection, [published(BASE_TAG)])
        self.assertEqual(self.api.calls, [])

    def test_output_inside_repository_rejected(self):
        self.output = self.root / 'preview'
        with self.assertRaisesRegex(release.ReleaseError, 'outside'):
            self.evaluate()
        self.assertFalse(self.output.exists())

    def test_existing_output_never_overwritten(self):
        self.output.mkdir()
        (self.output / 'keep.txt').write_bytes(b'keep')
        with self.assertRaisesRegex(release.ReleaseError, 'never overwritten'):
            self.evaluate()
        self.assertEqual((self.output / 'keep.txt').read_bytes(), b'keep')

    def test_source_secret_neither_required_nor_used(self):
        with patch.dict(os.environ, {'PGM_SOURCE_URL': 'https://example.invalid/private-owner/private-repository'}):
            self.significant()
            report, _ = self.evaluate()
        for path in self.output.iterdir():
            if path.suffix != '.zip':
                text = path.read_text(encoding='utf-8')
                self.assertNotIn('private-owner', text)
                self.assertNotIn('private-repository', text)
        self.assertNotIn('example.invalid', report)
        self.assertEqual(self.api.calls, [])

    def test_safe_context_matches_actual_updated_core(self):
        (self.root / '_PGM/cores/secondary.rbf').unlink()
        self.write('_PGM/cores/PGM-027A.rbf', b'old-core')
        baseline = self.commit()
        self.api.tags[BASE_TAG] = baseline
        self.write('_PGM/cores/PGM-027A.rbf', b'new-core')
        self.commit('Update PGM-027A core')
        self.assertIn('Updated PGM-027A core artifacts', self.evaluate()[0])

    def test_misleading_git_subject_ignored_in_notes(self):
        self.write('_PGM/game0.mra', UPDATED)
        self.commit('Update PGM-027A core https://example.invalid/private-owner/private-repository')
        report, result = self.evaluate()
        self.assertEqual(result['significant'], 'false')
        self.assertNotIn('PGM-027A', report)
        self.assertNotIn('private-owner', report)

    def test_fixture_publication_verifies_assets_then_publishes(self):
        self.significant()
        report, _ = self.evaluate(publish=True)
        self.assertIn('Preservation release published.', report)
        self.assertEqual(self.api.tags[TAG], self.api.main)
        self.assertFalse(self.api.draft['draft'])
        self.assertEqual(len(self.api.draft['assets']), 2)
        publish_index = self.api.calls.index(('/releases/100', 'PATCH'))
        self.assertTrue(all(i < publish_index for i, (path, method) in enumerate(self.api.calls) if '/assets' in path and method == 'POST'))

    def test_failed_upload_cleans_draft_without_publication(self):
        self.significant()
        self.api.fail_upload = True
        with self.assertRaises(release.ReleaseError):
            self.evaluate(publish=True)
        self.assertIsNone(self.api.draft)
        self.assertNotIn(TAG, self.api.tags)
        self.assertNotIn(('/releases/100', 'PATCH'), self.api.calls)

    def test_corrupt_uploaded_bytes_prevent_publication(self):
        self.significant()
        self.api.corrupt_upload = True
        with self.assertRaisesRegex(release.ReleaseError, 'asset verification'):
            self.evaluate(publish=True)
        self.assertIsNone(self.api.draft)
        self.assertNotIn(TAG, self.api.tags)

    def test_failed_publish_cleans_confirmed_draft(self):
        self.significant()
        self.api.fail_publish = True
        with self.assertRaises(release.ReleaseError):
            self.evaluate(publish=True)
        self.assertIsNone(self.api.draft)
        self.assertNotIn(TAG, self.api.tags)

    def test_lost_publish_response_never_deletes_public_release(self):
        self.significant()
        self.api.lost_publish_response = True
        with self.assertRaises(release.ReleaseError):
            self.evaluate(publish=True)
        self.assertFalse(self.api.draft['draft'])
        self.assertNotIn(('/releases/100', 'DELETE'), self.api.calls)

    def test_main_change_before_evaluation_fails(self):
        self.api.main = 'a' * 40
        with self.assertRaisesRegex(release.ReleaseError, 'current main'):
            self.evaluate(publish=True)
        self.assertEqual(self.api.calls, [])

    def test_main_change_during_upload_does_not_publish(self):
        self.significant()
        self.api.move_main_on_upload = True
        with self.assertRaisesRegex(release.ReleaseError, 'main changed'):
            self.evaluate(publish=True)
        self.assertIsNone(self.api.draft)
        self.assertNotIn(TAG, self.api.tags)

    def test_evaluated_commit_mismatch_fails(self):
        with self.assertRaisesRegex(release.ReleaseError, 'evaluated commit'):
            self.evaluate(publish=True, expected_commit='a' * 40)

    def test_evaluated_baseline_mismatch_fails(self):
        with self.assertRaisesRegex(release.ReleaseError, 'baseline changed'):
            self.evaluate(publish=True, expected_baseline='beta-2026-09-01')

    def test_baseline_tag_move_during_upload_fails_closed(self):
        self.significant()
        original = self.api.request
        def request(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if kwargs.get('upload'):
                self.api.tags[BASE_TAG] = self.api.main
            return result
        with patch.object(self.api, 'request', side_effect=request):
            with self.assertRaisesRegex(release.ReleaseError, 'baseline or main changed'):
                self.evaluate(publish=True)
        self.assertIsNone(self.api.draft)
        self.assertNotIn(TAG, self.api.tags)

    def test_evaluated_baseline_target_mismatch_fails(self):
        with self.assertRaisesRegex(release.ReleaseError, 'tag target changed'):
            self.evaluate(publish=True, expected_baseline_commit='a' * 40)
        self.assertEqual(self.api.calls, [])

    def test_atomic_tag_conflict_never_overwrites_other_tag(self):
        self.significant()
        original = self.api.request
        def request(path, *args, **kwargs):
            if path == '/git/refs':
                self.api.tags[TAG] = self.base
                raise release.ReleaseError('Fixture atomic ref conflict')
            return original(path, *args, **kwargs)
        with patch.object(self.api, 'request', side_effect=request):
            with self.assertRaises(release.ReleaseError):
                self.evaluate(publish=True)
        self.assertIsNone(self.api.draft)
        self.assertEqual(self.api.tags[TAG], self.base)
        self.assertNotIn(('/releases/100', 'PATCH'), self.api.calls)

    def test_invalid_local_assets_cannot_create_draft(self):
        self.significant()
        self.evaluate()
        current = release.inventory(self.root, self.api.main)
        changes = release.compare(release.inventory(self.root, self.base), current)
        manifest = self.output / 'PGM-MiSTer-2026-10-09-manifest.txt'
        manifest.write_text('corrupted', encoding='utf-8')
        with self.assertRaisesRegex(release.ReleaseError, 'Manifest'):
            release.publish(self.api, self.root, self.api.main, BASE_TAG, DAY, changes, current, [],
                            self.package(), manifest, release.notes(DAY, BASE_TAG, changes, current))
        self.assertEqual(self.api.calls, [])

    def test_invented_release_notes_cannot_create_draft(self):
        self.significant()
        self.evaluate()
        current = release.inventory(self.root, self.api.main)
        changes = release.compare(release.inventory(self.root, self.base), current)
        with self.assertRaisesRegex(release.ReleaseError, 'release-note'):
            release.publish(self.api, self.root, self.api.main, BASE_TAG, DAY, changes, current, [],
                            self.package(), self.output / 'PGM-MiSTer-2026-10-09-manifest.txt',
                            'Invented compatibility testing claims')
        self.assertEqual(self.api.calls, [])

    def test_utc_date_change_prevents_publication(self):
        with patch.object(release.Path, 'cwd', return_value=self.root), patch.dict(os.environ,
                {'GITHUB_STEP_SUMMARY': '', 'GITHUB_OUTPUT': ''}), patch.object(release, 'run') as run:
            self.assertEqual(release.main(['--output', str(self.output), '--publish',
                                          '--expected-date', '2000-01-01']), 1)
            run.assert_not_called()

    def test_summary_inside_repository_rejected_before_publication(self):
        self.significant()
        with patch.object(release.Path, 'cwd', return_value=self.root), patch.dict(os.environ,
                {'GITHUB_STEP_SUMMARY': str(self.root / 'summary.md')}), patch.object(release, 'run') as run:
            self.assertEqual(release.main(['--output', str(self.output), '--publish']), 1)
            run.assert_not_called()

    def test_workflow_permissions_and_source_boundary(self):
        workflow = (Path(__file__).parents[1] / '.github/workflows/pgm-release.yml').read_text(encoding='utf-8')
        self.assertIn("cron: '0 18 * * 5'", workflow)
        self.assertIn('contents: read', workflow.split('\n  publish:\n')[0])
        self.assertNotIn('contents: write', workflow.split('\n  publish:\n')[0])
        self.assertIn("github.event_name == 'workflow_dispatch'", workflow.split('\n  publish:\n')[1])
        self.assertIn('default: false', workflow)
        self.assertNotIn('PGM_SOURCE_URL', workflow)
        self.assertNotIn('PGM_SYNC_ENABLED', workflow)


if __name__ == '__main__':
    unittest.main()
