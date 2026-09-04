#!/usr/bin/env python3
"""Start the BrewieNext development backend with safe local settings."""

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    subprocess.run([sys.executable, str(ROOT / "scripts/bootstrap.py")], check=True)
    runtime = json.loads((ROOT / ".dev/runtime-env.json").read_text(encoding="utf-8"))
    env = os.environ.copy()
    env.update({
        "BREWIE_APP_ROOT": str(ROOT),
        "BREWIE_AVR_ENABLED": "0",
        "EDITOR_HOST": env.get("EDITOR_HOST", "127.0.0.1"),
        "EDITOR_PORT": env.get("EDITOR_PORT", "8081"),
        "EDITOR_DEBUG": "0",
        "PROCEDURES_DIR": runtime["procedures"],
        "PROCEDURES_CREATE_DIR": runtime["procedures_create_dir"],
        "SCHEMA_PATH": runtime["procedure_schema"],
        "GRAPH_SCHEMA_PATH": runtime["workflow_schema"],
        "RECIPE_SCHEMA_PATH": str(ROOT / "services/runtime/recipe.schema.json"),
        "BUNDLED_RECIPES_DIR": str(ROOT / "fixtures/recipes"),
        "RECIPES_DIR": runtime["recipes"],
        "BREWIE_HARDWARE_REGISTRY": str(ROOT / "services/runtime/hardware-device-registry.yml"),
    })
    os.execve(
        runtime["python"],
        [runtime["python"], str(ROOT / "services/runtime/editor_backend.py")],
        env,
    )


if __name__ == "__main__":
    main()
