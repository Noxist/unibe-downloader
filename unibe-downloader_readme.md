# Uni Bern ILIAS Downloader

Dieses Projekt automatisiert den persönlichen ILIAS-Ablauf auf dem Linux-Server:

1. Anmeldung über SWITCH edu-ID und MFA
2. inkrementeller Download von Kursunterlagen
3. Download veröffentlichter Opencast-Podcasts
4. Integration des Live-Recorders für Privatrecht
5. Speech-to-Text-Transkription
6. nicht löschender Archiv-Upload nach Google Drive
7. tägliche, einmalige Bereitstellung neuer Materialien für NotebookLM

Das Kursarchiv bleibt dauerhaft erhalten. `NotebookLM` ist dagegen eine flache
Wegwerf-Inbox: Nach dem Import darf der Benutzer ihre Dateien auf Drive löschen.
Für Lecture-Medien ist Google Drive der dauerhafte Speicher: Lokale MP4s und
Transkript-Nebenprodukte werden nach erfolgreichem Upload und anschließendem
Hashvergleich mit Drive entfernt. Bei Upload- oder Prüfungsfehlern bleiben sie
lokal für einen späteren Versuch.

## Struktur des Downloadpakets

Das vollständige Übergabepaket enthält keine Sitzungen, Passwörter, Downloads
oder Videos. Nach dem Entpacken sieht es so aus:

```text
ilias-automation/
  README.md                         diese Gesamtdokumentation
  unibe-downloader/
    app/
      downloader.py                ILIAS-Dokumentcrawler
      ilias_tools.py               Erweiterte Diagnose- & Download-Werkzeuge (CLI)
      eduid_auth.py                 Auth-Adapter zur bestehenden edu-ID-Automation
      podcast_sync.py               Opencast-Podcast-Erkennung und MP4-Download (mit Resume)
      live_recorder.py              terminierte HLS-Aufnahme und Integration
      transcribe.py                 faster-whisper Speech-to-Text
      notebooklm_export.py          einmalige flache Notebooklm-Inbox
      server_sync.py                Orchestrierung und Drive-Upload
      pdf_conversion.py             Office-/Bild-PDF-Konvertierung
      sync_support.py               Kalenderwochen, Hashes und Downloadindex
      config.server.example.json    bereinigte Serverkonfiguration
      test_*.py                     Tests (inkl. test_ilias_tools.py)
    requirements-server.txt        minimale Linux-Abhängigkeiten
    app/requirements.txt           Desktop-/Windows-Abhängigkeiten
    Start.cmd                       Windows-Einstieg
    docs/
      LIVE_RECORDER_HANDOFF.md      ursprüngliche Recorder-Übergabe
    systemd/
      ilias-live.service            Recorder-Unit-Vorlage
  ilias_analysis/                   Vollständige technische Reverse-Engineering-Dokumentation
  systemd-user/
    ilias-downloader.service
    ilias-downloader.timer
    ilias-live.service
```

Nach dem Entpacken muss `config.server.example.json` als
`config.server.json` kopiert und an Serverpfade, Auth-Befehl, Drive-Remote,
Folder-IDs, Kurse und Podcastobjekte angepasst werden. Die mitgelieferten
systemd-Dateien enthalten die aktuell verwendeten absoluten Pfade und dienen
bei einer Migration als nachvollziehbare Vorlagen.

## Gesamtfluss

```text
systemd-Timer (täglich)
  -> bestehende edu-ID-Zugangsdaten + TOTP
  -> verifizierter Playwright Storage-State
  -> ILIAS-Kursseiten und Dokumente
  -> veröffentlichte Opencast-Player und MP4-Dateien
  -> Whisper-turbo-Transkripte
  -> dauerhaftes Kursarchiv auf Google Drive
  -> neue Dateiversionen einmalig nach Notebooklm/<Fach>/

ilias-live.service (dauerhaft)
  -> Terminplan
  -> autorisierter Raum-HLS-Stream
  -> MPEG-TS-Segmente
  -> FFmpeg-MP4
  -> Kurs/YYYY/Lectures
  -> Whisper-Transkript
  -> Drive-Archiv + einmalige Notebooklm-Inbox
```

