#!/bin/sh
# Check the latest oh-my-pi (omp) release and update LATEST_OMP_VERSION if it
# differs from what is currently pinned.
#
# Bumping LATEST_OMP_VERSION (and pushing the change) is what triggers a new
# snap build, so run this whenever you want to pick up a new upstream release
# (e.g. from a scheduled job).
#
# Exit status:
#   0 - LATEST_OMP_VERSION was updated to a new version
#   0 - already up to date (prints a message, see below)
#   1 - an error occurred
set -eu

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
version_file="${repo_root}/LATEST_OMP_VERSION"

# Resolve the latest release tag without hitting the API, by following the
# redirect of the /releases/latest URL (e.g. .../tag/v15.12.3).
latest_url="$(curl -fsSL -o /dev/null -w '%{url_effective}' \
  "https://github.com/can1357/oh-my-pi/releases/latest")"
latest="${latest_url##*/tag/}"
latest="${latest#v}"

if [ -z "${latest}" ] || [ "${latest}" = "${latest_url}" ]; then
  echo "Could not determine the latest omp version (got: ${latest_url})" >&2
  exit 1
fi

current=""
if [ -f "${version_file}" ]; then
  current="$(cat "${version_file}")"
fi

if [ "${latest}" = "${current}" ]; then
  echo "Already up to date: omp ${current}"
  exit 0
fi

printf '%s\n' "${latest}" > "${version_file}"
echo "Updated omp version: ${current:-none} -> ${latest}"
