# Default repository task format

Use this format only when establishing a new file-based tracker or when the project already opts into it with `tracker_format: file-tasks-v1`. Existing project conventions take precedence.

## Layout and setup

```text
tasks/
  index.yml            Project policy and milestones
  epics.yml            Stable ownership areas
  t-001-short-name.yml Open task
  done/                completed and archived task files
  templates/           feature.yml, defect.yml, chore.yml
  BACKLOG.md           Generated, never hand-edited
```

Copy the contents of `assets/tracker/` into a new `tasks/` directory, preserving an existing tracker rather than overwriting it. Fill the project name and epics with actual project values. Templates deliberately contain empty fields to fill; they are not active tasks. Create `done/` when the first task finishes.

The default ID grammar is an uppercase prefix followed by a hyphen and at least three digits, for example `T-001` or `API-014`. A task file is `t-001.yml` or `t-001-short-name.yml`. The ID remains unchanged after a move or title edit.

## Fields

Every task includes `schema_version: "1.0"`, `id`, `title`, `epic`, `kind`, `priority`, `status`, `depends_on`, `goal`, `scope`, `acceptance_criteria` and `tests`. `priority` is P0–P3, with P0 most urgent. Optional `milestone` refers to a declared delivery milestone; optional `introduced_by` names a known task. `context` holds relevant source documents, issue URLs, finding evidence and siblings. Templates add suitable requirements and design cases.

Acceptance criteria are objects with unique `id` and either `text` or all of `given`, `when`, `then`. A test is an exact `command` plus a nonempty `covers` list of acceptance IDs. Describe manual verification as an explicit command/procedure only when the boundary is not usefully automated. Criteria without a test mapping remain incomplete specifications.

`scope.in` and `scope.out` are lists. Use empty `out` when no useful exclusion exists, rather than inventing one. Optional `requirements` use stable IDs; optional `failure_outcomes`, `cardinality`, `lifecycle` carry the cases the change actually introduces.

## Status and location

| Status | Location | Meaning |
| --- | --- | --- |
| pending | tasks/ | Eligible once all dependencies complete |
| in_progress | tasks/ | Claimed implementation, dependencies complete |
| deferred | tasks/ | Deliberately postponed, with reason and reopening condition |
| completed | tasks/done/ | Criteria and required tests passed, implementation evidence recorded |
| archived | tasks/done/ | Retained closed history, without implying success |

A `deferred` or `archived` task includes `disposition: {reason, date, previous_status, reopen_when}`. The validator checks these fields, not whether the user's authorization exists; the agent must establish that from the session/project policy.

A completed task includes:

```yaml
implementation_commit: "full existing Git commit hash"
completion:
  summary: "What observable outcome was delivered"
  verified_at: "2026-10-25"
  tests:
    - command: "exact command from tests"
      result: passed
      evidence: "Observed result, e.g. 14 tests passed with no skips"
```

The default policy requires a resolvable implementation commit. In a deliberately non-Git workflow, set `execution_policy.require_implementation_commit: false` during tracker setup and record durable evidence under `completion.summary` and `completion.tests`. Do not toggle this merely to bypass an incomplete Git delivery. Full hashes may be 40 or 64 hexadecimal characters, matching the repository object format.

When recording completion in Git, the implementation commit already exists. The completion-metadata commit then references it and moves the task; its own hash does not appear inside itself. When the acceptance criterion is the task metadata itself, put that work in the evidence-bearing implementation commit, then record completion in a follow-up metadata commit.

An epic entry has `id`, `title`, `scope`. A milestone entry in `index.yml` has `id`, `epic`, `outcome`, and `exit_task`. Every task assigned to a milestone must use that milestone’s epic. The exit task states verifiable delivery criteria; it must exist, use the same epic and explicitly name that milestone by the time the milestone is used. Do not let a milestone exit task complete before its delivery dependencies.

## Bundled helper

Run with Python 3.10+ and PyYAML from the project's environment. It uses the standard library apart from PyYAML and never executes task test commands.

```sh
python /path/to/manage-project-tasks/scripts/taskctl.py validate --tasks ./tasks
python /path/to/manage-project-tasks/scripts/taskctl.py backlog --tasks ./tasks
python /path/to/manage-project-tasks/scripts/taskctl.py backlog --tasks ./tasks --check
```

`validate` checks structural metadata, location, IDs, references, cycles, readiness of in-progress tasks, acceptance/test coverage, dispositions and completion evidence. With commit evidence enabled it also verifies the referenced object is a commit in the project repository. It does not run tests, prove acceptance criteria, infer user approval or authorize external changes.

`backlog` first validates, then writes `BACKLOG.md` deterministically. It refuses to replace a manually maintained file. `--check` only compares generated content and exits nonzero when absent/stale. Existing project generators remain authoritative for custom formats.

Bounds: 20,000 task files, 1 MiB per YAML file and 64 MiB of task YAML in total. The scanner reads task files only directly under `tasks/` and `tasks/done/`, excluding `index.yml`, `epics.yml` and templates; symlinks are rejected. IDs, filenames and metadata are checked before a backlog write. The helper is intentionally not a general YAML schema validator and accepts extra project-specific task fields.
