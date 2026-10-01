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
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / 'target'
        self.source = Path(self.temp.name) / 'source'
        for root in (self.target, self.source):
            self.write(root, '_PGM/base.mra', MRA)
            self.write(root, '_PGM/cores/base.rbf', b'compiled-core')
        self.write(self.target, 'README.md', b'Preservation documentation')
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
        self.assertFalse((self.target / 'legacy').exists())

    def test_c_updated_mra(self):
        self.write(self.source, '_PGM/base.mra', UPDATED)
        _, counts = self.run_sync()
        self.assertEqual(counts['MRA']['CHANGED'], 1)
        self.assertEqual((self.target / '_PGM/base.mra').read_bytes(), UPDATED)
        self.assertFalse((self.target / 'legacy').exists())

    def test_d_removed_mra(self):
        self.write(self.target, '_PGM/removed.mra', UPDATED)
        self.run_sync()
        self.assertEqual((self.target / 'legacy/mra/removed.mra').read_bytes(), UPDATED)
        self.assertFalse((self.target / '_PGM/removed.mra').exists())

    def test_e_updated_core(self):
        self.write(self.source, '_PGM/cores/base.rbf', b'new\x00\xffcore')
        self.run_sync()
        self.assertEqual((self.target / '_PGM/cores/base.rbf').read_bytes(), b'new\x00\xffcore')
        self.assertFalse((self.target / 'legacy').exists())

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
        with patch.object(sync, 'inventory', return_value={}):
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
            self.assertEqual(sync.main(), 1)
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
            self.assertEqual(sync.main(), 0)
            self.assertEqual(git_mock.call_count, 2)
        self.assertEqual(sync.snapshot(self.target), before)

    def test_main_stages_only_managed_changes_and_commits(self):
        self.write(self.source, '_PGM/new.mra', UPDATED)
        def clone(*args, **kwargs):
            sync.shutil.copytree(self.source, Path(args[0][-1]))
            (Path(args[0][-1]) / '.git').mkdir()
        with patch.object(sync.Path, 'cwd', return_value=self.target), patch.dict(
                sync.os.environ, {'PGM_SOURCE_URL': 'https://github.com/private-owner/private-repository'}), patch.object(
                sync, 'git', side_effect=[b'', b'0' * 40, b'', b'_PGM/new.mra\0', b'', b'']) as git_mock, patch.object(
                sync.subprocess, 'run', side_effect=clone):
            self.assertEqual(sync.main(), 0)
            calls = [call.args[1:] for call in git_mock.call_args_list]
            self.assertIn(('add', '--', '_PGM/new.mra'), calls)
            self.assertEqual(calls[-1], ('push', 'origin', 'HEAD'))
            self.assertTrue(any('Update PGM beta artifacts' in call for call in calls))

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


if __name__ == '__main__':
    unittest.main()
