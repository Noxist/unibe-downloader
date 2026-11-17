import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from pypdf import PdfWriter
import remarkable as rm
import downloader


class LocalSFTP:
    def __init__(self, root):
        self.root = root
        self.puts = 0

    def path(self, name):
        return self.root / Path(name).name

    def get_channel(self):
        return Mock()

    def stat(self, path):
        return self.root.stat()

    def listdir(self, path):
        return [p.name for p in self.root.iterdir()]

    def open(self, name, mode='r'):
        return self.path(name).open(mode + ('b' if 'b' not in mode else ''))

    def rename(self, old, new):
        if self.path(new).exists():
            raise FileExistsError(new)
        self.path(old).rename(self.path(new))

    def put(self, source, target):
        self.puts += 1
        shutil.copyfile(source, self.path(target))

    def remove(self, path):
        self.path(path).unlink()

    def close(self):
        pass


def pdf(path, pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(400, 600)
    writer.write(path)


class RemarkableTests(unittest.TestCase):
    def test_readable_encrypted_pdf_preserves_original(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'original.pdf'
            writer = PdfWriter()
            writer.add_blank_page(400, 600)
            writer.encrypt('', 'owner')
            writer.write(path)
            digest = rm.file_hash(path)
            target = rm.prepare_document(path, root)
            self.assertNotEqual(path, target)
            self.assertEqual(rm.file_hash(path), digest)
            self.assertEqual(rm.content_for(target)['pageCount'], 1)

    def test_collect_unicode_hidden_and_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in ['Übungen/KW (01)', 'Anderer Kurs', '.versions/alt']:
                (root / folder).mkdir(parents=True)
                (root / folder / 'Übersicht.PDF').touch()
            (root / 'Übungen/a.docx').touch()
            _, files, folders = rm.collect(root, {'Übungen'})
            self.assertEqual([p.name for p in files], ['Übersicht.PDF'])
            self.assertIn(Path('Übungen/KW (01)'), folders)

    def test_transfer_retry_versions_annotations_and_hash_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'Semester äöü'
            source.mkdir()
            course = source / 'Übungen__123' / '2027' / 'KW (01)'
            course.mkdir(parents=True)
            path = course / '456__Übersicht.pdf'
            pdf(path)
            remote = root / 'remote'
            remote.mkdir()
            sftp = LocalSFTP(remote)
            client = Mock()
            client.open_sftp.return_value = sftp
            cfg = {'library': '/library', 'folder': 'ILIAS'}

            def command(client, command):
                if command.startswith('sha256sum '):
                    return rm.file_hash(remote / command.split('/')[-1].strip("'")) + ' file'
                if 'is-active' in command:
                    return 'active'
                return ''

            with patch.object(rm, 'PRIVATE', root / 'private'), patch.object(rm, 'ROOT', root), \
                    patch.object(rm, 'open_device', return_value=(client, cfg)), \
                    patch.object(rm, 'run', side_effect=command):
                self.assertEqual(rm.sync_tree(source), 0)
                self.assertEqual(sftp.puts, 1)
                first = next(remote.glob('*.pdf'))
                annotation = remote / (first.stem + '.rm')
                annotation.write_bytes(b'handwriting')
                self.assertEqual(rm.sync_tree(source), 0)
                self.assertEqual(sftp.puts, 1)
                pdf(path, 2)
                self.assertEqual(rm.sync_tree(source), 0)
                self.assertEqual(len(list(remote.glob('*.pdf'))), 2)
                self.assertEqual(annotation.read_bytes(), b'handwriting')
                self.assertTrue(first.exists())
                # Remote corruption must be reported; never overwrite annotations/documents.
                latest = next(p for p in remote.glob('*.pdf') if p != first)
                latest.write_bytes(b'corrupt')
                self.assertEqual(rm.sync_tree(source), 1)
                self.assertEqual(sftp.puts, 2)

    def test_unverified_upload_never_publishes_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'a.pdf'
            pdf(source)
            remote = root / 'remote'
            remote.mkdir()
            client = Mock()
            client.open_sftp.return_value = LocalSFTP(remote)
            lib = rm.Library(client, '/library')
            with patch.object(lib, 'digest', return_value='wrong'):
                with self.assertRaises(RuntimeError):
                    lib.upload('new-id', source, '', 'A', rm.file_hash(source))
            self.assertFalse((remote / 'new-id.metadata').exists())

    def test_downloader_handoff_scope_and_failure(self):
        cfg = {'output': 'downloads', 'remarkable_auto': True, '_single_course': True,
               'courses': [{'name': 'Übungen', 'ref_id': 1}]}
        with patch.object(downloader, 'sync', return_value=0), \
                patch('pdf_conversion.convert_tree', return_value={'errors': 0}), \
                patch.object(rm, 'sync_tree', return_value=0) as send, \
                patch.object(downloader, 'check_stop'):
            self.assertEqual(downloader.sync_with_pdfs(None, cfg), 0)
            self.assertEqual(send.call_args.kwargs['only_folders'], {'Übungen__1'})
            send.reset_mock()
            downloader.sync_with_pdfs(None, cfg, dry_run=True)
            send.assert_not_called()
            with patch.object(downloader, 'sync', return_value=1):
                self.assertEqual(downloader.sync_with_pdfs(None, cfg), 1)
            send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
