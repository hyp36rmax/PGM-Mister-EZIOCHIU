import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('pgm_sync', Path(__file__).parents[1] / 'scripts/sync_pgm_artifacts.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
MRA = b'<misterromdescription><name>Game</name></misterromdescription>'
UPDATED = b'<misterromdescription><name>Updated Game</name></misterromdescription>'


class SynchronizationTests(unittest.TestCase):
    def setUp(self):
        summary_env = patch.dict(sync.os.environ, {'GITHUB_STEP_SUMMARY': ''})
        summary_env.start()
        self.addCleanup(summary_env.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / 'target'
        self.source = Path(self.temp.name) / 'source'
        for root in (self.target, self.source):
            self.write(root, '_PGM/base.mra', MRA)
            self.write(root, '_PGM/cores/base.rbf', b'compiled-core')
        self.write(self.target, 'README.md', b'Preservation documentation')
        for name in ('README.zh-CN.md', 'LICENSE', 'docs/HISTORY', 'docs/FEEDBACK.md',
                     '.github/workflows/other.yml', 'scripts/other.py', 'tests/other.py',
                     'utilities/tool.txt', 'legacy/README.md'):
            self.write(self.target, name, b'Manually maintained content')
        self.write(self.target, '_PGM/other/nested/ignored.mra', MRA)
        self.write(self.source, 'utils/untrusted.py', b'raise Exception()')
        self.tokens = ['private-owner', 'private-repository']

    def write(self, root, name, content):
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)

    def run_sync(self):
        changes, counts, expected, baseline = sync.plan(self.target, self.source, self.tokens)
        sync.apply(self.target, changes, expected, baseline)
        return changes, counts

    def assert_abort(self):
        before = sync.snapshot(self.target)
        with self.assertRaises(sync.SyncError):
            self.run_sync()
        self.assertEqual(before, sync.snapshot(self.target))

    def test_a_no_changes(self):
        before = sync.snapshot(self.target)
        changes, counts = self.run_sync()
        self.assertEqual(changes, {})
        self.assertEqual(before, sync.snapshot(self.target))
        self.assertEqual(counts['MRA']['UNCHANGED'], 1)

    def test_b_new_mra(self):
        self.write(self.source, '_PGM/new.mra', MRA)
        _, counts = self.run_sync()
        self.assertEqual(counts['MRA']['NEW'], 1)
        self.assertFalse((self.target / 'legacy/mra').exists())

    def test_c_updated_mra(self):
        self.write(self.source, '_PGM/base.mra', UPDATED)
        _, counts = self.run_sync()
        self.assertEqual(counts['MRA']['UPDATED'], 1)
        self.assertEqual((self.target / '_PGM/base.mra').read_bytes(), UPDATED)
        self.assertFalse((self.target / 'legacy/mra').exists())

    def test_d_removed_mra(self):
        self.write(self.target, '_PGM/removed.mra', UPDATED)
        self.run_sync()
        self.assertEqual((self.target / 'legacy/mra/removed.mra').read_bytes(), UPDATED)
        self.assertFalse((self.target / '_PGM/removed.mra').exists())

    def test_e_updated_core(self):
        self.write(self.source, '_PGM/cores/base.rbf', b'new\x00\xffcore')
        self.run_sync()
        self.assertEqual((self.target / '_PGM/cores/base.rbf').read_bytes(), b'new\x00\xffcore')
        self.assertFalse((self.target / 'legacy/cores').exists())

    def test_f_removed_core(self):
        self.write(self.target, '_PGM/cores/removed.rbf', b'last-known')
        self.run_sync()
        self.assertEqual((self.target / 'legacy/cores/removed.rbf').read_bytes(), b'last-known')
        self.assertFalse((self.target / '_PGM/cores/removed.rbf').exists())

    def test_g_empty_source(self):
        for p in (self.source / '_PGM/base.mra', self.source / '_PGM/cores/base.rbf'):
            p.unlink()
        self.assert_abort()

    def test_g_missing_source(self):
        self.source = Path(self.temp.name) / 'missing'
        self.assert_abort()

    def test_g_missing_cores(self):
        (self.source / '_PGM/cores/base.rbf').unlink()
        (self.source / '_PGM/cores').rmdir()
        self.assert_abort()

    def test_h_returning_artifact(self):
        self.write(self.target, 'legacy/mra/returned.mra', MRA)
        self.write(self.source, '_PGM/returned.mra', UPDATED)
        self.run_sync()
        self.assertEqual((self.target / 'legacy/mra/returned.mra').read_bytes(), MRA)
        self.assertEqual((self.target / '_PGM/returned.mra').read_bytes(), UPDATED)

    def test_i_collision_before_any_changes(self):
        self.write(self.source, '_PGM/new.mra', UPDATED)
        self.write(self.target, '_PGM/removed.mra', MRA)
        self.write(self.target, 'legacy/mra/removed.mra', UPDATED)
        self.assert_abort()

    def test_i_core_collision(self):
        self.write(self.target, '_PGM/cores/removed.rbf', b'current')
        self.write(self.target, 'legacy/cores/removed.rbf', b'historical')
        self.assert_abort()

    def test_j_mra_disclosure(self):
        self.write(self.source, '_PGM/base.mra', b'<misterromdescription>private-owner</misterromdescription>')
        self.assert_abort()

    def test_j_project_disclosure(self):
        self.write(self.target, 'docs/report.md', b'private-repository')
        self.assert_abort()

    def test_new_core_and_scope(self):
        before = (self.target / 'README.md').read_bytes()
        self.write(self.source, '_PGM/cores/new.rbf', b'new-core')
        _, counts = self.run_sync()
        self.assertEqual(counts['Cores']['NEW'], 1)
        self.assertEqual((self.target / 'README.md').read_bytes(), before)
        self.assertTrue((self.target / '_PGM/other/nested/ignored.mra').exists())
        self.assertFalse((self.target / 'utils/untrusted.py').exists())

    def test_malformed_mra(self):
        self.write(self.source, '_PGM/base.mra', b'not XML')
        self.assert_abort()

    def test_zero_byte_core(self):
        self.write(self.source, '_PGM/cores/base.rbf', b'')
        self.assert_abort()

    def test_verification_rolls_back(self):
        self.write(self.source, '_PGM/base.mra', UPDATED)
        changes, _, expected, baseline = sync.plan(self.target, self.source, self.tokens)
        with patch.object(sync, 'verify_final', side_effect=sync.SyncError('Verification failed')):
            with self.assertRaises(sync.SyncError):
                sync.apply(self.target, changes, expected, baseline)
        self.assertEqual(sync.snapshot(self.target), baseline)

    def test_symlink_rejected(self):
        p = self.source / '_PGM/base.mra'
        p.unlink()
        try:
            p.symlink_to(self.target / '_PGM/base.mra')
        except OSError:
            self.skipTest('Symlink creation unavailable on this host')
        self.assert_abort()

    def test_retrieval_failure_does_not_commit(self):
        before = sync.snapshot(self.target)
        with patch.object(sync.Path, 'cwd', return_value=self.target), patch.dict(
                sync.os.environ, {'PGM_SOURCE_URL': 'https://example.invalid/private-owner/private-repository'}), patch.object(
                sync, 'git', return_value=b'') as git_mock, patch.object(
                sync.subprocess, 'run', side_effect=sync.subprocess.CalledProcessError(1, 'git')):
            self.assertEqual(sync.main([]), 1)
            self.assertEqual(git_mock.call_count, 1)
        self.assertEqual(sync.snapshot(self.target), before)

    def test_no_change_main_does_not_commit(self):
        before = sync.snapshot(self.target)
        def clone(*args, **kwargs):
            sync.shutil.copytree(self.source, Path(args[0][-1]))
            (Path(args[0][-1]) / '.git').mkdir()
        with patch.object(sync.Path, 'cwd', return_value=self.target), patch.dict(
                sync.os.environ, {'PGM_SOURCE_URL': 'https://github.com/private-owner/private-repository'}), patch.object(
                sync, 'git', side_effect=[b'', b'0' * 40]) as git_mock, patch.object(
                sync.subprocess, 'run', side_effect=clone):
            self.assertEqual(sync.main([]), 0)
            self.assertEqual(git_mock.call_count, 2)
        self.assertEqual(sync.snapshot(self.target), before)

    def test_main_stages_only_managed_changes_and_commits(self):
        self.write(self.source, '_PGM/new.mra', UPDATED)
        def clone(*args, **kwargs):
            sync.shutil.copytree(self.source, Path(args[0][-1]))
            (Path(args[0][-1]) / '.git').mkdir()
        with patch.object(sync.Path, 'cwd', return_value=self.target), patch.dict(
                sync.os.environ, {'PGM_SOURCE_URL': 'https://github.com/private-owner/private-repository'}), patch.object(
                sync, 'git', side_effect=[b'', b'0' * 40, b'']), patch.object(
                sync.subprocess, 'run', side_effect=clone), patch.object(sync, 'publish') as publish_mock:
            self.assertEqual(sync.main(['--apply', '--expected-mra-removals', '0',
                                        '--expected-core-removals', '0',
                                        '--expected-alternative-mra-removals', '0']), 0)
            self.assertEqual(publish_mock.call_count, 1)
            self.assertEqual(set(publish_mock.call_args.args[1]), {'_PGM/new.mra'})

    def test_source_sha_disclosure(self):
        sha = '1' * 40
        self.write(self.target, '.github/report.txt', sha.encode())
        before = sync.snapshot(self.target)
        with self.assertRaises(sync.SyncError):
            sync.plan(self.target, self.source, self.tokens, (sha,))
        self.assertEqual(sync.snapshot(self.target), before)

    def test_private_host_and_encoded_url_disclosure(self):
        tokens = sync.identifiers('https://example.invalid/private-owner/private-repository.git')
        self.assertTrue(sync.disclosed(b'https%3A%2F%2Fexample.invalid%2Fprivate-owner%2Fprivate-repository', tokens))
        self.assertFalse(sync.disclosed(b'https://github.com/unrelated/documentation', tokens))

    def test_target_changes_after_plan_abort(self):
        changes, _, expected, baseline = sync.plan(self.target, self.source, self.tokens)
        self.write(self.target, 'README.md', b'concurrent edit')
        with self.assertRaises(sync.SyncError):
            sync.apply(self.target, changes, expected, baseline)
        self.assertEqual((self.target / 'README.md').read_bytes(), b'concurrent edit')

    def test_gitee_generic_host_and_specific_identifiers(self):
        tokens = sync.identifiers('https://gitee.com/private-owner/private-repository.git')
        self.assertNotIn('gitee.com', tokens)
        self.assertFalse(sync.disclosed(b'https://gitee.com/unrelated/documentation', tokens))
        self.assertTrue(sync.disclosed(b'https://gitee.com/private-owner/private-repository', tokens))
        self.assertTrue(sync.disclosed(b'private-owner', tokens))
        self.assertTrue(sync.disclosed(b'private-repository', tokens))
        self.write(self.source, '_PGM/base.mra', b'<misterromdescription><name>Game</name>'
                   b'<remark>https://gitee.com/unrelated/documentation</remark></misterromdescription>')
        changes, _, expected, baseline = sync.plan(self.target, self.source, tokens)
        sync.apply(self.target, changes, expected, baseline)
        self.write(self.source, '_PGM/base.mra', b'<misterromdescription>private-owner</misterromdescription>')
        self.assert_abort()

    def test_allowlist_rejects_nested_and_unrelated_paths(self):
        for name in ('_PGM/game.mra', '_PGM/cores/core.rbf', 'legacy/mra/game.mra', 'legacy/cores/core.rbf',
                     '_PGM/_alternatives/Region/Japan/game.mra', 'legacy/mra/_alternatives/Region/Japan/game.mra'):
            self.assertTrue(sync.allowed_path(name), name)
        for name in ('README.md', 'legacy/README.md', '.github/workflows/sync.yml',
                     '_PGM/cores/nested/core.rbf',
                     'legacy/mra/nested/game.mra', '_PGM/../game.mra',
                     '_PGM/core.rbf', 'legacy/cores/game.mra', '_PGM/evil\n.mra'):
            self.assertFalse(sync.allowed_path(name), name)

    def test_operation_boundary_independent_of_plan(self):
        changes, _, expected, baseline = sync.plan(self.target, self.source, self.tokens)
        changes['README.md'] = b'Unauthorized update'
        with self.assertRaisesRegex(sync.SyncError, 'boundary'):
            sync.apply(self.target, changes, expected, baseline)
        self.assertEqual(sync.snapshot(self.target), baseline)

    def test_updated_artifact_cannot_be_archived_by_plan(self):
        self.write(self.source, '_PGM/base.mra', UPDATED)
        changes, _, expected, baseline = sync.plan(self.target, self.source, self.tokens)
        changes['legacy/mra/base.mra'] = MRA
        with self.assertRaisesRegex(sync.SyncError, 'boundary'):
            sync.apply(self.target, changes, expected, baseline)
        self.assertEqual(sync.snapshot(self.target), baseline)

    def test_mid_application_failure_rolls_back(self):
        self.write(self.source, '_PGM/base.mra', UPDATED)
        self.write(self.source, '_PGM/cores/base.rbf', b'new-core')
        changes, _, expected, baseline = sync.plan(self.target, self.source, self.tokens)
        original = sync.Path.write_bytes
        failed = False
        def write(path, content):
            nonlocal failed
            if path == self.target / '_PGM/cores/base.rbf' and not failed:
                failed = True
                raise OSError('Simulated write failure')
            return original(path, content)
        with patch.object(sync.Path, 'write_bytes', new=write):
            with self.assertRaises(OSError):
                sync.apply(self.target, changes, expected, baseline)
        self.assertEqual(sync.snapshot(self.target), baseline)

    def test_removal_guard_requires_review(self):
        counts = {'MRA': {'NEW': 0, 'UPDATED': 0, 'UNCHANGED': 3, 'REMOVED': 2},
                  'Cores': {'NEW': 0, 'UPDATED': 0, 'UNCHANGED': 1, 'REMOVED': 0}}
        with self.assertRaises(sync.SyncError):
            sync.removal_guard(counts, True, {})
        with self.assertRaises(sync.SyncError):
            sync.removal_guard(counts, False, {'MRA': 1, 'Cores': 0})
        sync.removal_guard(counts, False, {'MRA': 2, 'Cores': 0})
        counts['MRA'].update(UNCHANGED=4, REMOVED=1)
        sync.removal_guard(counts, True, {})

    def test_preview_changes_no_files(self):
        self.write(self.target, '_PGM/removed.mra', MRA)
        before = sync.snapshot(self.target)
        def clone(*args, **kwargs):
            sync.shutil.copytree(self.source, Path(args[0][-1]))
            (Path(args[0][-1]) / '.git').mkdir()
        with patch.object(sync.Path, 'cwd', return_value=self.target), patch.dict(
                sync.os.environ, {'PGM_SOURCE_URL': 'https://gitee.com/private-owner/private-repository'}), patch.object(
                sync, 'git', side_effect=[b'', b'0' * 40]), patch.object(
                sync.subprocess, 'run', side_effect=clone), patch.object(sync, 'publish') as publish_mock:
            self.assertEqual(sync.main([]), 0)
            publish_mock.assert_not_called()
        self.assertEqual(sync.snapshot(self.target), before)

    def test_summary_path_inside_target_rejected(self):
        before = sync.snapshot(self.target)
        def clone(*args, **kwargs):
            sync.shutil.copytree(self.source, Path(args[0][-1]))
            (Path(args[0][-1]) / '.git').mkdir()
        with patch.object(sync.Path, 'cwd', return_value=self.target), patch.dict(
                sync.os.environ, {'PGM_SOURCE_URL': 'https://gitee.com/private-owner/private-repository',
                                 'GITHUB_STEP_SUMMARY': str(self.target / 'docs/report.md')}), patch.object(
                sync, 'git', side_effect=[b'', b'0' * 40]), patch.object(sync.subprocess, 'run', side_effect=clone):
            self.assertEqual(sync.main([]), 1)
        self.assertEqual(sync.snapshot(self.target), before)

    def preview_output(self):
        before = sync.snapshot(self.target)
        summary_file = Path(self.temp.name) / 'summary.md'
        output = io.StringIO()
        def clone(*args, **kwargs):
            sync.shutil.copytree(self.source, Path(args[0][-1]))
            (Path(args[0][-1]) / '.git').mkdir()
        with patch.object(sync.Path, 'cwd', return_value=self.target), patch.dict(
                sync.os.environ, {'PGM_SOURCE_URL': 'https://gitee.com/private-owner/private-repository',
                                 'GITHUB_STEP_SUMMARY': str(summary_file)}), patch.object(
                sync, 'git', side_effect=[b'', b'0' * 40]) as git_mock, patch.object(
                sync.subprocess, 'run', side_effect=clone), patch.object(sync, 'apply') as apply_mock, patch.object(
                sync, 'publish') as publish_mock, redirect_stdout(output):
            result = sync.main([])
            apply_mock.assert_not_called()
            publish_mock.assert_not_called()
            self.assertTrue(all(call.args[1] in {'status', 'rev-parse'} for call in git_mock.call_args_list))
        self.assertEqual(sync.snapshot(self.target), before)
        return result, output.getvalue(), summary_file.read_text(encoding='utf-8') if summary_file.exists() else ''

    def test_preview_added_updated_removed_filenames_and_counts(self):
        for directory, suffix, content in (('_PGM', '.mra', MRA), ('_PGM/cores', '.rbf', b'core')):
            self.write(self.source, directory + '/added' + suffix, content)
            self.write(self.target, directory + '/updated' + suffix, content)
            self.write(self.source, directory + '/updated' + suffix, UPDATED if suffix == '.mra' else b'new-core')
            self.write(self.target, directory + '/removed' + suffix, content)
        result, output, report = self.preview_output()
        self.assertEqual(result, 0)
        for text in (output, report):
            for suffix in ('.mra', '.rbf'):
                self.assertIn('Added:\n- added' + suffix, text)
                self.assertIn('Updated:\n- updated' + suffix, text)
                self.assertIn('Removed → Legacy:\n- removed' + suffix, text)
                self.assertNotIn('base' + suffix, text)
            self.assertIn('Added: 1\nUpdated: 1\nRemoved: 1\nUnchanged: 1', text)
            self.assertNotIn(str(self.source), text)
            self.assertNotIn('private-owner', text)
            self.assertNotIn('private-repository', text)
            self.assertNotIn('0' * 40, text)

    def test_preview_source_identifying_names_never_printed(self):
        for directory, suffix in (('_PGM', '.mra'), ('_PGM/cores', '.rbf')):
            for location in (self.source, self.target):
                with self.subTest(directory=directory, location=location):
                    name = 'private-owner' + suffix
                    path = location / directory / name
                    self.write(location, directory + '/' + name, MRA if suffix == '.mra' else b'core')
                    result, output, report = self.preview_output()
                    self.assertEqual(result, 1)
                    self.assertNotIn(name, output)
                    self.assertEqual(report, '')
                    path.unlink()

    def test_preview_renderer_rejects_unsafe_or_identifying_names(self):
        _, counts, _, baseline = sync.plan(self.target, self.source, self.tokens)
        for path in ('_PGM/private-repository.mra', '_PGM/cores/private-owner.rbf',
                     '_PGM/cores/' + '0' * 40 + '.rbf', '_PGM/nested/game.mra',
                     '_PGM/evil\n.mra', 'docs/report.mra'):
            with self.subTest(path=path), self.assertRaises(sync.SyncError):
                sync.preview_summary(counts, {path: None}, baseline, self.tokens, ('0' * 40,))

    def test_preview_escapes_filename_markup(self):
        name = 'Game [test] & copy.mra'
        self.write(self.source, '_PGM/' + name, MRA)
        result, _, report = self.preview_output()
        self.assertEqual(result, 0)
        self.assertIn('- Game \\[test\\] &amp; copy.mra', report)

    def test_alternative_new(self):
        self.write(self.source, '_PGM/_alternatives/Region/Japan/new.mra', MRA)
        _, counts = self.run_sync()
        self.assertEqual(counts['Alternative MRA']['NEW'], 1)
        self.assertEqual((self.target / '_PGM/_alternatives/Region/Japan/new.mra').read_bytes(), MRA)
        self.assertFalse((self.target / 'legacy/mra/_alternatives').exists())

    def test_alternative_updated_no_archive(self):
        self.write(self.target, '_PGM/_alternatives/Region/Japan/game.mra', MRA)
        self.write(self.source, '_PGM/_alternatives/Region/Japan/game.mra', UPDATED)
        _, counts = self.run_sync()
        self.assertEqual(counts['Alternative MRA']['UPDATED'], 1)
        self.assertEqual((self.target / '_PGM/_alternatives/Region/Japan/game.mra').read_bytes(), UPDATED)
        self.assertFalse((self.target / 'legacy/mra/_alternatives').exists())

    def test_alternative_unchanged(self):
        for root in (self.target, self.source):
            self.write(root, '_PGM/_alternatives/Region/Japan/game.mra', MRA)
        before = sync.snapshot(self.target)
        changes, counts = self.run_sync()
        self.assertEqual(changes, {})
        self.assertEqual(counts['Alternative MRA']['UNCHANGED'], 1)
        self.assertEqual(sync.snapshot(self.target), before)

    def test_alternative_removed_exact_legacy_copy(self):
        for root in (self.target, self.source):
            self.write(root, '_PGM/_alternatives/Region/Japan/retained.mra', MRA)
        self.write(self.target, '_PGM/_alternatives/Region/Japan/removed.mra', UPDATED)
        _, counts = self.run_sync()
        self.assertEqual(counts['Alternative MRA']['REMOVED'], 1)
        self.assertFalse((self.target / '_PGM/_alternatives/Region/Japan/removed.mra').exists())
        self.assertEqual((self.target / 'legacy/mra/_alternatives/Region/Japan/removed.mra').read_bytes(), UPDATED)

    def test_alternative_returning_retains_history(self):
        self.write(self.target, 'legacy/mra/_alternatives/Region/Japan/returned.mra', MRA)
        self.write(self.source, '_PGM/_alternatives/Region/Japan/returned.mra', UPDATED)
        self.run_sync()
        self.assertEqual((self.target / '_PGM/_alternatives/Region/Japan/returned.mra').read_bytes(), UPDATED)
        self.assertEqual((self.target / 'legacy/mra/_alternatives/Region/Japan/returned.mra').read_bytes(), MRA)

    def test_alternative_collision_aborts_whole_plan(self):
        self.write(self.target, '_PGM/_alternatives/Region/Japan/removed.mra', MRA)
        self.write(self.target, 'legacy/mra/_alternatives/Region/Japan/removed.mra', UPDATED)
        self.write(self.source, '_PGM/_alternatives/Region/Japan/retained.mra', MRA)
        self.write(self.source, '_PGM/new.mra', UPDATED)
        self.assert_abort()

    def test_alternative_missing_or_empty_source_fails_closed(self):
        self.write(self.target, '_PGM/_alternatives/Region/Japan/active.mra', MRA)
        self.assert_abort()
        (self.source / '_PGM/_alternatives/Region/Japan').mkdir(parents=True)
        self.assert_abort()
        self.write(self.source, '_PGM/_alternatives/Region/Japan/nested/active.mra', MRA)
        changes, counts, _, _ = sync.plan(self.target, self.source, self.tokens)
        self.assertEqual(counts['Alternative MRA']['NEW'], 1)

    def test_alternative_unreadable_source_fails_closed(self):
        self.write(self.source, '_PGM/_alternatives/Region/Japan/active.mra', MRA)
        original = sync.Path.iterdir
        def iterdir(path):
            if path == self.source / '_PGM/_alternatives':
                raise PermissionError('Unreadable fixture')
            return original(path)
        before = sync.snapshot(self.target)
        with patch.object(sync.Path, 'iterdir', new=iterdir), self.assertRaises(PermissionError):
            sync.plan(self.target, self.source, self.tokens)
        self.assertEqual(sync.snapshot(self.target), before)

    def test_alternative_malformed_and_disclosing_candidates(self):
        for name, content in [('bad.mra', b'broken XML'), ('private-owner.mra', MRA),
                              ('game.mra', b'<misterromdescription>private-repository</misterromdescription>')]:
            with self.subTest(name=name):
                path = self.source / '_PGM/_alternatives' / name
                self.write(self.source, str(path.relative_to(self.source)), content)
                self.assert_abort()
                path.unlink()

    def test_alternative_unrelated_files_ignored(self):
        for root in (self.source, self.target):
            for name in ('_PGM/other/game.mra', '_PGM/_alternatives/Region/Japan/nested/README.md',
                         '_PGM/_alternatives/Region/Japan/nested/image.png', '_PGM/_alternatives/Region/Japan/.hidden/game.mra',
                         '_PGM/_alternatives/Region/Japan/nested/.hidden.mra'):
                self.write(root, name, MRA)
        self.write(self.source, '_PGM/other/game.mra', UPDATED)
        before = sync.snapshot(self.target)
        changes, _ = self.run_sync()
        self.assertEqual(changes, {})
        self.assertEqual(sync.snapshot(self.target), before)

    def test_alternative_preview_lists_changes_only(self):
        for root in (self.source, self.target):
            self.write(root, '_PGM/_alternatives/Region/Japan/unchanged.mra', MRA)
            self.write(root, '_PGM/_alternatives/Region/Japan/updated.mra', MRA)
        self.write(self.source, '_PGM/_alternatives/Region/Japan/updated.mra', UPDATED)
        self.write(self.source, '_PGM/_alternatives/Region/Japan/added.mra', MRA)
        self.write(self.target, '_PGM/_alternatives/Region/Japan/removed.mra', MRA)
        result, output, report = self.preview_output()
        self.assertEqual(result, 0)
        for text in (output, report):
            self.assertIn('Alternative MRA\nAdded: 1\nUpdated: 1\nRemoved: 1\nUnchanged: 1', text)
            self.assertIn('Alternative MRA\n\nAdded:\n- Region/Japan/added.mra\n\nUpdated:\n- Region/Japan/updated.mra\n\nRemoved → Legacy:\n- Region/Japan/removed.mra', text)
            self.assertNotIn('unchanged.mra', text)

    def test_alternative_independent_removal_guard_and_confirmation(self):
        zero = {'NEW': 0, 'UPDATED': 0, 'UNCHANGED': 100, 'REMOVED': 0}
        counts = {'MRA': zero, 'Cores': zero,
                  'Alternative MRA': {'NEW': 0, 'UPDATED': 0, 'UNCHANGED': 1, 'REMOVED': 1}}
        with self.assertRaises(sync.SyncError):
            sync.removal_guard(counts, True, {})
        with self.assertRaises(sync.SyncError):
            sync.removal_guard(counts, False, {'MRA': 0, 'Cores': 0, 'Alternative MRA': 0})
        sync.removal_guard(counts, False, {'MRA': 0, 'Cores': 0, 'Alternative MRA': 1})

    def test_alternative_confirmation_mismatch_aborts_before_application(self):
        for root in (self.source, self.target):
            self.write(root, '_PGM/_alternatives/Region/Japan/retained.mra', MRA)
        self.write(self.target, '_PGM/_alternatives/Region/Japan/removed.mra', MRA)
        before = sync.snapshot(self.target)
        def clone(*args, **kwargs):
            sync.shutil.copytree(self.source, Path(args[0][-1]))
            (Path(args[0][-1]) / '.git').mkdir()
        with patch.object(sync.Path, 'cwd', return_value=self.target), patch.dict(sync.os.environ,
                {'PGM_SOURCE_URL': 'https://gitee.com/private-owner/private-repository'}), patch.object(
                sync, 'git', side_effect=[b'', b'0' * 40, b'']), patch.object(
                sync.subprocess, 'run', side_effect=clone), patch.object(sync, 'apply') as apply_mock, patch.object(
                sync, 'publish') as publish_mock, redirect_stdout(io.StringIO()):
            self.assertEqual(sync.main(['--apply', '--expected-mra-removals', '0',
                                       '--expected-core-removals', '0',
                                       '--expected-alternative-mra-removals', '0']), 1)
            apply_mock.assert_not_called()
            publish_mock.assert_not_called()
        self.assertEqual(sync.snapshot(self.target), before)

    def test_unsafe_source_message_never_reaches_apply_output(self):
        self.write(self.source, '_PGM/new.mra', MRA)
        raw = (b'Update MRAs https://gitee.com/private-owner/private-repository\n\0'
               b'Source Fixture Author\0author@example.invalid\0HEAD -> private-branch')
        output = io.StringIO()
        def clone(*args, **kwargs):
            sync.shutil.copytree(self.source, Path(args[0][-1]))
            (Path(args[0][-1]) / '.git').mkdir()
        with patch.object(sync.Path, 'cwd', return_value=self.target), patch.dict(sync.os.environ,
                {'PGM_SOURCE_URL': 'https://gitee.com/private-owner/private-repository'}), patch.object(
                sync, 'git', side_effect=[b'', b'0' * 40, raw]), patch.object(
                sync.subprocess, 'run', side_effect=clone), patch.object(sync, 'publish') as publish_mock, redirect_stdout(output):
            self.assertEqual(sync.main(['--apply', '--expected-mra-removals', '0',
                                       '--expected-core-removals', '0',
                                       '--expected-alternative-mra-removals', '0']), 0)
        self.assertEqual(publish_mock.call_args.args[-1], 'Update PGM beta artifacts')
        for text in ('private-owner', 'private-repository', 'Source Fixture Author',
                     'author@example.invalid', 'private-branch', '0' * 40):
            self.assertNotIn(text, output.getvalue())

    def test_no_changes_apply_does_not_read_source_message_or_commit(self):
        before = sync.snapshot(self.target)
        def clone(*args, **kwargs):
            sync.shutil.copytree(self.source, Path(args[0][-1]))
            (Path(args[0][-1]) / '.git').mkdir()
        with patch.object(sync.Path, 'cwd', return_value=self.target), patch.dict(sync.os.environ,
                {'PGM_SOURCE_URL': 'https://gitee.com/private-owner/private-repository'}), patch.object(
                sync, 'git', side_effect=[b'', b'9' * 40]) as git_mock, patch.object(
                sync.subprocess, 'run', side_effect=clone), patch.object(sync, 'publish') as publish_mock, redirect_stdout(io.StringIO()):
            self.assertEqual(sync.main(['--apply']), 0)
            publish_mock.assert_not_called()
            self.assertEqual(git_mock.call_count, 2)
        self.assertEqual(sync.snapshot(self.target), before)


    def test_recursive_identity_and_directory_creation(self):
        for name in ('Set A/game.mra', 'Set B/game.mra', 'root.mra'):
            self.write(self.source, '_PGM/_alternatives/' + name, MRA)
        _, counts = self.run_sync()
        self.assertEqual(counts['Alternative MRA']['NEW'], 3)
        for name in ('Set A/game.mra', 'Set B/game.mra', 'root.mra'):
            self.assertEqual((self.target / '_PGM/_alternatives' / name).read_bytes(), MRA)

    def test_recursive_cleanup_preserves_unmanaged_content(self):
        self.write(self.source, '_PGM/_alternatives/retained.mra', MRA)
        for name in ('Empty/Deep/game.mra', 'Notes/game.mra', 'Other/game.mra'):
            self.write(self.target, '_PGM/_alternatives/' + name, UPDATED)
        self.write(self.target, '_PGM/_alternatives/Notes/README.md', b'Keep')
        (self.target / '_PGM/_alternatives/Other/unmanaged').mkdir()
        self.run_sync()
        self.assertFalse((self.target / '_PGM/_alternatives/Empty').exists())
        self.assertEqual((self.target / '_PGM/_alternatives/Notes/README.md').read_bytes(), b'Keep')
        self.assertTrue((self.target / '_PGM/_alternatives/Other/unmanaged').is_dir())
        self.assertEqual((self.target / 'legacy/mra/_alternatives/Empty/Deep/game.mra').read_bytes(), UPDATED)

    def test_recursive_unsafe_paths(self):
        for relative in ('../escape.mra', '/absolute.mra', 'A/../escape.mra',
                         'A//game.mra', 'A/game\x00.mra', 'A/game\n.mra',
                         'C:/game.mra', 'A\\game.mra', 'A/hidden\x7f.mra'):
            with self.subTest(relative=relative):
                self.assertFalse(sync.allowed_path('_PGM/_alternatives/' + relative))
                with self.assertRaises(sync.SyncError):
                    sync.checked_path(self.target, relative)

    def test_recursive_symlink_rejected(self):
        folder = self.source / '_PGM/_alternatives/Set'
        folder.mkdir(parents=True)
        try:
            (folder / 'linked').symlink_to(self.target / '_PGM', target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation unavailable on this host')
        self.assert_abort()

    def test_recursive_validation_and_disclosure(self):
        for name, content in [('Set/bad.mra', b'broken'),
                              ('Set/game.mra', b'<misterromdescription>private-owner</misterromdescription>'),
                              ('private-repository/game.mra', MRA), ('Set/private-owner.mra', MRA)]:
            with self.subTest(name=name):
                self.write(self.source, '_PGM/_alternatives/' + name, content)
                result, output, report = self.preview_output()
                self.assertEqual(result, 1)
                self.assertEqual(report, '')
                self.assertNotIn('private-owner', output)
                self.assertNotIn('private-repository', output)
                (self.source / '_PGM/_alternatives' / name).unlink()

    def test_recursive_removal_percentage_and_counts(self):
        for number in range(5):
            name = f'_PGM/_alternatives/Set {number}/game.mra'
            self.write(self.target, name, MRA)
            if number:
                self.write(self.source, name, MRA)
        _, counts, _, _ = sync.plan(self.target, self.source, self.tokens)
        sync.removal_guard(counts, True, {})
        sync.removal_guard(counts, False, {'MRA': 0, 'Cores': 0, 'Alternative MRA': 1})
        (self.source / '_PGM/_alternatives/Set 1/game.mra').unlink()
        _, counts, _, _ = sync.plan(self.target, self.source, self.tokens)
        with self.assertRaises(sync.SyncError):
            sync.removal_guard(counts, True, {})
        with self.assertRaises(sync.SyncError):
            sync.removal_guard(counts, False, {'MRA': 0, 'Cores': 0, 'Alternative MRA': 1})

    def test_recursive_preview_preserves_directories(self):
        self.write(self.target, '_PGM/_alternatives/Old/removed.mra', MRA)
        self.write(self.source, '_PGM/_alternatives/New/added.mra', MRA)
        directories = {p.relative_to(self.target) for p in self.target.rglob('*') if p.is_dir()}
        result, _, _ = self.preview_output()
        self.assertEqual(result, 0)
        self.assertEqual(directories, {p.relative_to(self.target) for p in self.target.rglob('*') if p.is_dir()})

    def test_recursive_failure_rolls_back_created_directories(self):
        self.write(self.source, '_PGM/_alternatives/New/Deep/game.mra', MRA)
        changes, _, expected, baseline = sync.plan(self.target, self.source, self.tokens)
        with patch.object(sync, 'verify_final', side_effect=sync.SyncError('Fixture failure')):
            with self.assertRaises(sync.SyncError):
                sync.apply(self.target, changes, expected, baseline)
        self.assertEqual(sync.snapshot(self.target), baseline)
        self.assertFalse((self.target / '_PGM/_alternatives').exists())

class GitPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'target'
        self.source = Path(self.temp.name) / 'source'
        for root in (self.root, self.source):
            (root / '_PGM/cores').mkdir(parents=True)
            (root / '_PGM/base.mra').write_bytes(MRA)
            (root / '_PGM/cores/base.rbf').write_bytes(b'core')
        (self.root / 'README.md').write_bytes(b'Manual documentation')
        self.git(self.root, 'init', '--initial-branch=main')
        self.git(self.root, 'config', 'user.name', 'Test Maintainer')
        self.git(self.root, 'config', 'user.email', 'test@example.invalid')
        self.git(self.root, 'config', 'core.autocrlf', 'false')
        self.git(self.root, 'add', '.')
        self.git(self.root, 'commit', '-m', 'Initial fixture')
        self.remote = Path(self.temp.name) / 'remote.git'
        self.git(Path(self.temp.name), 'init', '--bare', '--initial-branch=main', str(self.remote))
        self.git(self.root, 'remote', 'add', 'origin', str(self.remote))
        self.git(self.root, 'push', '-u', 'origin', 'main')
        self.initial_sha = self.git(self.remote, 'rev-parse', 'main')

    def git(self, root, *args):
        return sync.git(root, *args)

    def prepare(self):
        (self.source / '_PGM/base.mra').write_bytes(UPDATED)
        (self.source / '_PGM/cores/base.rbf').write_bytes(b'updated\x00\xffcore')
        changes, _, expected, baseline = sync.plan(self.root, self.source, ['private-owner'])
        sync.apply(self.root, changes, expected, baseline)
        return changes, expected, baseline

    def test_successful_commit_and_exact_git_blob_bytes(self):
        changes, expected, baseline = self.prepare()
        sync.publish(self.root, changes, expected, baseline)
        self.assertNotEqual(self.git(self.remote, 'rev-parse', 'main'), self.initial_sha)
        self.assertEqual(self.git(self.remote, 'show', 'main:_PGM/cores/base.rbf'), b'updated\x00\xffcore')
        self.assertEqual(self.git(self.remote, 'show', 'main:README.md'), b'Manual documentation')
        self.assertEqual(self.git(self.remote, 'log', '-1', '--format=%s').strip(), b'Update PGM beta artifacts')
        self.assertEqual(set(self.git(self.remote, 'diff-tree', '--no-commit-id', '--name-only', '-r', 'main').decode().splitlines()), set(changes))

    def test_preview_leaves_git_index_history_and_remote_unchanged(self):
        for number in range(4):
            (self.root / f'_PGM/cores/removed{number}.rbf').write_bytes(b'last-known-core')
        for root in (self.root, self.source):
            (root / '_PGM/_alternatives/Region/Japan').mkdir(parents=True)
            (root / '_PGM/_alternatives/Region/Japan/retained.mra').write_bytes(MRA)
        (self.root / '_PGM/_alternatives/Region/Japan/removed.mra').write_bytes(UPDATED)
        self.git(self.root, 'add', '.')
        self.git(self.root, 'commit', '-m', 'Four removed core fixtures')
        self.git(self.root, 'push')
        before = sync.snapshot(self.root)
        index = (self.root / '.git/index').read_bytes()
        head = self.git(self.root, 'rev-parse', 'HEAD')
        remote = self.git(self.remote, 'rev-parse', 'main')
        output = io.StringIO()
        original_git = sync.git
        original_run = sync.subprocess.run
        def clone(*args, **kwargs):
            if 'clone' not in args[0]:
                return original_run(*args, **kwargs)
            sync.shutil.copytree(self.source, Path(args[0][-1]))
            (Path(args[0][-1]) / '.git').mkdir()
        def git(root, *args):
            if root != self.root:
                return b'0' * 40
            return original_git(root, *args)
        with patch.object(sync.Path, 'cwd', return_value=self.root), patch.dict(sync.os.environ,
                {'PGM_SOURCE_URL': 'https://gitee.com/private-owner/private-repository',
                 'GITHUB_STEP_SUMMARY': ''}), patch.object(sync, 'git', side_effect=git) as git_mock, patch.object(
                sync.subprocess, 'run', side_effect=clone), patch.object(sync, 'apply') as apply_mock, patch.object(
                sync, 'publish') as publish_mock, redirect_stdout(output):
            self.assertEqual(sync.main([]), 0, output.getvalue())
            apply_mock.assert_not_called()
            publish_mock.assert_not_called()
            self.assertTrue(all(call.args[1] in {'status', 'rev-parse'} for call in git_mock.call_args_list))
        self.assertEqual(sync.snapshot(self.root), before)
        self.assertEqual((self.root / '.git/index').read_bytes(), index)
        self.assertEqual(self.git(self.root, 'rev-parse', 'HEAD'), head)
        self.assertEqual(self.git(self.remote, 'rev-parse', 'main'), remote)
        self.assertIn('Removed: 4', output.getvalue())
        for number in range(4):
            self.assertIn(f'- removed{number}.rbf', output.getvalue())
        self.assertIn('Alternative MRA\n\nAdded:\n(none)\n\nUpdated:\n(none)\n\nRemoved → Legacy:\n- Region/Japan/removed.mra', output.getvalue())

    def test_unrelated_staged_path_aborts_before_commit_or_push(self):
        changes, expected, baseline = self.prepare()
        (self.root / 'unexpected.txt').write_bytes(b'Unrelated file')
        self.git(self.root, 'add', 'unexpected.txt')
        self.git(self.root, 'add', '--', *changes)
        with self.assertRaisesRegex(sync.SyncError, 'boundary'):
            sync.verify_staged(self.root, changes)
        self.assertEqual(self.git(self.root, 'rev-parse', 'HEAD'), self.initial_sha)
        self.assertEqual(self.git(self.remote, 'rev-parse', 'main'), self.initial_sha)

    def test_allowlist_rejects_unrelated_path_even_if_in_plan(self):
        (self.root / 'README.md').write_bytes(b'Unapproved')
        self.git(self.root, 'add', 'README.md')
        with self.assertRaisesRegex(sync.SyncError, 'boundary'):
            sync.verify_staged(self.root, {'README.md': b'Unapproved'})

    def test_unrelated_change_after_apply_blocks_publication(self):
        changes, expected, baseline = self.prepare()
        (self.root / 'README.md').write_bytes(b'Concurrent unrelated edit')
        with self.assertRaisesRegex(sync.SyncError, 'Unrelated'):
            sync.publish(self.root, changes, expected, baseline)
        self.assertEqual(self.git(self.remote, 'rev-parse', 'main'), self.initial_sha)

    def test_staged_bytes_must_match_approved_source(self):
        changes, _, _ = self.prepare()
        self.git(self.root, 'add', '--', *changes)
        (self.root / '_PGM/cores/base.rbf').write_bytes(b'Incorrect staging content')
        self.git(self.root, 'add', '_PGM/cores/base.rbf')
        with self.assertRaisesRegex(sync.SyncError, 'bytes'):
            sync.verify_staged(self.root, changes)

    def test_staged_symlink_mode_rejected(self):
        changes, _, _ = self.prepare()
        self.git(self.root, 'add', '--', *changes)
        blob = self.git(self.root, 'rev-parse', ':_PGM/base.mra').decode().strip()
        self.git(self.root, 'update-index', '--cacheinfo', '120000,' + blob + ',_PGM/base.mra')
        with self.assertRaisesRegex(sync.SyncError, 'regular file'):
            sync.verify_staged(self.root, changes)

    def test_removed_artifact_preserved_and_staged_as_move(self):
        (self.root / '_PGM/removed.mra').write_bytes(MRA)
        self.git(self.root, 'add', '_PGM/removed.mra')
        self.git(self.root, 'commit', '-m', 'Additional fixture')
        self.git(self.root, 'push')
        changes, expected, baseline = self.prepare()
        sync.publish(self.root, changes, expected, baseline)
        self.assertEqual(self.git(self.remote, 'show', 'main:legacy/mra/removed.mra'), MRA)
        self.assertNotIn(b'_PGM/removed.mra', self.git(self.remote, 'ls-tree', '-r', '--name-only', 'main'))

    def test_concurrent_remote_change_rejects_push(self):
        other = Path(self.temp.name) / 'maintainer'
        self.git(Path(self.temp.name), 'clone', str(self.remote), str(other))
        self.git(other, 'config', 'user.name', 'Test Maintainer')
        self.git(other, 'config', 'user.email', 'test@example.invalid')
        (other / 'README.md').write_bytes(b'Newer maintainer work')
        self.git(other, 'add', 'README.md')
        self.git(other, 'commit', '-m', 'Maintainer update')
        self.git(other, 'push')
        newer_sha = self.git(self.remote, 'rev-parse', 'main')
        changes, expected, baseline = self.prepare()
        with self.assertRaises(sync.subprocess.CalledProcessError):
            sync.publish(self.root, changes, expected, baseline)
        self.assertEqual(self.git(self.remote, 'rev-parse', 'main'), newer_sha)
        self.assertEqual(self.git(self.remote, 'show', 'main:README.md'), b'Newer maintainer work')

    def test_alternative_staging_and_removed_legacy_bytes(self):
        for root in (self.root, self.source):
            (root / '_PGM/_alternatives/Region/Japan').mkdir(parents=True)
            (root / '_PGM/_alternatives/Region/Japan/retained.mra').write_bytes(MRA)
        (self.root / '_PGM/_alternatives/Region/Japan/removed.mra').write_bytes(UPDATED)
        (self.source / '_PGM/_alternatives/Region/Japan/new.mra').write_bytes(MRA)
        self.git(self.root, 'add', '.')
        self.git(self.root, 'commit', '-m', 'Alternative fixtures')
        self.git(self.root, 'push')
        changes, _, expected, baseline = sync.plan(self.root, self.source, ['private-owner'])
        sync.apply(self.root, changes, expected, baseline)
        subject = sync.commit_subject(b'Refresh alternate game definitions', changes, ['private-owner'])
        self.assertEqual(subject, 'Update alternative MRA files')
        self.git(self.root, 'add', '--', *changes)
        legacy = self.root / 'legacy/mra/_alternatives/Region/Japan/removed.mra'
        legacy.write_bytes(MRA)
        self.git(self.root, 'add', 'legacy/mra/_alternatives/Region/Japan/removed.mra')
        with self.assertRaisesRegex(sync.SyncError, 'bytes'):
            sync.verify_staged(self.root, changes)
        legacy.write_bytes(UPDATED)
        self.git(self.root, 'reset', '--mixed', 'HEAD')
        sync.publish(self.root, changes, expected, baseline, subject)
        self.assertEqual(self.git(self.remote, 'show', 'main:legacy/mra/_alternatives/Region/Japan/removed.mra'), UPDATED)
        self.assertEqual(self.git(self.remote, 'show', 'main:_PGM/_alternatives/Region/Japan/new.mra'), MRA)
        self.assertEqual(self.git(self.remote, 'log', '-1', '--format=%s').strip(), subject.encode())
        self.assertEqual(self.git(self.remote, 'log', '-1', '--format=%an|%ae').strip(),
                         b'PGM Preservation Bot|pgm-preservation-bot@users.noreply.github.com')

    def test_alternative_staged_bytes_and_nested_path_violation(self):
        (self.source / '_PGM/_alternatives/Region/Japan').mkdir(parents=True)
        (self.source / '_PGM/_alternatives/Region/Japan/new.mra').write_bytes(MRA)
        changes, _, expected, baseline = sync.plan(self.root, self.source, ['private-owner'])
        sync.apply(self.root, changes, expected, baseline)
        self.git(self.root, 'add', '--', *changes)
        sync.verify_staged(self.root, changes)
        (self.root / '_PGM/_alternatives/Region/Japan/new.mra').write_bytes(UPDATED)
        self.git(self.root, 'add', '_PGM/_alternatives/Region/Japan/new.mra')
        with self.assertRaisesRegex(sync.SyncError, 'bytes'):
            sync.verify_staged(self.root, changes)
        (self.root / '_PGM/_alternatives/Region/Japan/nested').mkdir(parents=True)
        (self.root / '_PGM/_alternatives/Region/Japan/nested/outside.mra').write_bytes(MRA)
        self.git(self.root, 'add', '_PGM/_alternatives/Region/Japan/nested/outside.mra')
        with self.assertRaisesRegex(sync.SyncError, 'boundary'):
            sync.verify_staged(self.root, changes)
        self.assertEqual(self.git(self.remote, 'rev-parse', 'main'), self.initial_sha)

    def test_real_source_commit_context_never_copies_author_or_branch(self):
        self.git(self.source, 'init', '--initial-branch=fixture-source-branch')
        self.git(self.source, 'config', 'user.name', 'Source Fixture Author')
        self.git(self.source, 'config', 'user.email', 'source-author@example.invalid')
        self.git(self.source, 'add', '.')
        self.git(self.source, 'commit', '-m', 'Refresh core')
        source_sha = self.git(self.source, 'rev-parse', 'HEAD').decode().strip()
        output = io.StringIO()
        with redirect_stdout(output):
            subject = sync.source_commit_subject(self.source, {'_PGM/cores/PGM.rbf': b'core'},
                                                 ['private-owner'], (source_sha,))
        self.assertEqual(subject, 'Update PGM core')
        self.assertEqual(output.getvalue(), '')
        self.assertNotIn('Source Fixture Author', subject)
        self.assertNotIn('source-author@example.invalid', subject)
        self.assertNotIn('fixture-source-branch', subject)
        self.assertNotIn(source_sha, subject)


class CommitNamingTests(unittest.TestCase):
    tokens = ['private-owner', 'private-repository', 'https://example.invalid/private-owner/private-repository']
    sha = 'a1' * 20

    def name(self, subject, changes, identity=()):
        return sync.commit_subject(subject, changes, self.tokens, (self.sha,), identity)

    def test_safe_core_subject(self):
        self.assertEqual(self.name(b'Refresh core', {'_PGM/cores/PGM.rbf': b'core'}), 'Update PGM core')

    def test_safe_mra_subject(self):
        self.assertEqual(self.name(b'Fix MRAs', {'_PGM/game.mra': MRA}), 'Update MRA files')

    def test_safe_alternative_subject(self):
        self.assertEqual(self.name(b'Update alternate game definitions',
                                  {'_PGM/_alternatives/Region/Japan/game.mra': MRA}), 'Update alternative MRA files')

    def test_safe_combined_subject_and_verified_specific_core(self):
        changes = {'_PGM/cores/PGM-027A-TYPE1.rbf': b'core', '_PGM/game.mra': MRA}
        self.assertEqual(self.name(b'Update PGM-027A implementation and MRAs', changes),
                         'Update MRA files and PGM-027A core')
        self.assertEqual(self.name(b'Fix KOV2 MRA and update core',
                                  {'_PGM/KOV2.mra': MRA, '_PGM/cores/PGM.rbf': b'core'}),
                         'Update KOV2 MRA files and PGM core')

    def test_mismatched_categories_or_feature_use_fallback(self):
        for subject, changes in [(b'Update core and MRAs', {'_PGM/game.mra': MRA}),
                                 (b'Fix MRAs', {'_PGM/cores/PGM.rbf': b'core'}),
                                 (b'Fix KOV2 MRA', {'_PGM/unrelated.mra': MRA}),
                                 (b'Update PGM-027A core', {'_PGM/cores/other.rbf': b'core'})]:
            with self.subTest(subject=subject):
                self.assertEqual(self.name(subject, changes), sync.FALLBACK_SUBJECT)

    def test_unsafe_source_subjects_use_fallback(self):
        for subject in [b'Update core https://example.invalid/private-owner/private-repository',
                        b'Update core private-owner', b'Update core private-repository',
                        b'Update core ' + self.sha.encode(), b'Update core\x1b[31m',
                        b'Update core\r', b'Update core\t', b'Update core\nSource context',
                        b'Update core ' + b'x' * 121, b'Improve everything', b'Update core with AI',
                        b'Update add remove core', b'Update core core core', b'Remove core',
                        b'Update core Source Fixture Author', b'Update core author@example.invalid',
                        b'Update core /tmp/source', b'Update core main', b'Update core\xff']:
            with self.subTest(subject=subject):
                self.assertEqual(self.name(subject, {'_PGM/cores/PGM.rbf': b'core'},
                                           ('Source Fixture Author', 'author@example.invalid', 'main')),
                                 'Update PGM beta artifacts')

    def test_source_context_and_identity_are_never_printed(self):
        output = io.StringIO()
        raw = (b'Update core https://example.invalid/private-owner/private-repository\n\0'
               b'Source Fixture Author\0author@example.invalid\0HEAD -> main, origin/main')
        with patch.object(sync, 'git', return_value=raw), redirect_stdout(output):
            subject = sync.source_commit_subject(Path('fixture'), {'_PGM/cores/PGM.rbf': b'core'},
                                                 self.tokens, (self.sha,))
        self.assertEqual(subject, sync.FALLBACK_SUBJECT)
        self.assertEqual(output.getvalue(), '')

    def test_valid_source_context_uses_only_safe_target_words(self):
        raw = b'Refresh core\n\0Source Fixture Author\0author@example.invalid\0HEAD -> main, origin/main'
        with patch.object(sync, 'git', return_value=raw):
            subject = sync.source_commit_subject(Path('fixture'), {'_PGM/cores/PGM.rbf': b'core'},
                                                 self.tokens, (self.sha,))
        self.assertEqual(subject, 'Update PGM core')
        for forbidden in ('Source Fixture Author', 'author@example.invalid', 'main', self.sha):
            self.assertNotIn(forbidden, subject)

    def test_no_artifact_changes_or_verbatim_subject_uses_fallback(self):
        self.assertEqual(self.name(b'Refresh core', {}), sync.FALLBACK_SUBJECT)
        self.assertEqual(self.name(b'Update PGM core', {'_PGM/cores/PGM.rbf': b'core'}), sync.FALLBACK_SUBJECT)


if __name__ == '__main__':
    unittest.main()
