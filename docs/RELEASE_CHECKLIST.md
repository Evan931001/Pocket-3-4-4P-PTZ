# Release checklist

- [ ] Update version and `CHANGELOG.md`.
- [ ] Run Python syntax and lint checks.
- [ ] Build all three EXEs on clean Windows 10/11.
- [ ] Test one Pocket 3 and a three-camera configuration.
- [ ] Verify ports 8723–8727 and `manage-firewall.bat`.
- [ ] Test Director, Direct UVC, NDI, presets, macros, and keyboard control.
- [ ] Test OBS Media Source at `/obs.mjpg`.
- [ ] Verify closing Master Console stops hidden nodes.
- [ ] Scan repository and release archive for configs, logs, private IPs, recordings, and secrets.
- [ ] Attach checksums to the GitHub Release.
