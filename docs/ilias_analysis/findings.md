# Konsolidierte Findings & Evidenz-Katalog

Dieses Dokument fasst alle Ergebnisse des Reverse Engineerings und der Live-Tests zusammen. Es unterscheidet strikt zwischen:
- **BEWIESEN**: Direkt in HAR-Logs, HTML-Snapshots, Sourcecode oder Live-Tests verifiziert.
- **ABGELEITET**: Starke technische Schlussfolgerung aus konsistenten Beobachtungen.
- **HYPOTHESE**: Noch zu verifizierende Annahme.

---

## 1. BEWIESEN (Verifizierte Fakten)

### A. ILIAS-Dateidownloads
1. **Download-Endpunkt**: `https://ilias.unibe.ch/ilias.php?baseClass=ilRepositoryGUI&cmdClass=ilObjFileGUI&cmd=sendfile&ref_id={ref_id}` liefert die Datei direkt mit HTTP 200 OK aus (keine Weiterleitung, kein externer Storage).
2. **Minimal-Parameter**: `baseClass=ilRepositoryGUI` und `cmd=sendfile` sowie `ref_id` sind erforderlich. `cmdClass=ilObjFileGUI` ist optional. Ohne `baseClass` antwortet ILIAS mit 404.
3. **Dateiname**: Wird ausschliesslich über den HTTP-Header `Content-Disposition: attachment; filename="..."` geliefert.
4. **Caching & Caching-Header**: ILIAS liefert sowohl `ETag` (z.B. `"50ea-65a6967672ca9"`) als auch `Last-Modified` und antwortet bei `If-None-Match` / `If-Modified-Since` mit `304 Not Modified`.
5. **Range Requests auf ILIAS**: ILIAS unterstützt für Dateidownloads HTTP Range Requests (`206 Partial Content`, getestet mit `Range: bytes=0-100` -> `Content-Range: bytes 0-100/20714`).
6. **HEAD-Requests**: Werden von ILIAS für `sendfile` unterstützt und liefern Metadaten (`Content-Length`, `Content-Disposition`, `ETag`, `Last-Modified`) ohne Datei-Body.
7. **Bypass des Browsers**: Mit Cookies aus `session.json` können Downloads 100% über Standard-HTTP (`urllib.request`) ohne Playwright-Browserprozess abgewickelt werden.

### B. Authentifizierung & Sessions
1. **Authentifizierungs-Kette**: SWITCH edu-ID -> Shibboleth SAML IdP (`aai-idp.unibe.ch/idp/shibboleth`) -> ILIAS Session.
2. **Erforderliche Cookies**: Für Zugriffe auf `ilias.unibe.ch` sind nur 3 Cookies nötig: `PHPSESSID`, `_shibsession_*` und `ilClientId=ilias3_unibe`.
3. **Session-Erkennung**: Bei abgelaufener Session liefert ILIAS HTTP 200 mit der Login-Seite (`login.php`). Eine gültige Session wird durch das Vorhandensein von Logout-Glyphen / Menüs erkannt, nicht durch den HTTP-Statuscode allein.

### C. OpenCast & Podcasts
1. **Plugin-Routing**: OpenCast-Objekte laufen über `baseClass=ilobjplugindispatchgui` mit `cmdClass=ilObjOpenCastGUI` (Eventliste) und `xoctPlayerGUI` (Player).
2. **Player-Metadaten**: Der Paella-Player wird im HTML über `player.init({...})` mit einem vollständigen JSON-Payload konfiguriert (Auflösungen, MP4-URLs, Tracks).
3. **Video-Hosting**: Alle MP4-Dateien liegen auf `dist.opencast.unibe.ch/static/mh_default_org/engage-player/{event_id}/{track_id}/{filename}.mp4`.
4. **Auth-Freiheit der Videos**: Die MP4-Dateien auf `dist.opencast.unibe.ch` sind **ohne ILIAS-Session frei per HTTP abrufbar**. CORS beschränkt nur den Browser.
5. **Range Requests / Resumability**: `dist.opencast.unibe.ch` unterstützt HTTP Range Requests (`Accept-Ranges: bytes`, Status 206), ETag und Last-Modified. Unterbrochene Downloads können fortgesetzt werden.
6. **Stream-Typen**: Es gibt `presentation` (Folien/Screen) und `presenter` (Kamera). Je nach Vorlesung existieren beide oder nur einer davon.
7. **Auflösungen**: Typischerweise 1080p, 720p, 480p.

### D. Livestreams
1. **HLS-Endpunkte**: Werden über `https://livedist.opencast.unibe.ch/hls/{Raum-Recorder}/index.m3u8` bereitgestellt.
2. **Öffentlich erreichbar**: Live-Streams erfordern keinerlei Authentifizierung oder VPN.
3. **Reales Beispiel**: `H4-210-Aula-ext352` (Privatrecht I, Hauptgebäude 4, Aula 210) und `Fab6-001-ext352` (Fabrikstrasse 6, Raum 001).

---

## 2. ABGELEITET (Technische Schlussfolgerungen)

1. **Hardware-Modell im Thumbnail-Namen**: In den Thumbnails von `dist.opencast.unibe.ch` taucht der Recorder-Name auf (z.B. `H4-extron-smp351-220_..._141.176s-player.jpg`). Dies verweist auf Hardware des Typs *Extron SMP 351/352*.
2. **Pipeline-Trennung**: Der HLS-Live-Stream (`livedist`) streamt Live-TS-Segmente direkt vom Extron-Recorder. Die spätere OpenCast-Veröffentlichung (`dist`) wird serverseitig encodiert, gemuxt und als Paella-Event publiziert. Es handelt sich um denselben Inhalt, aber unterschiedliche Datei-Artefakte.

---

## 3. HYPOTHESE (Forschungsfragen)

1. **Raum-Mapping ohne Vorlesungsplan**: Kann man allein aus einer OpenCast Event-ID den physischen Hörsaal vorhersagen?
   - Aktuell nur möglich, wenn das Thumbnail bereits generiert wurde (da dort der Gerätename im Dateinamen steht), oder wenn der Vorlesungsplan den Raum angibt.
