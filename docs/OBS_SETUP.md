# OBS Setup

## Recommended: Media Source

1. Start the camera nodes.
2. In OBS, add **Media Source**.
3. Uncheck **Local File**.
4. Enter `http://CAMERA_PC_IP:8723/obs.mjpg`.
5. If available, set input format to `mjpeg`, reconnect delay to one second, and network buffering to the minimum stable value.

Use ports `8724–8727` for Cameras 2–5.

## Browser Source fallback

Use `http://CAMERA_PC_IP:8723/obs`, width `1280`, height `720`, and custom FPS `30`. Browser rendering may be less smooth than Media Source.

## Important

- Do not add the same Pocket device as an OBS Video Capture Device while the node owns UVC.
- Do not enable OBS-exclusive mode when consuming the node's `/obs.mjpg`; that mode intentionally releases UVC.
- For multi-camera broadcasts, use wired Gigabit Ethernet and distribute cameras across independent USB root hubs.

## Synchronization

MJPEG paths may have different buffering. Measure with a running stopwatch in all cameras, then add OBS Render Delay to faster sources so all sources match the slowest one. Repeat several captures before fixing offsets.