Podcast- und Live-Dateien verwenden unterschiedliche stabile Namen,
damit keine Aufnahme eine andere überschreibt. Hashindizes machen alle Schritte
inkrementell und wiederholbar.

## Produktionspfade

```text
Code:          /home/leandro/services/unibe-downloader
Konfiguration: /home/leandro/services/unibe-downloader/app/config.server.json
Sitzung:       /home/leandro/service_data/ilias-downloader/auth/session.json
Archiv:        /home/leandro/service_data/ilias-downloader/staging/1. Semester
Live-Plan:     /home/leandro/service_data/ilias-downloader/live/schedule
Live-Rohdaten: /home/leandro/service_data/ilias-downloader/live/recordings
```

Das Drive-Ziel ist der in `drive_root_folder_id` festgelegte Ordner und darin
`1. Semester`. Der Archiv-Upload verwendet `rclone copy`; nur auf Drive
vorhandene Dateien werden nicht gelöscht. Nach einem erfolgreichen Lauf prüft
`rclone check --one-way` die Inhalte unter `YYYY/Lectures`. Nur wenn alle
Dateien im Ziel vorhanden und identisch sind, entfernt der Dienst die lokalen
MP4s und Transkript-Nebenprodukte. Ein Prüfungsfehler lässt die lokalen Dateien
für einen späteren Lauf stehen.

## Automatischer Tageslauf

`ilias-downloader.timer` startet täglich um 04:30 Uhr `Europe/Zurich`, mit bis
zu fünf Minuten Zufallsversatz und `Persistent=true`. Die One-shot-Unit
`ilias-downloader.service` führt nacheinander aus:

1. `eduid_auth.py` erneuert und verifiziert die ILIAS-Sitzung.
2. `downloader.py` lädt neue/geänderte Kursunterlagen.
3. `podcast_sync.py` lädt neue veröffentlichte Lectures.
4. `transcribe.py` transkribiert neue Medien.
5. `notebooklm_export.py` befüllt die Inboxen einmalig.
6. `server_sync.py` kopiert Archiv und Inboxen nach Drive.
7. Geprüfte Lecture-Medien im lokalen Staging werden nach dem Upload entfernt.

Ein `fcntl`-Lock verhindert parallele Tagesläufe. Das Unit-Zeitlimit beträgt
sechs Stunden.

## Anmeldung und Geheimnisse

Der Auth-Adapter verwendet das bereits geschützte Konto
`account-1@example.invalid` aus
`/home/leandro/service_data/auto_reserve/settings.json`. Passwort und TOTP-Key
werden weder kopiert noch protokolliert. Der Storage-State liegt mit privaten
Rechten außerhalb des Repositorys. Ein Login gilt erst nach erfolgreichem Abruf
einer geschützten Kursseite als gültig; HTTP 200 allein reicht nicht.

## Kursarchiv

```text
Kursname__REF_ID/
  allgemeine Unterlagen
  2026/
    KW (38)/
    Lectures/
  NotebookLM/
```

Terminmaterial wird anhand ISO-Jahr/Kalenderwoche abgelegt. Allgemeine Dateien
bleiben im Kursstamm. `.download-index.json` verwaltet Revisionen und Hashes;
ältere Fassungen liegen in `.versions`. `.part` und `.tmp` gelten nie als fertig.
`YYYY/Lectures` ist lokaler Arbeitsbereich: Nach geprüftem Drive-Upload werden
dort MP4s und Dateien mit `.transcript.txt`, `.transcript.vtt` oder
`.transcript.json` entfernt. Die Podcastindizes behalten den bestätigten
Drive-Status, damit entfernte lokale MP4s nicht erneut heruntergeladen werden.
Drive-Kopien werden nicht gelöscht.

