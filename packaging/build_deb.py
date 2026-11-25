#!/usr/bin/env python3
"""Build a reproducible ``aiuse_<version>_all.deb`` for Debian and Ubuntu.

The package is architecture-independent: it unpacks the pure-Python aiuse
wheel into ``/usr/lib/python3/dist-packages`` and depends on the distro's
``python3-yaml``, ``python3-requests`` and ``python3-rich`` packages (no
bundled virtualenv). Needs Debian 12+ or Ubuntu 24.04+ (Python >= 3.11).

Usage::

    python3 packaging/build_deb.py                    # build the wheel from this tree
    python3 packaging/build_deb.py --wheel dist/aiuse-X.Y.Z-py3-none-any.whl
    python3 packaging/build_deb.py --out-dir dist-deb

Output is byte-for-byte reproducible for a given wheel and
``SOURCE_DATE_EPOCH`` (default: the wheel's newest member timestamp).
Requires ``dpkg-deb``.
"""

from __future__ import annotations

import argparse
import gzip
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from email.parser import Parser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_PACKAGES = Path("usr/lib/python3/dist-packages")
MAINTAINER = "djbclark <131936+djbclark@users.noreply.github.com>"
HOMEPAGE = "https://github.com/djbclark/aiuse"
# Debian package names for the runtime requirements in pyproject.toml.
DEPENDS = [
    "python3 (>= 3.11)",
    "python3-yaml (>= 6.0)",
    "python3-requests (>= 2.28)",
    "python3-rich (>= 13.0)",
]
ENTRY_POINTS = {"aiuse": ("aiuse.cli", "main"), "ai": ("aiuse.ai_stub", "main")}
COMPLETIONS = {
    "usr/share/bash-completion/completions/aiuse": "completions/aiuse.bash",
    "usr/share/bash-completion/completions/ai": "completions/ai.bash",
    "usr/share/zsh/vendor-completions/_aiuse": "completions/aiuse.zsh",
    "usr/share/zsh/vendor-completions/_ai": "completions/ai.zsh",
}
WHEEL_RE = re.compile(r"^aiuse-(?P<version>\d+\.\d+\.\d+)-py3-none-any\.whl$")


