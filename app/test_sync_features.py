import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
import downloader as app
from sync_support import parse_date, week_folder, file_revision, DownloadIndex


class DateTests(unittest.TestCase):
    def test_dates_and_iso_year(self):
        ref = date(2026, 9, 15)
        for text, expected in [('Gestern, 08:15', date(2026, 9, 14)),
                               ('Morgen, 16:15', date(2026, 9, 16)),
                               ('30. Sep 2026, 14:15', date(2026, 9, 30)),
                               ('17. Dez 2026', date(2026, 12, 17)),
                               ('Skript 21.09.2026', date(2026, 9, 21))]:
            self.assertEqual(parse_date(text, ref), expected)
        self.assertEqual(week_folder(Path('Kurs'), ref), Path('Kurs/2026/KW (38)'))
        self.assertEqual(week_folder(Path('Kurs'), date(2027, 1, 1)), Path('Kurs/2026/KW (53)'))
        self.assertEqual(week_folder(Path('Kurs'), date(2027, 1, 3)), Path('Kurs/2026/KW (53)'))
        self.assertEqual(week_folder(Path('Kurs'), date(2027, 1, 4)), Path('Kurs/2027/KW (01)'))
        self.assertEqual(parse_date('4. März 2027', ref), date(2027, 3, 4))
        self.assertEqual(parse_date('Übermorgen', ref), date(2026, 9, 17))
        self.assertIsNone(parse_date('31. Feb 2026', ref))
        self.assertIsNone(parse_date('Uebung 1', ref))

    def test_relative_revision_stays_stable(self):
        def row(value):
            return BeautifulSoup(f'<span class="il_ItemProperty">{value}, 10:00</span>', 'html.parser')
        self.assertEqual(file_revision(row('Heute'), 'A', date(2026, 9, 15)),
                         file_revision(row('15. Sep 2026'), 'A', date(2026, 9, 29)))
        self.assertIsNone(file_revision(BeautifulSoup('<p>pdf 20 KB</p>', 'html.parser'), 'A', date.today()))


