---
name: manage-project-tasks
description: Maintain repository-backed YAML tasks, dependencies, epics, milestones, verification evidence and completion records. Use when asked to create or organize a file-based project backlog, select ready work, or carry an implementation task through delivery. Adapt to an existing tracker; ordinary questions and read-only investigations do not need new task files.
---

# Manage project tasks

Keep each task a small, testable specification and an accurate record of work. The repository's instructions and existing tracker own the conventions. This skill supplies a portable default when the user asks to establish a tracker; it does not replace an established issue system or authorize unrelated work, deployment, publication or messaging.

## Establish the source of truth

Read `AGENTS.md`, the tracker policy, relevant task templates and the task's linked specification/design before changing code. Locate the commands that validate task metadata, generate the backlog and verify the affected implementation. Preserve the project's compatibility, branching, release and review policies.

Use task files as the authoritative status records. A generated backlog is a view of those files. Keep normative behavior in the project's specifications or API contracts and reference those documents from the task. Historical reports and plans do not establish current behavior.

If the user requests a new tracker, read [the default format](references/format.md) and adapt [the bootstrap assets](assets/tracker/index.yml) to the project. Do not overwrite an existing `tasks/` directory or introduce this format merely to answer a question. The bundled helper only supports the explicitly opted-in default format; keep existing project tools for other formats.

## Intake and scope

Create a task before implementation when the project uses task files for code changes. Read-only review, operational checks and questions do not themselves require tasks; create one when their outcome needs a code change. Reuse an existing matching task instead of duplicating it.

Choose the project's next unused stable ID after checking both open and finished tasks. Never renumber or reuse an ID. Check for concurrent claims before starting; an `in_progress` task belongs to its current assignee unless the user reassigns it.

Classify the work:

- `feature`: adds or extends a capability;
- `defect`: corrects behavior that was wrong;
- `chore`: changes no intended behavior, such as refactoring or documentation.

Use an **epic** for a lasting product/platform area, and a **milestone** for a delivery outcome within an area. Do not create one epic or milestone per fix. Reuse declared values; add a new one only for a distinct area or delivery outcome. When a fix repairs prior work, find the introducing task through history/blame and record `introduced_by`; if unknown, state the evidence searched and uncertainty instead of inventing provenance.

Start from the project's template, or [feature](assets/tracker/templates/feature.yml), [defect](assets/tracker/templates/defect.yml), or [chore](assets/tracker/templates/chore.yml). State:

- a concrete outcome and scope in/out;
- requirements and observable acceptance criteria with stable local IDs;
- exact test commands mapped to the criteria they verify;
- affected sibling paths and representations of any shared rule;
- relevant failure outcomes, work/response bounds and lifecycle cases.

Scale the detail to the change. For an external call or mutation, distinguish permanent failure, retryable failure, uncertain commit, cancellation and timeout. For collections, bound actual work as well as input count. For entities, cover retention and delete/cancel behavior. Reuse existing idempotency and lifecycle mechanisms. Record a boundary decision, evidence and reopening condition where future work could otherwise undo it.

A defect names the actual failing path and reproduction evidence, covers sibling paths, and asks for a regression test at the boundary that failed. Do not require a database or process harness for a purely local text change; do require the real store/process when its behavior is the defect.

## Select and execute

Only `completed` dependencies satisfy readiness. Missing, cyclic, `pending`, `in_progress`, `deferred` and `archived` dependencies block starting dependent work. Diagnose missing IDs and cycles; never silently delete dependencies to make a task eligible.

Set the selected ready task to `in_progress` before implementation. Record ownership when the project supports it. `deferred` and `archived` tasks are not a work queue; reactivate them only when the user's request or project policy authorizes it. A request to work on a specific deferred task is that authorization, so do not ask again.

Keep code, specifications, contracts, fixtures and all implementations of a changed rule consistent. Update the task's scope if necessary, preserving the original failure evidence. If newly discovered work is independently deliverable, create a linked follow-up; do not mark an unfinished acceptance criterion complete by moving it to a follow-up without making the scope change explicit.

## Verify and close

Run the declared checks and the affected package's existing suite. A gate selecting zero tests is not evidence. Record command, actual result, relevant environment and limitations; a skipped or unavailable required test is not a pass. Test concurrency and time using deterministic controls where practical. Do not close a task while required checks or acceptance criteria remain unresolved.

For Git-backed delivery:

1. Review the diff and stage only intended paths, preserving unrelated user changes.
2. Land the implementation commit(s) according to the project's branch/review policy.
3. Record the full hash of the **existing** commit that completes the implementation, plus verification evidence. Never guess a future hash or require a commit to contain its own hash.
4. Set `completed`, move the task to the finished location, update task links and regenerate the backlog. A separate completion-metadata commit may point to the implementation commit. For metadata-only tasks, the evidence-bearing document change is the implementation commit.

If the project is intentionally uncommitted or not Git-backed, follow its documented alternative completion evidence; otherwise leave completion pending until the commit exists. Do not invent a commit or change the completion policy to make validation pass.

For `deferred` or `archived`, record the reason, date, previous status, owner/reopening condition when relevant, and disposition of dependent tasks. Archiving retains evidence but does not mean success. Do not delete task history to make the backlog look complete.

Run the tracker validator and generated-backlog check after metadata changes. Finish by reporting the result, actual verification, implementation commit if any, and delivery state: committed/uncommitted, merged/unmerged, and remaining work. Do not equate a completed feature branch with a merge into the target branch.
