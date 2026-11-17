# File Downloads — Technische Analyse & Verifikation

## Evidenz-Klassifikation
- **BEWIESEN** = Direkt im Live-Test, HAR oder Code verifiziert
- **ABGELEITET** = Starke technische Schlussfolgerung
- **HYPOTHESE** = Noch zu verifizieren

---

## 1. Download-Endpunkte (BEWIESEN)

### Direkter Endpunkt
```text
GET https://ilias.unibe.ch/ilias.php?baseClass=ilRepositoryGUI&cmdClass=ilObjFileGUI&cmd=sendfile&ref_id={ref_id}
```

### Getestete Parameter-Variationen (Live-Test am 2026-09-23)

| URL / Parameter | Status | Content-Type | Ergebnis | Bewertung |
|---|---|---|---|---|
| `baseClass=ilRepositoryGUI&cmdClass=ilObjFileGUI&cmd=sendfile&ref_id={id}` | 200 OK | `application/pdf` | Datei empfangen | **Standard (stabil)** |
| `baseClass=ilRepositoryGUI&cmd=sendfile&ref_id={id}` (ohne cmdClass) | 200 OK | `application/pdf` | Datei empfangen | **Minimal funktionierend** |
| `cmdClass=ilObjFileGUI&cmd=sendfile&ref_id={id}` (ohne baseClass) | 404 Not Found | `text/html` | Fehlerseite | `baseClass` ist Pflicht |
| `cmd=sendfile&ref_id={id}` (nur cmd + ref_id) | 404 Not Found | `text/html` | Fehlerseite | `baseClass` ist Pflicht |
| `goto.php?target=file_{id}` | 200 OK (nach 302) | `application/pdf` | Datei empfangen | Shortcut mit 1 Redirect |
| `goto.php?target=file_{id}_download` | 200 OK (nach 302) | `application/pdf` | Datei empfangen | Shortcut mit 1 Redirect |

### Schlussfolgerungen zur URL-Struktur:
1. **Ist dies bereits der direkteste/stabilste Endpunkt?** (BEWIESEN)  
   **Ja.** Der Aufruf von `ilias.php?baseClass=ilRepositoryGUI&cmdClass=ilObjFileGUI&cmd=sendfile&ref_id={ref_id}` liefert die Datei direkt mit HTTP 200 OK ohne weitere Redirects aus.
2. **Finden Redirects statt?** (BEWIESEN)  
   Auf dem `sendfile`-Endpunkt finden **keine Redirects** statt. Auf `goto.php?target=file_{id}` findet genau ein 302-Redirect auf den `sendfile`-Endpunkt statt.
3. **Existiert dahinter ein weiterer Storage-/Download-Endpunkt?** (BEWIESEN)  
   **Nein.** Anders als bei OpenCast (wo Streaming über `dist.opencast.unibe.ch` läuft) streamt ILIAS Dateien direkt über den Webserver (Apache/PHP) per `sendfile` aus dem lokalen Storage. Es gibt keine Weiterleitung auf Cloud-Storage/S3.
4. **Welche Parameter sind wirklich erforderlich?** (BEWIESEN)  
   Erforderlich: `baseClass=ilRepositoryGUI`, `cmd=sendfile`, `ref_id={ref_id}`.  
   Optional: `cmdClass=ilObjFileGUI` (kann entfallen, ist aber zur Klarheit empfohlen).  
   `cmdNode` ist **nicht** erforderlich.

---

## 2. HTTP-Header & Caching (BEWIESEN)

Im Live-Test auf Datei `ref_id=3784400` ("Hörerschein Grundrechte.pdf") beobachtete Header:

```http
HTTP/1.1 200 OK
Server: Apache
Content-Type: application/pdf
Content-Length: 20714
Content-Disposition: attachment; filename="Hoererschein Grundrechte.pdf"
ETag: "50ea-65a6967672ca9"
Last-Modified: Fri, 14 Aug 2026 06:30:33 GMT
Set-Cookie: ilClientId=ilias3_unibe; Path=/; Secure; HttpOnly; SameSite=Lax
```

