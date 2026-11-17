# ILIAS Live Recorder auf einem Linux-Server

Dieses Dokument ist fuer die Uebergabe an einen Copilot oder Administrator auf dem Linux-Server gedacht. Es beschreibt die getestete Quelle, die Termin-Erkennung aus den HTML-Exporten, die Europe/Zurich-Zeitzone, die Installation, den fertigen Ablauf und die Fehlerdiagnose.

## Ziel

Der Server soll die Vorlesungen aus einem lokalen ILIAS-HTML-Export automatisch aufnehmen.

Der Ablauf ist:

1. Die exportierten HTML-Seiten werden lokal gelesen.
2. Aus den strukturierten ILIAS-Ereignisbezeichnern werden Datum und Startzeit gelesen.
3. Alle Termine werden als Europe/Zurich interpretiert.
4. Der Recorder startet standardmaessig fuenf Minuten vor der geplanten Startzeit oder nimmt einen bereits laufenden Termin sofort auf.
5. Die HLS-Playlist wird ohne Browser und ohne SWITCH-Session geladen.
6. Die nummerierten MPEG-TS-Segmente werden ab Segment 0 und danach fortlaufend lokal gespeichert.
7. Der Lauf endet bei `#EXT-X-ENDLIST`, nach 180 Sekunden ohne neue Segmente oder spaetestens nach 150 Minuten.
8. Danach werden die Segmente ohne Neucodierung nach MP4 gemuxt.

Der aktuelle Stand wurde am 15. September 2026 mit dem Stream der Universitaet Bern getestet.

## Bekannte Quelle

Aktuelle HLS-Adresse:

```text
https://livedist.opencast.unibe.ch/hls/H4-210-Aula-ext352/index.m3u8
```

Der direkte Abruf wurde ohne SWITCH edu-ID, Cookies, Referer oder Browser erfolgreich getestet:

```text
HTTP 200
Content-Type: application/vnd.apple.mpegurl
```

Die URL ist eine Raum-/Encoder-Adresse. Sie darf fuer weitere Termine nur wiederverwendet werden, wenn der Servertest und die erste Segmentdatei erfolgreich sind. Eine ILIAS-`eid` und eine Segmentnummer wie `2686.ts` sind keine dauerhaften HLS-Adressen.

Auf einem anderen Netz oder zu einem anderen Termin kann sich die URL aendern. In diesem Fall muss die `--url`-Konfiguration angepasst werden. Das Skript versucht nicht, unbekannte Raum- oder Streamnamen zu erraten.

## HTML-Export

Die vier Dateien aus dem Browserexport muessen auf den Server kopiert werden, zum Beispiel:

```text
/srv/ilias-live/html/page-0.html
/srv/ilias-live/html/page-1.html
/srv/ilias-live/html/page-2.html
/srv/ilias-live/html/page-3.html
```

Die echten Exporte enthalten strukturierte Elemente wie:

```html
<div class="c-entity__primary-identifier">
  HS2026: Privatrecht I: OR Allgemeiner Teil 1 - 2026-09-15 14:15
</div>
```

Das Skript verwendet genau diese strukturierten Werte und scannt nicht blind das gesamte SingleFile-Dokument. Das ist wichtig, weil SingleFile CSS, JavaScript und Metadaten mit vielen weiteren Datums- und Zeitwerten einbettet.

Aus dem aktuellen Export wurden 40 Termine erkannt:

- 15.09.2026 bis 22.10.2026: dienstags um 14:15, mittwochs und donnerstags um 10:15
- 27.10.2026 bis 26.11.2026: dienstags um 14:15, mittwochs und donnerstags um 10:15
- 03.12.2026 bis 17.12.2026: gemass Export dienstags um 14:15 sowie mittwochs und donnerstags um 10:15

Der vollstaendige Terminplan bleibt im HTML die Quelle der Wahrheit. Die Zusammenfassung oben dient nur zur Kontrolle. Ausfaelle, Verschiebungen und zusaetzliche Termine muessen durch einen neuen HTML-Export beruecksichtigt werden.