## Podcasts

`podcast_sync.py` liest authentifizierte Opencast-Listen, dedupliziert Bild- und
Textlinks über die Event-ID und ignoriert undatierte Anleitungsvideos. Die
Playerdaten werden als JSON ausgewertet; gewählt wird die höchste MP4-Auflösung
von erlaubten Uni-Bern-Hosts. Downloads werden gestreamt und atomar publiziert.

```text
Strafrecht I__3723286/2026/Lectures/
  2026-09-21_1015__dd123549__Vorlesung.mp4
```

`.podcast-index.json` verhindert Doppeluploads.

## Live-Recorder

`ilias-live.service` startet `app/live_recorder.py` direkt aus diesem Projekt.
Terminplan und Rohaufnahmen liegen als veränderliche Laufzeitdaten unter
`/home/leandro/service_data/ilias-downloader/live`. Der Stream
`H4-210-Aula-ext352` gehört zu `Privatrecht I - OR Allgemeiner Teil 1__3721432`.
Der Recorder startet fünf Minuten vor dem Termin, archiviert HLS-Segmente,
erzeugt eine MP4 und integriert sie in denselben Lectures-Ordner:

```text
2026-09-22_1415__live__Vorlesung.mp4
```

Danach folgen Transkription und Drive-Upload. Rohsegmente werden erst nach
erfolgreichem Upload und Hashvergleich sofort bereinigt. Die
integrierte Kurskopie der MP4 und ihrer Transkripte wird direkt nach einem
erfolgreichen rclone-Upload und Hashvergleich lokal entfernt.

## Transkription

`transcribe.py` verwendet `faster-whisper` auf sechs CPU-Threads mit `int8`,
Sprache `de` und dem bereits vorgeladenen mehrsprachigen Modell `turbo`.
Pro Lecture entstehen:

```text
Vorlesung.transcript.txt   # NotebookLM
Vorlesung.transcript.vtt   # Untertitel
Vorlesung.transcript.json  # Segmente, Modell- und Hashmetadaten
```

Der Medienhash verhindert Wiederholungen. NotebookLM erhält nur TXT.

## NotebookLM-Inbox: genau einmal

Jeder Kurs besitzt einen flachen `NotebookLM`-Ordner ohne Unterordner. Namen
enthalten den Kontext:

```text
Allgemein__3810123__Vorlesungsgliederung.pdf
KW38__3784413__Einfuehrung.pdf
Lecture__2026-09-22_1415__live__Vorlesung__Transkript.txt
Übungen__3799448__Ablaufplan.pdf
```

Regeln:

- PDF wird gegenüber einer gleichnamigen Office-Quelle bevorzugt.
- Liegt nur Word vor, wird PDF erzeugt.
- Bilder, PDFs, EPUBs und Textdateien werden übernommen.
- MP4, VTT, Transkript-JSON, versteckte Dateien und Indizes werden ausgelassen.
- Strafrecht übernimmt Material aus `Übungen Strafrecht I`.
- Privatrecht übernimmt Material aus der Privatrechtsübung.

Die zentrale Drive-Struktur lautet `Notebooklm/<Fach>/`; alle Kurs-Inboxen
werden damit an einer Stelle gesammelt. Das Ziel besitzt eine eigene
`notebooklm_drive_root_folder_id` und ist technisch vom Archivziel getrennt.

