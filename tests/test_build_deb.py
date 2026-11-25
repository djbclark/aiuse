"""Tests for packaging/build_deb.py (reproducible Debian package)."""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_deb", REPO_ROOT / "packaging" / "build_deb.py")
assert SPEC and SPEC.loader
build_deb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_deb)

needs_dpkg = pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb not installed")


def _fake_wheel(tmp_path: Path, version: str = "9.9.9") -> Path:
    wheel = tmp_path / f"aiuse-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        for name, data in {
            "aiuse/__init__.py": f'__version__ = "{version}"\n',
            "aiuse/cli.py": "def main():\n    return 0\n",
            f"aiuse-{version}.dist-info/METADATA": f"Metadata-Version: 2.4\nName: aiuse\nVersion: {version}\nSummary: test summary\n",
            f"aiuse-{version}.dist-info/RECORD": "",
        }.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 2, 3, 4, 5))
            zf.writestr(info, data)
    return wheel


def test_wheel_version_parses_and_rejects() -> None:
    assert build_deb.wheel_version(Path("aiuse-5.4.1-py3-none-any.whl")) == "5.4.1"
    with pytest.raises(SystemExit):
        build_deb.wheel_version(Path("aiuse-5.4.1-cp312-cp312-linux_x86_64.whl"))


def test_entry_script_calls_module_main() -> None:
    text = build_deb.script_text("aiuse.cli", "main")
    assert text.startswith("#!/usr/bin/python3\n")
    assert "from aiuse.cli import main" in text


@needs_dpkg
def test_build_is_reproducible_and_has_expected_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    wheel = _fake_wheel(tmp_path)
    a = build_deb.build(wheel, tmp_path / "a")
    b = build_deb.build(wheel, tmp_path / "b")
    assert a.name == "aiuse_9.9.9_all.deb"
    assert hashlib.sha256(a.read_bytes()).digest() == hashlib.sha256(b.read_bytes()).digest()

    def field(name: str) -> str:
        return subprocess.run(
            ["dpkg-deb", "-f", str(a), name], check=True, capture_output=True, text=True
        ).stdout.strip()

    assert field("Package") == "aiuse"
    assert field("Version") == "9.9.9"
    assert field("Architecture") == "all"
    assert "python3-rich" in field("Depends")
    listing = subprocess.run(["dpkg-deb", "-c", str(a)], check=True, capture_output=True, text=True).stdout
    assert "./usr/bin/aiuse" in listing
    assert "./usr/bin/ai\n" in listing
    assert "./usr/lib/python3/dist-packages/aiuse/cli.py" in listing
    assert "dist-info/RECORD" not in listing
    assert "dist-info/INSTALLER" in listing
    assert "root/root" in listing