### Verifizierte Fähigkeiten:

| Feature | Unterstützt? | Evidenz (Live-Test) |
|---|---|---|
| **HEAD-Requests** | **JA** | Status 200, liefert `Content-Length`, `Content-Disposition`, `ETag`, `Last-Modified` ohne Body |
| **ETag** | **JA** | `ETag: "50ea-65a6967672ca9"`, unterstützt bedingte Requests mit `If-None-Match` |
| **Last-Modified** | **JA** | `Last-Modified: Fri, 14 Aug 2026 06:30:33 GMT`, unterstützt `If-Modified-Since` |
| **HTTP 304** | **JA** | Server antwortet mit `304 Not Modified`, wenn ETag/Datum übereinstimmt |
| **Content-Length** | **JA** | Exakte Dateigrösse wird im Header geliefert |
| **Range Requests** | **JA** | **Status 206 Partial Content** bei `Range: bytes=0-100` mit `Content-Range: bytes 0-100/20714`! |

> [!NOTE]
> **Range Requests auf ILIAS:** Bisherige Annahme war, dass Range Requests nur bei OpenCast funktionieren. Der Live-Test beweist, dass auch der ILIAS-Webserver (Apache) für `cmd=sendfile` HTTP Range Requests mit Status 206 beantwortet. Damit sind auch für ILIAS-Dateien resumable Downloads möglich!

---

## 3. Authentifizierung & Cookies (BEWIESEN)

Für den Download sind ausschliesslich folgende Cookies auf der Domain `ilias.unibe.ch` nötig:
1. `PHPSESSID` (aktive PHP-Session)
2. `_shibsession_*` (aktive Shibboleth-Session)
3. `ilClientId=ilias3_unibe` (Mandanten-Kennung)

Alle anderen edu-ID/Switch-Cookies (`__Host-JSESSIONID`, `shib_idp_session`, etc.) befinden sich auf Subdomains von `.eduid.ch` und `.switch.ch` und werden von ILIAS für Dateidownloads **nicht** abgefragt.

---

## 4. Download ohne Browseraufruf (BEWIESEN)

Dateien lassen sich auf zwei Arten vollständig ohne Browser-Rendering herunterladen:

### A. Playwright APIRequestContext (`context.request.get`)
- Verwendet bestehende Session aus `storage_state`
- Führt reine HTTP-Requests aus (kein DOM, kein JS, kein Rendering)
- Schnell, robust, im aktuellen `downloader.py` implementiert

### B. Direkter Python HTTP-Client (`urllib.request` / `requests` / `httpx`)
- Liest Cookies aus `session.json` in einen Standard-CookieJar
- Sendet `GET`-Request mit `User-Agent`
- Funktioniert zu 100% ohne laufenden Browser/Playwright-Prozess
- Verifiziert in `test_download_mechanics.py`: Datei mit exaktem SHA256 (`3fb54bc7...`) heruntergeladen
- Ideal für CLI-Tools, Hintergrund-Tasks oder parallele Worker

---

## 5. Dateiablage & Integrität

1. **Atomares Schreiben:** Downloads werden immer in `.part`-Dateien geschrieben und erst nach Abschluss atomar umbenannt (`temp.replace(path)`).
2. **Versions-Archivierung:** Vor dem Überschreiben einer geänderten Datei wird die alte Version nach `.versions/{sha256}/{filename}` kopiert.
3. **Integritätsprüfung:** SHA-256 Hash wird über den Body berechnet und im `.download-index.json` gespeichert.
4. **Session-Validierung:** Bei Session-Ablauf antwortet ILIAS mit HTTP 200 und der HTML-Login-Seite. Der Downloader prüft den Body auf `type="password"` oder `doStandardAuthentication`, um ein Überschreiben von Dokumenten durch Login-Seiten zu verhindern.
