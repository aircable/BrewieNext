#!/usr/bin/env python3
"""Require a v-prefixed Git tag matching the root package version."""

import json
import sys
from pathlib import Path


tag = sys.argv[1] if len(sys.argv) == 2 else ""
version = json.loads((Path(__file__).resolve().parents[1] / "package.json").read_text())["version"]
if tag != f"v{version}":
    raise SystemExit(f"Tag {tag!r} does not match package version v{version}")
