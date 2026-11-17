import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import json
from downloader import (object_id, parse_items, safe_name, local_path, save_download,
                        diagnostic_url, authenticated, find_authenticated_page, BrowserLog,
                        session_expansions)


class DownloaderTests(unittest.TestCase):
    def test_unibe_course_links_and_session_info(self):
        self.assertEqual(object_id('/go/crs/3687719'), ('crs', '3687719'))
        self.assertEqual(object_id('ilias.php?baseClass=ilrepositorygui&cmd=infoScreen&ref_id=3687743'), ('info', '3687743'))
        html = '''<h3 class="il_ContainerItemTitle"><a href="#">Vorschau</a></h3>
        <a href="ilias.php?cmdClass=ilObjCourseGUI&cmd=view&ref_id=1&crs_next_sess=1">Mehr</a>
        <a href="ilias.php?cmdClass=ilObjCourseGUI&cmd=delete&ref_id=1&crs_prev_sess=1">Nein</a>'''
        self.assertEqual(parse_items(html, 'https://ilias.unibe.ch/go/crs/1'), {})
        self.assertEqual(len(session_expansions(html, 'https://ilias.unibe.ch/', '1')), 1)

    def test_provided_html_exports(self):
        folder = next((parent / 'html' for parent in Path(__file__).resolve().parents
                       if (parent / 'html').exists()), Path('missing-html-exports'))
        if not folder.exists():
            self.skipTest('Private HTML exports are not distributed with the code')
        course_pages = list(folder.glob('Meine Kurse*.html'))
        self.assertTrue(course_pages)
        for path in course_pages:
            items = parse_items(path.read_text(encoding='utf-8'), 'https://ilias.unibe.ch/')
            self.assertEqual(len([key for key in items if key[0] == 'crs']), 7)
        path = next(folder.glob('Inhalt*10025*'))
        items = parse_items(path.read_text(encoding='utf-8'), 'https://ilias.unibe.ch/')
        self.assertIn(('info', '3687743'), items)
        path = next(folder.glob('Info*Völkerrecht*'))
        self.assertIn(('file', '3787000'), parse_items(path.read_text(encoding='utf-8'), 'https://ilias.unibe.ch/'))

    def test_diagnostic_urls_remove_secrets(self):
        result = diagnostic_url('https://user:account-2@example.invalid/ilias.php?token=SECRET#SECRET')
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(result['query_keys'], ['token'])
        self.assertEqual(diagnostic_url('https://login.eduid.ch/session/SECRET')['path'], '[redacted]')

    def test_browser_login_and_logging(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as temp, patch('downloader.HERE', Path(temp)), sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                context = browser.new_context()
                log = BrowserLog(context, 'test')
                page = context.new_page()
                context.route('**/*', lambda route: route.fulfill(body='<html><body>Test</body></html>', content_type='text/html'))
                page.goto('https://ilias.unibe.ch/login.php')
                page.set_content('<input type="password" value="SECRET_PASSWORD">')
                self.assertFalse(authenticated(page))
                second = context.new_page()
                second.goto('https://ilias.unibe.ch/ilias.php?token=SECRET_TOKEN')
                second.set_content('<button onclick="logout()"><span class="glyphicon-logout"></span>Abmelden</button>')
                self.assertTrue(authenticated(second))
                self.assertEqual(find_authenticated_page(context), second)
                second.set_content('<main>ILIAS-Inhalt wird noch geladen</main>')
                self.assertTrue(authenticated(second))
                log.snapshot_all('test')
                second.set_content('<input type="password"><span class="glyphicon-logout"></span>')
                self.assertFalse(authenticated(second))
                raw = log.path.read_text(encoding='utf-8')
                self.assertNotIn('SECRET_PASSWORD', raw)
                self.assertNotIn('SECRET_TOKEN', raw)
                events = [json.loads(line)['event'] for line in raw.splitlines()]
                self.assertIn('request', events)
                self.assertIn('response', events)
                self.assertIn('page_structure', events)
            finally:
                browser.close()

    def test_link_formats(self):
        for link in ('goto.php?target=file_123', 'goto_ilias3_unibe_file_123.html',
                     '/file/123', 'ilias.php?cmdClass=ilObjFileGUI&ref_id=123&cmd=sendfile'):
            self.assertEqual(object_id(link), ('file', '123'))

    def test_reject_external_and_actions(self):
        for link in ('https://evil.example/goto.php?target=file_1',
                     'ilias.php?cmdClass=ilObjFileGUI&ref_id=1&cmd=delete',
                     'http://ilias.unibe.ch/goto.php?target=file_1'):
            self.assertIsNone(object_id(link))

    def test_only_repository_titles(self):
        html = '''<nav><a href="goto.php?target=fold_9">Parent</a></nav>
        <h3 class="il_ContainerItemTitle"><a href="goto.php?target=file_12">Skript &amp; Folien</a></h3>
        <div class="il-item-title"><a href="goto.php?target=fold_13">Woche 1</a></div>'''
        items = parse_items(html, 'https://ilias.unibe.ch/')
        self.assertEqual(set(items), {('file', '12'), ('fold', '13')})
        self.assertEqual(items[('file', '12')][0], 'Skript & Folien')

    def test_safe_paths(self):
        self.assertEqual(safe_name('CON.pdf'), '_CON.pdf')
        self.assertNotIn('/', safe_name('../../foo.pdf'))
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                local_path(Path(temp), '../outside')

    def test_updates_preserve_old_version_and_identical_skips(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = (root, Path('Kurs'), '123', 'skript.pdf')
            path, status = save_download(*args, b'old')
            self.assertEqual(status, 'gespeichert')
            self.assertEqual(save_download(*args, b'old')[1], 'unveraendert')
            save_download(*args, b'new')
            self.assertEqual(path.read_bytes(), b'new')
            self.assertEqual(next((root / 'Kurs/.versions').rglob('*.pdf')).read_bytes(), b'old')
            other, _ = save_download(root, Path('Kurs'), '124', 'skript.pdf', b'other')
            self.assertNotEqual(path, other)


if __name__ == '__main__':
    unittest.main()
