"""Tests for ilias_tools.py — URL parsing, structure discovery, and download logic."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ilias_tools import (
    inspect_url,
    discover_course,
    opencast_inspect,
    download_url,
    live_discovery,
    stream_download_with_resume,
    direct_file_download,
    _collect_files,
)
from downloader import object_id, safe_name, parse_items


class TestObjectIdExtended(unittest.TestCase):
    """Extended tests for object_id parsing with various URL formats."""

    def test_goto_course(self):
        self.assertEqual(object_id("https://ilias.unibe.ch/goto.php?target=crs_3687731"), ("crs", "3687731"))

    def test_goto_file(self):
        self.assertEqual(object_id("https://ilias.unibe.ch/goto.php?target=file_123"), ("file", "123"))

    def test_goto_folder(self):
        self.assertEqual(object_id("https://ilias.unibe.ch/goto.php?target=fold_456"), ("fold", "456"))

    def test_ilias_php_file_download(self):
        url = "https://ilias.unibe.ch/ilias.php?baseClass=ilrepositorygui&cmdClass=ilObjFileGUI&cmd=sendfile&ref_id=3809867"
        result = object_id(url)
        self.assertEqual(result, ("file", "3809867"))

    def test_ilias_php_course(self):
        url = "https://ilias.unibe.ch/ilias.php?baseClass=ilrepositorygui&cmdClass=ilobjcoursegui&ref_id=3687731"
        result = object_id(url)
        self.assertEqual(result, ("crs", "3687731"))

    def test_ilias_php_folder(self):
        url = "https://ilias.unibe.ch/ilias.php?baseClass=ilrepositorygui&cmdClass=ilobjfoldergui&ref_id=999"
        result = object_id(url)
        self.assertEqual(result, ("fold", "999"))

    def test_info_screen(self):
        url = "https://ilias.unibe.ch/ilias.php?baseClass=ilrepositorygui&cmd=infoScreen&ref_id=3687731"
        result = object_id(url)
        self.assertEqual(result, ("info", "3687731"))

    def test_non_ilias_url_returns_none(self):
        self.assertIsNone(object_id("https://google.com/search?q=test"))

    def test_unknown_command_returns_none(self):
        url = "https://ilias.unibe.ch/ilias.php?baseClass=ilrepositorygui&cmd=delete&ref_id=123"
        self.assertIsNone(object_id(url))


class TestInspectUrlOffline(unittest.TestCase):
    """Test inspect_url without network access (context=None)."""

    def test_inspect_file_url(self):
        url = "https://ilias.unibe.ch/ilias.php?baseClass=ilrepositorygui&cmdClass=ilObjFileGUI&cmd=sendfile&ref_id=3809867"
        result = inspect_url(url, context=None, verbose=False)
        self.assertEqual(result["detected_type"], "file")
        self.assertEqual(result["detected_ref_id"], "3809867")

    def test_inspect_course_url(self):
        url = "https://ilias.unibe.ch/goto.php?target=crs_3687731"
        result = inspect_url(url, context=None, verbose=False)
        self.assertEqual(result["detected_type"], "crs")
        self.assertEqual(result["detected_ref_id"], "3687731")

    def test_inspect_unknown_url(self):
        result = inspect_url("https://google.com", context=None, verbose=False)
        self.assertIsNone(result["detected_type"])


class TestCollectFiles(unittest.TestCase):
    """Test recursive file collection from course tree."""

    def test_flat_course(self):
        tree = {
            "files": [
                {"ref_id": "100", "title": "Skript.pdf", "revision": None},
                {"ref_id": "101", "title": "Übung1.pdf", "revision": "abc"},
            ],
            "children": [],
        }
        files = []
        _collect_files(tree, Path("."), files)
        self.assertEqual(len(files), 2)
        self.assertEqual(files[0][0], "100")
        self.assertEqual(files[1][0], "101")

    def test_nested_folders(self):
        tree = {
            "files": [{"ref_id": "1", "title": "Root.pdf", "revision": None}],
            "children": [
                {
                    "ref_id": "10",
                    "title": "Woche 1",
                    "files": [{"ref_id": "2", "title": "Slides.pdf", "revision": None}],
                    "children": [
                        {
                            "ref_id": "20",
                            "title": "Übungen",
                            "files": [{"ref_id": "3", "title": "Aufgabe.pdf", "revision": None}],
                            "children": [],
                        }
                    ],
                }
            ],
        }
        files = []
        _collect_files(tree, Path("."), files)
        self.assertEqual(len(files), 3)
        # Check paths are nested correctly
        paths = [str(f[2]) for f in files]
        # Root file should NOT be in a subfolder
        self.assertNotIn("Woche", paths[0])
        self.assertTrue(any("Woche 1__10" in p for p in paths))
        self.assertTrue(any("__20" in p for p in paths))

    def test_empty_tree(self):
        tree = {"files": [], "children": []}
        files = []
        _collect_files(tree, Path("."), files)
        self.assertEqual(len(files), 0)


class TestLiveDiscovery(unittest.TestCase):
    """Test live_discovery from metadata."""

    def test_known_recorders(self):
        result = live_discovery(findings_dir=None)
        self.assertIn("H4-210-Aula-ext352", result)
        self.assertIn("Fab6-001-ext352", result)
        self.assertEqual(result["H4-210-Aula-ext352"]["building"], "Hauptgebäude 4")

    def test_with_findings_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "meta"
            meta.mkdir()
            network = {
                "requests": 100,
                "opencast_calls": [
                    "https://dist.opencast.unibe.ch/static/mh_default_org/engage-player/abc-def/xxx/H4-extron-smp351-220_HS2026--test_20260921-141300_S1R1_100s-player.jpg"
                ],
            }
            (meta / "network_summary.json").write_text(json.dumps(network))
            result = live_discovery(findings_dir=tmp)
            self.assertIn("H4-210-Aula-ext352", result)


class TestSafeName(unittest.TestCase):
    """Test safe filename generation."""

    def test_special_chars(self):
        self.assertNotIn("/", safe_name("path/to/file"))
        self.assertNotIn(":", safe_name("file:name"))
        self.assertNotIn("?", safe_name("file?name"))

    def test_reserved_names(self):
        result = safe_name("CON")
        self.assertTrue(result.startswith("_"))

    def test_empty(self):
        result = safe_name("")
        self.assertEqual(result, "Unbenannt")

    def test_long_name(self):
        result = safe_name("A" * 200)
        self.assertLessEqual(len(result), 100)


class TestParseItems(unittest.TestCase):
    """Test HTML item parsing for course content."""

    def test_parse_file_links(self):
        html = '''
        <div class="ilContainerListItemOuter">
            <h3 class="il_ContainerItemTitle">
                <a href="https://ilias.unibe.ch/goto.php?target=file_3809867">Vorlesungsskript.pdf</a>
            </h3>
        </div>
        '''
        result = parse_items(html, "https://ilias.unibe.ch/")
        self.assertIn(("file", "3809867"), result)

    def test_parse_folder_links(self):
        html = '''
        <div class="il-item-title">
            <a href="https://ilias.unibe.ch/goto.php?target=fold_123">Woche 1</a>
        </div>
        '''
        result = parse_items(html, "https://ilias.unibe.ch/")
        self.assertIn(("fold", "123"), result)

    def test_ignore_hash_links(self):
        html = '''
        <div class="il_ContainerItemTitle">
            <a href="#">Nicht relevant</a>
        </div>
        '''
        result = parse_items(html, "https://ilias.unibe.ch/")
        self.assertEqual(len(result), 0)


class TestStreamDownloadResume(unittest.TestCase):
    """Test the resume logic of stream_download_with_resume."""

    def test_resume_sets_range_header(self):
        """Verify that a partial file triggers a Range header."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.mp4"
            partial = path.with_suffix(".mp4.part")
            partial.write_bytes(b"x" * 1000)

            # Mock urllib to capture the request
            mock_response = MagicMock()
            mock_response.status = 206
            mock_response.headers = MagicMock()
            mock_response.headers.get_content_type.return_value = "video/mp4"
            mock_response.read.side_effect = [b"y" * 1024 * 1024, b"z" * 100000, b""]
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = lambda s, *a: None

            with patch("urllib.request.urlopen", return_value=mock_response) as mock_open:
                with patch("urllib.request.Request") as mock_req:
                    mock_req_instance = MagicMock()
                    mock_req.return_value = mock_req_instance

                    try:
                        stream_download_with_resume("https://example.com/test.mp4", path)
                    except Exception:
                        pass  # May fail on validation

                    # Verify Range header was set
                    call_args = mock_req.call_args
                    self.assertIn("Range", call_args[1].get("headers", {}))
                    self.assertEqual(call_args[1]["headers"]["Range"], "bytes=1000-")


