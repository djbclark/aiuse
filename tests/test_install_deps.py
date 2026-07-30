import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging" / "install-deps.sh"
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def _stub(path: Path, body: str = "exit 0") -> None:
    path.write_text(f"#!/usr/bin/env sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def test_install_deps_check_does_not_create_openusage_sh_wrapper(tmp_path):
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    formula = tmp_path / "formula"
    bin_dir.mkdir()
    (formula / "bin").mkdir(parents=True)
    _stub(formula / "bin" / "openusage")
    for command in ("cswap", "codexbar", "caut", "openusage", "tokscale", "curl"):
        _stub(bin_dir / command)
    _stub(bin_dir / "brew", 'if [ "$1" = "--prefix" ]; then printf "%s\\n" "$FAKE_FORMULA"; fi')

    env = os.environ | {
        "HOME": str(home),
        "PATH": f"{bin_dir}:{SYSTEM_PATH}",
        "FAKE_FORMULA": str(formula),
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), "--check"], cwd=ROOT, env=env, text=True, capture_output=True, check=False
    )

    assert result.returncode == 1
    assert "openusage-sh wrapper" in result.stdout
    assert not (home / ".local" / "bin" / "openusage-sh").exists()


def test_install_deps_check_recognizes_openusage_sh_wrapper(tmp_path):
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for command in ("cswap", "codexbar", "caut", "openusage", "openusage-sh", "tokscale", "curl"):
        _stub(bin_dir / command)

    env = os.environ | {"HOME": str(home), "PATH": f"{bin_dir}:{SYSTEM_PATH}"}
    result = subprocess.run(
        ["bash", str(SCRIPT), "--check"], cwd=ROOT, env=env, text=True, capture_output=True, check=False
    )

    assert result.returncode == 0
    assert "ok       openusage-sh" in result.stdout


def test_launchd_template_includes_local_wrapper_path_placeholder():
    template = (ROOT / "packaging" / "launchd" / "com.djbclark.aiuse.plist").read_text(encoding="utf-8")
    installer = (ROOT / "packaging" / "launchd" / "install.sh").read_text(encoding="utf-8")

    assert "USER_LOCAL_BIN:/opt/homebrew/bin" in template
    assert 's|USER_LOCAL_BIN|${HOME}/.local/bin|g' in installer
