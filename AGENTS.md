# Project Workflow

Before making code, config, documentation, or test changes in this repository, prefer working from a dedicated git worktree.

## Worktree First

- Check `git status --short --branch` and `git worktree list` before editing.
- Use `.worktrees/<topic>` for local worktrees. This directory is ignored by git.
- Create a task branch from the trunk branch before editing. Use the `codex/<topic>` branch prefix unless the user asks for another name.
- Do not move or overwrite unrelated uncommitted changes from the main checkout into the task worktree. If those changes are needed, ask before carrying them across.
- Skip creating a new worktree only when the task is read-only, the current directory is already the intended task worktree, or the user explicitly asks to work in place.

## Finish and Merge

- Run the relevant verification before calling the work complete.
- At the end of a change, ask the user whether to merge the task branch back to the trunk branch.
- Merge only after the user confirms. For this repository, the current trunk branch is `master`; if the default branch changes, use the actual trunk branch.
- After a successful local merge, rerun relevant verification on the trunk checkout, remove the completed worktree with `git worktree remove`, and delete the merged task branch.
- If the user does not want to merge yet, keep the branch and worktree intact and report their paths.
