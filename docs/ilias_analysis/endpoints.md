# Endpoints

Structured list of all observed endpoints grouped by service.

## ILIAS Core Endpoints

| Purpose | Method | URL Template | Auth Required |
| :--- | :--- | :--- | :--- |
| **File Download** | GET | `ilias.php?baseClass=ilRepositoryGUI&cmdClass=ilObjFileGUI&cmd=sendfile&ref_id={ref}` | Yes |
| **Course Page** | GET | `ilias.php?baseClass=ilRepositoryGUI&cmdClass=ilobjcoursegui&ref_id={ref}` | Yes |
| **Folder View** | GET | `ilias.php?baseClass=ilRepositoryGUI&cmdClass=ilobjfoldergui&ref_id={ref}` | Yes |

## OpenCast / Podcast Endpoints (ILIAS Plugin)

| Purpose | Method | URL Template | Auth Required |
| :--- | :--- | :--- | :--- |
| **Event List** | GET | `ilias.php?cmdClass=ilObjOpenCastGUI&cmd=showContent&ref_id={podcast_ref}` | Yes |
| **Pagination** | GET | `ilias.php?cmdClass=xoctEventGUI&cmd=index&ref_id={ref}&page={page_num}` | Yes |
| **Player Frame** | GET | `ilias.php?cmdClass=xoctPlayerGUI&cmd=streamVideo&ref_id={ref}&eid={event_id}` | Yes |

## External OpenCast Delivery (`dist.opencast`)

| Purpose | Method | URL Template | Auth Required |
| :--- | :--- | :--- | :--- |
| **Video MP4** | GET | `https://dist.opencast.unibe.ch/static/mh_default_org/engage-player/{event_id}/{track_id}/{filename}.mp4` | No |
| **Thumbnail** | GET | `https://dist.opencast.unibe.ch/static/mh_default_org/engage-player/{event_id}/{attachment_id}/{recorder}_{timestamp}-player.jpg` | No |

## External Live Streaming (`livedist.opencast`)

| Purpose | Method | URL Template | Auth Required |
| :--- | :--- | :--- | :--- |
| **HLS Playlist** | GET | `https://livedist.opencast.unibe.ch/hls/{building}-{room}-{device}/index.m3u8` | No |

## Tracking & Analytics

| Purpose | Method | URL Template | Auth Required |
| :--- | :--- | :--- | :--- |
| **Matomo** | GET/POST | `https://webstats.opencast.unibe.ch/matomo.php?...` | No |
