#!/usr/bin/env python3
"""Prepare an isolated BrewieNext development runtime."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / ".dev"
CACHE = ROOT / ".cache"
LOCK_PATH = ROOT / "programs.lock.json"


def find_procedure_source() -> Path | None:
    """Find an authoring checkout without weakening the pinned release path."""
    configured = os.environ.get("BREWIE_PROCEDURES_SOURCE")
    candidate = Path(configured).expanduser() if configured else ROOT.parent / "BrewieNextProcedures"
    candidate = candidate.resolve()
    required = (
        candidate / "program-package.yml",
        candidate / "workflows/beer_brewing.yml",
        candidate / "procedures",
        candidate / "schemas/procedure.schema.json",
        candidate / "schemas/workflow.schema.json",
    )
    if all(path.exists() for path in required):
        return candidate
    if configured:
        raise RuntimeError(
            f"BREWIE_PROCEDURES_SOURCE is not a BrewieNextProcedures checkout: {candidate}"
        )
    return None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_venv() -> Path:
    target = DEV / "venv"
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        print("Creating Python environment...")
        venv.EnvBuilder(with_pip=True).create(target)

    requirements = ROOT / "services/runtime/requirements.txt"
    expected = sha256(requirements)
    stamp = target / ".requirements.sha256"
    if not stamp.exists() or stamp.read_text(encoding="ascii").strip() != expected:
        print("Installing runtime dependencies...")
        subprocess.run(
            [str(python), "-m", "pip", "install", "-r", str(requirements)],
            check=True,
        )
        stamp.write_text(expected + "\n", encoding="ascii")
    return python


def safe_extract(archive: Path, destination: Path) -> Path:
    destination_resolved = destination.resolve()
    with tarfile.open(archive, "r:gz") as package:
        members = package.getmembers()
        for member in members:
            member_path = (destination / member.name).resolve()
            if destination_resolved not in member_path.parents and member_path != destination_resolved:
                raise RuntimeError(f"Unsafe path in procedure archive: {member.name}")
            if member.issym() or member.islnk():
                raise RuntimeError(f"Links are not allowed in procedure archive: {member.name}")
        try:
            package.extractall(destination, members=members, filter="data")
        except TypeError:  # Python 3.10/3.11
            package.extractall(destination, members=members)

    roots = [path for path in destination.iterdir() if path.is_dir()]
    if len(roots) != 1 or not (roots[0] / "bundle-manifest.json").is_file():
        raise RuntimeError("Procedure archive does not contain one valid bundle root")
    return roots[0]


def ensure_programs() -> tuple[dict, Path, Path]:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    CACHE.mkdir(parents=True, exist_ok=True)
    archive = CACHE / lock["asset"]
    if not archive.exists() or sha256(archive) != lock["sha256"]:
        print(f"Downloading procedure programs {lock['version']}...")
        temporary = archive.with_suffix(archive.suffix + ".download")
        temporary.unlink(missing_ok=True)
        urllib.request.urlretrieve(lock["url"], temporary)
        actual = sha256(temporary)
        if actual != lock["sha256"]:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(
                f"Procedure bundle checksum mismatch: expected {lock['sha256']}, got {actual}"
            )
        temporary.replace(archive)

    programs_root = DEV / "programs"
    releases = programs_root / "releases"
    release = releases / lock["version"]
    if not (release / "bundle-manifest.json").is_file():
        releases.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=releases, prefix=".extract-") as temporary:
            extracted = safe_extract(archive, Path(temporary))
            if release.exists():
                shutil.rmtree(release)
            shutil.move(str(extracted), release)

    workspace = programs_root / "workspace"
    if not workspace.exists():
        print("Creating editable procedure workspace...")
        shutil.copytree(release / "programs", workspace)
        (workspace / ".base-release").write_text(lock["version"] + "\n", encoding="utf-8")
    return lock, release, workspace


def main() -> None:
    DEV.mkdir(parents=True, exist_ok=True)
    python = ensure_venv()
    lock, release, workspace = ensure_programs()
    source = find_procedure_source()
    procedures = source or workspace
    schema_root = source / "schemas" if source else release / "schemas"
    create_dir = source / "procedures/brewing" if source else workspace
    recipes = DEV / "recipes"
    recipes.mkdir(parents=True, exist_ok=True)
    runtime = {
        "python": str(python.resolve()),
        "program_version": lock["version"],
        "program_archive": str((CACHE / lock["asset"]).resolve()),
        "program_release": str(release.resolve()),
        "program_mode": "source_checkout" if source else "release_workspace",
        "program_source": str(source) if source else None,
        "procedures": str(procedures.resolve()),
        "procedures_create_dir": str(create_dir.resolve()),
        "procedure_schema": str((schema_root / "procedure.schema.json").resolve()),
        "workflow_schema": str((schema_root / "workflow.schema.json").resolve()),
        "recipes": str(recipes.resolve()),
    }
    (DEV / "runtime-env.json").write_text(
        json.dumps(runtime, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    origin = f"source checkout {source}" if source else f"release workspace {lock['version']}"
    print(f"BrewieNext development runtime is ready ({origin}).")


if __name__ == "__main__":
    main()