Die Inbox ist eine einmalige Zustellwarteschlange mit folgenden Garantien:
- **Nur bei neuen Dateien:** Ein Unterordner für ein Fach (z. B. `Notebooklm/Strafrecht I/`) wird auf Google Drive **nur dann erstellt, wenn tatsächlich neue oder geänderte Dateien gespeichert wurden**.
- **Keine leeren Ordner:** Gibt es an einem Tag keine neuen Dateien für ein Fach, wird dafür auf Google Drive kein Ordner angelegt.
- **Einmalige Zustellung:** Nach erfolgreichem Upload wird die lokale Warteschlange geleert und der Quellhash in `Kurs/.notebooklm-export-index.json` gespeichert.
- **Sauberes Löschen:** Löscht der Benutzer den Ordner oder die Dateien nach dem Import in NotebookLM auf Google Drive, werden sie **niemals wieder neu erzeugt oder hochgeladen**.
- **Nur echte Änderungen:** Erst wenn auf ILIAS eine inhaltlich neue Version (neuer Hash) hochgeladen wird, erscheint das Fach wieder mit dieser neuen Datei in der Inbox.

Der normale Archiv-Upload schließt `NotebookLM/**` aus. Inboxen werden separat
mit `rclone copy` (ohne `--create-empty-src-dirs`), niemals mit `sync`, übertragen. Benutzerlöschungen bleiben
daher dauerhaft bestehen.

## Installation

Systempakete:

```text
python3 python3-venv ffmpeg ffprobe ca-certificates tzdata rclone
```

Python:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-server.txt
```

Der vorhandene Chromium aus `auto_reserve` wird wiederverwendet.

### rclone auf dem Produktionsserver

Der Ubuntu-Paketstand von `rclone` unterstützt den aktuellen Google-OAuth-
Browserfluss nicht mehr. Deshalb liegt die produktiv verwendete, anhand der
offiziellen `SHA256SUMS` geprüfte Version unter
`/home/leandro/.local/bin/rclone`. Systemd-Drop-ins für
`ilias-downloader.service` und `ilias-live.service` stellen diesen Pfad vor
`/usr/bin`. Ein Update darf nur aus der offiziellen rclone-Distribution
erfolgen und muss gegen deren veröffentlichte SHA-256-Summe geprüft werden.

Die OAuth-Konfiguration bleibt mit Modus `0600` unter
`/home/leandro/.config/rclone/rclone.conf`. Vor einer Neuanmeldung wird eine
ebenfalls nur für den Benutzer lesbare Sicherung unter
`/home/leandro/.config/rclone/backups/` angelegt. Tokenwerte dürfen weder in
Dokumentation noch in Befehlsausgaben übernommen werden.

Die Anmeldung auf diesem headless Server erfolgt ausschließlich über den von
`rclone` bereitgestellten Localhost-Callback und einen SSH-Tunnel. Es wird dafür
kein öffentlicher Host-Port geöffnet.

## Betrieb und Diagnose

```bash
systemctl --user start ilias-downloader.service
systemctl --user status ilias-downloader.service
systemctl --user list-timers ilias-downloader.timer
journalctl --user -u ilias-downloader.service -f
journalctl --user -u ilias-live.service -f
```

Inbox lokal prüfen:

```bash
.venv/bin/python app/notebooklm_export.py \
  --root '/home/leandro/service_data/ilias-downloader/staging/1. Semester' \
  --config app/config.server.json
```

Transkripte nachholen:

```bash
.venv/bin/python app/transcribe.py \
  --root '/home/leandro/service_data/ilias-downloader/staging/1. Semester' \
  --model turbo --language de
