"""Smoke tests for the initial monorepo package boundaries."""

import importlib
import unittest


class PackageImportTests(unittest.TestCase):
    """Ensure every Python package created in C01 is importable."""

    def test_empty_packages_are_importable(self) -> None:
        module_names = (
            "apps.api",
            "packages.domain",
            "packages.harness",
            "packages.agents",
            "packages.tools",
            "packages.evals",
        )

        for module_name in module_names:
            with self.subTest(module=module_name):
                self.assertIsNotNone(importlib.import_module(module_name))


if __name__ == "__main__":
    unittest.main()
