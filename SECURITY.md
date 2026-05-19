# Security Policy

## Reporting a vulnerability

If you believe you've found a security issue in Auditarr, **please do
not open a public GitHub issue**. Use one of the private channels
instead:

- GitHub's "Report a vulnerability" button at
  <https://github.com/ShogyX/Auditarr/security/advisories/new>
- Email the maintainers — see the address in `pyproject.toml` /
  the repo's profile.

We aim to acknowledge reports within five business days and ship a
fix or mitigation within thirty days for critical issues. Lower-
severity findings track the regular release cadence.

## What's in scope

- The FastAPI backend in `backend/`.
- The React/TypeScript frontend in `frontend/`.
- The bare-metal + Docker installers in `install-*.sh` and
  `docker/`.
- The updater scripts in `updater/`.

The CodeQL workflow (`.github/workflows/codeql.yml`) scans every PR
plus a weekly cron — findings show up under the repo's *Security*
tab. The dependency-review workflow flags packages with known
advisories on every PR.

## What's out of scope

- Issues that require physical access to a host already running
  Auditarr (the threat model assumes a trusted operator).
- Denial of service via deliberate misconfiguration of a connected
  integration (Sonarr, Radarr, Bazarr, etc.).
- Issues in third-party dependencies that are already disclosed
  upstream and on track for a patched release.

## Coordinated disclosure

Once a fix lands, we'll credit the reporter in the release notes
unless they prefer otherwise. CVE assignment is requested for any
issue with a CVSS v3 base score ≥ 7.0.
