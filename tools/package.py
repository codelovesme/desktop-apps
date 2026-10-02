#!/usr/bin/env python3
"""Assemble one application's CDLVSM release: tools/package.py <app> <version> <out-dir>.

The archive is <app>-<version>-x86_64-linux.tar.gz, staging a directory of
the same name with the launcher named after the application (what CDLVSM
runs), the shared window, the application's app.conf and icon, and an
app.info CDLVSM reads for the desktop's applications menu.
"""

from pathlib import Path
import shutil
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def read_conf(path: Path) -> dict:
    conf = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            conf[key.strip()] = value.strip()
    return conf


def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: tools/package.py <app> <version> <out-dir>")
    app, version, destination = sys.argv[1:]
    source = ROOT / "apps" / app
    if not (source / "app.conf").is_file():
        raise SystemExit(f"no such application: {app}")
    conf = read_conf(source / "app.conf")
    out = Path(destination).resolve()
    out.mkdir(parents=True, exist_ok=True)
    name = f"{app}-{version}-x86_64-linux"
    stage = out / name
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    shutil.copy2(ROOT / "bin/launcher", stage / app)
    (stage / app).chmod(0o755)
    shutil.copy2(ROOT / "window/app_window.py", stage / "app_window.py")
    for entry in ("README.md", "LICENSE"):
        shutil.copy2(ROOT / entry, stage / entry)
    for entry in source.iterdir():
        shutil.copy2(entry, stage / entry.name)
    (stage / "VERSION").write_text(version + "\n")
    (stage / "app.info").write_text(
        f"name={conf['name']}\n"
        f"comment={conf.get('comment', '')}\n"
        "terminal=false\n"
        f"icon={conf.get('icon', '')}\n"
        f"categories={conf.get('categories', 'Utility;')}\n"
        f"keywords={conf.get('keywords', '')}\n"
    )
    archive = out / (name + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(stage, arcname=name)
    print(archive)


if __name__ == "__main__":
    main()
