# Contributing

## Main is the single source of truth

ChatGPT, Grok Bot, Cursor, and humans all change this repository. `main` is the only line those changes start from and land on.

- Branch from current `main`.
- Rebase on or merge the latest `main` right before opening a pull request.
- Bump the version (semver) in every place it is declared.
- Change `main` only by merging a pull request.
- Do not push directly to `main`.
- Do not deploy from an unmerged branch.
