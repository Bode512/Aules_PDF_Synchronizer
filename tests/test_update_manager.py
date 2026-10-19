import unittest
import hashlib
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from credential_store import _target_name
from update_manager import (
    ReleaseInfo,
    _version_tuple,
    check_for_update,
    download_update,
)


class UpdateManagerTests(unittest.TestCase):
    def test_version_tuple_compares_numeric_components(self):
        self.assertGreater(_version_tuple("v1.1.10"), _version_tuple("1.1.9"))
        self.assertEqual(_version_tuple("1.1"), _version_tuple("1.1.0"))

    def test_returns_release_with_matching_executable_and_digest(self):
        response = Mock()
        response.json.return_value = {
            "tag_name": "v1.2.0",
            "assets": [
                {
                    "name": "Aules_PDF_Synchronizer.exe",
                    "browser_download_url": (
                        "https://github.com/Bode512/Aules_PDF_Synchronizer/"
                        "releases/download/v1.2.0/Aules_PDF_Synchronizer.exe"
                    ),
                    "size": 123,
                    "digest": "sha256:abc123",
                }
            ],
        }
        with patch("update_manager.requests.get", return_value=response):
            release = check_for_update("1.1.50")

        self.assertEqual(release.version, "1.2.0")
        self.assertEqual(
            release.download_url,
            "https://github.com/Bode512/Aules_PDF_Synchronizer/"
            "releases/download/v1.2.0/Aules_PDF_Synchronizer.exe",
        )
        self.assertEqual(release.expected_size, 123)
        self.assertEqual(release.sha256, "abc123")
        response.raise_for_status.assert_called_once()

    def test_returns_none_when_latest_release_is_not_newer(self):
        response = Mock()
        response.json.return_value = {"tag_name": "v1.1.50", "assets": []}
        with patch("update_manager.requests.get", return_value=response):
            self.assertIsNone(check_for_update("1.1.50"))

    def test_credentials_target_is_stable_and_case_insensitive(self):
        self.assertEqual(
            _target_name("https://aules.example/fp/"),
            _target_name("HTTPS://AULES.EXAMPLE/FP"),
        )

    def test_download_validates_executable_and_digest(self):
        content = b"MZ\x90\x00executable"
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [content]
        release = ReleaseInfo(
            version="1.2.0",
            download_url="https://example.test/app.exe",
            expected_size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "Aules_PDF_Synchronizer.exe"
            with patch("update_manager.requests.get", return_value=response):
                staged = download_update(release, target)

            self.assertEqual(staged.read_bytes(), content)
            self.assertEqual(staged.parent, target.parent)


if __name__ == "__main__":
    unittest.main()
