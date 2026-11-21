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


def version(text):
    """A release number as a tuple, trailing zeros dropped: 0.1 and 0.1.0 compare equal."""
    parts = [int(part) for part in text.split(".")]
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def dependencies(path):
    """Every requirement a package declares: its dependencies, and each of its extras'."""
    project = tomllib.loads(path.read_text(encoding="utf-8")).get("project", {})
    return project.get("dependencies", []) + [
        requirement for extra in project.get("optional-dependencies", {}).values() for requirement in extra
    ]


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
        self.assertEqual(sorted(set(dependents)), ["cli", "runtime"])

    def test_no_package_accepts_the_placeholder(self):
        # The name holds a placeholder, 0.0.0, that installs nothing: a requirement it satisfies
        # would install it in place of ../core, and the import would fail later.
        core = tomllib.loads((ROOT / "core" / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        for package in ("cli", "runtime"):
            requirements = [r for r in dependencies(ROOT / package / "pyproject.toml") if requirement_name(r) == RESERVED]
            with self.subTest(package=package):
                self.assertEqual(len(requirements), 1)
                bound = re.fullmatch(rf"{re.escape(RESERVED)}\s*>=\s*([0-9]+(?:\.[0-9]+)*)", requirements[0].strip())
                self.assertIsNotNone(bound, f"{requirements[0]!r} has no lower bound alone")
                self.assertGreater(version(bound.group(1)), version("0.0.0"))
                self.assertLessEqual(version(bound.group(1)), version(core))

    def test_the_workflow_reserves_the_core_s_name(self):
        workflow = (ROOT / ".github" / "workflows" / "pypi.yml").read_text(encoding="utf-8")
        code = "\n".join(line for line in workflow.splitlines() if not line.lstrip().startswith("#"))
        self.assertIn('tomllib.load(open("core/pyproject.toml", "rb"))["project"]["name"]', code)
        self.assertNotIn(RESERVED, code)

    def test_the_guard_can_fail(self):
        self.assertEqual(requirement_name("Cortex_Core>=1; python_version>'3'"), "cortex-core")


if __name__ == "__main__":
    unittest.main()
