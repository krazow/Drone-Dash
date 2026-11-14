# Changelog

Release notes for Drone Dash. `/releasenow` adds a section here for every release, and the GitHub release notes are
taken from it.

## Unreleased

### Added
- Releases through the shared Krazow release kit: `/releasenow` publishes a beta zip, `/releasenow promote` the
  production one.
- `update.sh`: updates the Pi from GitHub releases (`sudo ./update.sh`), or installs the newest beta beside the real
  dashboard (`sudo ./update.sh beta`, port 8081, its own settings and data).
- `install.sh` takes `APP_PORT` (default 8080).
- The version (`APP_VERSION`) is reported by `/api/state`.
