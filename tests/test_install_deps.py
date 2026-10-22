import os
import subprocess
from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _stub(path: Path, body: str = "exit 0") -> None:
    path.write_text(f"#!/usr/bin/env sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def test_install_deps_check_accepts_supported_set_without_deprecated_tools(tmp_path):
    """Default --check is cswap, CodexBar, and tokscale, and it must not install anything."""
    root = _root()
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    formula = tmp_path / "formula"
    bin_dir.mkdir()
    (formula / "bin").mkdir(parents=True)
    _stub(formula / "bin" / "openusage")
    for command in ("cswap", "codexbar", "tokscale", "curl"):
        _stub(bin_dir / command)
    _stub(bin_dir / "brew", 'if [ "$1" = "--prefix" ]; then printf "%s\\n" "$FAKE_FORMULA"; fi')

    env = os.environ | {
        "HOME": str(home),
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "FAKE_FORMULA": str(formula),
    }
    result = subprocess.run(
        ["bash", str(root / "packaging" / "install-deps.sh"), "--check"],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "All data-source tools present." in result.stdout
    assert "openusage" not in result.stdout
    assert not (home / ".local" / "bin" / "openusage-sh").exists()


def test_install_deps_check_reports_a_missing_supported_tool(tmp_path):
    root = _root()
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for command in ("codexbar", "tokscale", "curl"):
        _stub(bin_dir / command)

    env = os.environ | {"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin"}
    result = subprocess.run(
        ["bash", str(root / "packaging" / "install-deps.sh"), "--check"],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "MISSING" in result.stdout
    assert "cswap" in result.stdout


def test_launchd_template_includes_local_wrapper_path_placeholder():
    template = (_root() / "packaging" / "launchd" / "com.djbclark.aiuse.plist").read_text(encoding="utf-8")
    installer = (_root() / "packaging" / "launchd" / "install.sh").read_text(encoding="utf-8")

    assert "USER_LOCAL_BIN:/opt/homebrew/bin" in template
    assert "s|USER_LOCAL_BIN|${HOME}/.local/bin|g" in installer
