"""Desktop interface; all settings resolve relative to the movable app folder."""
import codecs
import json
import os
import sys
from pathlib import Path
from PySide6.QtCore import QProcess, QProcessEnvironment, Qt
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QPlainTextEdit, QProgressBar, QFileDialog, QInputDialog, QMessageBox, QCheckBox)
from downloader import atomic_json, object_id

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / 'app/config.json'


class App(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('ILIAS Uni Bern · Downloader')
        self.resize(960, 760)
        self.setMinimumSize(820, 680)
        self.config_data = json.loads(CONFIG.read_text(encoding='utf-8-sig'))
        self.process = None
        self.setStyleSheet('''
            QWidget { background:#f3f5f9; color:#183040; font:10pt "Segoe UI"; }
            QLabel#title { font-size:23pt; font-weight:650; }
            QPushButton { background:white; border:1px solid #c9d4df; border-radius:6px; padding:9px 12px; }
            QPushButton:hover { background:#e3eef2; }
            QPushButton:disabled { color:#94a3ad; background:#edf0f4; }
            QPushButton#accent { background:#146c73; color:white; border:0; }
            QLineEdit,QTableWidget { background:white; border:1px solid #c9d4df; border-radius:5px; padding:5px; }
            QTableWidget { alternate-background-color:#f3f6f9; selection-background-color:#d7ecee; selection-color:#183040; }
            QTableWidget::item:selected { background:#d7ecee; color:#183040; }
            QHeaderView::section { background:#e6ecf2; padding:7px; border:0; font-weight:600; }
            QPlainTextEdit { background:#152632; color:#e4eef2; border:0; border-radius:6px; padding:10px; font:10pt "Consolas"; }
            QProgressBar { border:0; background:#e0e7ee; height:5px; }
            QProgressBar::chunk { background:#146c73; }
        ''')
        body = QVBoxLayout(self)
        body.setContentsMargins(24, 20, 24, 20)
        body.setSpacing(12)
        title = QLabel('ILIAS Uni Bern')
        title.setObjectName('title')
        body.addWidget(title)
        target = QHBoxLayout()
        target.addWidget(QLabel('Speicherort'))
        self.output = QLineEdit(self.config_data.get('output', 'downloads'))
        target.addWidget(self.output, 1)
        self.choose = self.button('Ordner wählen', self.choose_folder, target)
        self.button('Öffnen', self.open_folder, target)
        body.addLayout(target)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Aktiv', 'Kurs', 'ILIAS-ID'])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 70)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnHidden(2, True)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(160)
        self.table.itemChanged.connect(self.changed)
        body.addWidget(self.table, 2)
        edit = QHBoxLayout()
        self.edit_buttons = [self.button(text, action, edit) for text, action in
                             [('Hinzufügen', self.add_course), ('Bearbeiten', self.edit_course), ('Entfernen', self.remove_course)]]
        edit.addStretch()
        body.addLayout(edit)
        actions = QHBoxLayout()
        self.action_buttons = []
        for text, command, args in [('Anmelden', 'login', []), ('Alle aktualisieren', 'sync', [])]:
            b = self.button(text, lambda checked=False, c=command, a=args: self.run(c, a), actions)
            if text == 'Alle aktualisieren':
                b.setObjectName('accent')
            self.action_buttons.append(b)
        self.single_button = self.button('Diesen Kurs aktualisieren', self.run_selected, actions)
        self.single_button.setEnabled(False)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        body.addLayout(actions)
        tablet = QHBoxLayout()
        self.rm_auto = QCheckBox('Nach Update an reMarkable senden')
        self.rm_auto.setChecked(self.config_data.get('remarkable_auto', False))
        self.rm_auto.toggled.connect(lambda: self.save())
        tablet.addWidget(self.rm_auto)
        tablet.addStretch()
        self.rm_setup = self.button('reMarkable einrichten', self.setup_remarkable, tablet)
        self.rm_send = self.button('Jetzt senden', lambda: self.run('sync', [], script='remarkable.py'), tablet)
        body.addLayout(tablet)
        self.status = QLabel('Bereit.')
        self.status.setWordWrap(True)
        body.addWidget(self.status)
        controls = QHBoxLayout()
        self.confirm = self.button('Im Browser angemeldet → Weiter', self.confirm_login, controls)
        self.confirm.hide()
        controls.addStretch()
        self.stop_button = self.button('Stop', self.stop, controls)
        self.stop_button.setEnabled(False)
        body.addLayout(controls)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1)
        body.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setMinimumHeight(70)
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(1500)
        body.addWidget(self.log, 2)
        self.refresh()

    def button(self, text, action, layout):
        button = QPushButton(text)
        button.clicked.connect(action)
        layout.addWidget(button)
        return button

    def refresh(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.config_data['courses']))
        for i, course in enumerate(self.config_data['courses']):
            active = QTableWidgetItem()
            active.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            active.setCheckState(Qt.CheckState.Checked if course.get('enabled', True) else Qt.CheckState.Unchecked)
            self.table.setItem(i, 0, active)
            name = QTableWidgetItem(course['name'])
            name.setToolTip(course['name'])
            self.table.setItem(i, 1, name)
            self.table.setItem(i, 2, QTableWidgetItem(str(course['ref_id'])))
        self.table.blockSignals(False)
        self.selection_changed()

    def changed(self, item):
        if item.column() == 0:
            self.config_data['courses'][item.row()]['enabled'] = item.checkState() == Qt.CheckState.Checked
            self.save()

    def save(self):
        value = self.output.text().strip()
        if not value:
            raise ValueError('Bitte einen Speicherort angeben.')
        self.config_data['output'] = value
        self.config_data['remarkable_auto'] = self.rm_auto.isChecked()
        atomic_json(CONFIG, self.config_data)

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, 'Speicherort wählen', str(ROOT / self.output.text()))
        if path:
            destination = Path(path).resolve()
            self.output.setText(str(destination.relative_to(ROOT)) if destination.is_relative_to(ROOT) else str(destination))
            self.save()

    def open_folder(self):
        destination = (ROOT / self.output.text()).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        os.startfile(destination)

    def add_course(self):
        url, ok = QInputDialog.getText(self, 'Kurs hinzufügen', 'Kurslink aus ILIAS oder ref_id:')
        if not ok or not url.strip():
            return
        url = url.strip()
        identity = ('crs', url) if url.isdigit() else object_id(url)
        if not identity or identity[0] != 'crs':
            QMessageBox.warning(self, 'Kurslink', 'Bitte die Kurs-Inhaltsseite (z.B. /go/crs/3723279) verwenden.')
            return
        if any(str(c['ref_id']) == identity[1] for c in self.config_data['courses']):
            QMessageBox.information(self, 'Bereits vorhanden', 'Dieser Kurs steht bereits in der Liste.')
            return
        name, ok = QInputDialog.getText(self, 'Ordnername', 'Name der Vorlesung / Übung:')
        if ok and name.strip():
            self.config_data['courses'].append({'ref_id': int(identity[1]), 'name': name.strip(), 'enabled': True})
            self.save()
            self.refresh()

    def edit_course(self):
        row = self.table.currentRow()
        if row >= 0:
            course = self.config_data['courses'][row]
            name, ok = QInputDialog.getText(self, 'Kursname', 'Name (neuer Zielordner beim nächsten Update):', text=course['name'])
            if ok and name.strip():
                course['name'] = name.strip()
                self.save()
                self.refresh()

    def remove_course(self):
        row = self.table.currentRow()
        if row >= 0:
            del self.config_data['courses'][row]
            self.save()
            self.refresh()

    def busy(self, value):
        for widget in self.action_buttons + self.edit_buttons + [self.table, self.output, self.choose,
                                                                self.rm_auto, self.rm_setup, self.rm_send]:
            widget.setEnabled(not value)
        self.stop_button.setEnabled(value)
        self.single_button.setEnabled(not value and self.table.currentRow() >= 0)
        self.progress.setRange(0, 0 if value else 1)

    def selection_changed(self):
        self.single_button.setEnabled(self.process is None and self.table.currentRow() >= 0)

    def run_selected(self):
        row = self.table.currentRow()
        if row >= 0:
            self.run('sync', ['--course', str(self.config_data['courses'][row]['ref_id'])])

    def setup_remarkable(self):
        username, ok = QInputDialog.getText(self, 'reMarkable · 1/2',
            'Tablet per USB anschliessen. SSH-Benutzername:', text='root')
        if not ok or not username.strip():
            return
        password, ok = QInputDialog.getText(self, 'reMarkable · 2/2',
            'SSH-Passwort (Hilfe → Copyrights und Lizenzen):', QLineEdit.EchoMode.Password)
        if not ok or not password:
            return
        self.run('setup', ['--setup-stdin'], script='remarkable.py',
                 stdin_data={'username': username.strip(), 'password': password, 'wifi': ''})

    def run(self, command, args, script='downloader.py', stdin_data=None):
        if self.process:
            return
        try:
            self.save()
        except Exception as exc:
            QMessageBox.warning(self, 'Einstellungen', str(exc))
            return
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(ROOT))
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert('PYTHONUTF8', '1')
        env.insert('PYTHONIOENCODING', 'utf-8')
        self.process.setProcessEnvironment(env)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)
        self.current_job = (script, command)
        if stdin_data is not None:
            self.setup_payload = (json.dumps(stdin_data) + '\n').encode('utf-8')
            self.process.started.connect(self.send_setup)
        self.busy(True)
        self.confirm.setVisible(command == 'login')
        self.log.appendPlainText('\n' + '─' * 50)
        self.status.setText('Wird gestartet …')
        self.process.start(str(Path(sys.executable).with_name('python.exe')),
                           ['-u', str(ROOT / 'app' / script), command, *args])

    def send_setup(self):
        self.process.write(self.setup_payload)
        self.process.closeWriteChannel()
        self.setup_payload = None

    def read_output(self):
        text = self.decoder.decode(bytes(self.process.readAllStandardOutput()))
        cursor = self.log.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.log.setTextCursor(cursor)
        if text.strip():
            self.status.setText(text.strip().splitlines()[-1][:200])

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.setup_payload = None
            self.status.setText('Start fehlgeschlagen: ' + self.process.errorString())
            self.process.deleteLater()
            self.process = None
            self.busy(False)
            self.confirm.hide()

    def finished(self, code, status):
        self.read_output()
        self.process.deleteLater()
        self.process = None
        self.busy(False)
        self.confirm.hide()
        if code == 0 and self.current_job == ('remarkable.py', 'setup'):
            self.rm_auto.setChecked(True)
        self.status.setText('Fertig.' if code == 0 else 'Gestoppt.' if code == 130 else 'Mit Fehlern beendet – Details im Verlauf.')

    def confirm_login(self):
        if self.process:
            self.process.write(b'\n')

    def stop(self):
        (ROOT / '.auth').mkdir(exist_ok=True)
        (ROOT / '.auth/stop-request').touch()
        if self.process:
            self.process.write(b'q\n')
        self.status.setText('Stop angefordert – laufende Anfrage/Konvertierung wird noch abgeschlossen.')

    def closeEvent(self, event):
        if self.process:
            self.stop()
            event.ignore()
        else:
            try:
                self.save()
            except Exception as exc:
                QMessageBox.warning(self, 'Einstellungen', str(exc))
                event.ignore()


if __name__ == '__main__':
    application = QApplication(sys.argv)
    application.setStyle('Fusion')
    window = App()
    window.show()
    sys.exit(application.exec())
