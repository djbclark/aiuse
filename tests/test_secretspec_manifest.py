"""The installed SecretSpec manifest declares ANTHROPIC_ADMIN_KEY and upgrades older ones."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from aiuse.secretspec import ensure_manifest

ROOT = Path(__file__).resolve().parents[1]
PLIST = ROOT / "packaging/launchd/com.djbclark.aiuse-credits-refresh.plist"


def test_new_manifest_declares_the_admin_key_as_optional(tmp_path):
    path = tmp_path / "secretspec.toml"
    ensure_manifest(path)
    line = next(x for x in path.read_text().splitlines() if x.startswith("ANTHROPIC_ADMIN_KEY"))
    assert "required = false" in line


def test_existing_manifest_without_the_key_gets_it_appended_once(tmp_path):
    path = tmp_path / "secretspec.toml"
    path.write_text('[project]\nname = "aiuse"\n\n[profiles.default]\nOPENCODE_ZEN_COOKIE = { required = false }')
    ensure_manifest(path)
    ensure_manifest(path)
    text = path.read_text()
    assert text.count("ANTHROPIC_ADMIN_KEY =") == 1 and text.startswith("[project]")
    assert "OPENCODE_ZEN_COOKIE = { required = false }\n" in text


def test_plist_substitution_leaves_valid_key_names(tmp_path):
    """The README substitution must not rewrite the AIUSE_BIN <key>, only values."""
    out = subprocess.run(  # noqa: S603
        [
            "python3",
            "-c",
            "import sys,re;t=open(sys.argv[1]).read();"
            "t=t.replace('@REFRESH_BIN_PATH@','/r/refresh').replace('@AIUSE_BIN_PATH@','/a/aiuse');print(t)",
            str(PLIST),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "<key>AIUSE_BIN</key>" in out and "<string>/a/aiuse</string>" in out
    assert not re.search(r"<key>/", out) and "@" not in re.sub(r"<!--.*?-->", "", out, flags=re.S)
