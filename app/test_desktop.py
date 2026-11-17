import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
import gui


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_courses_and_offline_worker_in_relocated_folder(self):
        with tempfile.TemporaryDirectory(prefix='ILIAS test ') as temp:
            root = Path(temp) / 'Verschobene App äöü'
            app = root / 'app'
            app.mkdir(parents=True)
            (root / 'downloads').mkdir()
            for filename in ['downloader.py', 'pdf_conversion.py', 'sync_support.py']:
                shutil.copyfile(Path(__file__).parent / filename, app / filename)
            config = app / 'config.json'
            config.write_text(json.dumps({'output': 'downloads', 'courses': [{'ref_id': 1, 'name': 'Test'}]}))
            with patch.object(gui, 'ROOT', root), patch.object(gui, 'CONFIG', config):
                window = gui.App()
                window.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
                self.assertFalse(json.loads(config.read_text())['courses'][0]['enabled'])
                with patch.object(gui.QInputDialog, 'getText', side_effect=[('https://ilias.unibe.ch/go/crs/2', True), ('Neuer Kurs', True)]):
                    window.add_course()
                self.assertEqual(window.table.rowCount(), 2)
                with patch.object(gui.QInputDialog, 'getText', side_effect=[('root', True), ('test-secret', True)]), \
                        patch.object(window, 'run') as setup:
                    window.setup_remarkable()
                    self.assertEqual(setup.call_args.args, ('setup', ['--setup-stdin']))
                    self.assertEqual(setup.call_args.kwargs['script'], 'remarkable.py')
                    self.assertEqual(setup.call_args.kwargs['stdin_data']['password'], 'test-secret')
                self.assertNotIn('test-secret', config.read_text())
                self.assertNotIn('test-secret', window.log.toPlainText())
                window.table.selectRow(0)
                with patch.object(window, 'run') as run:
                    window.run_selected()
                    run.assert_called_once_with('sync', ['--course', '1'])
                window.run('convert-pdfs', [])
                deadline = time.monotonic() + 20
                while window.process and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(.02)
                self.assertIsNone(window.process)
                self.assertEqual(window.status.text(), 'Fertig.')
                self.assertIn('PDF-Pruefung', window.log.toPlainText())
                window.close()
            result = subprocess.run([sys.executable, str(app / 'downloader.py'), '--help'],
                                    cwd=Path(temp), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
