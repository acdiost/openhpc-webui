# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a lightweight web management portal for HPC centers, built to run in offline intranet environments. It manages LDAP identity systems, Slurm cluster resources, and job status.

**Key Characteristics:**
- All-Chinese UI interface (全中文界面)
- Theme color: #dc3023 (China Red / Research Red)
- Offline deployment: All static resources must be localized (no external CDNs)
- Minimalist, professional admin dashboard aesthetic
- Target environment: Python 3.9+

## Development Commands

### Running the Application
```bash
# Development server (from project root)
uvicorn openhpc_webui.application:app --reload --port 6827

# Production server
uvicorn openhpc_webui.application:app --host 127.0.0.1 --port 6827 --proxy-headers --forwarded-allow-ips=127.0.0.1
```

### Package Management
This project uses `uv` for dependency management:
```bash
# Install dependencies
uv sync

# Add a new dependency
uv add <package-name>

# Activate virtual environment
source .venv/bin/activate
```

## Architecture Overview

### Technology Stack
- **Backend:** FastAPI (Python 3.9+)
- **Frontend:** Native HTML/JS + Tailwind CSS (must be localized)
- **Authentication:** LDAP Admin binding
- **Dependencies:** python-ldap, pyslurm (or subprocess-based Slurm CLI parsing), uvicorn

### Planned Module Structure

1. **LDAP User & Group Management** (`/users`)
   - User CRUD operations (list, create with UID/GID/home/shell, password change, delete)
   - Group management with user assignment
   - LDAP connection health checks

2. **Slurm Resource Management** (`/partitions`)
   - Partition overview (name, node status: Alloc/Idle/Down, quotas)
   - Node monitoring (status, CPU/VRAM usage)
   - Partition control (Up/Down operations)

3. **Job Management** (`/jobs`)
   - Real-time job queue from `squeue` (JobID, user, partition, status, runtime, nodes)
   - Job details (submission script, output paths)
   - Admin job cancellation via `scancel`

4. **Dashboard** (`/dashboard`)
   - Overview metrics: total users, active jobs, idle nodes

### UI/UX Guidelines
- **Layout:** Fixed left sidebar navigation, top breadcrumb, main content area on right
- **Colors:**
  - Primary: #dc3023 (navigation, buttons, logo)
  - Background: #f8f9fa (light gray)
  - Text: #1a1a1a
- **Components:**
  - Card-style containers with subtle shadows
  - Compact tables with column sorting
  - Status badges: Running (green), Queued (yellow), Completed/Failed (gray/red)
- **Fonts:** System sans-serif (PingFang SC, Microsoft YaHei)

### Offline Requirements (Critical)
- **NO external CDN dependencies** (Tailwind, FontAwesome, Google Fonts)
- All CSS/JS must be in `static/` directory
- Use system fonts only

## Development Guidelines

### File Organization
- `openhpc_webui/application.py` - FastAPI application factory and route layer
- `openhpc_webui/services/` - LDAP, Slurm, quota, and system integrations
- `openhpc_webui/schemas.py` - API request models
- `openhpc_webui/config.py` - environment settings and runtime paths
- `templates/` - HTML templates (Jinja2)
- `static/` - All CSS, JS, and static assets (must be self-contained)
- `requirement.md` - Complete project requirements in Chinese

### Slurm Integration Notes
- Parse Slurm commands via subprocess: `sinfo --json`, `squeue`, `sacct`
- Implement proper error handling for LDAP connection failures
- All Slurm operations require admin privileges verification

### Security Considerations
- LDAP admin credentials must be securely configured
- Validate all user inputs before LDAP/Slurm operations
- Implement proper session management for admin authentication
- Add confirmation modals for destructive operations (delete user, cancel job)

## Important Context

- This is designed for **internal network deployment only** - no internet access assumed
- All UI text should be in **Chinese**
- When implementing Slurm features, prefer JSON output formats where available (`sinfo --json`)
- Use Tailwind utility classes but ensure the CSS file is bundled locally in `static/`
