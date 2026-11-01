# GitHub repository setup

## Suggested metadata

- Repository name: `Pocket-3-PTZ`
- Description: `LAN multi-camera PTZ controller and OBS preview bridge for DJI Osmo Pocket 3 on Windows.`
- Website: leave blank unless a documentation site is deployed.
- Topics: `dji`, `osmo-pocket-3`, `ptz`, `obs`, `ndi`, `uvc`, `directshow`, `livestreaming`, `python`, `windows`
- License shown by GitHub: MIT

## Recommended repository settings

- Default branch: `main`
- Enable Issues and Discussions.
- Disable Wikis unless they will be maintained.
- Enable private vulnerability reporting.
- Enable Dependabot security updates.
- Enable automatic deletion of merged branches.

## Branch protection for main

- Require pull request before merging.
- Require the `validate` CI job.
- Require conversation resolution.
- Block force pushes and branch deletion.
- For a solo-maintainer project, one approving review can remain optional.

## First publication

```powershell
git init
git add .
git commit -m "release: Pocket 3 PTZ LAN Controller v1.1.0"
git branch -M main
git remote add origin https://github.com/Evan931001/Pocket-3-4-4P-PTZ.git
git push -u origin main
```

Before running these commands, confirm the remote repository is empty or intentionally replacing its contents.

## Release v1.1.0

1. Build EXEs on Windows using `build-windows-exe.bat`.
2. Complete `docs/RELEASE_CHECKLIST.md`.
3. Create a source archive and a separate Windows binary archive.
4. Generate SHA-256 checksums:

```powershell
Get-FileHash .\Pocket-3-PTZ-LAN-v1.1.0-Windows.zip -Algorithm SHA256
```

5. Create tag `v1.1.0` and attach the binary archive plus checksum to the GitHub Release.

Do not commit generated EXEs to the main branch. Publish them only as Release assets.
