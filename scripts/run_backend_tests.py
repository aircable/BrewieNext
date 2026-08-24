#!/usr/bin/env python3
"""Run backend tests against the pinned development program workspace."""

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
        "PROCEDURES_DIR": runtime["procedures"],
        "SCHEMA_PATH": str(Path(runtime["program_release"]) / "schemas/procedure.schema.json"),
        "GRAPH_SCHEMA_PATH": str(Path(runtime["program_release"]) / "schemas/workflow.schema.json"),
        "RECIPE_SCHEMA_PATH": str(ROOT / "services/runtime/recipe.schema.json"),
        "BUNDLED_RECIPES_DIR": str(ROOT / "fixtures/recipes"),
        "RECIPES_DIR": runtime["recipes"],
        "BREWIE_HARDWARE_REGISTRY": str(ROOT / "services/runtime/hardware-device-registry.yml"),
    })
    subprocess.run(
        [runtime["python"], "-m", "unittest", "discover", "-s", "services/runtime", "-p", "test_*.py", "-v"],
        cwd=ROOT,
        env=env,
        check=True,
    )


if __name__ == "__main__":
    main()
