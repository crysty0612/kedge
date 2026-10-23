"""
Tests for dependency checking and package manager detection.
"""

import unittest

from kedge.core.deps import DependencyManager


class TestDependencies(unittest.TestCase):
    def test_package_manager_detection(self):
        pm = DependencyManager.detect_package_manager()
        # On Linux machines like Debian/Ubuntu or Fedora, this should detect a package manager
        self.assertIn(pm, ["apt-get", "dnf", "pacman", "apk", "zypper", None])

    def test_check_dependencies(self):
        missing_req, missing_opt = DependencyManager.check_dependencies()
        # python3, git, tar, gpg, sqlite3 are present on this machine
        self.assertIsInstance(missing_req, list)
        self.assertIsInstance(missing_opt, list)


if __name__ == "__main__":
    unittest.main()
