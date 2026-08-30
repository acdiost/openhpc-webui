# 版本调整与发布

项目使用 `openhpc_webui/__about__.py` 中的 `__version__` 作为唯一版本源，并由
`pyproject.toml` 在构建时读取。请使用 `scripts/bump_version.py` 同步调整版本，避免
包版本、测试、README 和安装文档不一致。

## 调整版本

发布前先将新功能记录在 `CHANGELOG.md` 的 `Unreleased` 标题下，然后执行：

```bash
uv run python scripts/bump_version.py 0.2.2
```

脚本会同步修改：

- `openhpc_webui/__about__.py`
- `tests/test_application_factory.py`
- `README.md`
- `docs/DEPLOYMENT.md`
- `docs/PYPI_USAGE.md`
- `docs/TECHNICAL_GUIDE.md`
- `docs/USER_MANUAL.md`
- `CHANGELOG.md`

`CHANGELOG.md` 中的 `Unreleased` 内容会归入新版本标题，发布日期默认使用执行当天，
格式为 `YYYYMMDD`。

## 预览和检查

只查看将修改的文件，不写入磁盘：

```bash
uv run python scripts/bump_version.py 0.2.2 --dry-run
```

指定发布日期：

```bash
uv run python scripts/bump_version.py 0.2.2 --date 20260901
```

检查当前版本是否已同步到所有受管文件：

```bash
uv run python scripts/bump_version.py --check
```

脚本默认拒绝降级。只有明确需要回退版本时才使用 `--allow-downgrade`。

## 发布前验证

检查差异并运行完整测试：

```bash
git diff --check
git diff
uv run python -m compileall -q openhpc_webui
uv run python -m unittest discover -s tests
```

确认没有旧版本残留后，提交并推送：

```bash
git add CHANGELOG.md README.md docs openhpc_webui/__about__.py \
  tests/test_application_factory.py
git commit -m "Release version 0.2.2"
git push origin main
```

## 创建 GitHub Release

GitHub Release 的标签必须使用 `v<包版本>` 格式。发布工作流会验证标签与
`openhpc_webui.__version__` 一致，通过测试和构建后自动上传到 PyPI。

```bash
gh release create v0.2.2 \
  --target main \
  --title "v0.2.2" \
  --generate-notes \
  --notes-start-tag v0.2.1 \
  --latest
```

发布后检查工作流：

```bash
gh run list --event release --limit 5
```

