import unittest
from unittest.mock import MagicMock, patch

from aules_client import AulesClient, CourseInfo
from selenium.common.exceptions import NoSuchElementException


class CourseFilterTests(unittest.TestCase):
    def test_excludes_orientation_course_names_with_or_without_accent(self):
        self.assertTrue(AulesClient._is_orientation_course("Orientación"))
        self.assertTrue(AulesClient._is_orientation_course("Tutoría y ORIENTACION"))
        self.assertFalse(AulesClient._is_orientation_course("Orientalismo"))
        self.assertFalse(AulesClient._is_orientation_course("Historia"))


class EmbeddedImagesTests(unittest.TestCase):
    IMG_URL = (
        "https://aules.test/pluginfile.php/8694271/mod_resource/content/1/"
        "IMG_Muslo%20antero%20lateral.jpg"
    )
    ICON_URL = "https://aules.test/theme/image.php/tema/icono.png"

    def _fake_element(self, tag, **attributes):
        element = MagicMock()
        element.tag_name = tag

        def get_attribute(name):
            return attributes.get(name)

        element.get_attribute = get_attribute
        element.text = attributes.get("_text", "")
        return element

    def test_extracts_embedded_images_and_ignores_theme_icons(self):
        client = AulesClient()
        client.driver = MagicMock()

        section = MagicMock()
        link = self._fake_element("a", href="https://aules.test/pluginfile.php/1/apuntes/Tema%201.pdf")
        image = self._fake_element("img", src=self.IMG_URL)  # sin alt: el nombre sale de la URL
        icon = self._fake_element("img", src=self.ICON_URL)  # icono del tema, fuera

        def fake_find_elements(*args):
            joined = " ".join(str(a) for a in args)
            if "img[" in joined:
                return [image, icon]
            if "pluginfile.php" in joined:
                return [link]
            return []

        section.find_elements = fake_find_elements
        section.find_element.side_effect = NoSuchElementException("sin nombre de sección")
        client.driver.find_elements = lambda selector, by=None: [section]

        course = CourseInfo(name="Anatomía", url="https://aules.test/course/view.php?id=1")
        with (
            patch("aules_client.WebDriverWait"),
            patch("aules_client.time.sleep"),
            patch.object(client, "is_cancelled", return_value=False),
        ):
            resources = client.extract_resources_from_course(course)

        urls = [r.url for r in resources]
        self.assertIn(self.IMG_URL, urls)
        self.assertNotIn(self.ICON_URL, urls)

        names = [r.name for r in resources]
        # Sin alt, el nombre se deduce del último segmento decodificado de la URL
        self.assertIn("IMG_Muslo antero lateral.jpg", names)


if __name__ == "__main__":
    unittest.main()
