# Feiyu Pocket 3 experimental setup

Feiyu support uses the same HTTP control API and Director panel as the DJI node, but a different camera transport:

- control: camera Wi-Fi, PTP/IP on TCP `15740`, plus joystick packets on UDP `7777`;
- video: a separate UVC or NDI source selected in the Director;
- node API: HTTP `8723` by default.

This backend was reconstructed from Feiyu Cam traffic and its packet builder is covered by captured-packet tests. It still needs validation on the target camera and firmware, so do not rely on it for an unrehearsed production.

## Start it

1. Turn on the Feiyu camera Wi-Fi and connect the Windows computer to that network. Use the password configured on the camera; the project does not store it.
2. Confirm that the camera is reachable at `192.168.1.1`.
3. Run `start-cameras.bat` and choose **3 · Feiyu Pocket 3 experimental**.
4. Start `Pocket3-Director.exe` and set a channel endpoint to `http://FEIYU_PC_IP:8723`.
5. In the channel's video selector, choose the matching external NDI source. The Feiyu node itself does not serve camera video.

From source, the equivalent command is:

```powershell
python feiyu-server.py --host 0.0.0.0 --port 8723 --camera-host 192.168.1.1 --label "FEIYU POCKET 3"
```

Open `http://127.0.0.1:8723/status` to diagnose the connection. A successful vendor heartbeat normally reports `heartbeat_code` as decimal `42497` (`0xA601`).

## Supported controls

| Feature | Feiyu backend |
|---|---|
| Continuous pan / tilt | Experimental |
| Stop and speed | Experimental |
| Zoom | Not available |
| Home / Set Home | Not available |
| Absolute position / Presets | Not available |
| Node-hosted UVC / OBS MJPEG | Not available |
| Director external NDI selection | Available |
| Software auto tracking | Disabled pending hardware validation |

Unsupported controls are disabled automatically in the shared Director panel.

## Network limits

The camera Wi-Fi commonly uses `192.168.1.1`. Running more than one Feiyu camera on one computer therefore requires separate Wi-Fi adapters and explicit routing/interface isolation; that setup is not automated. Keep the HTTP node ports on a trusted LAN and do not expose them directly to the Internet.

If the computer loses its normal LAN while joining camera Wi-Fi, use a second Ethernet or Wi-Fi adapter: one interface for the Feiyu camera, the other for the Director/NDI network.
