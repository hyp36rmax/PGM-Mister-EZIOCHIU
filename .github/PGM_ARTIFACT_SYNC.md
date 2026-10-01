# PGM artifact synchronization setup

1. In this repository's GitHub **Settings → Secrets and variables → Actions**, choose **New repository secret**.
2. Name it `PGM_SOURCE_URL`. Enter the current artifact repository's credential-free HTTPS clone URL as its value. Never paste the value into issues, pull requests, workflow files, documentation or logs.
3. Merge the reviewed implementation. The workflow supports manual execution and runs daily at **09:23 UTC** on the default branch. Its safety tests run before every synchronization.
4. In **Actions → PGM Artifact Sync**, run the workflow manually and inspect the count-only summary. Verify the initial inventory before relying on scheduled runs.

The secret is unavailable to contributor pull requests: this workflow has only manual and scheduled triggers. It uses `contents: write` to commit and push directly to the default branch. Repository rules must permit the bot's ordinary fast-forward updates; otherwise pushes fail without bypassing protection. No separate source access token is configured; the source must be accessible through the provided HTTPS URL without embedded credentials.

Only direct `_PGM/*.mra` and `_PGM/cores/*.rbf` files are managed. Nested alternatives, documentation and utilities remain unchanged. Removed files move to `legacy/mra/` or `legacy/cores/`; updates do not create archives. Returning files retain their archived copy. Legacy filename collisions abort the entire plan even if the bytes are identical.

Source retrieval, layout, nonempty inventories, XML validity, disclosure checks and collision checks all precede target changes. A final byte/inventory and unrelated-file check precedes staging and commit; application failures roll back the managed files. A failed push never force-updates the branch. Summaries contain counts, and commits use `Update PGM beta artifacts` with the neutral PGM Preservation Bot identity.

The disclosure audit scans all target working files outside `.git`, filenames, and candidate MRA content. It blocks source owner/repository identifiers, the clone URL, private hostnames and the retrieved source commit SHA. Shared hosting domains alone (such as GitHub) do not identify a source; existing unrelated links on shared hosts remain valid. Source-specific references still block synchronization. Existing attribution that matches the configured source owner will require maintainer review; the sync never rewrites attribution or MRA metadata to hide it. Candidate MRA content failures identify the artifact without echoing offending content; unsafe names are not echoed. RBF bytes are never altered. No source Git metadata is copied or retained after the temporary checkout is cleaned up.

Validation cannot prove that a nonempty source distribution is complete. An intentional or accidental partial distribution that still contains valid MRA and RBF files meets the minimum checks; maintainers should review unexpected removal counts. Source identity checks are conservative literal checks, not a guarantee against every possible encoded or indirect reference. Public source discovery remains outside this repository's control.

Tests cover the requested A–J cases plus new cores, malformed XML, empty binaries, scope isolation, symlinks, rollback and commit suppression. Run locally with `python3 -m unittest discover -s tests -p 'test_pgm_sync.py' -v`. No real source URL is needed for these synthetic fixtures.
