#!/usr/bin/env python3
"""Synchronize the OpenHPC WebUI version across source and documentation."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence, Tuple


VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
VERSION_ASSIGNMENT = re.compile(r'^__version__ = "(\d+\.\d+\.\d+)"$', re.MULTILINE)
UNRELEASED_HEADING = "## Unreleased"

MANAGED_VERSION_FILES: Tuple[str, ...] = (
    "openhpc_webui/__about__.py",
    "tests/test_application_factory.py",
    "README.md",
    "docs/DEPLOYMENT.md",
    "docs/PYPI_USAGE.md",
    "docs/TECHNICAL_GUIDE.md",
    "docs/USER_MANUAL.md",
)


class VersionUpdateError(RuntimeError):
    """Raised when the version files are incomplete or inconsistent."""


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def validate_version(value: str) -> Tuple[int, int, int]:
    if not VERSION_PATTERN.fullmatch(value):
        raise VersionUpdateError("版本号必须使用 X.Y.Z 格式，例如 0.2.2")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def read_current_version(root: Path) -> str:
    about_path = root / "openhpc_webui" / "__about__.py"
    try:
        content = about_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise VersionUpdateError(f"找不到版本源文件: {about_path}") from exc
    match = VERSION_ASSIGNMENT.search(content)
    if not match:
        raise VersionUpdateError(f"无法从 {about_path} 读取 __version__")
    return match.group(1)


def check_versions(root: Path) -> str:
    current_version = read_current_version(root)
    missing = []
    for relative_path in MANAGED_VERSION_FILES:
        path = root / relative_path
        if not path.is_file():
            missing.append(f"{relative_path}（文件不存在）")
            continue
        if current_version not in path.read_text(encoding="utf-8"):
            missing.append(f"{relative_path}（未包含 {current_version}）")
    if missing:
        details = "\n  - ".join(missing)
        raise VersionUpdateError(f"版本文件不同步:\n  - {details}")
    return current_version


def build_updates(
    root: Path,
    new_version: str,
    release_date: str,
    allow_downgrade: bool = False,
) -> Dict[Path, str]:
    new_parts = validate_version(new_version)
    old_version = check_versions(root)
    old_parts = validate_version(old_version)
    if new_version == old_version:
        raise VersionUpdateError(f"当前版本已经是 {new_version}")
    if new_parts < old_parts and not allow_downgrade:
        raise VersionUpdateError(
            f"拒绝从 {old_version} 降级到 {new_version}；如确有需要请使用 --allow-downgrade"
        )
    if not re.fullmatch(r"\d{8}", release_date):
        raise VersionUpdateError("发布日期必须使用 YYYYMMDD 格式")

    updates: Dict[Path, str] = {}
    for relative_path in MANAGED_VERSION_FILES:
        path = root / relative_path
        content = path.read_text(encoding="utf-8")
        updated = content.replace(old_version, new_version)
        if updated == content:
            raise VersionUpdateError(f"{relative_path} 中未找到当前版本 {old_version}")
        updates[path] = updated

    changelog_path = root / "CHANGELOG.md"
    changelog = changelog_path.read_text(encoding="utf-8")
    release_heading = f"## {new_version} - {release_date}"
    if release_heading in changelog:
        raise VersionUpdateError(f"CHANGELOG.md 已包含 {release_heading}")
    if UNRELEASED_HEADING not in changelog:
        raise VersionUpdateError(f"CHANGELOG.md 缺少 '{UNRELEASED_HEADING}' 标题")
    updates[changelog_path] = changelog.replace(
        UNRELEASED_HEADING,
        f"{UNRELEASED_HEADING}\n\n{release_heading}",
        1,
    )
    return updates


def write_updates(updates: Dict[Path, str]) -> None:
    for path, content in updates.items():
        path.write_text(content, encoding="utf-8")


def relative_paths(paths: Iterable[Path], root: Path) -> Iterable[str]:
    for path in paths:
        yield str(path.relative_to(root))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="同步调整 OpenHPC WebUI 的项目版本号。",
    )
    parser.add_argument("version", nargs="?", help="新版本号，格式为 X.Y.Z")
    parser.add_argument(
        "--date",
        default=date.today().strftime("%Y%m%d"),
        help="CHANGELOG 发布日期，格式为 YYYYMMDD（默认：今天）",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅显示将修改的文件")
    parser.add_argument("--check", action="store_true", help="检查当前版本是否已同步")
    parser.add_argument(
        "--allow-downgrade",
        action="store_true",
        help="允许将版本号调整为更低版本",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=project_root(),
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    try:
        if args.check:
            if args.version:
                raise VersionUpdateError("--check 不接受新版本号")
            current = check_versions(root)
            print(f"版本文件已同步: {current}")
            return 0
        if not args.version:
            raise VersionUpdateError("请提供新版本号，或使用 --check")

        old_version = read_current_version(root)
        updates = build_updates(
            root,
            args.version,
            args.date,
            allow_downgrade=args.allow_downgrade,
        )
        action = "将修改" if args.dry_run else "已修改"
        if not args.dry_run:
            write_updates(updates)
            check_versions(root)
        print(f"{action}版本: {old_version} -> {args.version}")
        for relative_path in relative_paths(updates, root):
            print(f"  - {relative_path}")
        if args.dry_run:
            print("dry-run：未写入文件")
        return 0
    except (OSError, VersionUpdateError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