## Dateien auf dem Server

Empfohlene Struktur:

```text
/srv/ilias-live/
  linux_lecture_recorder.py
  html/
    page-0.html
    page-1.html
    page-2.html
    page-3.html
  recordings/
  logs/
```

Das fertige Skript befindet sich im Projekt als `linux_lecture_recorder.py`. Es benoetigt keine Python-Zusatzpakete.

## Installation auf Debian oder Ubuntu

```bash
sudo apt-get update
sudo apt-get install -y python3 ffmpeg ca-certificates tzdata rclone
sudo install -d -m 0750 /srv/ilias-live/html /srv/ilias-live/recordings /srv/ilias-live/logs
sudo install -m 0750 linux_lecture_recorder.py /srv/ilias-live/linux_lecture_recorder.py
```

Der Systembenutzer sollte nur auf das Aufnahmeverzeichnis Zugriff haben:

```bash
sudo useradd --system --home /srv/ilias-live --shell /usr/sbin/nologin recorder || true
sudo chown -R recorder:recorder /srv/ilias-live
sudo chmod 0750 /srv/ilias-live
sudo chmod 0700 /srv/ilias-live/html /srv/ilias-live/recordings /srv/ilias-live/logs
```

Die vier HTML-Dateien danach als Benutzer `recorder` nach `/srv/ilias-live/html` kopieren. Falls sie von einem anderen System uebertragen werden, sollten sie als UTF-8 erhalten bleiben.

## Erstes Testen

Zuerst nur den Terminparser ausfuehren. Dieser laedt noch keine Segmente:

```bash
cd /srv/ilias-live
sudo -u recorder python3 linux_lecture_recorder.py \
  --html-dir /srv/ilias-live/html \
  --url 'https://livedist.opencast.unibe.ch/hls/H4-210-Aula-ext352/index.m3u8' \
  --list-only
```

Erwartet werden 40 Eintraege mit ISO-Zeitstempeln und `+02:00` im Sommer beziehungsweise `+01:00` im Winter. Das Skript verwendet `Europe/Zurich`; die Sommer-/Winterzeit wird nicht durch eine manuell feste Serverzeitzone bestimmt.

Danach die HLS-Adresse auf dem Server pruefen:

```bash
python3 - <<'PY'
import urllib.request
url = 'https://livedist.opencast.unibe.ch/hls/H4-210-Aula-ext352/index.m3u8'
with urllib.request.urlopen(url, timeout=20) as response:
    body = response.read().decode('utf-8-sig')
    print(response.status)
    print(response.headers.get('Content-Type'))
    print(body[:300])
assert body.startswith('#EXTM3U')
PY
```

Ein Status `200` mit `application/vnd.apple.mpegurl` ist erforderlich. `401`, `403`, `404`, HTML statt HLS oder Timeout sind unterschiedliche Fehler und duerfen nicht automatisch als SWITCH-Loginproblem interpretiert werden. Ein Login auf ILIAS kann fuer die Webseite erforderlich sein, waehrend der Opencast-HLS-Server den Stream direkt ausliefert.

## Einmaliger Lauf

Das Skript verarbeitet standardmaessig den aktuell laufenden Termin (falls vorhanden) und danach alle zukuenftigen Termine im Export nacheinander. Es wartet selbst bis zum naechsten Termin. Der Vorlauf betraegt fuenf Minuten. Beim ersten Playlist-Abruf werden ausserdem die direkt erreichbaren nummerierten Segmente ab `0.ts` nachgeladen, damit ein spaeter gestarteter Recorder die Aufnahme ab Minute 0 rekonstruieren kann:

```bash
cd /srv/ilias-live
sudo -u recorder python3 linux_lecture_recorder.py \
  --html-dir /srv/ilias-live/html \
  --url 'https://livedist.opencast.unibe.ch/hls/H4-210-Aula-ext352/index.m3u8' \
  --output /srv/ilias-live/recordings \
  --pre-roll-minutes 5 \
  --max-minutes 150 \
  --idle-seconds 180 \
  --poll-seconds 5 \
  --backfill-start 0
```

