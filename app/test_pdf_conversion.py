import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from pypdf import PdfWriter

import downloader as app
from pdf_conversion import convert_tree


def fake_converter(source, target, work):
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=800)
    writer.add_metadata({'/Title': source.read_text()})
    writer.write(target)


class ConversionTests(unittest.TestCase):
    def test_unicode_conversion_is_scoped_to_selected_course(self):
        with tempfile.TemporaryDirectory(prefix='Übersicht ') as temp:
            root = Path(temp)
            for course in ['Übungen', 'Völkerrecht']:
                (root / course).mkdir()
                (root / course / 'äöü.txt').write_text('Test')
            self.assertEqual(convert_tree(root, fake_converter, only_folders={'Übungen'})['converted'], 1)
            self.assertTrue((root / 'Übungen/äöü.pdf').exists())
            self.assertFalse((root / 'Völkerrecht/äöü.pdf').exists())
            self.assertEqual(convert_tree(root, fake_converter, only_folders={'Übungen'})['unchanged'], 1)

    def test_legacy_name_is_migrated_without_reconversion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'file.docx').write_text('original')
            convert_tree(root, fake_converter)
            (root / 'file.pdf').rename(root / 'file.docx.pdf')
            index_path = root / '.pdf-index.json'
            index = json.loads(index_path.read_text())
            index['file.docx'].pop('pdf_path')
            index_path.write_text(json.dumps(index))
            converter = Mock(side_effect=RuntimeError('must not run'))
            self.assertEqual(convert_tree(root, converter)['unchanged'], 1)
            self.assertTrue((root / 'file.pdf').exists())
            self.assertFalse((root / 'file.docx.pdf').exists())
            converter.assert_not_called()

    def test_backfill_incremental_changes_and_no_overwrite_of_original_pdf(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'existing.docx'
            source.write_text('version1')
            original_pdf = root / 'existing.pdf'
            original_pdf.write_bytes(b'original PDF untouched')
            (root / 'book.epub').write_bytes(b'epub untouched')
            converter = Mock(side_effect=fake_converter)
            self.assertEqual(convert_tree(root, converter)['converted'], 1)
            target = root / 'existing (konvertiert).pdf'
            self.assertTrue(target.exists())
            self.assertEqual(source.read_text(), 'version1')
            self.assertEqual(original_pdf.read_bytes(), b'original PDF untouched')
            self.assertEqual(convert_tree(root, converter)['unchanged'], 1)
            self.assertEqual(converter.call_count, 1)
            source.write_text('version2')
            self.assertEqual(convert_tree(root, converter)['converted'], 1)
            self.assertEqual(len(list(root.glob('.versions/*/existing (konvertiert).pdf'))), 1)
            target.unlink()
            self.assertEqual(convert_tree(root, converter)['converted'], 1)

    def test_failed_conversion_preserves_previous_pdf_and_retries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'existing.docx'
            source.write_text('v1')
            convert_tree(root, fake_converter)
            target = root / 'existing.pdf'
            previous = target.read_bytes()
            source.write_text('v2')
            self.assertEqual(convert_tree(root, Mock(side_effect=RuntimeError('conversion failed')))['errors'], 1)
            self.assertEqual(target.read_bytes(), previous)
            self.assertEqual(convert_tree(root, fake_converter)['converted'], 1)

    def test_connection_failure_still_runs_conversion_and_dry_run_does_not(self):
        with patch('pdf_conversion.convert_tree', return_value={'errors': 0}) as convert:
            with patch.object(app, 'sync', side_effect=app.ConnectionFailure('offline')):
                with self.assertRaises(app.ConnectionFailure):
                    app.sync_with_pdfs(None, {'output': 'downloads'})
                self.assertEqual(convert.call_count, 1)
            convert.reset_mock()
            with patch.object(app, 'sync', return_value=0):
                app.sync_with_pdfs(None, {}, dry_run=True)
                convert.assert_not_called()

    def test_retry_transient_and_permanent_http(self):
        failed = Mock(status=503)
        good = Mock(status=200)
        operation = Mock(side_effect=[failed, good])
        pause = Mock()
        self.assertIs(app.request_with_retry(operation, 'test', pause), good)
        pause.assert_called_once_with(2)
        failed.dispose.assert_called_once()
        operation = Mock(return_value=Mock(status=403))
        app.request_with_retry(operation, 'test', pause)
        operation.assert_called_once()
        with self.assertRaises(app.ConnectionFailure):
            app.request_with_retry(Mock(return_value=Mock(status=503)), 'test', Mock())


if __name__ == '__main__':
    unittest.main()
