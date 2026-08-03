#!/usr/bin/env bash
set -euo pipefail

echo "Direct working-tree deployment is disabled: it cannot prove secret/artifact exclusion." >&2
echo "Use scripts/deploy_manifold_windows.ps1 with the verified offline package." >&2
exit 2
