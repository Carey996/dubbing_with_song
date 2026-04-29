# Worktree Change Workflow Design

## Goal

Make future project changes happen in an isolated git worktree by default, then require an explicit user decision before merging the work back to the trunk branch. When a local merge succeeds, the temporary worktree should be cleaned up so the main checkout stays tidy.

## Scope

This workflow applies to code, config, documentation, test, and project metadata changes in this repository.

It does not apply to read-only inspection, command output checks, or cases where the user explicitly asks to work in the current checkout.

## Trunk Branch

The current trunk branch for this repository is `master`. Future agents should verify the actual trunk branch before merging, because the project may later rename it.

## Start-of-Change Flow

1. Inspect the current checkout with `git status --short --branch`.
2. Inspect existing worktrees with `git worktree list`.
3. If not already in the intended task worktree, create one under `.worktrees/<topic>` from the trunk branch.
4. Name task branches with the `codex/<topic>` prefix unless the user requests a different branch name.
5. Keep unrelated uncommitted changes in the main checkout untouched. If a task depends on those changes, ask before copying or recreating them in the worktree.

The project already ignores `.worktrees/`, so task worktrees can live inside the repository folder without polluting git status.

## Working Rules

- Make all task edits inside the task worktree.
- Keep the branch focused on the requested change.
- Do not clean, revert, or stage unrelated changes from another checkout.
- Run verification that matches the change risk before presenting the work as complete.
- If verification cannot run or is blocked by environment constraints, report the blocker and the remaining risk.

## Finish Flow

When the change is ready, ask the user what to do next. The default prompt should include at least:

1. Merge the task branch back to the trunk branch locally.
2. Keep the branch and worktree for later.
3. Discard the task branch and worktree, with explicit confirmation before deletion.

For local merge:

1. Switch to the trunk checkout.
2. Merge the task branch.
3. Rerun relevant verification from the trunk checkout.
4. Remove the completed worktree with `git worktree remove <path>`.
5. Delete the merged task branch.
6. Report the merge, verification, and cleanup results.

For "keep for later", leave both branch and worktree intact and report the branch name plus worktree path.

For discard, require explicit confirmation before deleting any branch or worktree.

## Failure Handling

- If worktree creation fails because of permissions or git safety checks, surface the exact blocker and request the narrow permission needed.
- If merge conflicts occur, stop after reporting the conflicted files. Resolve only after the user confirms the merge should continue.
- If verification fails after merge, report the failure and keep the worktree or branch available for repair unless the user explicitly asks to undo.

## Acceptance Criteria

- Repository-level instructions tell future agents to prefer worktrees before edits.
- The detailed spec documents start, work, finish, merge, and cleanup behavior.
- The workflow preserves unrelated dirty changes in the main checkout.
- The workflow asks before merging and cleans the worktree only after a successful merge or confirmed discard.
