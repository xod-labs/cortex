"""The services of a workspace — found once, for the validator and for the prompt (#88)."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cortex_core.workspace import service_index, services  # noqa: E402


class WorkspaceTests(unittest.TestCase):
    def workspace(self):
        root = Path(tempfile.mkdtemp(prefix="cortex-workspace-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        overviews = {
            "svc-b": "<!-- @alias: web -->\n\n# Web application\n",
            "svc-a": "<!-- @alias: api -->\n\n# Main API\n\nWhat it does.\n",
            "tools/batch": "# Nightly batch\n",                       # no alias: its folder names it
            "cortex/core/tests/fixtures/host": "# Cortex's own fixture\n",  # inside the base: not a service
            ".git/modules/old": "# a submodule's git dir\n",
        }
        for folder, text in overviews.items():
            (root / folder).mkdir(parents=True)
            (root / folder / "project-overview.md").write_text(text, encoding="utf-8")
        (root / "project-overview.md").write_text("# The workspace itself\n", encoding="utf-8")
        (root / "agents").mkdir()
        (root / "agents" / "project-overview.md").write_text("# The team's overview — ADR-006's team tier\n", encoding="utf-8")
        return root

    def test_services_are_the_folders_with_an_overview(self):
        root = self.workspace()
        self.assertEqual(sorted(Path(s).relative_to(root).as_posix() for s in services(str(root))),
                         ["svc-a", "svc-b", "tools/batch"])

    def test_the_index_names_each_service_and_marks_the_active_one(self):
        self.assertEqual(service_index(self.workspace(), active="svc-a"),
                         "- `@api` — `svc-a/` — Main API (active)\n"
                         "- `@web` — `svc-b/` — Web application\n"
                         "- `@batch` — `tools/batch/` — Nightly batch")

    def test_dependency_trees_are_not_searched(self):
        # node_modules, vendor, .venv: never a service, and the bulk of a workspace's files.
        root = self.workspace()
        for tree in ("svc-a/node_modules/pkg", "vendor/acme/lib", "svc-b/.venv/lib"):
            (root / tree).mkdir(parents=True)
            (root / tree / "project-overview.md").write_text("# not a service\n", encoding="utf-8")
        self.assertEqual(sorted(Path(s).relative_to(root).as_posix() for s in services(str(root))),
                         ["svc-a", "svc-b", "tools/batch"])

    def test_a_service_whose_agents_is_the_team_s_folder_is_not_listed(self):
        # The runtime refuses it — the team's overlays would be read twice (ADR-013 §3.6): the index names it not.
        root = self.workspace()
        (root / "team").mkdir()
        for service, folder in (("svc-c", root / "agents"), ("svc-d", root / "team")):
            (root / service).mkdir()
            (root / service / "project-overview.md").write_text(f"# {service}\n", encoding="utf-8")
            try:
                os.symlink(folder, root / service / "agents", target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"no symbolic link here: {error}")
        self.assertNotIn("`svc-c/`", service_index(root))
        self.assertIn("`svc-d/`", service_index(root))                   # team/ is no team's folder by default
        index = service_index(root, team_folder=str(root / "team"))
        self.assertNotIn("`svc-d/`", index)
        self.assertIn("`svc-c/`", index)                                  # agents/ renamed: svc-c's is its own
        self.assertIn("`svc-a/`", index)

    def test_agents_in_another_case_is_not_listed_where_names_ignore_case(self):
        # The scan compares names as written; the disk opens Agents/ as agents/, which the runtime refuses, and
        # every service below it. On a disk that keeps case apart, Agents/ is a folder of its own, listed.
        root = self.workspace()
        (root / "agents" / "inner").mkdir()
        (root / "agents" / "inner" / "project-overview.md").write_text("# Inner\n", encoding="utf-8")
        (root / "agents").rename(root / "Agents")
        if not (root / "agents").exists():
            self.skipTest("this disk keeps case apart: Agents/ is no agents/")
        index = service_index(root)
        self.assertNotIn("`Agents/", index)
        self.assertIn("`svc-a/`", index)

    def test_no_service_no_index(self):
        root = Path(tempfile.mkdtemp(prefix="cortex-workspace-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "project-overview.md").write_text("# Single project\n", encoding="utf-8")
        self.assertEqual(service_index(root), "")


if __name__ == "__main__":
    unittest.main()
