# Repository Guidelines

## Project Structure & Module Organization
- `main.py` is the FastAPI entrypoint and route layer.
- Core service modules live at repo root: `ldap_manager.py`, `slurm_manager.py`, `auth_manager.py`, `admin_manager.py`, `partition_config.py`, `node_config.py`.
- UI templates are in `templates/` (with shared layout/components in `templates/base.html` and `templates/components/`).
- Frontend static assets are in `static/` (offline Tailwind CSS bundle plus page scripts).
- Operations/deployment docs are in `README.md`, `DEPLOYMENT.md`, and `USER_MANUAL.md`.

## Build, Test, and Development Commands
- `uv sync` installs Python dependencies into the project environment.
- `uvicorn main:app --reload --port 6827` starts local development server with auto-reload.
- `uvicorn main:app --host 0.0.0.0 --port 6827` runs production-style server binding.
- `python -m py_compile main.py ldap_manager.py slurm_manager.py` performs a quick syntax validation pass.
- `bash update_offline.sh` refreshes offline assets/content for disconnected deployment scenarios.

## Coding Style & Naming Conventions
- Target Python `>=3.9`; follow PEP 8 and keep 4-space indentation.
- Use `snake_case` for modules, functions, and variables; use clear action-based names (for example, `create_user`, `get_partition_status`).
- Prefer type hints for public functions and API helpers.
- Keep route handlers thin; place LDAP/Slurm logic in manager modules.
- Templates and static files should use feature-oriented names (for example, `jobs.html`, `nodes.js`).

## Testing Guidelines
- There is no formal automated test suite yet; add tests under a new `tests/` directory when introducing non-trivial logic.
- Name test files `test_<module>.py` and test functions `test_<behavior>()`.
- Before opening a PR, run syntax checks and manually verify key flows: login, user/group CRUD, partition/node/jobs pages.

## Commit & Pull Request Guidelines
- Follow the repository’s existing style: short, imperative, task-focused subjects (examples: `Add auth toggle`, `修复节点数据异常问题`).
- Keep each commit scoped to one logical change.
- PRs should include: purpose, changed modules/templates, manual verification steps, related issue/task, and UI screenshots for template/static changes.

## Security & Configuration Tips
- Never commit real credentials; keep `.env` local and start from `env.example`.
- Rotate `SECRET_KEY` and LDAP admin credentials in production.
- Validate Slurm command permissions and run behind internal network controls.