`--output` ist der frei waehlbare Speicherstamm. Darunter wird pro Termin ein eigenes Verzeichnis mit Datum und Titel angelegt, zum Beispiel `recordings/2026-09-17_HS2026_Privatrecht_I_OR_Allgemeiner_Teil_1/`. Mit `--no-backfill` kann das Nachladen alter Segmentnummern abgeschaltet werden, falls ein anderer HLS-Server keine historische Segmentdatei mehr anbietet.

## Google Drive und lokale Aufbewahrung

Der Recorder kann nach erfolgreichem MP4-Export optional ueber `rclone` nach Google Drive kopieren. Es werden nur `lecture.mp4` und `ffprobe.json` hochgeladen; die vielen temporaeren MPEG-TS-Segmente bleiben lokal und werden erst nach der Aufbewahrungsfrist geloescht. Ein fehlgeschlagener Upload loescht nichts.

Einmalig als derselbe Benutzer, unter dem der Recorder laeuft, `rclone config` ausfuehren, einen Google-Drive-Remote mit dem Namen `gdrive` anlegen und den Zielordner testen:

```bash
rclone lsd gdrive:
rclone mkdir 'gdrive:ILIAS Live Recordings'
```

Danach den Recorder mit diesen Optionen starten:

```bash
--gdrive-remote gdrive \
--gdrive-path 'ILIAS Live Recordings' \
--local-retention-hours 24
```

Nach einem erfolgreichen Upload steht `status=uploaded` in `state.json`. Nach 24 Stunden wird der lokale Terminordner beim naechsten Service-Durchlauf entfernt; die MP4-Datei bleibt in Google Drive erhalten. Fuer einen sofortigen lokalen Loeschlauf kann `--local-retention-hours 0` verwendet werden.

Fuer einen einzelnen Termin kann der erkannte Index verwendet werden:

```bash
sudo -u recorder python3 /srv/ilias-live/linux_lecture_recorder.py \
  --html-dir /srv/ilias-live/html \
  --url 'https://livedist.opencast.unibe.ch/hls/H4-210-Aula-ext352/index.m3u8' \
  --output /srv/ilias-live/recordings \
  --event-index 0
```

Der Index muss vorher mit `--list-only` geprueft werden. Fuer den normalen Serverbetrieb ist kein `--event-index` noetig.

## Ende-Erkennung

Der Stream ist eine Live-Media-Playlist. Der Recorder beendet einen Termin bei einem dieser Ereignisse:

1. Die Playlist enthaelt `#EXT-X-ENDLIST`.
2. Drei Minuten lang erscheint kein neues Segment (`--idle-seconds 180`).
3. Die Sicherheitsfrist `Startzeit + 150 Minuten` wird erreicht (`--max-minutes 150`).
4. Sechs aufeinanderfolgende Playlist-Anfragen schlagen fehl.

Die Sicherheitsfrist ist absichtlich vorhanden. Ein Live-Server kann ein Endsignal auslassen oder eine fehlerhafte Playlist liefern. Ohne Frist koennte der Prozess unbegrenzt weiterlaufen.

Wenn die Vorlesung regelmaessig deutlich laenger oder kuerzer ist, kann `--max-minutes` angepasst werden. Die Frist sollte nicht pauschal auf viele Stunden erweitert werden, weil dieselbe Raum-/Encoder-Adresse spaeter einen anderen Termin liefern kann.

## Ergebnisdateien

Fuer jeden Termin entsteht ein Ordner wie:

```text
recordings/2026-09-17_HS2026_Privatrecht_I_OR_Allgemeiner_Teil_1/
  segments/
    00000000.ts
    00000001.ts
    ...
  latest.m3u8
  state.json
  local.m3u8
  lecture.mp4
  ffprobe.json
```