class IncrementalTests(unittest.TestCase):
    def test_unicode_download_and_reloaded_index(self):
        from urllib.parse import quote
        with tempfile.TemporaryDirectory(prefix='Übungen äöü ') as temp:
            root = Path(temp)
            folder = Path('Öffentliches Recht – Übungen/2027/KW (01)')
            name = 'Übersicht äöü ÄÖÜ ß.pdf'
            response = Mock(ok=True, status=200, url=app.BASE + 'ilias.php')
            response.headers = {'content-disposition': "attachment; filename*=UTF-8''" + quote(name)}
            response.body.return_value = b'%PDF-test'
            context = Mock()
            context.request.get.return_value = response
            path, _ = app.download(context, root, folder, '7', 'v1', DownloadIndex(root))
            self.assertEqual(path.name, '7__' + name)
            again, status = app.download(context, root, folder, '7', 'v1', DownloadIndex(root))
            self.assertEqual(path, again)
            self.assertEqual(context.request.get.call_count, 1)

    def test_single_course_can_be_inactive_without_changing_config(self):
        config = {'courses': [{'ref_id': 1, 'name': 'ÖR', 'enabled': False},
                              {'ref_id': 2, 'name': 'Übung'}]}
        self.assertEqual([c['ref_id'] for c in app.select_courses(config)['courses']], [2])
        self.assertEqual([c['ref_id'] for c in app.select_courses(config, 1)['courses']], [1])
        self.assertFalse(config['courses'][0]['enabled'])
        with self.assertRaises(ValueError):
            app.select_courses(config, 3)

    def test_new_unchanged_updated_and_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            context = Mock()
            response = Mock(ok=True, status=200, url=app.BASE + 'ilias.php')
            response.headers = {'content-disposition': 'attachment; filename="slides.pdf"'}
            response.body.return_value = b'%PDF-old'
            context.request.get.return_value = response
            index = DownloadIndex(root)
            path, _ = app.download(context, root, Path('Kurs/2026/KW (38)'), '1', 'v1', index)
            self.assertEqual(context.request.get.call_count, 1)
            # Reload from disk as if started again two weeks later.
            index = DownloadIndex(root)
            app.download(context, root, Path('Kurs/2026/KW (38)'), '1', 'v1', index)
            self.assertEqual(context.request.get.call_count, 1)
            # Same file linked in another week is copied locally, not downloaded again.
            app.download(context, root, Path('Kurs/2026/KW (39)'), '1', 'v1', index)
            self.assertEqual(context.request.get.call_count, 1)
            response.body.return_value = b'%PDF-new'
            app.download(context, root, Path('Kurs/2026/KW (38)'), '1', 'v2', index)
            self.assertEqual(context.request.get.call_count, 2)
            self.assertEqual(path.read_bytes(), b'%PDF-new')
            self.assertEqual(next(path.parent.glob('.versions/*/*')).read_bytes(), b'%PDF-old')
            path.unlink()
            app.download(context, root, Path('Kurs/2026/KW (38)'), '1', 'v2', index)
            self.assertEqual(context.request.get.call_count, 3)
            app.download(context, root, Path('Kurs/2026/KW (38)'), '1', 'v2', index, verify=True)
            self.assertEqual(context.request.get.call_count, 4)

    def test_failed_response_never_enters_index(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            context = Mock()
            response = Mock(ok=True, status=200, url=app.BASE + 'login.php', headers={})
            context.request.get.return_value = response
            index = DownloadIndex(root)
            with self.assertRaises(RuntimeError):
                app.download(context, root, Path('Kurs'), '1', 'v1', index)
            self.assertEqual(index.files, {})
            self.assertFalse(index.path.exists())

    def test_add_course_preserves_existing_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'config.json'
            config = {'courses': [{'ref_id': 1, 'name': 'Alt'}], 'output': 'downloads'}
            app.add_course(path, config, 'https://ilias.unibe.ch/go/crs/2', 'Neu')
            self.assertEqual(len(json.loads(path.read_text(encoding='utf-8'))['courses']), 2)
            app.add_course(path, config, '2', 'Doppelt')
            self.assertEqual(len(config['courses']), 2)


class WorkflowTests(unittest.TestCase):
    def test_browser_crawl_week_placement_and_second_run(self):
        from playwright.sync_api import sync_playwright
        def item(ref, title):
            return f'''<div class="ilContainerListItemOuter"><h3 class="il_ContainerItemTitle">
            <a href="{app.BASE}ilias.php?cmdClass=ilObjFileGUI&cmd=sendfile&ref_id={ref}">{title}</a></h3>
            <span class="il_ItemProperty">14. Aug 2026, 10:00</span></div>'''
        def link(url, title):
            return f'<h3 class="il_ContainerItemTitle"><a href="{url}">{title}</a></h3>'
        course = item(1, 'Allgemein') + item(2, 'Datiertes Skript')
        course += link(app.BASE + 'ilias.php?cmd=infoScreen&ref_id=10', '14. Sep 2026, 10:15: Sitzung')
        course += link(app.BASE + 'ilias.php?cmd=infoScreen&ref_id=11', '21. Sep 2026, 10:15: Sitzung')
        course += link(app.BASE + 'go/fold/20', 'Uebung 1')
        course += link(app.BASE + 'go/fold/21', 'Uebung 2 (noch leer)')
        course += link(app.BASE + 'ilias.php?cmd=infoScreen&ref_id=12', '4. Jan 2027: Noch leer')
        count = []
        def route_handler(route):
            url = route.request.url
            if 'sendfile' in url:
                count.append(url)
                route.fulfill(body=b'%PDF-test', headers={'Content-Disposition': 'attachment; filename="Skript.pdf"'})
                return
            if 'ref_id=10' in url:
                body = '<h1>14. Sep 2026, 10:15: Sitzung</h1>' + item(2, 'Datiertes Skript')
            elif 'ref_id=11' in url:
                body = '<h1>21. Sep 2026, 10:15: Sitzung</h1>' + item(2, 'Datiertes Skript')
            elif '/fold/20' in url:
                body = item(3, 'Fall')
            elif '/fold/21' in url:
                body = ''
            else:
                body = course
            route.fulfill(body='<span class="glyphicon-logout"></span>' + body, content_type='text/html')
        with tempfile.TemporaryDirectory() as temp, sync_playwright() as p:
            root = Path(temp)
            state = root / 'session.json'
            state.write_text('{}')
            browser = p.chromium.launch()
            context = browser.new_context()
            context.route('**/*', route_handler)
            # APIRequestContext doesn't use page routing; provide equivalent responses.
            request = Mock()
            def get(url, **kwargs):
                response = Mock(ok=True, status=200, url=url)
                if 'sendfile' in url:
                    count.append(url)
                    response.headers = {'content-disposition': 'attachment; filename="Skript.pdf"'}
                    response.body.return_value = b'%PDF-test'
                    return response
                if 'ref_id=10' in url:
                    body = '<h1>14. Sep 2026, 10:15: Sitzung</h1>' + item(2, 'Datiertes Skript')
                elif 'ref_id=11' in url:
                    body = '<h1>21. Sep 2026, 10:15: Sitzung</h1>' + item(2, 'Datiertes Skript')
                elif '/fold/20' in url:
                    body = item(3, 'Fall')
                elif '/fold/21' in url:
                    body = ''
                elif 'ref_id=12' in url:
                    body = '<h1>4. Jan 2027: Noch leer</h1>'
                else:
                    body = course
                response.headers = {'content-type': 'text/html'}
                response.text.return_value = '<span class="glyphicon-logout"></span>' + body
                response.body.return_value = ('<span class="glyphicon-logout"></span>' + body).encode()
                return response
            request.get.side_effect = get
            wrapper = SimpleNamespace(new_page=context.new_page, pages=context.pages,
                                      on=context.on, request=request, storage_state=context.storage_state)
            fake_p = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kw: SimpleNamespace(
                new_context=lambda **kw: wrapper, close=lambda: None)))
            config = {'output': str(root / 'downloads'), 'courses': [{'ref_id': 99, 'name': 'Kurs'}],
                      'delay_seconds': 0, 'max_pages': 20}
            try:
                with patch.object(app, 'HERE', root), patch.object(app, 'STATE', state):
                    self.assertEqual(app.sync(fake_p, config, False), 0)
                    folder = root / 'downloads/Kurs__99'
                    self.assertTrue((folder / '1__Skript.pdf').exists())
                    self.assertFalse((folder / '2__Skript.pdf').exists())
                    self.assertTrue((folder / '2026/KW (38)/2__Skript.pdf').exists())
                    self.assertTrue((folder / '2026/KW (39)/2__Skript.pdf').exists())
                    self.assertTrue((folder / 'Uebung 1__20/3__Skript.pdf').exists())
                    self.assertTrue((folder / 'Uebung 2 (noch leer)__21').is_dir())
                    self.assertTrue((folder / '2027/KW (01)').is_dir())
                    self.assertEqual(len(count), 3)
                    self.assertEqual(app.sync(fake_p, config, False), 0)
                    self.assertEqual(len(count), 3)
                    course += item(4, 'Neu hinzugefuegt')
                    self.assertEqual(app.sync(fake_p, config, False), 0)
                    self.assertEqual(len(count), 4)
                    self.assertTrue((folder / '4__Skript.pdf').exists())
                    with patch.object(app, 'request_with_retry', side_effect=app.ConnectionFailure('offline')):
                        # Even when every page fails, return a partial-run status so
                        # the interval runner can try again rather than exit forever.
                        self.assertEqual(app.sync(fake_p, config, False), 1)
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
