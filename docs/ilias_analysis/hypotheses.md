# Hypotheses Status & Tracking

## Übersicht der Hypothesen

| # | Hypothese | Status | Verifikation / Evidenz |
|---|---|---|---|
| **H1** | ILIAS File-Downloads (`cmd=sendfile`) unterstützen HTTP Range Requests | **BEWIESEN** | Live-Test 2026-09-23: Request mit `Range: bytes=0-100` lieferte Status 206 Partial Content mit `Content-Range: bytes 0-100/20714`. |
| **H2** | Direkter Download über Standard-HTTP-Libraries (ohne Playwright) mit exportierten Cookies ist möglich | **BEWIESEN** | Live-Test 2026-09-23: `urllib.request` mit CookieJar aus `session.json` lud PDF fehlerfrei mit identischem SHA256 herunter. |
| **H3** | Die Hardware-Gerätebezeichnung im Live-Recorder-HLS-Endpunkt (`ext352`, etc.) korreliert mit dem Recorder-Namen in dist.opencast Thumbnails | **ABGELEITET / TEILWEISE BELEGT** | `H4-210-Aula-ext352` (Live-HLS) und `H4-extron-smp351-220` (Thumbnail) verweisen beide auf Gebäude H4 (Hauptgebäude) und Extron SMP Recorder. |
| **H4** | Live-Recording und veröffentlichter OpenCast-Podcast basieren auf derselben Primäraufnahme, durchlaufen aber getrennte Pipelines | **ABGELEITET** | Der Live-Stream liefert ungeschnittene Roh-TS-Segmente, während dist.opencast fertige MP4-Dateien mit Intro/Outro, Paella-Schnittpunkten und variablen Auflösungen liefert. |
| **H5** | OpenCast MP4-Downloads benötigen keine ILIAS-Authentifizierung | **BEWIESEN** | HAR-Analyse und HTTP-Tests belegen: dist.opencast MP4-URLs sind frei per HTTP abrufbar (CORS schränkt nur Browser ein). |
| **H6** | OpenCast Live-HLS-Streams sind ohne Login weltweit erreichbar | **BEWIESEN** | livedist.opencast.unibe.ch HLS-Playlists (`.m3u8`) und `.ts`-Segmente erfordern keinerlei Authentifizierung oder VPN. |

---

## Noch offene Forschungsfragen

1. **Gibt es einen internen API-Endpunkt, der OpenCast Event-IDs direkt auf den Aufnahme-Raum abbildet?**
   - Aktuell: Die Metadaten `location` im Paella-Player-JSON waren im Test `null`. Die Raumzuordnung ist aktuell nur über den Dateinamen des Thumbnails (`H4-extron-smp351-220...`) oder den Vorlesungsplan ableitbar.
   - Benötigte zusätzliche Daten: Weitere HAR-Dateien von Vorlesungen in anderen Hörsälen (z.B. Fabrikstrasse, UniS, ExWi), um die Namenskonvention aller Extron-Recorder zu kartieren.

2. **Existiert ein Kurs-weites Archiv-Download-Feature in ILIAS?**
   - ILIAS bietet im Webinterface für manche Ordner "Mehrere Objekte herunterladen" (ZIP-Export).
   - Dieser Endpunkt erfordert aber meist serverseitiges ZIP-Packen (kann bei grossen Kursen zu Timeouts führen).
   - Der rekursive Einzeldatei-Download über `discover_course` / `download_folder` ist stabiler und unterstützt inkrementelles Caching via ETag.
