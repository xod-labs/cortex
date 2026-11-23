"""bin/setup-release.sh against a fake GitHub: what --check reports, what a run applies, and that a
second run changes nothing (ADR-009 §3.10). The live check is on a throwaway repository."""

import copy
import io
import os
import re
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

BIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BIN))
import setup_release as sr  # noqa: E402


# The summary job of each workflow that gates a merge, but its `name` and `needs` (ADR-009 §3.10).
SUMMARY = ("    if: always()\n"
           "    runs-on: ubuntu-latest\n"
           "    steps:\n"
           "      - name: Every job passed, or was skipped\n"
           "        env:\n"
           "          RESULTS: ${{ toJSON(needs.*.result) }}\n"
           "        run: |\n"
           "          echo \"$RESULTS\"\n"
           "          echo \"$RESULTS\" | jq -e 'all(.[]; . == \"success\" or . == \"skipped\")' > /dev/null")


# The environment release-signing as the script leaves it — as GitHub serves it, with its branch policies.
READY = {"name": "release-signing", "protection_rules": [{"type": "branch_policy"}],
         "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
         "policies": [{"id": 1, "name": "main", "type": "branch"}, {"id": 2, "name": "[0-9]*.x", "type": "branch"},
                      {"id": 3, "name": "release/**", "type": "branch"}]}


# GitHub's message for each refusal of a classic protection's required checks.
MESSAGES = {404: "Branch not protected", 403: "Resource not accessible by integration", 500: "Server Error"}


def github_matches(pattern, branch):
    """A ref pattern against a branch, as GitHub reads it: File.fnmatch with FNM_PATHNAME."""
    if pattern in ("~ALL", "~DEFAULT_BRANCH"):
        return pattern == "~ALL" or branch == "main"
    regex = re.escape(pattern).replace(r"\*\*/", "(?:.*/)?").replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
    return re.fullmatch(regex, f"refs/heads/{branch}") is not None


