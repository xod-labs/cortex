"""#89 — the project's view is an option: a deployment's default, a run's own, the run winning.

The view closes the system prompt in three parts, `# Project overview`, `# Workspace services`
and `# Project context` (ADR-002 §9). Each is kept or left out on its own; with no option set, the
prompt is byte for byte what it was before the option existed.
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cortex_runtime import run as run_module  # noqa: E402
from cortex_runtime.loop import ModelTurn, ToolCall  # noqa: E402
from cortex_runtime.app import WorkspaceConfig, build_run_request, project_view_setting  # noqa: E402
from cortex_runtime.run import RunRequest, resolve_run  # noqa: E402
from cortex_runtime.runtime import build_runtime  # noqa: E402
from cortex_runtime.state_store import InMemoryStateStore  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HEADINGS = {"overview": "# Project overview", "services": "# Workspace services", "context": "# Project context"}


def view_workspace(test: unittest.TestCase) -> Path:
    """The host fixture with every tier of the view: team and developer overviews and contexts."""
    root = Path(tempfile.mkdtemp(prefix="cortex-view-")) / "ws"
    test.addCleanup(shutil.rmtree, root.parent, ignore_errors=True)
    shutil.copytree(FIXTURES / "host", root)
    for rel, text in {"agents/project-overview.md": "TEAM-VISION\n", "project-overview.md": "DEV-VISION\n",
                      "agents/project-context.md": "TEAM-RULES\n"}.items():
        (root / rel).write_text(text, encoding="utf-8")
    return root


def request(**fields) -> RunRequest:
    return RunRequest(workspace="host", role="lead-backend", service="svc-a", **fields)


class DefaultTests(unittest.TestCase):
    def setUp(self):
        self.root = view_workspace(self)

    def test_no_option_is_the_prompt_before_the_option(self):
        # Captured from resolve_run on release/1.10.0, before #89: no option set changes nothing.
        golden = (FIXTURES / "golden" / "prompt.project-view.txt").read_text(encoding="utf-8")
        run = resolve_run(request(), self.root, theme="h2g2")
        self.assertEqual(run.system_prompt, golden)
        self.assertEqual(run.project_view, ["overview", "services", "context"])

    def test_all_three_named_is_the_default(self):
        default = resolve_run(request(), self.root, theme="h2g2").system_prompt
        for view in (["overview", "services", "context"], ["context", "overview", "services", "context"]):
            with self.subTest(view=view):
                self.assertEqual(resolve_run(request(project_view=view), self.root, theme="h2g2").system_prompt, default)
                self.assertEqual(resolve_run(request(), self.root, theme="h2g2", project_view=view).system_prompt,
                                 default)


class PartsTests(unittest.TestCase):
    def setUp(self):
        self.root = view_workspace(self)
        self.full = resolve_run(request(), self.root, theme="h2g2").system_prompt

    def test_each_part_is_kept_or_left_out_on_its_own(self):
        sections = {part: next(s for s in self.full.split("\n\n---\n\n") if s.startswith(heading))
                    for part, heading in HEADINGS.items()}
        for kept in ([], ["overview"], ["services"], ["context"], ["overview", "context"], ["services", "context"]):
            with self.subTest(kept=kept):
                prompt = resolve_run(request(project_view=kept), self.root, theme="h2g2").system_prompt
                for part, section in sections.items():
                    self.assertEqual(section in prompt, part in kept, part)
                    self.assertEqual(HEADINGS[part] in prompt, part in kept, part)
                # what is left is the identity, untouched, then the parts kept in their order
                identity = self.full.split("\n\n---\n\n# Project overview")[0]
                self.assertEqual(prompt, "\n\n---\n\n".join([identity] + [sections[p] for p in HEADINGS if p in kept]))

    def test_none_kept_leaves_the_untracked_notes_out(self):
        prompt = resolve_run(request(project_view=[]), self.root, theme="h2g2").system_prompt
        for mark in ("DEV-VISION", "TEAM-VISION", "TEAM-RULES", "Technical context (fixture)"):
            self.assertNotIn(mark, prompt)

    def test_a_part_left_out_is_not_read(self):
        with mock.patch.object(run_module, "read_project_overview") as overview, \
                mock.patch.object(run_module, "service_index") as services, \
                mock.patch.object(run_module, "read_project_context") as context:
            resolve_run(request(project_view=[]), self.root, theme="h2g2")
        overview.assert_not_called()
        services.assert_not_called()
        context.assert_not_called()

    def test_capabilities_still_follow_the_context(self):
        # Leaving the context's text out does not narrow the capabilities (a separate change).
        run = resolve_run(request(project_view=[]), self.root, theme="h2g2")
        self.assertEqual(run.capabilities, ["languages/php.md"])
        self.assertIn("BASE-CAP", run.system_prompt)


class PrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.root = view_workspace(self)

    def test_the_deployment_default_applies_when_the_request_says_nothing(self):
        run = resolve_run(request(), self.root, theme="h2g2", project_view=["services"])
        self.assertEqual(run.project_view, ["services"])
        self.assertNotIn("# Project overview", run.system_prompt)
        self.assertIn("# Workspace services", run.system_prompt)

    def test_the_request_wins_over_the_deployment(self):
        for deployment in (None, (), ["services"]):
            with self.subTest(deployment=deployment):
                run = resolve_run(request(project_view=["overview"]), self.root, theme="h2g2", project_view=deployment)
                self.assertEqual(run.project_view, ["overview"])
        # an empty list is a request of its own: none, whatever the deployment keeps
        run = resolve_run(request(project_view=[]), self.root, theme="h2g2", project_view=["context"])
        self.assertEqual(run.project_view, [])

    def test_the_runtime_takes_the_workspace_default(self):
        rt = build_runtime({"host": WorkspaceConfig(root=self.root, theme="h2g2", project_view=["context"])},
                           store=InMemoryStateStore(), model_backend="demo")
        self.assertEqual(rt.resolve({"workspace": "host", "role": "lead-backend"}).project_view, ["context"])
        self.assertEqual(rt.resolve({"workspace": "host", "role": "lead-backend",
                                     "project_view": ["overview"]}).project_view, ["overview"])

    def test_an_endpoint_alias_may_carry_it(self):
        req = build_run_request({"workspace": "host"}, alias={"role": "lead-backend", "project_view": ["services"]})
        self.assertEqual(req.project_view, ["services"])
        req = build_run_request({"workspace": "host", "project_view": []},
                                alias={"role": "lead-backend", "project_view": ["services"]})
        self.assertEqual(req.project_view, [])


class ValidationTests(unittest.TestCase):
    def test_an_unknown_part_is_refused_naming_the_parts(self):
        for view in (["overview", "readme"], ["Overview"], "overview", [""]):
            with self.subTest(view=view):
                with self.assertRaisesRegex(ValueError, "overview, services, context"):
                    resolve_run(request(project_view=view), FIXTURES / "host")

    def test_an_unknown_part_in_the_deployment_is_refused_at_startup(self):
        with self.assertRaisesRegex(ValueError, "unknown part 'readme', the parts are overview, services, context"):
            WorkspaceConfig(root=FIXTURES / "host", project_view=["readme"])

    def test_the_setting(self):
        cases = {None: None, "": None, "  ": None, "none": (), " none ": (),
                 "overview": ("overview",), "context, overview": ("overview", "context"),
                 "overview,services,context": ("overview", "services", "context")}
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(project_view_setting(value), expected)

    def test_an_unknown_setting_names_the_accepted_values(self):
        for value in ("readme", "overview,", "none,overview", "all", "Overview"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "CORTEX_PROJECT_VIEW: unknown part .*, the accepted values "
                                                        "are overview, services, context, separated by commas, "
                                                        "or none"):
                    project_view_setting(value)


class QueuedRunTests(unittest.TestCase):
    """The request is not stored by the state store: it travels in the job, and the worker reads it there."""

    def executed_views(self, deployment, **fields):
        root = view_workspace(self)
        rt = build_runtime({"host": WorkspaceConfig(root=root, theme="h2g2", project_view=deployment)},
                           store=InMemoryStateStore(), model_backend="demo")
        payload = {"workspace": "host", "role": "support-engineer", "subject": "ACME-7",
                   "input": {"issue": "ACME-7"}, **fields}
        prepared = rt.prepare(payload)
        job = {"run_id": prepared["run_id"], "payload": dict(payload), "alias": None, "cortex": prepared["cortex"]}
        seen = []
        real = run_module.resolve_run

        def spy(*args, **kwargs):
            seen.append(real(*args, **kwargs))
            return seen[-1]

        with mock.patch("cortex_runtime.runtime.resolve_run", spy):
            out = rt.execute(job)
        self.assertFalse(out["failed"])
        return seen

    def test_a_queued_run_keeps_its_view(self):
        seen = self.executed_views(None, project_view=["services"])
        self.assertEqual([r.project_view for r in seen], [["services"]])
        self.assertNotIn("DEV-VISION", seen[0].system_prompt)

    def test_a_queued_run_takes_the_deployment_default(self):
        seen = self.executed_views(["context"])
        self.assertEqual([r.project_view for r in seen], [["context"]])


class ToolAccessTests(unittest.TestCase):
    """A part left out is not put in the system prompt; the run's tools still open its files unless
    the run's autonomy leaves code-read out (runtime README, ADR-002 amendment of #89)."""

    def run_reading_the_notes(self, **fields):
        root = view_workspace(self)
        rt = build_runtime({"host": WorkspaceConfig(root=root, theme="h2g2", project_view=())},
                           store=InMemoryStateStore(), model_backend="demo")
        seen = []

        class Reader:
            def __init__(self):
                self.turns = [ModelTurn(tool_calls=[ToolCall("read_file", {"path": "project-overview.md"})]),
                              ModelTurn(final_text="done")]

            def propose(self, system_prompt, history):
                seen.append((system_prompt, [dict(h) for h in history]))
                return self.turns.pop(0)

        with mock.patch("cortex_runtime.runtime.make_model_client", lambda *a, **k: Reader()):
            out = rt.run({"workspace": "host", "role": "lead-backend", "subject": "ACME-7", **fields})
        return out, seen

    def test_the_default_autonomy_still_reads_a_part_left_out(self):
        out, seen = self.run_reading_the_notes()
        self.assertEqual(out["actions_taken"], [("read_file", "code-read")])
        for system_prompt, _ in seen:
            self.assertNotIn("DEV-VISION", system_prompt)
            self.assertNotIn("# Project overview", system_prompt)
        results = [h["content"] for h in seen[-1][1] if h["role"] == "tool-result"]
        self.assertEqual(results, ["DEV-VISION\n"])

    def test_without_code_read_the_files_stay_closed(self):
        out, seen = self.run_reading_the_notes(autonomy=["internal-comment"])
        self.assertEqual(out["gated_action"], ("read_file", "code-read"))
        self.assertEqual(out["actions_taken"], [])
        self.assertEqual(len(seen), 1)
        self.assertNotIn("DEV-VISION", repr(seen))


