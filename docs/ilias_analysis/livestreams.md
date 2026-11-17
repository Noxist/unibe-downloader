# Live Stream Analysis

Analysis of live lecture streaming via OpenCast.

## HLS Endpoints

Live streams are served via HTTP Live Streaming (HLS) from `livedist.opencast.unibe.ch`.

*   **Endpoint Pattern**: `https://livedist.opencast.unibe.ch/hls/{room-recorder}/index.m3u8`
*   **Authentication**: UNAUTHENTICATED. No login or cookies are required to access these streams.

### Known Endpoints

*   `https://livedist.opencast.unibe.ch/hls/H4-210-Aula-ext352/index.m3u8` (Privatrecht I)
*   `https://livedist.opencast.unibe.ch/hls/Fab6-001-ext352/index.m3u8` (User-reported)

## Naming Conventions

The room recorder identifier in the URL follows the pattern: `{building}-{room}-{device}`

*   **H4**: Hauptgebäude 4
*   **Fab6**: Fabrikstrasse 6
*   **ext352**: Extron SMP 352 recorder (hardware)

*Note: The recorder name can sometimes be found in OpenCast thumbnails, e.g., `H4-extron-smp351-220`.*

## Live to Podcast Pipeline

The lifecycle of a recording is as follows:

1.  **Capture**: Room recorder captures the lecture and serves an HLS stream (`livedist`).
2.  **Local Recording (Automation)**: `live_recorder.py` polls the `.m3u8` playlist and saves `.ts` segments.
3.  **Merge**: FFmpeg is used to merge `.ts` segments into an `.mp4` container (`-c copy` to avoid re-encoding).
4.  **Local Integration**: The resulting MP4 is placed into the local `Lectures/` folder.
5.  **Official Processing**: Independently, OpenCast processes the room recording.
6.  **Publication**: The processed video is published as an OpenCast event on `dist.opencast`.
7.  **ILIAS Integration**: The event appears in the ILIAS podcast list.

## Hypotheses

*   **HYPOTHESIS**: The device ID used in the live recorder URL correlates directly with the `dist.opencast` recorder name found in thumbnail URLs.
*   **HYPOTHESIS**: The live recording captured manually and the later OpenCast event reference the same content, but are processed through entirely different pipelines (local FFmpeg vs. official OpenCast server processing).