class FakeGh(sr.Gh):
    """One repository's settings, as GitHub's API serves and takes them."""

    def __init__(self, owner_type="Organization", rulesets=None, immutable=False, delete_on_merge=False, branches=("main",),
                 org_rulesets=None, protections=None, environment=..., secret_set=True, environment_error=None):
        self.owner_type = owner_type
        self.branches = list(branches)
        self.rulesets = {r["id"]: r for r in (rulesets or [])}
        self.org_rulesets = {r["id"]: r for r in (org_rulesets or [])}   # in no listing of the repository's own
        self.immutable = immutable
        self.delete_on_merge = delete_on_merge
        self.writes = []
        # A classic branch protection's required checks, by branch: GitHub's answer, or the HTTP
        # status of its refusal. A branch with none answers 404, as GitHub does.
        self.protections = dict(protections or {})
        self.read = []
        # The environment release-signing, as GitHub serves it, and its branch policies: as the script
        # leaves it by default, as the repository has it; None, none. `secret_set`: whether the
        # maintainer has set its secret — a value no script writes.
        self.environment = copy.deepcopy(READY if environment is ... else environment)
        self.secret_set = secret_set
        self.environment_error = environment_error

    def _environment(self, path, method, body):
        base = "repos/o/r/environments/release-signing"
        if self.environment_error:
            raise sr.GhError(f"{method} {path}: gh: Forbidden (HTTP {self.environment_error})", self.environment_error)
        env = self.environment
        if path == base and method == "PUT":
            policy = body["deployment_branch_policy"]
            rules = [{"type": "branch_policy"}] if policy and policy["custom_branch_policies"] else []
            if body.get("reviewers"):
                rules.append({"type": "required_reviewers", "reviewers": body["reviewers"]})
            if body.get("wait_timer"):
                rules.append({"type": "wait_timer", "wait_timer": body["wait_timer"]})
            policies = env["policies"] if env and policy and policy["custom_branch_policies"] else []
            self.environment = {"name": "release-signing", "protection_rules": rules,
                                "deployment_branch_policy": policy, "policies": policies}
            return self.environment
        if env is None:
            raise sr.GhError(f"{method} {path}: gh: Not Found (HTTP 404)", 404)
        if path == base:
            return {k: copy.deepcopy(v) for k, v in env.items() if k != "policies"}
        if path == f"{base}/secrets?per_page=100":
            return {"total_count": int(self.secret_set),
                    "secrets": [{"name": "RELEASE_SIGNING_KEY"}] if self.secret_set else []}
        if path == f"{base}/deployment-branch-policies?per_page=100":
            if not (env.get("deployment_branch_policy") or {}).get("custom_branch_policies"):
                raise sr.GhError(f"{method} {path}: gh: Not Found (HTTP 404)", 404)
            return {"total_count": len(env["policies"]), "branch_policies": copy.deepcopy(env["policies"])}
        if path == f"{base}/deployment-branch-policies" and method == "POST":
            new = dict(body, id=max([p["id"] for p in env["policies"]] + [0]) + 1)
            env["policies"].append(new)
            return new
        match = re.fullmatch(rf"{base}/deployment-branch-policies/(\d+)", path)
        if match and method == "DELETE":
            env["policies"] = [p for p in env["policies"] if p["id"] != int(match.group(1))]
            return None
        raise AssertionError((method, path))

    def api(self, path, method="GET", body=None):
        if method != "GET":
            self.writes.append((method, path))
        self.read.append(path)
        if path.startswith("repos/o/r/environments/"):
            return self._environment(path, method, body)
        match = re.fullmatch(r"repos/o/r/branches/(.+)/protection/required_status_checks", path)
        if match and method == "GET":
            answer = self.protections.get(match.group(1), 404)
            if isinstance(answer, int):
                raise sr.GhError(f"GET {path}: gh: {MESSAGES[answer]} (HTTP {answer})", answer)
            return copy.deepcopy(answer)
        if path == "repos/o/r" and method == "GET":
            return {"owner": {"type": self.owner_type}, "delete_branch_on_merge": self.delete_on_merge}
        if path == "repos/o/r" and method == "PATCH":
            self.delete_on_merge = body["delete_branch_on_merge"]
            return {}
        if path.startswith("repos/o/r/rulesets?"):
            return [{"id": i, "name": r["name"], "source_type": "Repository"} for i, r in self.rulesets.items()]
        if path.startswith("repos/o/r/branches?"):
            return [{"name": b} for b in self.branches] if "page=1" in path else []
        if path.startswith("repos/o/r/rulesets") and method in ("POST", "PUT"):
            # As GitHub does: no merge queue on a pattern.
            if any(r["type"] == "merge_queue" for r in body["rules"]) and any("*" in ref for ref in body["conditions"]["ref_name"]["include"]):
                raise sr.GhError("Invalid rule 'merge_queue': Wildcard ref names are not supported when merge queue is enabled")
        if path == "repos/o/r/rulesets" and method == "POST":
            new = dict(copy.deepcopy(body), id=len(self.rulesets) + 100)
            self.rulesets[new["id"]] = new
            return new
        match = re.fullmatch(r"repos/o/r/rules/branches/(.+)\?per_page=100&page=(\d+)", path)
        if match:
            # Every active rule GitHub applies to the branch, whether it exists or not, with its ruleset.
            branch, every = match.group(1), {**self.rulesets, **self.org_rulesets}
            if match.group(2) != "1":
                return []
            return [dict(copy.deepcopy(rule), ruleset_id=i, ruleset_source_type=r.get("source_type", "Repository"))
                    for i, r in every.items()
                    if r.get("enforcement") == "active" and r.get("target", "branch") == "branch"
                    and any(github_matches(p, branch) for p in r["conditions"]["ref_name"]["include"])
                    and not any(github_matches(p, branch) for p in r["conditions"]["ref_name"].get("exclude", []))
                    for rule in r.get("rules", [])]
        match = re.fullmatch(r"repos/o/r/rulesets/(\d+)\?includes_parents=true", path)
        if match and int(match.group(1)) in self.org_rulesets:
            return copy.deepcopy(self.org_rulesets[int(match.group(1))])
        match = re.fullmatch(r"repos/o/r/rulesets/(\d+)(\?includes_parents=true)?", path)
        if match:
            if method == "PUT":
                self.rulesets[int(match.group(1))] = dict(copy.deepcopy(body), id=int(match.group(1)))
            # GitHub adds parameters of its own to a rule: --check must not take them for drift.
            served = copy.deepcopy(self.rulesets[int(match.group(1))])
            for rule in served.get("rules", []):
                if rule["type"] == "pull_request":
                    rule["parameters"]["required_reviewers"] = []
            return served
        if path == "repos/o/r/immutable-releases":
            if method == "PUT":
                self.immutable = True
                return None
            return {"enabled": self.immutable, "enforced_by_owner": False}
        raise AssertionError((method, path))


