#!/usr/bin/env bash
# Build the current Soft Hub zip and drop older copies in dist/.
# Version lives in hub_package/hub.plugin.json (and git tags), not in a pile of zips.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="${SOFT_HUB_BUILD_PLUGIN:-/Users/sprintray/codex_soft/soft-hub/scripts/build_plugin.py}"
VER="$(python3 -c "import json; print(json.load(open('${ROOT}/hub_package/hub.plugin.json'))['version'])")"
STABLE="${ROOT}/dist/checkpoint-testnet.softhub.zip"
VERSIONED="${ROOT}/dist/checkpoint-testnet-${VER}.softhub.zip"

mkdir -p "${ROOT}/dist"
python3 "${BUILD}" "${ROOT}/hub_package" "${STABLE}"
cp -f "${STABLE}" "${VERSIONED}"

# Keep only this version on disk.
find "${ROOT}/dist" -maxdepth 1 -name 'checkpoint-testnet*.softhub.zip' ! -name "checkpoint-testnet.softhub.zip" ! -name "checkpoint-testnet-${VER}.softhub.zip" -delete

echo "built ${VER}"
echo "  ${STABLE}"
echo "  ${VERSIONED}"