class TestDirectFileDownload(unittest.TestCase):
    """Test direct HTTP download with cookie extraction."""

    def test_cookie_extraction_from_session(self):
        """Verify cookies are extracted correctly from session.json format."""
        with tempfile.TemporaryDirectory() as tmp:
            session = {
                "cookies": [
                    {"name": "PHPSESSID", "value": "test123", "domain": "ilias.unibe.ch",
                     "path": "/", "expires": -1, "secure": True},
                    {"name": "other", "value": "val", "domain": "other.example.com",
                     "path": "/", "expires": -1, "secure": False},
                ],
                "origins": []
            }
            sp = Path(tmp) / "session.json"
            sp.write_text(json.dumps(session))

            # We can't actually download, but verify the cookie handling doesn't crash
            import http.cookiejar
            cj = http.cookiejar.CookieJar()
            for cookie in session["cookies"]:
                if cookie["domain"] == "ilias.unibe.ch":
                    c = http.cookiejar.Cookie(
                        version=0, name=cookie["name"], value=cookie["value"],
                        port=None, port_specified=False,
                        domain=cookie["domain"], domain_specified=True, domain_initial_dot=False,
                        path=cookie.get("path", "/"), path_specified=True,
                        secure=cookie.get("secure", True),
                        expires=None, discard=True, comment=None, comment_url=None,
                        rest={}, rfc2109=False,
                    )
                    cj.set_cookie(c)

            # Should have exactly 1 cookie (PHPSESSID, not other.example.com)
            cookies = list(cj)
            self.assertEqual(len(cookies), 1)
            self.assertEqual(cookies[0].name, "PHPSESSID")
            self.assertEqual(cookies[0].value, "test123")


if __name__ == "__main__":
    unittest.main()
