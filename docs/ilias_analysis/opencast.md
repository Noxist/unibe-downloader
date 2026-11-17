# OpenCast Integration Analysis

## Evidence Classification
- **BEWIESEN** = Directly observed in HAR/HTML/Code
- **ABGELEITET** = Strong technical inference
- **HYPOTHESE** = Needs verification

## ILIAS OpenCast Plugin (BEWIESEN)

The ILIAS instance uses the `OpenCast` repository object plugin located at:
```
Customizing/global/plugins/Services/Repository/RepositoryObject/OpenCast/
```

### Endpoint Chain

```
1. Podcast List Page (cmdClass=ilObjOpenCastGUI)
   GET ilias.php?baseClass=ilobjplugindispatchgui
       &cmdNode=qp:qi
       &cmdClass=ilObjOpenCastGUI
       &cmd=showContent
       &ref_id={podcast_ref}
   → 302 redirect to xoctEventGUI

2. Event List (cmdClass=xoctEventGUI)  
   GET ilias.php?baseClass=ilobjplugindispatchgui
       &cmdNode=qp:qi:17g
       &cmdClass=xoctEventGUI
       &cmd=index
       &ref_id={podcast_ref}
       &page={n}           ← pagination

3. Player Page (cmdClass=xoctPlayerGUI)
   GET ilias.php?baseClass=ilobjplugindispatchgui
       &cmdNode=qp:qi:17g:17u
       &cmdClass=xoctPlayerGUI
       &cmd=streamVideo
       &ref_id={podcast_ref}
       &eid={event_uuid}
```

### Player JSON Extraction (BEWIESEN)

The player HTML page contains a Paella player initialization call.
The JSON config is extracted by searching for `player.init(` in the HTML source.

```python
# From podcast_sync.py L20-27:
marker = 'player.init('
start = html.find(marker)
start += len(marker)
value, _ = json.JSONDecoder().raw_decode(html[start:].lstrip())
```

### Player JSON Structure (BEWIESEN — from inventory.json)

```json
{
  "streams": [
    {
      "content": "presenter",        // Camera/speaker view
      "sources": {
        "mp4": [
          {
            "src": "https://dist.opencast.unibe.ch/static/mh_default_org/engage-player/{event_id}/{track_id}/{filename}.mp4",
            "res": {"w": 1920, "h": 1080},
            "mimetype": "video/mp4"
          },
          {"src": "...", "res": {"w": 1280, "h": 720}, "mimetype": "video/mp4"},
          {"src": "...", "res": {"w": 854, "h": 480}, "mimetype": "video/mp4"}
        ]
      }
    },
    {
      "content": "presentation",      // Slides/screen capture
      "sources": {"mp4": [...]}
    }
  ],
  "metadata": {
    "title": "...",
    "duration": 5700000             // milliseconds
  },
  "frameList": [
    {"id": "...", "mimetype": "image/jpeg", "time": 0, "url": "..."},
    {"id": "...", "mimetype": "image/jpeg", "time": 120, "url": "..."}
  ],
  "captions": [],
  "transcriptions": []
}
```

### Stream Content Types (BEWIESEN)

| Content Type | Description | Example |
|---|---|---|
| `presenter` | Camera/speaker view | Strafrecht lectures |
| `presentation` | Slides/screen capture | Grundrechte lectures |

Some events have BOTH streams, some only one.

## dist.opencast.unibe.ch (BEWIESEN)

### MP4 Distribution URLs

```
https://dist.opencast.unibe.ch/static/mh_default_org/engage-player/{event_id}/{track_id}/{filename}.mp4
```

**Example:**
```
https://dist.opencast.unibe.ch/static/mh_default_org/engage-player/
  b57bcbbc-e4f4-48e1-aedf-807f788dedc5/
  23d3e0d1-3b27-4b6a-a04d-e6dc9543252e/
  presentation-107771be-2b6f-4bec-acaa-3698348309bb.mp4
```

### Response Headers (BEWIESEN — from HAR)

| Header | Value | Significance |
|---|---|---|
| `Accept-Ranges` | `bytes` | **Range Requests supported!** |
| `ETag` | `"6ab230e5-284168da"` | Conditional requests possible |
| `Last-Modified` | `Tue, 22 Sep 2026 07:40:21 GMT` | If-Modified-Since possible |
| `Content-Length` | `675375322` (644 MiB) | Size known in advance |
| `Content-Type` | `video/mp4` | |
| `Content-Range` | `bytes 0-675375321/675375322` | Partial content delivered |
| `Access-Control-Allow-Origin` | `https://ilias.unibe.ch` | CORS — browser-only restriction |
| `Access-Control-Allow-Methods` | `GET, POST, OPTIONS` | |
| `Server` | `nginx` | |

### Authentication (BEWIESEN)

**dist.opencast.unibe.ch MP4 URLs are UNAUTHENTICATED.**

The CORS header restricts browser access to `https://ilias.unibe.ch`, but direct HTTP requests (e.g., via urllib, curl, wget) are NOT blocked. This means:
- Podcast MP4 files can be downloaded without ILIAS session cookies
- Only the event list and player page (on ilias.unibe.ch) require authentication
- Once you have the MP4 URL, downloads can use plain HTTP

### Thumbnail URLs (BEWIESEN)

```
https://dist.opencast.unibe.ch/static/mh_default_org/engage-player/
  {event_id}/{attachment_id}/{recorder_name}_{timestamp}-player.jpg
```

**Example:**
```
H4-extron-smp351-220_HS2026--_ffentliches_20260921-141300_S1R1_141.176s-player.jpg
```

Thumbnail naming reveals:
- Recorder device name: `H4-extron-smp351-220`
- Semester: `HS2026`
- Recording date/time: `20260921-141300`
- Stream/Recorder: `S1R1`
- Timestamp in recording: `141.176s`

### Resolutions Available (BEWIESEN)

- 1920×1080 (Full HD)
- 1280×720 (HD)
- 854×480 (SD)

## Observed Event IDs (BEWIESEN)

| Event ID | Course | Date |
|---|---|---|
| `b57bcbbc-e4f4-48e1-aedf-807f788dedc5` | Grundrechte | 2026-09-21 |
| `76e946c6-ef3a-4c3a-b636-05e59b20bb36` | Grundrechte | 2026-09-14 |
| `dd123549-21e8-46c4-a8f0-c29134493de2` | Strafrecht | 2026-09-21 |
| `87908574-211b-4ac8-b0d0-38a99b302a7c` | Strafrecht | 2026-09-16 |
| `9e2b84bc-7ed7-4627-b433-cff067ea89b3` | — | Tutorial: "Podcasts optimal nutzen" |

## Observed Series ID (BEWIESEN — from webstats)

```
408c9877-2a99-40f5-9e6e-20e2497165cf  →  Grundrechte series
```

Discovered via webstats tracking pixel:
```
GET https://webstats.opencast.unibe.ch/3012.php
  ?ma_ti={event_id}          ← dimension1
  &dimension2={series_id}    ← series ID
  &ma_pn=html5video
  &ma_mt=Video
```

## Paella Player Config (BEWIESEN)

Static config at:
```
https://ilias.unibe.ch/Customizing/global/plugins/Services/Repository/RepositoryObject/OpenCast/js/opencast/src/Paella/config/config.json
```

## Event CSS/Icons (BEWIESEN)

```
https://ilias.unibe.ch/Customizing/global/plugins/Services/Repository/RepositoryObject/OpenCast/templates/default/events10.css
https://ilias.unibe.ch/Customizing/global/plugins/Services/Repository/RepositoryObject/OpenCast/templates/images/icon_xoct.svg
```
