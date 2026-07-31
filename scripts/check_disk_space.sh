#!/usr/bin/env bash
set -euo pipefail

check_path="."
minimum_mib=""
operation_name="operation"
warn_only=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --path)
      check_path="${2:?Missing value for --path}"
      shift 2
      ;;
    --min-mib)
      minimum_mib="${2:?Missing value for --min-mib}"
      shift 2
      ;;
    --operation)
      operation_name="${2:?Missing value for --operation}"
      shift 2
      ;;
    --warn-only)
      warn_only=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ ! "${minimum_mib}" =~ ^[1-9][0-9]*$ ]]; then
  echo "--min-mib must be a positive integer" >&2
  exit 2
fi
if [[ ! -e "${check_path}" ]]; then
  echo "Disk check path does not exist: ${check_path}" >&2
  exit 2
fi

available_kib="$(
  df -Pk "${check_path}" \
    | awk 'NR == 2 { print $4 }'
)"
if [[ ! "${available_kib}" =~ ^[0-9]+$ ]]; then
  echo "Unable to determine free disk space for ${check_path}" >&2
  exit 2
fi

required_kib=$((minimum_mib * 1024))
available_mib=$((available_kib / 1024))
if (( available_kib < required_kib )); then
  echo "WARNING: ${operation_name} needs at least ${minimum_mib} MiB free;" \
    "${available_mib} MiB is available at ${check_path}." >&2
  if (( warn_only == 0 )); then
    exit 3
  fi
else
  echo "disk_space_ok operation=${operation_name} available_mib=${available_mib}" \
    "minimum_mib=${minimum_mib}"
fi
