# Working on Taskline

This repository contains a local YAML task viewer/editor. Application behavior and commands are documented in `README.md`.

- Python owns file discovery, validation and writes; `public/` is buildless browser JavaScript/CSS/HTML.
- Use temporary task directories for tests and browser QA. Do not edit tasks in the connected project as test data.
- File edits must retain YAML comments, reject stale versions, and report ambiguous failures without automatically retrying writes. Do not claim atomic coordination with independent editors.
- Keep startup instructions, API behavior and UI messages consistent. Validate new constrained fields on both read and write.
- Run the complete Python and JavaScript suites after changes to the corresponding boundary. See `README.md` for commands. Report skipped or unavailable checks separately.
- The reusable task-management skill lives at `.agents/skills/manage-project-tasks/SKILL.md`. Follow it when setting up or maintaining a project's task tracker; first inspect that project's existing conventions. Do not initialize or migrate a tracker merely to answer a question or perform read-only review.
- Stage only files belonging to the current change. Never include `.venv/`, caches, task fixtures from connected projects, or local credentials.
