# Troubleshooting

## PTZ online but preview is black

- Wait for automatic mode fallback.
- Inspect `/status` and check `capture_mode` and `preview_status`.
- Stop any app opening the same UVC camera directly.
- Move the camera to another USB root hub.

## OBS cannot open the stream

- Test `http://CAMERA_PC_IP:PORT/status` from the director computer.
- Run `manage-firewall.bat` and choose the allow-LAN option.
- Confirm the correct port and private IPv4 address.
- Use `/obs.mjpg` with Media Source or `/obs` with Browser Source.

## Low FPS or high latency

- Prefer Media Source over Browser Source.
- Check whether capture fell back to 15fps.
- Use wired Ethernet.
- Avoid multiple consumers of the same high-quality stream.
- Check CPU usage and USB controller bandwidth.

## Ports already in use

Close old Launcher/Node processes. The current launcher stops its hidden children when it exits, but older builds may leave nodes running.

## NDI source missing

Confirm NDI Runtime/Tools and the Python NDI binding are installed, both computers are on a discoverable network, and firewall rules allow NDI traffic. Direct UVC LAN remains available without NDI.
