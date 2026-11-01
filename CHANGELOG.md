# Changelog

All notable changes are documented here. This project follows Semantic Versioning where practical.

## [Unreleased]

- GitHub community files, CI, documentation, dependency manifests, and repository hygiene.
- Consolidated ten overlapping launch/firewall/autostart batch files into three menu-driven tools.

## [1.1.0] - 2026-11-01

### Added

- Multi-camera launcher with ports 8723–8727.
- Three-channel Blackmagic-style director console.
- Twenty named presets per camera and three-camera scene macros.
- Timed moves with easing curves.
- Direct UVC LAN and NDI previews.
- Shared 720p MJPEG output for OBS.
- Runtime UVC release mode for OBS-exclusive workflows.
- Black-frame detection and per-camera capture-mode fallback.
- Single master console with hidden child nodes.

### Fixed

- Repeated browser opening and background restart behavior.
- Third-camera preview reconnection.
- Duplicate camera endpoint selection.
- Batch-file encoding/build reliability.