```

Loginfehler stehen im Journal. Fehlgeschlagene Medien/Transkripte bekommen
keinen Erfolgsindex und werden erneut versucht. Erscheint eine gelöschte
## Erweiterte Diagnose- & Download-Werkzeuge (ilias_tools.py)

Das neue Modul `app/ilias_tools.py` stellt gezielte Diagnose- und Download-Befehle bereit:

### 1. URL diagnostizieren (`inspect-url`)
Analysiert eine beliebige ILIAS-URL, ermittelt den Objekttyp, folgt Redirects und zeigt HTTP-Header (ETag, Last-Modified, Dateiname, Status):

```bash
python app/ilias_tools.py inspect-url "https://ilias.unibe.ch/goto.php?target=crs_3687731" --json
```

### 2. Kursstruktur exportieren (`discover-course`)
Traversiert den Kursbaum rekursiv und erzeugt einen strukturierten JSON-Export aller Ordner, Dateien und Podcasts:

```bash
python app/ilias_tools.py discover-course 3687731 -o kursstruktur.json
```

### 3. OpenCast-Event & Medienquellen inspizieren (`opencast-inspect`)
Liest alle veröffentlichten Podcast-Episoden, Auflösungen und direkte MP4-URLs eines OpenCast-Objekts aus:

```bash
python app/ilias_tools.py opencast-inspect 3762440
```

### 4. Automatische URL-Erkennung und Download (`download-url`)
Erkennt Dateitypen automatisch und lädt Dateien, Ordner oder Kurse atomar herunter:

```bash
python app/ilias_tools.py download-url "https://ilias.unibe.ch/goto.php?target=file_3784400" -o ./downloads
```

### 5. Rekursiver Ordner-Download (`download-folder`)
Lädt alle Inhalte eines Ordners oder Kurses rekursiv herunter, inklusive inkrementeller ETag-Prüfung:

```bash
python app/ilias_tools.py download-folder 3687731 -o ./kurs_downloads
```

### 6. Direkter HTTP-Download ohne Browser (`direct-download`)
Lädt eine Datei ausschliesslich über Standard-HTTP mit Cookies aus `session.json` (ohne Playwright/Browser-Overhead, unter 1s):

```bash
python app/ilias_tools.py direct-download 3784400 -o ./downloads
```

### 7. Live-Recorder-Zuordnungen ableiten (`live-discovery`)
Ermittelt aus Metadaten und Thumbnail-Dateinamen bekannte Recorder-Geräte und Hörsaal-Zuordnungen:

```bash
python app/ilias_tools.py live-discovery
```

### 8. Resumable Podcast-Downloads mit Range Requests
In `app/podcast_sync.py` wurde der MP4-Download erweitert:
- Verwendet HTTP Range Requests (`Accept-Ranges: bytes`)
- Teildateien (`.part`) bleiben bei Verbindungsabbruch erhalten
- Erneuter Aufruf setzt den Download an der exakten Byte-Position fort (Resume)
- Spart Bandbreite bei 600+ MiB Vorlesungsaufzeichnungen

---

## Sicherheit

- Keine Passwörter, Cookies, TOTP-Keys oder OAuth-Tokens committen.
- `.auth`, Storage-State, Indizes und Logs nicht nach Drive kopieren.
- Für das Gesamtarchiv nie `rclone sync` verwenden.
- Signierte Medien-URLs/JWTs nicht dauerhaft speichern.
- Nur Inhalte aufnehmen, für die eine Berechtigung besteht.
- `NotebookLM` ist abgeleitet und löschbar; das Kursarchiv ist die Quelle.

## Windows und reMarkable

`Start.cmd` und die Desktopmodule bleiben für lokale PDF-/reMarkable-Abläufe
verfügbar. Der Linux-Server greift nicht direkt auf das Tablet zu. Google Drive
synchronisiert Archiv und NotebookLM-Inbox zum Laptop. Videos werden nicht nach
PDF konvertiert und nicht ans reMarkable gesendet.

## Purpose

Downloads authorized ILIAS course/podcast material, records configured live lectures, transcribes media, and copies derived outputs to Google Drive.

## Architecture

`systemd --user timer/services -> Python tools -> authenticated ILIAS/Opencast -> local staging -> rclone copy -> Drive`.

## Location

`/home/leandro/services/unibe-downloader`

## Runtime

Native Python virtual environment with user systemd units. `ilias-live.service` is active; the daily downloader timer is active and the one-shot service is normally inactive between runs.

## Services and Containers

No Docker container. User units: `ilias-downloader.service`, `ilias-downloader.timer`, and `ilias-live.service`.

## Networking

Outbound HTTPS only; no host listener or public IPv4/IPv6 port was identified.

## Public Access

None. This project is not routed through Cloudflare and exposes no server endpoint.

## Configuration

- `app/config.server.json` — server paths/settings; treat as potentially sensitive.
- User unit files: `/home/leandro/.config/systemd/user/`.
- Persistent state: `/home/leandro/service_data/ilias-downloader`.

## Secrets

| Name | Purpose | Storage location | Scope/Notes |
|---|---|---|---|
| ILIAS session state | Authenticated downloads | Current: `service_data/ilias-downloader/auth/session.json`; target: `/etc/server-secrets/unibe-downloader/session.json` or approved user-scoped equivalent | Session credential |
| Booking/edu-ID credential source | Login bootstrap | Current shared auto-reserve settings; target: project-specific canonical secret files | Avoid duplicate values |
| rclone credentials/tokens | Drive upload | Current: `/home/leandro/.config/rclone/rclone.conf`; target: `/etc/server-secrets/unibe-downloader/rclone.conf` or approved user-scoped equivalent | Least Drive scope |

## Persistent Data

`/home/leandro/service_data/ilias-downloader` contains auth state, staging files, live schedule/recordings, indexes, and derived outputs. Lecture MP4s and transcript sidecars are temporary staging artifacts and are removed after their Drive copy passes `rclone check`; Drive is their durable archive.

## Backup

Back up configuration and non-reproducible source material separately from auth/rclone secrets. Drive is the durable copy of lecture media. Do not use destructive sync for the Drive archive.

## Restore

Restore code/config, then persistent archive/state, then secrets through the approved channel. Validate against a protected page before enabling scheduled work.

## Deployment / Start / Stop

Use the documented `systemctl --user` commands as `leandro`; do not manipulate unrelated system services.

## Update Procedure

Back up configuration/indexes, update the virtual environment/code, run non-destructive diagnostics, then restart only the affected user unit if required.

## Health Checks

Check timer state, service exit status, recent sanitized journal, lock state, and expected new artifacts. Lecture cleanup is allowed only after `rclone check --one-way` succeeds. HTTP 200 alone does not prove authentication.

## Logs

Use the user journal. `.auth/logs` may contain security-sensitive request metadata; two historical files are mode 664 and require later review. Never copy JWTs/signed URLs into reports.

## Security Model

No inbound service; outbound authenticated sessions, user-level units, private state files, and non-destructive Drive copy semantics.

## Security Exceptions

- Authentication/rclone state is outside the canonical `/etc/server-secrets` target.
- Two historical `.auth` log files are group/world-readable metadata candidates.

### Additional rclone/OAuth rollback detail

Restore backed-up code/config/indexes and restart only the affected user unit. Do not roll back by deleting the course archive.

## Troubleshooting

Use the existing diagnostics and journals; never log cookies, passwords, TOTP, JWTs, or signed media URLs.

If Drive upload or hash verification fails, lecture files remain in local
`YYYY/Lectures` and are retried on a later run. A podcast entry with
`drive_uploaded: true` has already been verified remotely and intentionally has
no local media copy.

### Additional maintenance history

Only content for which access is authorized may be archived. Drive uses copy, not destructive sync.

### OAuth recovery review

2026-10-06 — Stage 1.5 governance standardization.

## Rollback

Falls die benutzerspezifische `rclone`-Version Probleme verursacht, die beiden
Systemd-Pfad-Drop-ins deaktivieren, `systemctl --user daemon-reload` ausführen
und nur den betroffenen Dienst neu starten. Für einen fehlgeschlagenen OAuth-
Reconnect kann die unmittelbar davor angelegte, geschützte Konfigurationskopie
aus `/home/leandro/.config/rclone/backups/` nach Prozessstopp zurückgespielt
werden. Ein Rollback darf niemals laufende Aufnahmen oder das Staging-Archiv
löschen.

Die Lecture-Aufbewahrungsänderung lässt sich zurücknehmen, indem die gesicherten
Projektdateien und die installierte `ilias-live.service` aus
`/home/leandro/service_data/ilias-downloader/ops-backups/local-lecture-retention-20261013/`
wiederhergestellt werden. Danach nur `ilias-live.service` nach Abschluss einer
Aufnahme neu laden und starten. Bereits entfernte lokale Lecture-Dateien können
aus den zuvor geprüften Drive-Archivpfaden mit `rclone copy` wiederhergestellt
werden; Drive wird dabei nicht verändert.

## Maintenance Notes

- 2026-10-06: Benutzerlokales `rclone` 1.75.1 installiert, nachdem das alte
  OOB-OAuth-Verfahren von Google abgewiesen wurde; Systemd-Pfad-Drop-ins
  eingerichtet. Laufende Aufnahme nicht neu gestartet.
- 2026-10-13: Lecture-MP4s und Transkript-Nebenprodukte werden nach
  erfolgreichem rclone-Upload und Remote-Hashvergleich lokal freigegeben.
  Podcastindizes merken bestätigte Uploads, damit gelöschte lokale Dateien
  nicht erneut heruntergeladen werden. Live-HLS-Rohdaten werden nach bestätigtem
  Upload sofort bereinigt; fehlgeschlagene Uploads behalten ihre Quelldaten.

## Last Reviewed

2026-10-13 — Lokale Lecture-Aufbewahrung auf Upload-Zeit begrenzt.

## Maintenance 2026-10-13

rclone user executable updated to 1.75.2. Native virtual environment dependencies refreshed within project compatibility limits; pypdf added to server requirements for the existing PDF feature. 50 tests passed; private HTML export test has the same missing fixture failure in both old/new environments and was excluded from the clean run. Reportlab stays on latest compatible <5 and mpmath remains constrained by Sympy. Auxiliary .python-packages soupsieve refreshed. At the time of this dependency update the recorder was not restarted and archive/indexes were unchanged. Later on 2026-10-13 the retention change was deployed: after a 52-file Drive hash check, local Lecture artifacts were removed, podcast upload markers updated, and the recorder restarted with immediate raw-recording cleanup. About 14 GB of local storage was reclaimed; no Drive archive files were deleted. Rollback uses .venv-before-20261013 and executable backups at /var/backups/server-update-20261013/; keep current archives and authentication state.


## Private GitHub repository (2026-11-17)

A private, reviewed source or configuration snapshot is available at `https://github.com/Noxist/unibe-downloader` on `main`. The publication checkout is separate from the live working copy. Runtime data, secrets and other private material are excluded; any substituted placeholders and deployment limitations are documented in the repository. For updates review each changed file, run relevant isolated checks and secret scanning, push only a fast-forward, and verify the GitHub commit and tree. Do not overwrite or merge the live working history.

## PPTX-Konvertierung: Prüfstand 2026-11-23

LibreOffice Impress 7.3.7 wurde nach Reparatur der Paketkonfiguration durch den Betreiber installiert. Eine künstliche PPTX-Datei wurde über `notebooklm_export.convert_office` in ein PDF konvertiert; PDF-Validierung, Seitenzahl und Textprüfung bestanden. Der zuvor fehlgeschlagene produktive ILIAS-Lauf wurde nicht wiederholt; der nächste reguläre Lauf und die Verarbeitung der tatsächlich betroffenen Datei bleiben zu überprüfen.

2026-11-24: The regular scheduled downloader completed successfully. The previously failing presentation was separately converted with the existing converter in a network-isolated temporary environment, and the resulting PDF passed page validation. The earlier delivery ledger match remained unconfirmed; this conversion test does not establish prior Drive delivery or independent off-host recovery. No second timer or production downloader run was created.
