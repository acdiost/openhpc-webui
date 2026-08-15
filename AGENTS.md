# Repository Guidelines

## Project Structure & Module Organization
- `openhpc_webui/application.py` contains the FastAPI application factory and route layer.
- Core integrations live in `openhpc_webui/services/`; request models and settings live in `openhpc_webui/schemas.py` and `openhpc_webui/config.py`.
- UI templates are in `templates/` (with shared layout/components in `templates/base.html` and `templates/components/`).
- Frontend static assets are in `static/` (offline Tailwind CSS bundle plus page scripts).
- Operations/deployment docs are in `README.md`, `docs/DEPLOYMENT.md`, and `docs/USER_MANUAL.md`.

## Build, Test, and Development Commands
- `uv sync` installs Python dependencies into the project environment.
- `uvicorn openhpc_webui.application:app --reload --port 6827` starts local development server with auto-reload.
- `uvicorn openhpc_webui.application:app --host 127.0.0.1 --port 6827 --proxy-headers --forwarded-allow-ips=127.0.0.1` runs a production-style local binding behind Nginx.
- `python -m compileall -q openhpc_webui` performs a quick syntax validation pass.
- `bash update_offline.sh` refreshes offline assets/content for disconnected deployment scenarios.

## Coding Style & Naming Conventions
- Target Python `>=3.9`; follow PEP 8 and keep 4-space indentation.
- Use `snake_case` for modules, functions, and variables; use clear action-based names (for example, `create_user`, `get_partition_status`).
- Prefer type hints for public functions and API helpers.
- Keep route handlers thin; place LDAP/Slurm logic in manager modules.
- Templates and static files should use feature-oriented names (for example, `jobs.html`, `nodes.js`).

## Testing Guidelines
- Automated tests live under `tests/`; extend them when introducing non-trivial logic.
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
