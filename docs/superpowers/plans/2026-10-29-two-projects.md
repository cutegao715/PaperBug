# Two Project Separation Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement task-by-task.

**Goal:** Independently installable cloud API and local workspace using the existing HTTP contract.

**Architecture:** Separate Python distributions under `backend/` and `frontend/`, with FastAPI cloud API and loopback-only local UI server. Preserve the legacy project and database.

**Tech Stack:** Python 3.10+, FastAPI, Uvicorn, SQLite, existing HTML and JavaScript.

**Spec:** `docs/superpowers/specs/2026-10-29-two-project-boundary-design.md`

## Global Constraints

- Keep legacy imports compatible and avoid two divergent cloud implementations.
- No cloud dependency on the legacy package; no model credentials in the frontend.
- User data and existing API behavior remain intact.

## Review Focus

- A second user cannot read a first user's space or messages.
- Repeated message request returns the same result; failed calls can retry.
- A built wheel serves static files outside the repository.
- Cloud wheel starts without the legacy package.
- Desktop binds only loopback and communicates with the cloud API.

## Task 1: Cloud package

Files: `backend/pyproject.toml`, `backend/src/paperagent_cloud/`, `backend/tests/`, legacy cloud shim.

- [ ] Add and run failing independent cloud import and identity tests.
- [ ] Move cloud implementation and necessary model client/configuration into the backend package; retain an import-compatible shim.
- [ ] Run backend tests and legacy cloud tests; build and install the wheel independently.

## Task 2: Local package

Files: `frontend/pyproject.toml`, `frontend/src/paperagent_desktop/`, `frontend/tests/`.

- [ ] Add and run failing test for the local FastAPI page and packaged assets.
- [ ] Package the existing UI and add a loopback-only launcher; preserve the cloud API contract.
- [ ] Build and install the frontend wheel independently, verify assets from outside the repository.

## Task 3: Integration and documentation

Files: `docs/cloud_workspace_phase1.md`, project READMEs, integration tests.

- [ ] Exercise both independently installed services through HTTP with a simulated model, two users and restart persistence.
- [ ] Update installation, configuration, database compatibility and limitations.
- [ ] Run the relevant tests and project suite; report results and outstanding limitations.

No Git commit is possible in this workspace because it is not a Git checkout.
