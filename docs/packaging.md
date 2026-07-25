# Packaging `aiuse`

Distribution name / CLI: **`aiuse`**. Compatibility console script **`ai`**
calls the same entrypoint (`aiuse.ai_stub:main`).

External data sources stay separate installs (this package only ships the
aggregator CLI). Install all five with:

```bash
./packaging/install-deps.sh
# or: just -f ~/ops/site-djbclark/justfile install-aiuse-deps
```

| Tool | Typical install |
| --- | --- |
| **cswap** | `uv tool install claude-swap` |
| **codexbar** | `brew install --cask codexbar` |
| **caut** | `cargo install --locked --git https://github.com/Dicklesworthstone/coding_agent_usage_tracker` |
| **openusage** | `brew install --cask openusage` (+ optional CLI from app Settings; or leave app running for `:6736`) |
| **tokscale** | PATH shim via `npx tokscale@latest` (created by `install-deps.sh`) |

## Status

| Channel                              | Status                                                                             |
| ------------------------------------ | ---------------------------------------------------------------------------------- |
| Editable / venv (`pip install -e .`) | Supported (dev)                                                                    |
| **pipx** from PyPI                   | Live: `pipx install aiuse` → https://pypi.org/project/aiuse/                       |
| **pipx** from GitHub                 | `pipx install 'git+https://github.com/djbclark/aiuse.git'`                         |
| **PyPI Trusted Publishing (OIDC)**   | **Live and verified** — see below                                                  |
| **Homebrew** personal tap            | Live: `brew tap djbclark/aiuse && brew trust djbclark/aiuse && brew install aiuse` |
| homebrew-core                        | Not submitted                                                                      |

## Version note

Git tag **`v2.0.0`** is historical (pre-rename `src/ai/`). First renamed PyPI
release was **`2.1.0`** (API token). **`2.1.1`** was the first release uploaded
by GitHub Actions via Trusted Publishing. Current published release: **`2.1.7`**.

## Install (end users)

```bash
# preferred
pipx install aiuse

# Homebrew
brew tap djbclark/aiuse
brew trust djbclark/aiuse
brew install aiuse
```

Upgrade: `pipx upgrade aiuse` or `brew upgrade aiuse`.

## PyPI Trusted Publishing (OIDC) — how releases publish

**Preferred path for every release.** No PyPI token in GitHub Actions secrets.

| Piece              | Value                                                               |
| ------------------ | ------------------------------------------------------------------- |
| Workflow           | [`.github/workflows/publish.yml`](../.github/workflows/publish.yml) |
| Trigger            | GitHub Release `published`, or `workflow_dispatch`                  |
| GitHub Environment | `pypi` (repo Settings → Environments)                               |
| PyPI project       | [`aiuse`](https://pypi.org/project/aiuse/)                          |
| Publisher settings | https://pypi.org/manage/project/aiuse/settings/publishing/          |
| Owner              | `djbclark`                                                          |
| Repository         | `aiuse`                                                             |
| Workflow name      | `publish.yml`                                                       |
| Environment name   | `pypi`                                                              |

**Verified:** [Actions run for `v2.1.1`](https://github.com/djbclark/aiuse/actions/runs/30099193664)
uploaded https://pypi.org/project/aiuse/2.1.1/ with OIDC (no token).

If the publisher is ever removed, re-add it on the PyPI publishing settings page
with the table above (must match exactly). Keep the PyPI project name **`aiuse`**
(not `ai` — taken by Vercel’s AI SDK).

### Maintainer release flow (OIDC)

1. Bump `version` in [`pyproject.toml`](../pyproject.toml) and `__version__` in
   [`src/aiuse/__init__.py`](../src/aiuse/__init__.py); `uv lock`.
2. `.venv/bin/python -m pytest -q` — green.
3. Commit, push `main` (use SSH if HTTPS lacks `workflow` scope for
   `.github/workflows/*` changes).
4. `git tag -a vX.Y.Z -m "…"` && `git push origin vX.Y.Z` (SSH ok).
5. Build and attach artifacts (optional but nice):

   ```bash
   uv run --with build python -m build
   gh release create vX.Y.Z dist/* --title "aiuse X.Y.Z" --notes "…"
   ```

6. The **publish** workflow runs on that Release and uploads to PyPI via OIDC.
   Confirm: `gh run list --workflow=publish.yml -L 1` and
   https://pypi.org/project/aiuse/.
7. Refresh Homebrew (below) and push the tap.

Do **not** re-run an old publish job for a version already on PyPI — uploads
will fail with “file already exists” even when OIDC is fine.

## Manual upload via secretspec (optional fallback)

Only for local/emergency publishes. Declarations:
[`secretspec.toml`](../secretspec.toml). Value in gitignored `.env` (dotenv).

```bash
secretspec set PYPI_TOKEN          # prompts if omitted
secretspec check -n --explain
uv run --with build python -m build
secretspec run --reason "publish aiuse to PyPI" -- \
  bash -lc 'uv publish --token "$PYPI_TOKEN" dist/*'
```

OIDC is the normal path; keep or revoke the API token as you prefer.

## Homebrew

Canonical formula in this repo:
[`packaging/homebrew/aiuse.rb`](../packaging/homebrew/aiuse.rb).

Tap: https://github.com/djbclark/homebrew-aiuse → `brew tap djbclark/aiuse`.

```bash
brew tap djbclark/aiuse
brew trust djbclark/aiuse   # required for third-party taps
brew install aiuse
```

After each tagged release, update `url` / `sha256` in the in-repo formula and
sync `Formula/aiuse.rb` in the tap:

```bash
curl -sL "https://github.com/djbclark/aiuse/archive/refs/tags/vX.Y.Z.tar.gz" \
  | shasum -a 256
```

## Config paths

- Config: `~/.config/aiuse/` (`services.yaml`, `config.toml`)
- Snapshots: `~/.cache/aiuse/snapshots`

The old `~/.config/ai/` path is no longer read. If you still have files there:

```bash
mkdir -p ~/.config/aiuse
cp -n ~/.config/ai/* ~/.config/aiuse/
```
