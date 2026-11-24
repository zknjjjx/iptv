#!/usr/bin/env python3
"""Build an unsigned local OpenWrt APK v3; requires apk-tools 3 mkpkg on build host."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
VERSION = "1.0.0-r4"
NAME = "luci-app-iptv-speedtest"
DEPENDENCIES = "python3-light python3-urllib python3-openssl python3-logging python3-codecs luci-base rpcd-mod-file"


def build(tool, arch, output):
    if os.geteuid() != 0:
        raise SystemExit("Run this isolated build with sudo so APK files are owned by root, not the build user.")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="iptv-apk-") as td:
        root = Path(td) / "root"
        shutil.copytree(HERE / "files", root, ignore=shutil.ignore_patterns(".*", "__pycache__", "*.pyc"))
        app = root / "usr/share/iptv-speedtest/app.py"
        app.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HERE.parent / "app.py", app)
        # Never embed a router config, password, playlist or build-machine identity.
        for path in root.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
            elif path.is_file():
                path.chmod(0o755 if "/init.d/" in str(path) or "/usr/libexec/" in str(path) else 0o644)
        (root / "etc/config/iptv_speedtest").chmod(0o600)
        artifact = output / f"{NAME}-{VERSION}.{arch}.apk"
        # Include root/parent nodes: OpenWrt's APK 3.0.5 extractor needs them.
        command = [tool, "mkpkg", "--compat", "3.0.0_pre1", "--files", str(root),
                   "--output", str(artifact)]
        info = {"name": NAME, "version": VERSION, "arch": arch,
                "description": "Native IPTV speedtest service and LuCI settings (no Docker)",
                "url": "https://github.com/zknjjjx/iptv", "license": "NOASSERTION",
                "depends": DEPENDENCIES}
        for key, value in info.items():
            command += ["--info", f"{key}:{value}"]
        for kind in ("post-install", "post-upgrade", "pre-deinstall"):
            command += ["--script", f"{kind}:{HERE / 'scripts' / kind}"]
        env = dict(os.environ, SOURCE_DATE_EPOCH=os.getenv("SOURCE_DATE_EPOCH", "1792368000"))
        subprocess.run(command, check=True, env=env)
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        artifact.with_suffix(artifact.suffix + ".sha256").write_text(f"{digest}  {artifact.name}\n")
        print(artifact)
        print("sha256:", digest)
        return artifact


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", default="apk", help="Build-host apk-tools 3 binary with mkpkg")
    parser.add_argument("--arch", default="aarch64_cortex-a53")
    parser.add_argument("--output", default=str(HERE / "dist"))
    args = parser.parse_args()
    build(args.apk, args.arch, args.output)
