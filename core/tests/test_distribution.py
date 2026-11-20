"""The core's distribution name is the one reserved on PyPI (#86).

A package that depends on the core by a name nobody holds can be served someone else's code by an
index: dependency confusion. `.github/workflows/pypi.yml` reserves the name it reads from
`core/pyproject.toml`; every package of this repository that depends on the core names it so.
"""

import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESERVED = "xodlabs-cortex-core"


def requirement_name(requirement):
    """A requirement's distribution name, normalised as an index compares it (PEP 503)."""
    name = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement).group(1)
    return re.sub(r"[-_.]+", "-", name).lower()


def dependencies(path):
    return tomllib.loads(path.read_text(encoding="utf-8")).get("project", {}).get("dependencies", [])


class DistributionNameTests(unittest.TestCase):
    def test_the_core_is_distributed_under_the_reserved_name(self):
        project = tomllib.loads((ROOT / "core" / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual(project["name"], RESERVED)

    def test_every_package_names_the_core_by_the_reserved_name(self):
        dependents = []
        for path in sorted(ROOT.glob("*/pyproject.toml")):
            for name in map(requirement_name, dependencies(path)):
                if "cortex-core" in name or "cortexcore" in name:
                    dependents.append(path.parent.name)
                    with self.subTest(package=path.parent.name):
                        self.assertEqual(name, RESERVED)
        self.assertEqual(sorted(dependents), ["cli", "runtime"])

    def test_the_workflow_reserves_the_core_s_name(self):
        workflow = (ROOT / ".github" / "workflows" / "pypi.yml").read_text(encoding="utf-8")
        self.assertIn("core/pyproject.toml", workflow)

    def test_the_guard_can_fail(self):
        self.assertEqual(requirement_name("Cortex_Core>=1; python_version>'3'"), "cortex-core")


if __name__ == "__main__":
    unittest.main()
