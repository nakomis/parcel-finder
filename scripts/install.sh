#!/bin/bash
# Install (or reinstall) parcel-finder's LaunchAgents for the current user.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
# Put the CLI on PATH (~/.local/bin) so `parcel-finder authorise` etc. work from a shell.
uv tool install --editable --force "$repo"
dest=~/Library/LaunchAgents
mkdir -p "$dest" ~/Library/Logs/parcel-finder
for label in com.nakomis.parcel-finder com.nakomis.parcel-finder.refresh; do
  sed -e "s|__REPO__|$repo|" -e "s|__HOME__|$HOME|g" -e "s|__PATH__|$PATH|" \
    "$repo/launchd/$label.plist" > "$dest/$label.plist"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  # bootout returns before the job is gone; bootstrapping too early fails with an I/O error.
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1 || break
    sleep 0.5
  done
  launchctl bootstrap "gui/$(id -u)" "$dest/$label.plist"
  echo "loaded $label"
done
echo "health: $(curl -s --retry 5 --retry-connrefused 127.0.0.1:8775/health)"
