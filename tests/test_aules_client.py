import unittest

from aules_client import AulesClient


class CourseFilterTests(unittest.TestCase):
    def test_excludes_orientation_course_names_with_or_without_accent(self):
        self.assertTrue(AulesClient._is_orientation_course("Orientación"))
        self.assertTrue(AulesClient._is_orientation_course("Tutoría y ORIENTACION"))
        self.assertFalse(AulesClient._is_orientation_course("Orientalismo"))
        self.assertFalse(AulesClient._is_orientation_course("Historia"))


if __name__ == "__main__":
    unittest.main()
