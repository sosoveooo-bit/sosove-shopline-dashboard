# Debian ARM64 NAS Installer Adaptation

## Observed Environment

The user supplied Debian 12 (bookworm), Docker 28.5.2 linux/arm64, a working `docker compose` plugin, and DockerRootDir `/vol1/docker`. `/vol1` has about 142 GB available, while the system root has about 1.5 GB. No SSH connection or change to the user's NAS is part of this task.

## Design

- Use explicit `SOSOVE_INSTALL_DIR=/vol1/sosove-dashboard-docker` so application files are on the observed data volume. Leave existing Docker storage, NAS management and other containers untouched.
- Add a reuse-only mode that fails if the NAS Docker/Compose/daemon is unavailable instead of changing packages or starting its daemon. Existing downloader/configuration helper prerequisites may be installed if missing.
- Select Debian or Ubuntu APT source only when installing Docker on a fresh supported host. Supported variants are Debian 12/13 and existing Ubuntu LTS versions.
- Derive native build platform from Docker server architecture and continue using source builds to avoid GHCR credentials. Do not change package visibility or advertise the amd64-only published image as multiarch.
- Check application-volume and Docker-volume space separately. Do not clear caches, prune images or move storage automatically.

## Validation

- Shell regression tests for distro selection, no Docker/service changes in NAS mode, architecture mapping, unsupported distro rejection and data-volume-root refusal.
- Actual Debian 12 container OS detection, plus native ARM64 Linux image build and container health/background-snapshot tests in GitHub Actions.
- Existing amd64 pipeline, Python and JavaScript regressions remain required before image publication.