`segments/` enthaelt die Originaldaten. `latest.m3u8` ist die zuletzt vom Server gelieferte Playlist. `state.json` erlaubt die Diagnose und die Wiederaufnahme. `local.m3u8` verweist nur auf die lokalen Dateien. `lecture.mp4` ist ein Containerwechsel ohne Neucodierung. `ffprobe.json` dokumentiert Video- und Audiostream.

Die Originalsegmente sollten erst geloescht werden, nachdem das MP4 geprueft wurde. Sie sind bei Fehleranalyse und erneutem Export wertvoll.

## systemd-Betrieb

Im Projekt liegt zusaetzlich `ilias-live.service` fuer einen User-Service. Die lokale `schedule/schedule.html` ist eine Fallback-Terminquelle aus den unten dokumentierten Zeiten, solange die echten vier ILIAS-HTML-Exporte noch nicht kopiert wurden. Sobald echte Exporte vorliegen, diese nach `schedule/` kopieren; sie ersetzen die Fallback-Datei als Terminquelle.

Installation als Benutzer `leandro`:

```bash
mkdir -p ~/.config/systemd/user
cp /home/leandro/services/unibe-downloader/systemd/ilias-live.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ilias-live.service
systemctl --user status ilias-live.service
```

Falls der Dienst auch ohne aktive Login-Sitzung laufen soll, einmalig als Administrator aktivieren:

```bash
sudo loginctl enable-linger leandro
```

Logs anzeigen:

```bash
journalctl --user -u ilias-live.service -f
```

Datei `/etc/systemd/system/ilias-live.service`:

```ini
[Unit]
Description=ILIAS live lecture recorder
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=recorder
Group=recorder
WorkingDirectory=/srv/ilias-live
ExecStart=/usr/bin/python3 /srv/ilias-live/linux_lecture_recorder.py --html-dir /srv/ilias-live/html --url https://livedist.opencast.unibe.ch/hls/H4-210-Aula-ext352/index.m3u8 --output /srv/ilias-live/recordings --pre-roll-minutes 5 --max-minutes 150 --idle-seconds 180 --poll-seconds 5 --gdrive-remote gdrive --gdrive-path 'ILIAS Live Recordings' --local-retention-hours 24
Restart=on-failure
RestartSec=30
UMask=0077
```

Starten:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ilias-live.service
sudo systemctl status ilias-live.service
journalctl -u ilias-live.service -f
```

Diese Variante startet den Prozess einmal. Er verarbeitet danach den laufenden und alle zukuenftigen Termine aus dem Export und wartet zwischen den Terminen. Nach einem Serverneustart werden bereits beendete Termine uebersprungen; der laufende oder naechste Termin kann bei Bedarf mit `--event-index` manuell gestartet werden. Fuer einen stabilen Dauerbetrieb sollte vor jedem Semester ein frischer HTML-Export erstellt werden.

Ein Stoppen erfolgt kontrolliert mit:

```bash
sudo systemctl stop ilias-live.service
```

Ein `STOP`-Dateimechanismus ist in diesem vereinfachten Serverprogramm nicht eingebaut; systemd sendet beim Stoppen das normale Terminationssignal. Bereits gespeicherte Segmente bleiben erhalten.

## Speicherbedarf und Aufbewahrung

Der Bedarf haengt von der Bitrate ab. Bei 1080p sollte vorab grosszuegig freier Platz eingeplant werden. Segmentarchiv und MP4 existieren nach dem Export gleichzeitig. Pruefen:

```bash
df -h /srv/ilias-live
 du -sh /srv/ilias-live/recordings
