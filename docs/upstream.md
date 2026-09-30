# Connecting the staging repository to upstream

This is a standalone Git repository. `main` records the initial source import;
`feat/initial-node-ci` adds standalone CI and preparation work. No remote is
configured and nothing has been pushed. Keep committing preparation work on that
branch until the real repository is available.

## When upstream becomes available

Confirm the repository URL, its default branch and push permissions, then:

```bash
git remote add origin https://github.com/YOUR_ORG/YOUR_REPO.git
git fetch origin
```

If upstream is still empty, an owner must establish its default branch before a
pull request can be opened. Agree whether our import commit should be that initial
baseline. With permission, push `main` as the baseline and then push the preparation
branch. The resulting PR covers CI changes; the node source is in the baseline.

If the intended PR should review the entire node import, have the owner first
initialize upstream with a minimal baseline, then follow the next procedure.

## Upstream already has an initial commit

Preserve the local history, then replay both import and CI commits onto its branch:

```bash
git switch feat/initial-node-ci
git branch backup/pre-upstream-sync
git rebase --onto origin/main --root
```

Replace `main` if upstream uses another default branch. Review and resolve
README/license/configuration conflicts deliberately, rerun the checks, then:

```bash
make install check
make node-images smoke-images CONTAINER_ENGINE=docker TAG=upstream-review
git push -u origin feat/initial-node-ci
gh pr create --draft --base main --head feat/initial-node-ci
```

Do not overwrite upstream history. If only fork access is available, push the
preparation branch to the fork and target the organization's repository in the PR.

## Activation work

Update the development branch in GitHub workflows, source-image label defaults,
repository links and dependency-bot configuration. Enable the `nodes-ci` branch
check after its first successful hosted run. GitHub CI needs no registry publishing
or AAP credentials. Konflux activation follows [its onboarding guide](../konflux/README.md).

Syntara's existing draft PR and local development environment are independent of
this staging repo. Do not remove their node workspace until the new image release
and protocol dependency have been consumed and tested by Syntara.
