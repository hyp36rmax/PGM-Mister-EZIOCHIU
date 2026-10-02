# Weekly preservation releases

**PGM Weekly Release** evaluates `main` every Friday at **18:00 UTC**, independently of daily artifact synchronization. Friday runs are read-only previews. Automatic publication is disabled in this phase.

The latest published release with a valid `beta-YYYY-MM-DD` calendar-date tag is the baseline. Drafts, unrelated tags and malformed dates are ignored. An eligible published release must resolve to an ancestor of current `main`; lookup or validation failures stop the run. If no eligible release exists, the workflow prepares the complete active distribution for maintainer review and never publishes an initial baseline, even with the manual publication option enabled. A maintainer must review and publish the first baseline separately.

A release is significant when any compiled core changes, any primary or Alternative MRA is added or removed, or at least five existing primary and Alternative MRAs have changed. Relocations and hierarchy changes appear as additions/removals. One to four MRA-only updates remain accumulated against the same last published baseline. Documentation, workflow and Legacy changes do not trigger releases.

To inspect a preview, choose **Actions → PGM Weekly Release → Run workflow**, select `main`, and leave **Publish release** off. The summary reports the selected baseline, actual artifact counts, significance reason, proposed title/tag and release notes. The run's preview artifact contains the evaluation, full artifact diff and, when significant or awaiting an initial baseline, the validated ZIP, notes and manifest. Insignificant runs save only the evaluation report and create no release assets or tags.

Names use `PGM MiSTer FPGA Public Beta - YYYY-MM-DD`, `beta-YYYY-MM-DD` and `PGM-MiSTer-YYYY-MM-DD.zip`, using the execution date in UTC. Existing tags/releases are never overwritten. The manifest lists the date, three artifact counts, package filename and SHA-256.

The ZIP contains only `_PGM/*.mra`, recursive `_PGM/_alternatives/**/*.mra` with its full relative hierarchy, and `_PGM/cores/*.rbf`. It excludes hidden files, other extensions, Legacy, documentation and maintenance files. Git symlinks, empty artifacts, malformed MRA XML and unsafe paths stop evaluation. Artifact bytes are preserved exactly. Original PGM core credit remains with Eizo Chiu.

Release evaluation reads only this preservation repository's Git objects and GitHub release/tag metadata. It does not retrieve the artifact source or its history. Arbitrary commit subjects are never copied into public notes; only recognized preservation wording can provide context confirmed by the actual artifact diff.

Manual **Publish release** is an explicit publication path after preview review. Its separate job alone has write permission. It rechecks the baseline and current `main`, validates all local assets, uploads to a draft, verifies uploaded bytes, creates a new tag without overwriting any existing ref, then publishes and verifies the release. Upload failures leave no public partial release where avoidable. Cleanup removes only a confirmed draft created by this run and its newly created tag if no published release uses it and its target is unchanged. Unknown remote outcomes stop without retries and require maintainer inspection. Do not enable this option during initial rollout.

After the first real preview is reviewed, automatic Friday publication would require a separately reviewed workflow change to admit scheduled events to the publishing job. No daily-sync setting needs to change.

Run all fixture tests on Linux with `python3 -B -m unittest discover -s tests -p 'test_pgm*.py' -v`. Tests use local Git repositories and a fake release service; they do not create real releases.