```

Dateirechte bleiben durch `User=recorder` und `UMask=0077` privat. Die HLS-URL ist nicht geheim, kann aber den Inhalt einer Vorlesung bereitstellen. Sie sollte nicht unkontrolliert veroeffentlicht werden.

## MP4-Pruefung

```bash
ffprobe -v error -show_streams -show_format -of json recordings/TERMIN/lecture.mp4
ffmpeg -hide_banner -v error -i recordings/TERMIN/lecture.mp4 -f null -
```

Der zweite Befehl dekodiert die gesamte Datei und meldet kaputte Pakete. Zusaetzlich Anfang, Mitte und Ende manuell anhoeren. Das Vorhandensein eines Audiostreams garantiert nicht, dass Sprache verstaendlich aufgenommen wurde.

## Fehlerdiagnose

### Keine Termine erkannt

```bash
python3 linux_lecture_recorder.py --html-dir /srv/ilias-live/html --url URL --list-only
```

Pruefen:

- Liegen Dateien mit `.html` oder `.htm` im angegebenen Verzeichnis?
- Sind die Dateien vollstaendige SingleFile-Exporte?
- Enthalten sie `c-entity__primary-identifier`?
- Stimmen Datumsformat und Jahr?

### Playlist nicht erreichbar

Vom Server direkt testen. Ein Browserlogin behebt keine fehlende Netzroute, keinen VPN-Zwang und keinen DNS- oder TLS-Fehler. Falls der Zugriff nur aus dem Uni-Netz funktioniert, muss der Server eine autorisierte Netzverbindung oder VPN-Konfiguration erhalten.

### Aufnahme stoppt zu frueh

`journalctl -u ilias-live.service` pruefen. Ein Idle-Timeout kann durch einen pausierten oder gestoppten Stream entstehen. Wenn das Ende sicher spaeter kommt, `--idle-seconds` vorsichtig erhoehen. Die Sicherheitsfrist nicht unkontrolliert entfernen.

### Sequenzluecke oder fehlendes Segment

Das Programm exportiert nicht, wenn Segmentnummern fehlen oder eine Datei nicht gespeichert wurde. Der Ordner bleibt erhalten. Netzwerk- und Serverfehler pruefen und denselben Termin erneut ausfuehren. Bereits vorhandene nichtleere Segmente werden wiederverwendet.

### HLS-URL spaeter anders

Den direkten URL-Test wiederholen und die `--url`-Option bzw. die systemd-Service-Datei anpassen. Die URL darf nicht aus einer einzelnen Segmentnummer oder nur aus einer ILIAS-`eid` konstruiert werden.

## Sicherheit und Berechtigung

Nur Streams aufnehmen, fuer die eine ausdrueckliche Berechtigung besteht. Das Skript umgeht keinen DRM-Schutz und implementiert keine SWITCH-Authentifizierung auf Linux. Es verwendet hier den technisch beobachteten, direkt erreichbaren HLS-Endpunkt. Falls die Hochschule spaeter Cookies, Token, VPN oder andere Zugriffskontrollen verlangt, muss die autorisierte Zugriffsmethode mit der Hochschulrichtlinie abgestimmt werden.

## Kurzauftrag fuer den Server-Copilot

Diesen Text kann man zusammen mit `linux_lecture_recorder.py` an den Copilot auf dem Linux-Server geben:

> Installiere und betreibe das beigefuegte Python-Skript als autorisierten ILIAS-Live-Recorder unter `/srv/ilias-live`. Kopiere die vier HTML-Exporte nach `/srv/ilias-live/html`, installiere `python3`, `ffmpeg`, `ffprobe`, `ca-certificates` und `tzdata`, und teste zuerst mit `--list-only`, ob 40 Europe/Zurich-Termine erkannt werden. Verwende als HLS-URL exakt `https://livedist.opencast.unibe.ch/hls/H4-210-Aula-ext352/index.m3u8`. Richte den Prozess als systemd-Service mit Benutzer `recorder`, `UMask=0077`, fuenf Minuten Vorlauf, maximal 150 Minuten Laufzeit, 180 Sekunden Idle-Timeout und fuenf Sekunden Playlist-Polling ein. Speichere unter `/srv/ilias-live/recordings`, pruefe HTTP 200 und `#EXTM3U` direkt vom Server und fuehre nach dem ersten Termin `ffprobe` sowie einen vollstaendigen FFmpeg-Dekodiertest aus. Verwende Europe/Zurich fuer alle Zeitberechnungen und berichte jeden Fehler, statt Termine oder Stream-URLs zu erraten.
