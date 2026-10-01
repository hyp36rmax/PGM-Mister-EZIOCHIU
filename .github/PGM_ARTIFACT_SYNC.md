# PGM artifact synchronization

This workflow keeps the current primary MRAs, alternative MRAs and compiled cores up to date. It manages direct primary MRAs and cores, and recursive alternative MRAs in `_PGM/*.mra`, `_PGM/_alternatives/**/*.mra` and `_PGM/cores/*.rbf`, with removed files preserved under `legacy/mra/*.mra`, `legacy/mra/_alternatives/**/*.mra` and `legacy/cores/*.rbf`. Alternative relative directory paths are preserved in active and Legacy collections. All other repository content stays read-only, including the Legacy README.

New files join the active collection. Updates replace the active copy without creating an archive. Removed files move to Legacy with their last-known bytes intact. Returning files keep their historical Legacy copy. If a removed filename already exists in Legacy, the whole run stops for review.

## Setup and first run

1. Review and merge this implementation before configuring or using the source. Leave the `PGM_SYNC_ENABLED` repository variable unset while checking the first run.
2. In **Settings → Secrets and variables → Actions → Secrets**, create a repository secret named `PGM_SOURCE_URL`. Use the current artifact repository's HTTPS clone URL, without embedded credentials. Keep the value out of documentation, issues and logs. The source must be readable without a separate access token.
3. In **Actions → PGM Artifact Sync → Run workflow**, select the default branch and leave **Apply** off. This previews counts and changed filenames and relative Alternative paths without changing, committing or pushing any files.
4. Review Added, Updated and Removed → Legacy filenames and counts separately for MRA, Alternative MRA and Cores. If removals are unexpected, stop and investigate the source layout and collection.
5. When the preview looks right, run again with **Apply** on. Enter the reviewed **MRA Removed**, **Alternative MRA Removed** and **Cores Removed** counts. If those counts have changed, the run stops before changing files. Review the resulting artifact commit.
6. Once the first live update is verified, create the repository variable `PGM_SYNC_ENABLED` with the value `true` under **Variables**. This enables the daily run at **09:23 UTC**. Remove the variable or change its value to pause daily synchronization. Manual previews remain available.

Scheduled runs stop before applying changes if more than **20% of any active collection** would be removed. Review a new manual preview before approving those removals. Nonempty MRA and core collections alone cannot prove that a distribution is complete; smaller unexpected removals still need attention.

## Checks and maintenance

Alternatives are optional when the target has no active alternative MRAs. If active alternatives exist, a missing, unreadable or empty source collection stops the whole run. Removing the last alternative file therefore requires maintainer review outside automatic synchronization. The full recursive inventory is validated before any removals are classified. Only visible `.mra` files are managed; hidden files and other extensions are excluded. Symlinks and unsafe paths stop the run. Empty removal directories are pruned only when they contain no other content.

Every run tests the synchronization logic before retrieving artifacts. Retrieval failures, missing directories, empty collections, malformed MRA XML, empty cores, source-reference failures and Legacy collisions stop the run before changes are applied. Application failures roll back managed files where practical. Final inventories, exact artifact bytes, unrelated files, staged paths and staged bytes are checked before committing. Any staged file outside the six allowed locations stops publication.

The workflow uses a normal push and never force-pushes. If another update reaches the branch first, the push fails without overwriting it. Repository rules must allow the maintenance identity to update the default branch; the workflow does not bypass protection. No-change runs create no commit. The maintenance identity stays `PGM Preservation Bot`.

Commit subjects use fixed local wording for the artifact groups actually changed. A short, safe source subject can provide context, such as a core update or alternative MRA update. Unknown, complex, mismatched or unsafe messages use `Update PGM beta artifacts`. Source wording is never copied verbatim; source subjects, author details and branches stay out of previews and logs. No external service is used for naming.

The source URL stays in the secret. Temporary source Git data is deleted and never copied into the target. Previews contain artifact counts and changed filenames and relative Alternative paths, without source URLs, branch names or commit provenance. The source-reference check protects the configured owner and repository identifiers. Generic references to GitHub, GitLab, Bitbucket and Gitee are allowed. Existing attribution that matches the configured source identifiers stops the run for review; synchronization does not rewrite attribution or legitimate MRA metadata. Compiled cores are copied without alteration.

Synchronization supports manual runs on the default branch and daily runs. Contributor pull requests run a separate read-only test workflow with no source secret; they cannot trigger privileged synchronization. To test locally, run:

```sh
python3 -B -m unittest discover -s tests -p 'test_pgm_sync.py' -v
```

The tests use local fixtures and do not need the real source URL. Automated test results and previews do not count as a verified live update.
