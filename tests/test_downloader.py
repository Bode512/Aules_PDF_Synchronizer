import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from downloader import AuthenticatedDownloader
from file_manager import build_destination_path


class FakeResponse:
    def __init__(self, content, headers, url):
        self.content = content
        self.headers = headers
        self.url = url
        self.status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def iter_content(self, chunk_size):
        yield self.content


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base = Path(self.temp_dir.name)
        self.downloader = AuthenticatedDownloader([], "https://aules.example/")

    def test_downloads_docx_using_server_filename_and_keeps_course_folders(self):
        response = FakeResponse(
            b"PK\x03\x04word-document",
            {"Content-Type": "application/octet-stream", "Content-Disposition": 'attachment; filename="Tema.docx"'},
            "https://aules.example/pluginfile.php/123",
        )
        destination = build_destination_path(self.base, "Matemáticas", "Tema 1", "Presentación")
        with patch.object(self.downloader.session, "get", return_value=response):
            result = self.downloader.download("https://aules.example/mod/resource/view.php?id=123", destination)

        self.assertTrue(result.success)
        self.assertEqual(result.path, self.base / "Matemáticas" / "Tema 1" / "Tema.docx")
        self.assertEqual(result.path.read_bytes(), response.content)

    def test_downloads_powerpoint_and_pdf_without_changing_their_extensions(self):
        cases = [
            ("Slides.pptx", b"PK\x03\x04powerpoint"),
            ("Notes.pdf", b"%PDF-1.7\ncontent"),
        ]
        for filename, content in cases:
            with self.subTest(filename=filename):
                response = FakeResponse(
                    content,
                    {"Content-Type": "application/octet-stream", "Content-Disposition": f'attachment; filename="{filename}"'},
                    "https://aules.example/pluginfile.php/123",
                )
                destination = build_destination_path(self.base, "Historia", "Unidad 2", "Recurso")
                with patch.object(self.downloader.session, "get", return_value=response):
                    result = self.downloader.download("https://aules.example/resource", destination)
                self.assertTrue(result.success)
                self.assertEqual(result.path.name, filename)

    def test_rejects_html_instead_of_saving_it_as_a_document(self):
        response = FakeResponse(
            b"<!doctype html><html>login</html>",
            {"Content-Type": "application/octet-stream", "Content-Disposition": 'attachment; filename="Tema.txt"'},
            "https://aules.example/resource",
        )
        destination = build_destination_path(self.base, "Historia", "Unidad 2", "Tema.txt")
        with patch.object(self.downloader.session, "get", return_value=response):
            result = self.downloader.download("https://aules.example/resource", destination)

        self.assertFalse(result.success)
        self.assertIn("no es un archivo", result.error)
        self.assertFalse(destination.exists())

    def test_skips_existing_file_after_resolving_real_filename(self):
        destination = build_destination_path(self.base, "Historia", "Unidad 2", "Recurso")
        existing = destination.parent / "Tema.pptx"
        existing.write_bytes(b"old version")
        response = FakeResponse(
            b"PK\x03\x04new powerpoint",
            {"Content-Type": "application/octet-stream", "Content-Disposition": 'attachment; filename="Tema.pptx"'},
            "https://aules.example/pluginfile.php/123",
        )
        with patch.object(self.downloader.session, "get", return_value=response):
            result = self.downloader.download("https://aules.example/resource", destination)

        self.assertTrue(result.success)
        self.assertTrue(result.skipped)
        self.assertEqual(existing.read_bytes(), b"old version")

    def test_response_filename_uses_mime_type_when_header_has_no_filename(self):
        response = requests.Response()
        response.url = "https://aules.example/pluginfile.php/123"
        response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

        self.assertEqual(
            self.downloader._response_filename(response, "Trabajo"),
            "Trabajo.docx",
        )


if __name__ == "__main__":
    unittest.main()