def run(gh, *args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = sr.main(["o/r", *args], gh=gh)
    return code, out.getvalue()


class SetupReleaseTests(unittest.TestCase):
    def test_check_reports_every_setting_that_differs_and_changes_nothing(self):
        gh = FakeGh()
        code, out = run(gh, "--check")
        self.assertEqual(code, 1)
        self.assertEqual(gh.writes, [])
        self.assertIn("there is no ruleset named 'release cycle'", out)
        self.assertIn("✗ immutable releases\n    they are off", out)
        self.assertIn("✗ head branches deleted on merge\n    they are kept", out)
        self.assertIn("there is no ruleset named 'release cycle: merge queue'", out)
        self.assertIn("4 setting(s) differ.", out)

    def test_one_run_applies_them_and_a_second_changes_nothing(self):
        gh = FakeGh()
        self.assertEqual(run(gh)[0], 0)
        self.assertEqual([w[0] for w in gh.writes], ["POST", "POST", "PUT", "PATCH"])
        code, out = run(gh, "--check")
        self.assertEqual(code, 0, out)
        self.assertIn("Every setting holds.", out)
        before = list(gh.writes)
        code, out = run(gh)
        self.assertEqual((code, gh.writes), (0, before))
        self.assertIn("Nothing to change.", out)

    def rulesets(self, gh):
        return {r["name"]: r for r in gh.rulesets.values()}

    def test_the_rulesets_an_organisation_gets(self):
        gh = FakeGh(branches=["main", "1.x", "release/1.4.0", "feature.x"])
        run(gh)
        ruleset = self.rulesets(gh)["release cycle"]
        self.assertEqual(ruleset["enforcement"], "active")
        self.assertEqual(ruleset["bypass_actors"], [])
        self.assertEqual(ruleset["conditions"]["ref_name"]["include"], ["~DEFAULT_BRANCH", "refs/heads/*.x"])
        rules = {r["type"]: r.get("parameters", {}) for r in ruleset["rules"]}
        self.assertEqual(sorted(rules), ["deletion", "non_fast_forward", "pull_request", "required_status_checks"])
        self.assertEqual(rules["pull_request"]["allowed_merge_methods"], ["merge"])
        self.assertTrue(rules["required_status_checks"]["strict_required_status_checks_policy"])
        queue = self.rulesets(gh)["release cycle: merge queue"]
        # Named exactly: GitHub refuses a merge queue on a pattern. `feature.x` is no line.
        self.assertEqual(queue["conditions"]["ref_name"]["include"], ["~DEFAULT_BRANCH", "refs/heads/1.x"])
        self.assertEqual(queue["bypass_actors"], [])
        params = queue["rules"][0]["parameters"]
        self.assertEqual((queue["rules"][0]["type"], params["merge_method"], params["max_entries_to_merge"]), ("merge_queue", "MERGE", 1))

    def test_a_new_line_is_drift_until_the_script_runs_again(self):
        gh = FakeGh()
        run(gh)
        gh.branches.append("2.x")
        code, out = run(gh, "--check")
        self.assertEqual(code, 1)
        self.assertIn("✗ the ruleset 'release cycle: merge queue', on the default branch, 2.x", out)
        self.assertIn("it applies to ['~DEFAULT_BRANCH'], and should apply to ['refs/heads/2.x', '~DEFAULT_BRANCH']", out)
        self.assertEqual(run(gh)[0], 0)
        self.assertEqual(run(gh, "--check")[0], 0)

    def test_a_personal_repository_gets_no_merge_queue(self):
        gh = FakeGh(owner_type="User")
        code, out = run(gh)
        self.assertIn("No merge queue: GitHub offers it to repositories of organisations.", out)
        self.assertEqual(list(self.rulesets(gh)), ["release cycle"])
        code, out = run(gh, "--check")
        self.assertEqual(code, 0)
        self.assertIn("No merge queue: GitHub offers it to repositories of organisations.", out)

    def test_no_required_check_is_a_matrix_job(self):
        for name in sr.DEFAULT_CHECKS:
            with self.subTest(check=name):
                self.assertNotIn("${{", name)
                self.assertNotIn("(", name)

    def test_the_required_checks_are_job_names_of_this_repository(self):
        workflows = BIN.parent / ".github" / "workflows"
        names = set()
        for path in workflows.glob("*.yml"):
            names |= set(re.findall(r"(?m)^    name: (.+?)\s*$", path.read_text(encoding="utf-8")))
        for check in sr.DEFAULT_CHECKS:
            with self.subTest(check=check):
                self.assertIn(check, names)

    def test_each_required_check_of_several_jobs_fails_when_one_of_them_does(self):
        # A summary runs whatever happened, needs every job a pull request runs — a job of a tag's
        # push only is none — and passes on success or skipped only (§3.10, #236): all five read the same,
        # and no workflow-wide `defaults:` can soften one. Read as text: the runner's Python has no YAML parser.
        tag_push_only = "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/')"
        summaries = set()
        for path in sorted((BIN.parent / ".github" / "workflows").glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            block = text.split("\njobs:\n", 1)[1]
            parts = re.split(r"(?m)^  ([A-Za-z0-9_-]+):\s*\n", block)
            jobs = dict(zip(parts[1::2], parts[2::2]))

            def key(job, name):
                match = re.search(rf"(?m)^    {name}: (.+?)\s*$", job)
                return match.group(1) if match else ""

            for job_id, job in jobs.items():
                if key(job, "name") not in sr.DEFAULT_CHECKS or len(jobs) == 1:
                    continue
                summaries.add(key(job, "name"))
                others = {other for other, text in jobs.items() if other != job_id and key(text, "if") != tag_push_only}
                with self.subTest(workflow=path.name, check=key(job, "name")):
                    self.assertNotRegex(text, r"(?m)^[\"']?defaults[\"']?\s*:")
                    self.assertEqual(re.sub(r"(?m)^    (name|needs): .*\n", "", job).rstrip(), SUMMARY)
                    self.assertEqual(set(re.findall(r"[A-Za-z0-9_-]+", key(job, "needs"))), others)
        self.assertEqual(summaries, {"builds", "cli tests", "core tests", "repo checks", "runtime tests"})

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "bash and jq, which GitHub's Ubuntu runners have")
    def test_a_summary_passes_on_success_and_skipped_only(self):
        # GitHub gives `abandoned` to a job no runner took: a condition listing what fails let it
        # through, and the merge queue merged 1.3.1 without its macOS build (#236).
        condition = SUMMARY.splitlines()[-1].strip()
        cases = {'["success"]': 0, '["success", "skipped"]': 0, '["skipped", "skipped"]': 0,
                 '["success", "failure"]': 1, '["success", "cancelled"]': 1, '["success", "abandoned"]': 1,
                 '["timed_out"]': 1}
        for results, expected in cases.items():
            with self.subTest(results=results):
                done = subprocess.run(["bash", "-c", condition], env=dict(os.environ, RESULTS=results),
                                      capture_output=True, text=True)
                self.assertEqual(done.returncode, expected, done.stderr)

    def test_a_disabled_or_loosened_ruleset_is_drift(self):
        gh = FakeGh()
        run(gh)
        ruleset = self.rulesets(gh)["release cycle"]
        ruleset["enforcement"] = "disabled"
        ruleset["bypass_actors"] = [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]
        for rule in ruleset["rules"]:
            if rule["type"] == "pull_request":
                rule["parameters"]["allowed_merge_methods"] = ["merge", "squash"]
            if rule["type"] == "required_status_checks":
                rule["parameters"]["required_status_checks"] = rule["parameters"]["required_status_checks"][1:] + [
                    {"context": "build (linux-x86_64)", "integration_id": 15368}]
        ruleset["rules"].append({"type": "required_signatures"})
        code, out = run(gh, "--check")
        self.assertEqual(code, 1)
        for expected in ("it is disabled, not active",
                         "it lets 1 actor(s) bypass it, and should let nobody",
                         'its pull_request rule has allowed_merge_methods = ["merge", "squash"], and should have ["merge"]',
                         "missing ['actions tests (15368)'], not required here ['build (linux-x86_64) (15368)']",
                         "it has a required_signatures rule, which the cycle does not set"):
            with self.subTest(expected=expected):
                self.assertIn(expected, out)
        self.assertEqual(run(gh)[0], 0)
        self.assertEqual(run(gh, "--check")[0], 0)

    def test_another_ruleset_that_requires_checks_is_reported_and_left(self):
        # Cortex's own, made by hand before the cycle: matrix jobs, required of main.
        block = {"id": 7, "name": "Block default", "target": "branch", "enforcement": "active", "bypass_actors": [],
                 "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
                 "rules": [{"type": "non_fast_forward"}, {"type": "required_status_checks", "parameters": {
                     "required_status_checks": [{"context": "English-only"}, {"context": "build (linux-x86_64)"}]}}]}
        def ruleset(id, name, include, checks=("ci",), enforcement="active", target="branch"):
            rules = [{"type": "non_fast_forward"}] + ([{"type": "required_status_checks", "parameters": {
                "required_status_checks": [{"context": c} for c in checks]}}] if checks else [])
            return {"id": id, "name": name, "target": target, "enforcement": enforcement,
                    "conditions": {"ref_name": {"include": include, "exclude": []}}, "rules": rules}
        left = [ruleset(8, "tags", ["~ALL"], target="tag"), ruleset(9, "disabled", ["~DEFAULT_BRANCH"], enforcement="disabled"),
                ruleset(10, "no checks", ["~DEFAULT_BRANCH"], checks=()), ruleset(11, "features", ["refs/heads/feature/*"])]
        reported = [ruleset(12, "every branch", ["~ALL"]), ruleset(13, "lines", ["refs/heads/*.x"])]
        gh = FakeGh(rulesets=[block, *left, *reported], branches=["main", "1.x"])
        code, out = run(gh)
        self.assertEqual(code, 1)
        for name in ("Block default", "every branch", "lines"):
            self.assertIn(f"! the ruleset {name!r}, which the cycle does not set: left to you", out)
        for name in ("tags", "disabled", "no checks", "features"):
            self.assertNotIn(f"{name!r}", out)
        self.assertIn(7, gh.rulesets)
        self.assertNotIn(("DELETE", "repos/o/r/rulesets/7"), gh.writes)
        code, out = run(gh, "--check")
        self.assertEqual(code, 1)
        self.assertIn("✗ the ruleset 'Block default', which the cycle does not set\n"
                      "    it requires 2 check(s) of its own — English-only, build (linux-x86_64)", out)
        self.assertIn("3 setting(s) differ.", out)
        for id in (7, 12, 13):
            del gh.rulesets[id]
        self.assertEqual(run(gh, "--check")[0], 0)

    def test_github_says_which_rulesets_apply_where_no_pattern_is_matched_here(self):
        # The script reads the rules GitHub applies to each branch (#226): a nested pattern, an
        # exclude, a line not cut yet, an organisation's or an enterprise's ruleset are GitHub's to
        # resolve. A rule that names no check requires nothing.
        def ruleset(id, name, include, exclude=(), source="Repository", checks=("ci", "lint")):
            return {"id": id, "name": name, "target": "branch", "enforcement": "active", "source_type": source,
                    "conditions": {"ref_name": {"include": include, "exclude": list(exclude)}},
                    "rules": [{"type": "required_status_checks", "parameters": {
                        "required_status_checks": [{"context": c} for c in checks]}}]}
        gh = FakeGh(rulesets=[ruleset(20, "nested", ["refs/heads/**/*"]),
                              ruleset(21, "all but the cycle", ["~ALL"], exclude=["~DEFAULT_BRANCH", "refs/heads/*.x"]),
                              ruleset(22, "zero", ["refs/heads/0.x"]),
                              ruleset(23, "no check", ["~DEFAULT_BRANCH"], checks=())],
                    org_rulesets=[ruleset(30, "org checks", ["~DEFAULT_BRANCH"], source="Organization"),
                                  ruleset(31, "enterprise checks", ["~DEFAULT_BRANCH"], source="Enterprise")])
        run(gh)
        code, out = run(gh, "--check")
        self.assertEqual(code, 1)
        # On main and 0.x both, reported once, its checks once.
        self.assertIn("✗ the ruleset 'nested', which the cycle does not set\n"
                      "    it requires 2 check(s) of its own — ci, lint", out)
        self.assertIn("✗ the ruleset 'zero', which the cycle does not set", out)
        self.assertIn("✗ the organisation's ruleset 'org checks', which the cycle does not set", out)
        self.assertIn("✗ the enterprise's ruleset 'enterprise checks', which the cycle does not set", out)
        self.assertNotIn("all but the cycle", out)
        self.assertNotIn("no check", out)
        self.assertIn("4 setting(s) differ.", out)

    def protection(self, *checks, legacy=False):
        """GitHub's answer for a classic protection's required checks: `checks`, and `contexts` alone before apps."""
        return {"strict": True, "contexts": list(checks),
                "checks": [] if legacy else [{"context": c, "app_id": None} for c in checks]}

    def test_a_classic_protection_that_requires_checks_is_reported_and_left(self):
        # A repository that adopts the cycle with main's protection from before: an old CI job's
        # name, which no workflow of the cycle reports (#235).
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                gh = FakeGh(protections={"main": self.protection("build (linux-x86_64)", "lint", legacy=legacy)})
                code, out = run(gh)
                self.assertEqual(code, 1)
                self.assertIn("! the classic branch protection of 'main', which the cycle does not set: left to you"
                              " — remove it by hand, in the Settings → Branches of the repository", out)
                self.assertFalse([w for w in gh.writes if "/protection" in w[1]])
                code, out = run(gh, "--check")
                self.assertEqual(code, 1)
                self.assertIn("✗ the classic branch protection of 'main', which the cycle does not set\n"
                              "    it requires 2 check(s) of its own — build (linux-x86_64), lint", out)
                self.assertIn("1 setting(s) differ.", out)
                del gh.protections["main"]
                self.assertEqual(run(gh, "--check")[0], 0)

    def test_a_classic_protection_with_no_required_check_is_not_reported(self):
        gh = FakeGh(protections={"main": self.protection()})
        run(gh)
        code, out = run(gh, "--check")
        self.assertEqual(code, 0, out)
        self.assertNotIn("classic", out)
        self.assertIn("repos/o/r/branches/main/protection/required_status_checks", gh.read)

    def test_a_404_is_no_classic_protection(self):
        # GitHub answers 404 for a branch with no protection, and for one that requires no check.
        gh = FakeGh()
        run(gh)
        code, out = run(gh, "--check")
        self.assertEqual(code, 0, out)
        self.assertIn("Every setting holds.", out)
        self.assertIn("repos/o/r/branches/main/protection/required_status_checks", gh.read)

    def test_gh_gives_the_status_of_a_refusal(self):
        answer = '{"message":"Branch not protected","status":"404"}'
        done = subprocess.CompletedProcess([], 1, stdout=answer, stderr="gh: Branch not protected (HTTP 404)\n")
        with mock.patch.object(sr.subprocess, "run", return_value=done):
            with self.assertRaises(sr.GhError) as raised:
                sr.Gh().api("repos/o/r/branches/main/protection/required_status_checks")
        self.assertEqual(raised.exception.status, 404)
        self.assertIn("Branch not protected", str(raised.exception))
        done = subprocess.CompletedProcess([], 1, stdout="", stderr="error connecting to api.github.com\n")
        with mock.patch.object(sr.subprocess, "run", return_value=done):
            with self.assertRaises(sr.GhError) as raised:
                sr.Gh().api("repos/o/r")
        self.assertIsNone(raised.exception.status)

    def test_any_other_error_on_a_classic_protection_is_reported(self):
        # A token that may not read it, or GitHub failing: never taken for no protection.
        for status in (403, 500):
            with self.subTest(status=status):
                gh = FakeGh(protections={"main": status})
                for args in ((), ("--check",)):
                    err = io.StringIO()
                    saved, sys.stderr = sys.stderr, err
                    try:
                        code, out = run(gh, *args)
                    finally:
                        sys.stderr = saved
                    self.assertEqual(code, 2)
                    self.assertIn("setup-release: GET repos/o/r/branches/main/protection/required_status_checks", err.getvalue())
                    self.assertIn(f"(HTTP {status})", err.getvalue())
                    self.assertNotIn("Every setting holds.", out)
                    self.assertEqual(gh.writes, [])

    def test_each_line_s_classic_protection_is_read(self):
        # 0.x is a line too. A line not cut has no protection to read: a pattern's reaches it once it is.
        gh = FakeGh(branches=["main", "0.x", "1.x", "release/1.4.0", "feature.x"],
                    protections={"0.x": self.protection("ci"), "1.x": self.protection("old ci"),
                                 "release/1.4.0": self.protection("ci"), "feature.x": self.protection("ci")})
        run(gh)
        code, out = run(gh, "--check")
        self.assertEqual(code, 1)
        self.assertIn("✗ the classic branch protection of '0.x', which the cycle does not set\n"
                      "    it requires 1 check(s) of its own — ci", out)
        self.assertIn("✗ the classic branch protection of '1.x', which the cycle does not set\n"
                      "    it requires 1 check(s) of its own — old ci", out)
        self.assertNotIn("release/1.4.0'", out)
        self.assertNotIn("feature.x'", out)
        self.assertIn("2 setting(s) differ.", out)
        self.assertFalse([p for p in gh.read if "/protection" in p and not re.search(r"/branches/(main|0\.x|1\.x)/", p)])
        gh.branches.append("2.x")
        gh.protections["2.x"] = self.protection("ci")
        self.assertIn("✗ the classic branch protection of '2.x'", run(gh, "--check")[1])

    def test_the_signing_environment_is_made_open_to_the_release_branches_with_no_reviewer(self):
        # ADR-015 §3.1: where a release or a pre-release is cut, and nowhere else.
        gh = FakeGh(environment=None, secret_set=False)
        code, out = run(gh, "--check")
        self.assertEqual(code, 1)
        self.assertIn("✗ the environment 'release-signing', open to main, [0-9]*.x, release/**, with no reviewer\n"
                      "    there is no environment named 'release-signing'", out)
        self.assertIn("✗ the secret RELEASE_SIGNING_KEY of the environment 'release-signing'\n    it is not set", out)
        code, out = run(gh)
        self.assertEqual(code, 1, out)   # the secret is left to the maintainer
        self.assertIn("→ set the environment 'release-signing'", out)
        self.assertIn("! the secret RELEASE_SIGNING_KEY of the environment 'release-signing': left to you — set it by "
                      "hand, the private key in PEM on its standard input:\n"
                      "    gh secret set RELEASE_SIGNING_KEY --env release-signing --repo o/r < release-signing.pem", out)
        env = gh.environment
        self.assertEqual(env["deployment_branch_policy"], {"protected_branches": False, "custom_branch_policies": True})
        self.assertEqual(sorted((p["name"], p["type"]) for p in env["policies"]),
                         [("[0-9]*.x", "branch"), ("main", "branch"), ("release/**", "branch")])
        self.assertFalse([r for r in env["protection_rules"] if r["type"] in ("required_reviewers", "wait_timer")])
        gh.secret_set = True
        self.assertEqual(run(gh, "--check")[0], 0)
        before = list(gh.writes)
        self.assertEqual(run(gh)[0], 0)
        self.assertEqual(gh.writes, before)

    def test_a_loosened_or_guarded_environment_is_drift(self):
        cases = {
            "it requires a reviewer, and should require none: the maintainer publishes each draft by hand":
                dict(READY, protection_rules=[{"type": "required_reviewers", "reviewers": [{"type": "User", "reviewer": {"id": 1}}]},
                                              {"type": "branch_policy"}]),
            "it waits 5 minute(s) before a job runs, and should not wait":
                dict(READY, protection_rules=[{"type": "wait_timer", "wait_timer": 5}, {"type": "branch_policy"}]),
            "it is open to every branch, and should be open to main, [0-9]*.x, release/** only":
                dict(READY, deployment_branch_policy=None, policies=[]),
            "it is open to the protected branches, and should be open to main, [0-9]*.x, release/** only":
                dict(READY, deployment_branch_policy={"protected_branches": True, "custom_branch_policies": False}, policies=[]),
            "it is open to ['[0-9]*.x', 'feature/*', 'main', 'v* (tag)'], and should be open to ['[0-9]*.x', 'main', 'release/**']":
                dict(READY, policies=[{"id": 1, "name": "main", "type": "branch"}, {"id": 2, "name": "[0-9]*.x", "type": "branch"},
                                      {"id": 7, "name": "feature/*", "type": "branch"}, {"id": 8, "name": "v*", "type": "tag"}]),
        }
        for drift, environment in cases.items():
            with self.subTest(drift=drift):
                gh = FakeGh(environment=environment)
                run(gh)   # the rest of the settings
                gh.environment = copy.deepcopy(environment)
                code, out = run(gh, "--check")
                self.assertEqual(code, 1)
                self.assertIn(f"✗ the environment 'release-signing', open to main, [0-9]*.x, release/**, with no reviewer\n"
                              f"    {drift}", out)
                self.assertEqual(run(gh)[0], 0)
                self.assertEqual(run(gh, "--check")[0], 0)
                self.assertEqual(sorted(p["name"] for p in gh.environment["policies"]), ["[0-9]*.x", "main", "release/**"])

    def test_an_environment_the_api_refuses_stops_the_script(self):
        gh = FakeGh(environment_error=403)
        err = io.StringIO()
        saved, sys.stderr = sys.stderr, err
        try:
            code, out = run(gh, "--check")
        finally:
            sys.stderr = saved
        self.assertEqual(code, 2)
        self.assertIn("setup-release: GET repos/o/r/environments/release-signing: gh: Forbidden (HTTP 403)", err.getvalue())

    def test_another_repositorys_checks(self):
        gh = FakeGh()
        run(gh, "--require", "ci", "--require", "lint")
        rules = {r["type"]: r.get("parameters", {}) for r in self.rulesets(gh)["release cycle"]["rules"]}
        self.assertEqual([c["context"] for c in rules["required_status_checks"]["required_status_checks"]], ["ci", "lint"])

    def test_an_api_error_stops_with_its_message(self):
        class Refusing(FakeGh):
            def api(self, path, method="GET", body=None):
                raise sr.GhError(f"{method} {path}: HTTP 404")
        err = io.StringIO()
        saved, sys.stderr = sys.stderr, err
        try:
            self.assertEqual(run(Refusing())[0], 2)
        finally:
            sys.stderr = saved
        self.assertIn("HTTP 404", err.getvalue())


if __name__ == "__main__":
    unittest.main()