def build_wheel(out_dir: Path) -> Path:
    if shutil.which("uv"):
        # sdist first, then the wheel from it, so a stale in-tree build/ cannot leak in.
        cmd = ["uv", "build", "--quiet", "--out-dir", str(out_dir), str(REPO_ROOT)]
    else:
        cmd = [sys.executable, "-m", "build", "--outdir", str(out_dir), str(REPO_ROOT)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    wheels = sorted(out_dir.glob("aiuse-*-py3-none-any.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected one aiuse wheel in {out_dir}, found {wheels}")
    return wheels[0]


def wheel_version(wheel: Path) -> str:
    m = WHEEL_RE.match(wheel.name)
    if not m:
        raise SystemExit(f"not an aiuse py3-none-any wheel: {wheel.name}")
    return m.group("version")


def wheel_epoch(zf: zipfile.ZipFile) -> int:
    import calendar

    return max(calendar.timegm((*info.date_time, 0, 0, 0)) for info in zf.infolist())


def script_text(module: str, func: str) -> str:
    return f"#!/usr/bin/python3\nimport sys\n\nfrom {module} import {func}\n\nif __name__ == '__main__':\n    sys.exit({func}())\n"


def write(path: Path, data: bytes | str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    path.chmod(mode)


def gzip_bytes(data: bytes, epoch: int) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=epoch, filename="") as gz:
        gz.write(data)
    return buf.getvalue()


def stage(wheel: Path, root: Path, source_root: Path, epoch: int) -> tuple[str, str]:
    version = wheel_version(wheel)
    with zipfile.ZipFile(wheel) as zf:
        metadata = Parser().parsestr(zf.read(f"aiuse-{version}.dist-info/METADATA").decode())
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            if info.is_dir():
                continue
            name = info.filename
            if name.endswith("/RECORD"):
                continue  # dpkg owns the files; a stale RECORD would mislead pip
            write(root / DIST_PACKAGES / name, zf.read(info))
    write(root / DIST_PACKAGES / f"aiuse-{version}.dist-info" / "INSTALLER", "dpkg\n")
    for name, (module, func) in ENTRY_POINTS.items():
        write(root / "usr/bin" / name, script_text(module, func), 0o755)
    for dest, src in COMPLETIONS.items():
        src_path = source_root / src
        if src_path.is_file():
            write(root / dest, src_path.read_bytes())
    doc = root / "usr/share/doc/aiuse"
    license_text = (source_root / "LICENSE").read_text() if (source_root / "LICENSE").is_file() else ""
    write(
        doc / "copyright",
        "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n"
        f"Upstream-Name: aiuse\nSource: {HOMEPAGE}\n\nFiles: *\nCopyright: djbclark\nLicense: MIT\n"
        + "".join(f" {line}\n" if line.strip() else " .\n" for line in license_text.splitlines()),
    )
    write(
        doc / "changelog.Debian.gz",
        gzip_bytes(
            (
                f"aiuse ({version}) unstable; urgency=medium\n\n"
                f"  * Upstream release {version}; see {HOMEPAGE}/blob/v{version}/CHANGELOG.md\n\n"
                f" -- {MAINTAINER}  {format_date(epoch)}\n"
            ).encode(),
            epoch,
        ),
    )
    return version, metadata.get("Summary", "AI usage aggregator")


def format_date(epoch: int) -> str:
    from email.utils import formatdate

    return formatdate(epoch, usegmt=False).replace("-0000", "+0000")


def installed_size_kib(root: Path) -> int:
    total = sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and "DEBIAN" not in p.parts)
    return (total + 1023) // 1024


def write_control(root: Path, version: str, summary: str) -> None:
    debian = root / "DEBIAN"
    write(
        debian / "control",
        f"Package: aiuse\nVersion: {version}\nArchitecture: all\nMaintainer: {MAINTAINER}\n"
        f"Installed-Size: {installed_size_kib(root)}\nDepends: {', '.join(DEPENDS)}\n"
        "Section: utils\nPriority: optional\n"
        f"Homepage: {HOMEPAGE}\nDescription: {summary}\n"
        " aiuse aggregates AI subscription and API usage from local tools\n"
        " (CodexBar, cswap, caut, tokscale and others) and flags\n"
        " use-it-or-lose-it allotments before they reset.\n",
    )
    write(
        debian / "postinst",
        "#!/bin/sh\nset -e\nif command -v py3compile >/dev/null 2>&1; then\n    py3compile -p aiuse\nfi\n",
        0o755,
    )
    write(
        debian / "prerm",
        "#!/bin/sh\nset -e\nif command -v py3clean >/dev/null 2>&1; then\n    py3clean -p aiuse\nfi\n",
        0o755,
    )


def normalize(root: Path, epoch: int) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir():
            path.chmod(0o755)
        os.utime(path, (epoch, epoch), follow_symlinks=False)
    os.utime(root, (epoch, epoch))


def build(wheel: Path, out_dir: Path, source_root: Path = REPO_ROOT) -> Path:
    if shutil.which("dpkg-deb") is None:
        raise SystemExit("dpkg-deb not found (apt install dpkg)")
    with zipfile.ZipFile(wheel) as zf:
        default_epoch = wheel_epoch(zf)
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", default_epoch))
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "root"
        version, summary = stage(wheel, root, source_root, epoch)
        write_control(root, version, summary)
        normalize(root, epoch)
        out = out_dir / f"aiuse_{version}_all.deb"
        env = {**os.environ, "SOURCE_DATE_EPOCH": str(epoch)}
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(root), str(out)],
            check=True,
            env=env,
            stdout=subprocess.DEVNULL,
        )
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--wheel", type=Path, help="Existing aiuse wheel (default: build one from this tree)")
    p.add_argument("--out-dir", type=Path, default=REPO_ROOT / "dist-deb")
    args = p.parse_args(argv)
    if args.wheel:
        out = build(args.wheel.resolve(), args.out_dir)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            out = build(build_wheel(Path(tmp)), args.out_dir)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