@unittest.skipUnless(__import__("importlib").util.find_spec("uvicorn"), "uvicorn not installed")
class StartupTests(unittest.TestCase):
    def test_an_unknown_setting_stops_the_service(self):
        from cortex_runtime.__main__ import main
        env = {"CORTEX_ROOT": str(FIXTURES / "host"), "CORTEX_PROJECT_VIEW": "overview,readme"}
        with mock.patch.dict(os.environ, env), mock.patch("uvicorn.run") as serve:
            with self.assertRaises(SystemExit) as stop:
                main()
        serve.assert_not_called()
        self.assertEqual(str(stop.exception), "CORTEX_PROJECT_VIEW: unknown part 'readme', the accepted values are "
                                              "overview, services, context, separated by commas, or none")

    def test_the_setting_reaches_the_workspace(self):
        from cortex_runtime.__main__ import main
        env = {"CORTEX_ROOT": str(FIXTURES / "host"), "CORTEX_PROJECT_VIEW": "none"}
        with mock.patch.dict(os.environ, env), mock.patch("uvicorn.run") as serve:
            main()
        app = serve.call_args.args[0]
        from fastapi.testclient import TestClient
        body = TestClient(app).post("/resolve", json={"workspace": "local", "role": "lead-backend"}).json()
        self.assertEqual(body["project_view"], [])
        self.assertNotIn("# Project context", body["system_prompt"])


if __name__ == "__main__":
    unittest.main()
