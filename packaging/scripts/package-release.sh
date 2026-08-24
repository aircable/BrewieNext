#!/bin/sh
set -eu

VERSION=${1:-0.5.0}
REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
WEB_ROOT="$REPO_ROOT/apps/web"
RUNTIME_ROOT="$REPO_ROOT/services/runtime"
OUTPUT_DIR=${2:-$REPO_ROOT/releases}

case "$VERSION" in
  ''|*[!A-Za-z0-9._-]*) echo "Invalid version: $VERSION" >&2; exit 1 ;;
esac

[ -f "$WEB_ROOT/dist/index.html" ] || {
  echo "apps/web/dist/index.html is missing; run npm run build first" >&2
  exit 1
}

python3 "$REPO_ROOT/scripts/bootstrap.py"
PROGRAM_VERSION=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$REPO_ROOT/programs.lock.json")
PROGRAM_ASSET=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["asset"])' "$REPO_ROOT/programs.lock.json")
PROGRAM_SHA256=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha256"])' "$REPO_ROOT/programs.lock.json")
PROGRAM_ARCHIVE="$REPO_ROOT/.cache/$PROGRAM_ASSET"
[ -f "$PROGRAM_ARCHIVE" ] || { echo "Pinned program archive is missing" >&2; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM
PACKAGE_ROOT="$WORK/brewienext-$VERSION"
mkdir -p "$PACKAGE_ROOT/backend" "$PACKAGE_ROOT/recipes" "$PACKAGE_ROOT/program-bundle"
cp -R "$WEB_ROOT/dist/." "$PACKAGE_ROOT/"
cp "$RUNTIME_ROOT/editor_backend.py" \
  "$RUNTIME_ROOT/brewie_procedure_validation.py" \
  "$RUNTIME_ROOT/avr_serial.py" \
  "$RUNTIME_ROOT/hardware_registry.py" \
  "$RUNTIME_ROOT/runtime_engine.py" \
  "$RUNTIME_ROOT/recipe.schema.json" \
  "$RUNTIME_ROOT/hardware-device-registry.yml" \
  "$RUNTIME_ROOT/hardware-device-registry.schema.json" \
  "$RUNTIME_ROOT/requirements.txt" "$PACKAGE_ROOT/backend/"
cp -R "$REPO_ROOT/fixtures/recipes/." "$PACKAGE_ROOT/recipes/"
cp "$REPO_ROOT/programs.lock.json" "$PROGRAM_ARCHIVE" "$PACKAGE_ROOT/program-bundle/"
printf '%s\n' "$PROGRAM_VERSION" > "$PACKAGE_ROOT/program-bundle/version"
printf '%s\n' "$PROGRAM_ASSET" > "$PACKAGE_ROOT/program-bundle/archive-name"
printf '%s\n' "$PROGRAM_SHA256" > "$PACKAGE_ROOT/program-bundle/archive.sha256"

CREATED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '{"name":"BrewieNext","version":"%s","created":"%s","format":2,"program_version":"%s"}\n' \
  "$VERSION" "$CREATED" "$PROGRAM_VERSION" > "$PACKAGE_ROOT/manifest.json"

mkdir -p "$OUTPUT_DIR"
ARCHIVE="$OUTPUT_DIR/brewienext-$VERSION.tar.gz"
tar -czf "$ARCHIVE" -C "$WORK" "brewienext-$VERSION"
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
echo "Created $ARCHIVE"
echo "Pinned programs: $PROGRAM_VERSION ($PROGRAM_SHA256)"
