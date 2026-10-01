import importlib.util
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
        self.write(self.target, '_PGM/_alternatives/ignored.mra', MRA)
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
        self.assertTrue((self.target / '_PGM/_alternatives/ignored.mra').exists())
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
                sync, 'git', side_effect=[b'', b'0' * 40]), patch.object(
                sync.subprocess, 'run', side_effect=clone), patch.object(sync, 'publish') as publish_mock:
            self.assertEqual(sync.main(['--apply', '--expected-mra-removals', '0',
                                        '--expected-core-removals', '0']), 0)
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
        for name in ('_PGM/game.mra', '_PGM/cores/core.rbf', 'legacy/mra/game.mra', 'legacy/cores/core.rbf'):
            self.assertTrue(sync.allowed_path(name), name)
        for name in ('README.md', 'legacy/README.md', '.github/workflows/sync.yml',
                     '_PGM/_alternatives/game.mra', '_PGM/cores/nested/core.rbf',
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


if __name__ == '__main__':
    unittest.main()
