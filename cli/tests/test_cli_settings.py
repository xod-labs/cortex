"""``cortex config``, the machine's ``config.toml`` and the version read first — ADR-013 phase 1.

The settings of a project, each with its origin (§3.9); the machine's file and the variables over
it (§3.7); whether ``cortex.toml`` is committed (§3.1); and a version newer than the binary, said
before any key is checked (§3.8). From source the command stands for Cortex 9.9.9, so that it may
download the fake releases 9.9.7 and 9.9.8.
"""

import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the harness, not a package named tests
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "core")]

import cli_harness as harness  # noqa: E402
from cortex_cli import config, machine, store, sync, upgrade  # noqa: E402
from cortex_cli.devversion import development  # noqa: E402
from cortex_cli.semver import Version  # noqa: E402
from release_fixture import FakeRelease  # noqa: E402

OWN = harness.EXPECTED_VERSION if harness.BINARY else "9.9.9"
DOWNLOADS = unittest.skipIf(harness.BINARY, "a build of no release downloads no version")
GIT = shutil.which("git")
NEEDS_GIT = unittest.skipIf(GIT is None, "needs git")
POSIX = unittest.skipIf(os.name == "nt", "a git stand-in written as a shell script")
# A build of no release — a source checkout, X.Y.Z-dev, or a CI build, X.Y.Z-dev.N — takes the version
# its branch prepares (ADR-009 §9): from source with no CORTEX_SOURCE_VERSION, the changelog's.
DEV_BUILD = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-dev(\.[0-9]+)?")
OWN_DEV = harness.EXPECTED_VERSION if harness.BINARY else \
    development((harness.REPO / "CHANGELOG.md").read_text(encoding="utf-8"))
RELEASE_BUILD = unittest.skipIf(harness.BINARY is not None and not DEV_BUILD.fullmatch(harness.EXPECTED_VERSION or ""),
                                "a release's build, which is no development build")


def display(path):
    return str(path).replace(os.sep, "/")


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def remove_store(path):
    """Remove a version of the store, read-only as the store writes it (ADR-008 §3.3): writable first — a user
    who is not root, and Windows, remove no read-only file."""
    for directory, _, files in os.walk(path):
        os.chmod(directory, 0o700)
        for name in files:
            os.chmod(os.path.join(directory, name), 0o600)
    shutil.rmtree(path)


class SettingsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-settings-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "cortex-home"
        self.home.mkdir()
        self.release = FakeRelease(("9.9.7", "9.9.8"))
        self.addCleanup(self.release.close)
        self.env = harness.environment(CORTEX_HOME=str(self.home), CORTEX_RELEASES_URL=self.release.url,
                                       CORTEX_SOURCE_VERSION=OWN, CORTEX_NO_REGISTER="1", CORTEX_NO_UPDATE_CHECK="1")

    def project(self, version=OWN, extra="", name="app"):
        root = self.tmp / name
        root.mkdir(parents=True)
        (root / "cortex.toml").write_text(f'version = "{version}"\ntheme = "h2g2"\n{extra}', encoding="utf-8")
        return root

    def machine(self, text):
        (self.home / "config.toml").write_text(text, encoding="utf-8")

    def cortex(self, cwd, *args, **env):
        proc = harness.run(*args, cwd=cwd, env={**self.env, **env})
        proc.out, proc.err = proc.stdout.decode(), proc.stderr.decode()
        return proc

    def settings(self, cwd, *args, **env):
        proc = self.cortex(cwd, "config", "--json", *args, **env)
        self.assertEqual(proc.returncode, 0, proc.err)
        return json.loads(proc.out)

    def assert_ok(self, proc):
        self.assertEqual(proc.returncode, 0, proc.out + proc.err)


class ConfigCommandTests(SettingsTestCase):
    def test_each_setting_its_value_and_its_origin(self):
        project = self.project(extra="claude_access = true\n")
        (project / "cortex.local.toml").write_text('theme = "none"\n', encoding="utf-8")
        before = snapshot(self.tmp)
        proc = self.cortex(project, "config")
        self.assert_ok(proc)
        lines = {line.split()[0]: line.split(None, 2)[1:] for line in proc.out.splitlines()}
        self.assertEqual(list(lines), ["version", "theme", "sync", "claude_access", "agents", "releases_url",
                                       "update_check", "register"])
        self.assertEqual(lines["version"], [OWN, "cortex.toml"])
        self.assertEqual(lines["theme"], ["none", "cortex.local.toml"])
        self.assertEqual(lines["sync"], ["store", "default"])
        self.assertEqual(lines["agents"], ["agents", "default"])
        self.assertEqual(lines["claude_access"], ["true", "cortex.toml"])
        self.assertEqual(lines["register"], ["false", "CORTEX_NO_REGISTER"])
        self.assertEqual(snapshot(self.tmp), before)                     # it writes nothing

    def test_locked_is_the_team_s_resolution_without_the_local_file(self):
        project = self.project()
        (project / "cortex.local.toml").write_text('theme = "none"\nclaude_access = true\n', encoding="utf-8")
        found = self.settings(project, "--locked")
        self.assertEqual((found["format"], found["locked"]), (1, True))
        self.assertEqual(found["settings"]["theme"], {"value": "h2g2", "origin": "cortex.toml", "overridden": [],
                                                      "locked": False})
        self.assertEqual(found["settings"]["claude_access"]["value"], None)
        found = self.settings(project)
        self.assertEqual(found["settings"]["theme"]["overridden"], [{"origin": "cortex.toml", "value": "h2g2"}])

    def test_a_key_alone_and_a_key_unknown(self):
        project = self.project()
        proc = self.cortex(project, "config", "version", "--locked")
        self.assert_ok(proc)
        self.assertEqual(proc.out, f"{OWN}\n")
        proc = self.cortex(project, "config", "claude_access")
        self.assertEqual(proc.out, "\n")                                  # unset
        proc = self.cortex(project, "config", "verison")
        self.assertEqual(proc.returncode, 2, proc.err)
        self.assertIn('cortex config: no setting "verison" — cortex config knows version, theme', proc.err)

    def test_outside_a_project_the_machine_s_keys_only(self):
        found = self.settings(self.tmp)
        self.assertEqual(list(found["settings"]), ["releases_url", "update_check", "register"])
        self.assertIsNone(found["project"])
        proc = self.cortex(self.tmp, "config", "version")
        self.assertEqual(proc.returncode, 2, proc.err)
        self.assertIn("outside a project, cortex config knows releases_url, update_check and register", proc.err)

    def test_a_refused_file_exits_1_naming_it(self):
        project = self.project()
        (project / "cortex.local.toml").write_text('verison = "x"\n', encoding="utf-8")
        proc = self.cortex(project, "config")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn('cortex.local.toml: unknown key "verison"', proc.err)

    @harness.outside_a_repository
    def test_a_root_that_is_no_repository_reads_cortex_toml_s_version_locked_too(self):
        # A 2.B root, with no team's file: the version is cortex.toml's, whoever reads it (§3.1).
        project = self.project()
        self.assert_ok(self.cortex(project, "sync"))
        found = self.settings(project, "--locked")
        self.assertEqual((found["committed"], found["settings"]["version"]["value"]), (False, OWN))
        self.assert_ok(self.cortex(project, "sync", "--locked"))


class CommittedTests(SettingsTestCase):
    def refusing_git(self):
        """A git that refuses every repository, as one does a repository another user owns."""
        bin_dir = self.tmp / "refusing-git"
        bin_dir.mkdir()
        script = bin_dir / "git"
        script.write_text("#!/bin/sh\necho 'fatal: detected dubious ownership in repository' >&2\nexit 128\n",
                          encoding="utf-8")
        script.chmod(0o755)
        return str(bin_dir)

    @POSIX
    @harness.outside_a_repository
    def test_a_git_that_refuses_counts_as_no_git(self):
        project = self.project()
        path = self.refusing_git()
        self.assertIs(self.settings(project, PATH=path)["committed"], False)
        (self.tmp / ".git").mkdir()                                      # a .git above the root counts
        self.assertIs(self.settings(project, PATH=path)["committed"], True)
        self.assertIs(self.settings(project)["committed"], True)         # no git at all: the same rule

    @NEEDS_GIT
    def test_tracked_in_a_subdirectory_of_its_repository_and_untracked(self):
        repo = self.tmp / "repo"
        project = repo / "projects" / "foo"
        project.mkdir(parents=True)
        (project / "cortex.toml").write_text(f'version = "{OWN}"\ntheme = "h2g2"\n', encoding="utf-8")
        subprocess.run([GIT, "init", "-q", str(repo)], check=True)
        path = os.path.dirname(GIT)
        self.assertIs(self.settings(project, PATH=path)["committed"], False)      # in a repository, untracked
        subprocess.run([GIT, "-C", str(repo), "add", "projects/foo/cortex.toml"], check=True)
        self.assertIs(self.settings(project, PATH=path)["committed"], True)

    @NEEDS_GIT
    def test_every_git_call_asks_the_project_s_own_repository(self):
        # sync's and upgrade's git, as committed's: the variables that point git at a repository — a hook's — are not
        # followed, and the others stay: the author's name.
        other, mine = self.tmp / "other", self.tmp / "mine"
        for repo in (other, mine):
            subprocess.run([GIT, "init", "-q", str(repo)], check=True)
        project = self.project()
        alone = sync.ignored(project, "cortex.local.toml")             # None in no repository, as TMPDIR has it
        with mock.patch.dict(os.environ, {"GIT_DIR": str(other / ".git"), "GIT_AUTHOR_NAME": "Kept"}):
            self.assertEqual(sync.ignored(project, "cortex.local.toml"), alone)
            for call in (sync._git, upgrade._git):
                with self.subTest(call=call.__module__):
                    found = call(str(mine), "rev-parse", "--absolute-git-dir").stdout.strip()
                    self.assertEqual(os.path.realpath(found), os.path.realpath(mine / ".git"))
            environment = sync.git_environment()
            self.assertEqual(environment.get("GIT_AUTHOR_NAME"), "Kept")
            self.assertNotIn("GIT_DIR", environment)

    @NEEDS_GIT
    def test_git_s_configuration_of_the_caller_is_kept(self):
        # A CI gives git its identity, or a safe.directory, through GIT_CONFIG_COUNT and GIT_CONFIG_PARAMETERS: git
        # keeps them itself on its way to another repository (prepare_other_repo_env), and so does Cortex.
        mine = self.tmp / "mine"
        subprocess.run([GIT, "init", "-q", str(mine)], check=True)
        given = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "cortex.counted", "GIT_CONFIG_VALUE_0": "kept",
                 "GIT_CONFIG_PARAMETERS": "'cortex.given=kept'"}
        with mock.patch.dict(os.environ, given):
            for call in (sync._git, upgrade._git):
                for key in ("cortex.counted", "cortex.given"):
                    with self.subTest(call=call.__module__, key=key):
                        self.assertEqual(call(str(mine), "config", "--get", key).stdout.strip(), "kept")

    @NEEDS_GIT
    def test_git_s_variables_of_the_caller_do_not_name_another_repository(self):
        # A hook exports GIT_DIR: asked of that repository, a root that is in none would read as
        # committed, and a project its own repository tracks as not.
        other, empty = self.tmp / "other", self.tmp / "empty"
        other.mkdir()
        (other / "cortex.toml").write_text(f'version = "{OWN}"\ntheme = "h2g2"\n', encoding="utf-8")
        for repo in (other, empty):
            subprocess.run([GIT, "init", "-q", str(repo)], check=True)
        subprocess.run([GIT, "-C", str(other), "add", "cortex.toml"], check=True)
        path = os.path.dirname(GIT)
        project = self.project()                                         # in no repository
        self.assertIs(self.settings(project, PATH=path, GIT_DIR=str(other / ".git"))["committed"], False)
        self.assertIs(self.settings(other, PATH=path, GIT_DIR=str(empty / ".git"))["committed"], True)
        self.assertIs(self.settings(other, PATH=path, GIT_INDEX_FILE=str(empty / "index"))["committed"], True)
        # GIT_LITERAL_PATHSPECS reads `:(literal)cortex.toml` as a file of that name, which git tracks not.
        self.assertIs(self.settings(other, PATH=path, GIT_LITERAL_PATHSPECS="1")["committed"], True)
        # GIT_ICASE_PATHSPECS matches a tracked CORTEX.TOML: the developer's own cortex.toml, untracked, would read
        # as committed.
        icase = self.tmp / "icase"
        icase.mkdir()
        subprocess.run([GIT, "init", "-q", str(icase)], check=True)
        (icase / "CORTEX.TOML").write_text("", encoding="utf-8")
        subprocess.run([GIT, "-C", str(icase), "add", "CORTEX.TOML"], check=True)
        if not (icase / "cortex.toml").exists():                         # two files, where names keep case
            (icase / "cortex.toml").write_text(f'version = "{OWN}"\ntheme = "h2g2"\n', encoding="utf-8")
            self.assertIs(self.settings(icase, PATH=path)["committed"], False)
            self.assertIs(self.settings(icase, PATH=path, GIT_ICASE_PATHSPECS="1")["committed"], False)

    @NEEDS_GIT
    def test_a_project_too_new_says_whether_it_is_committed(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        (repo / "cortex.toml").write_text('version = "99.0.0"\ntheme = "h2g2"\nagents = "team"\n', encoding="utf-8")
        subprocess.run([GIT, "init", "-q", str(repo)], check=True)
        path = os.path.dirname(GIT)
        found = self.settings(repo, PATH=path)
        self.assertEqual((found["too_new"], found["committed"]), (True, False))
        subprocess.run([GIT, "-C", str(repo), "add", "cortex.toml"], check=True)
        found = self.settings(repo, PATH=path)
        self.assertEqual((found["too_new"], found["committed"]), (True, True))


class MachineFileTests(SettingsTestCase):
    @DOWNLOADS
    def test_the_file_s_releases_url_downloads_and_the_variable_goes_over_it(self):
        self.machine(f'releases_url = "{self.release.url}"\n')
        project = self.project("9.9.8")
        proc = self.cortex(project, "sync", CORTEX_RELEASES_URL="")       # empty: unset, the file's
        self.assert_ok(proc)
        self.assertTrue((self.home / "versions" / "9.9.8").is_dir())
        found = self.settings(project, CORTEX_RELEASES_URL="")
        self.assertEqual(found["settings"]["releases_url"]["origin"], display(self.home / "config.toml"))
        self.machine('releases_url = "http://example.com/releases"\n')   # refused, if it were read
        remove_store(self.home / "versions")
        self.assert_ok(self.cortex(project, "sync"))                     # the variable's
        self.assertEqual(self.settings(project)["settings"]["releases_url"]["origin"], "CORTEX_RELEASES_URL")

    @DOWNLOADS
    def test_an_invalid_releases_url_is_refused_naming_the_file(self):
        self.machine('releases_url = "http://example.com/releases"\n')
        proc = self.cortex(self.project("9.9.8"), "sync", CORTEX_RELEASES_URL="")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f"{display(self.home / 'config.toml')}: ", proc.err)
        self.assertIn("https", proc.err)

    def test_config_says_a_releases_url_a_download_refuses_and_exits_0(self):
        # cortex config prints every setting, and CI reads `cortex config version`: a warning, never an error.
        self.machine('releases_url = "http://example.com/releases"\n')
        project = self.project()
        where = display(self.home / "config.toml")
        proc = self.cortex(project, "config", "version", CORTEX_RELEASES_URL="")
        self.assert_ok(proc)
        self.assertEqual(proc.out, f"{OWN}\n")
        self.assertIn(f"warning: {where}: http://example.com/releases: only https is downloaded from", proc.err)
        found = self.settings(project, CORTEX_RELEASES_URL="")
        self.assertEqual(len([warning for warning in found["warnings"] if "a download refuses it" in warning]), 1)
        proc = self.cortex(project, "config", CORTEX_RELEASES_URL="ftp://mirror.example/r")    # the variable's
        self.assert_ok(proc)
        self.assertIn("warning: CORTEX_RELEASES_URL: ftp://mirror.example/r: only https", proc.err)
        self.assertNotIn("a download refuses it", self.cortex(project, "config").err)        # the fake release's

    def test_register_and_update_check_off_in_the_file(self):
        self.machine("register = false\nupdate_check = false\n")
        project = self.project()
        proc = self.cortex(project, "sync", CORTEX_NO_REGISTER="")
        self.assert_ok(proc)
        self.assertNotIn("registered as", proc.out)
        self.assertFalse((self.home / "projects.toml").exists())
        found = self.settings(project, CORTEX_NO_REGISTER="", CORTEX_NO_UPDATE_CHECK="")
        self.assertEqual((found["settings"]["register"]["value"], found["settings"]["update_check"]["value"]),
                         (False, False))
        self.machine("register = true\n")
        proc = self.cortex(project, "sync", CORTEX_NO_REGISTER="1")     # the variable over the file
        self.assertFalse((self.home / "projects.toml").exists())
        proc = self.cortex(project, "sync", CORTEX_NO_REGISTER="")
        self.assertIn("registered as app", proc.out)

    def test_a_warning_is_said_once_by_a_command_that_reads_the_file_more_than_once(self):
        # cortex upgrade reads it for the release check, the registry, and its sync.
        self.machine('endpoints = "later"\n')
        project = self.project()
        proc = self.cortex(project, "upgrade", CORTEX_NO_UPDATE_CHECK="", CORTEX_NO_REGISTER="")
        self.assert_ok(proc)
        self.assertEqual(proc.err.count('unknown key "endpoints"'), 1, proc.err)

    def test_an_unknown_key_or_a_wrong_type_warns_and_the_rest_is_read(self):
        self.machine('register = "no"\nendpoints = "later"\nupdate_check = false\n')
        project = self.project()
        proc = self.cortex(project, "sync", CORTEX_NO_REGISTER="")      # the variable unset: the file is read
        self.assert_ok(proc)
        self.assertIn("registered as app", proc.out)                    # register = "no" not read: the default
        where = display(self.home / "config.toml")
        self.assertEqual(proc.err.count(f'warning: {where}: "register" must be true or false'), 1, proc.err)
        self.assertIn(f'warning: {where}: unknown key "endpoints", not read', proc.err)
        found = self.settings(project, CORTEX_NO_UPDATE_CHECK="")
        self.assertEqual(found["settings"]["update_check"]["origin"], where)
        self.machine("register = [\n")
        proc = self.cortex(project, "sync", CORTEX_NO_REGISTER="")
        self.assert_ok(proc)
        self.assertIn(f"warning: {where} is not read", proc.err)


class TooNewTests(SettingsTestCase):
    """A version newer than the binary is said before any key is checked (§3.8, steps 1–2)."""

    WHY = f"this project uses Cortex 99.0.0, which its cortex.toml pins, newer than this cortex, {OWN}"

    def test_sync_init_and_validate_say_the_version_not_the_key(self):
        project = self.project("99.0.0", extra='agents = "team"\n')      # a key this cortex does not know
        for args, code in ((("sync",), 1), (("init", "--tool", "claude"), 1), (("validate",), 2)):
            with self.subTest(args=args):
                proc = self.cortex(project, *args)
                self.assertEqual(proc.returncode, code, proc.err)
                self.assertIn(self.WHY, proc.err)
                self.assertIn("cortex self-update 99.0.0", proc.err)
                self.assertNotIn("unknown key", proc.err)

    def test_validate_of_a_synced_project_or_of_a_spec_not_there_says_the_version(self):
        # Only a spec that names no version — a checkout's — passes the gate: the store's version does
        # not, nor a spec that is not there, nor one that is no checkout: an empty cortex/, a copy whose
        # marker is gone or does not read — its trees there all the same —, a release's copy, whose marker names its
        # version, or a link to a checkout moved since.
        project = self.project("99.0.0", extra='agents = "team"\n')
        stored = self.home / "versions" / "99.0.0"
        for tree in ("agents", "templates", "docs"):
            shutil.copytree(harness.REPO / tree, stored / tree)
        cases = [(display(stored), None), ("cortex", None), ("cortex", "empty"), ("cortex", "no marker"),
                 ("cortex", "a marker that does not read"), ("cortex", "a release's copy")]
        if os.name != "nt":
            cases.append(("cortex", "dangling"))
        for spec, cortex in cases:
            with self.subTest(spec=spec, cortex=cortex):
                if cortex == "empty":
                    (project / "cortex").mkdir()
                elif cortex == "no marker":
                    for tree in ("agents", "templates", "docs"):
                        shutil.copytree(harness.REPO / tree, project / "cortex" / tree)
                elif cortex == "a marker that does not read":
                    (project / "cortex" / ".synced").write_text("{", encoding="utf-8")
                elif cortex == "a release's copy":         # every `sync = "copy"` project's: its marker names the version
                    (project / "cortex" / ".synced").write_text('{"version": "99.0.0", "files": {}}', encoding="utf-8")
                elif cortex == "dangling":
                    shutil.rmtree(project / "cortex")
                    os.symlink(self.tmp / "moved-checkout", project / "cortex")
                (project / "cortex.local.toml").write_text(f'spec = "{spec}"\n', encoding="utf-8")
                proc = self.cortex(project, "validate")
                self.assertEqual(proc.returncode, 2, proc.err)
                self.assertIn(self.WHY, proc.err)
                self.assertNotIn("unknown key", proc.err)

    def test_validate_of_a_checkout_s_copy_checks_no_version(self):
        # A copy of a checkout — its marker says no version — passes the gate: the key is said, not the version.
        project = self.project("99.0.0", extra='endpoints = "team"\n')        # a key no version takes yet
        for tree in ("agents", "templates", "docs"):
            shutil.copytree(harness.REPO / tree, project / "cortex" / tree)
        (project / "cortex" / ".synced").write_text('{"version": null, "files": {}}', encoding="utf-8")
        (project / "cortex.local.toml").write_text('spec = "cortex"\n', encoding="utf-8")
        proc = self.cortex(project, "validate")
        self.assertEqual(proc.returncode, 2, proc.err)
        self.assertIn('unknown key "endpoints"', proc.err)
        self.assertNotIn(self.WHY, proc.err)

    def test_upgrade_says_the_version_not_the_key(self):
        # Never down to this cortex's version without naming it.
        proc = self.cortex(self.project("99.0.0", extra='agents = "team"\n'), "upgrade")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(self.WHY, proc.err)
        self.assertIn("cortex self-update 99.0.0", proc.err)
        self.assertNotIn("unknown key", proc.err)

    def test_config_prints_the_version_and_says_so(self):
        known = self.project("99.0.0", name="known")                      # every key known to this cortex
        project = self.project("99.0.0", extra='agents = "team"\n')      # one it does not know
        for project, args in ((known, ()), (project, ()), (project, ("--locked",))):
            with self.subTest(project=project.name, args=args):
                proc = self.cortex(project, "config", *args)
                self.assert_ok(proc)
                self.assertIn(f"99.0.0", proc.out.splitlines()[0])
                self.assertIn(f"cortex.toml — newer than this cortex, {OWN}: cortex self-update 99.0.0",
                              proc.out.splitlines()[0])
                self.assertIn("register", proc.out)

    @harness.outside_a_repository
    def test_a_known_key_of_a_project_too_new_says_the_version_and_an_unknown_one_is_unknown(self):
        project = self.project("99.0.0", extra='agents = "team"\n')
        proc = self.cortex(project, "config", "theme")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(self.WHY, proc.err)
        proc = self.cortex(project, "config", "verison")
        self.assertEqual(proc.returncode, 2, proc.err)
        self.assertIn("cortex config knows version, theme, sync, claude_access, agents, releases_url", proc.err)
        found = self.settings(project)
        self.assertIs(found["too_new"], True)

    def test_upgrade_naming_a_version_still_moves_down(self):
        project = self.project("99.0.0")
        proc = self.cortex(project, "upgrade", OWN)
        self.assert_ok(proc)
        self.assertIn(f'version = "{OWN}"', (project / "cortex.toml").read_text(encoding="utf-8"))

    def test_a_checkout_passes(self):
        # --from checks no version, as in 1.7.0; nor does validate of a spec that names none.
        checkout = self.tmp / "checkout"
        for tree in ("agents", "templates", "docs"):
            shutil.copytree(harness.REPO / tree, checkout / tree)
        project = self.project("99.0.0")
        self.assert_ok(self.cortex(project, "sync", "--link", "--from", str(checkout)))
        proc = self.cortex(project, "validate")
        self.assertNotIn("newer than this cortex", proc.err)
        self.assertEqual(proc.returncode, 0, proc.out + proc.err)
        proc = self.cortex(project, "init", "--tool", "claude", "--from", str(checkout))
        self.assertNotIn("newer than this cortex", proc.err)
        self.assertEqual(proc.returncode, 0, proc.out + proc.err)


@RELEASE_BUILD
class DevelopmentBuildTests(SettingsTestCase):
    """A source checkout, X.Y.Z-dev, or a CI build of no release, X.Y.Z-dev.N, is the version its branch
    prepares (ADR-009 §9, 2026-11-09): the version gate compares it as any other — no exception — and a
    plain upgrade never moves a project to it unnamed."""

    PREPARED = "{}.{}.{}".format(*Version(OWN_DEV).core)      # the release it prepares: newer than it

    def setUp(self):
        super().setUp()
        del self.env["CORTEX_SOURCE_VERSION"]

    def test_it_says_the_version_it_prepares(self):
        proc = self.cortex(self.tmp, "--version")
        self.assertEqual(proc.out, f"cortex {OWN_DEV}\n")
        self.assertTrue(store.development(Version(OWN_DEV)), OWN_DEV)

    def test_validate_from_a_development_build_checks_a_project_pinned_to_an_older_release(self):
        project = self.project("1.7.0")
        stored = self.home / "versions" / "1.7.0"
        for tree in ("agents", "templates", "docs"):
            shutil.copytree(harness.REPO / tree, stored / tree)
        (project / "cortex.local.toml").write_text(f'spec = "{display(stored)}"\n', encoding="utf-8")
        proc = self.cortex(project, "validate")
        self.assertEqual(proc.returncode, 0, proc.out + proc.err)
        self.assertNotIn("newer than this cortex", proc.err)
        found = self.settings(project)
        self.assertIs(found["too_new"], False)
        self.assertEqual(found["cortex"], OWN_DEV)
        before = (project / "cortex.toml").read_bytes()
        proc = self.cortex(project, "upgrade")                    # never up to a version no teammate downloads
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f", {OWN_DEV}, the version its branch prepares, which no teammate can download — name the "
                      "version to move to: `cortex upgrade VERSION`. Nothing was changed", proc.err)
        self.assertEqual((project / "cortex.toml").read_bytes(), before)

    def test_the_release_it_prepares_is_too_new_for_it(self):
        project = self.project(self.PREPARED)
        why = f"this project uses Cortex {self.PREPARED}, which its cortex.toml pins, newer than this cortex, {OWN_DEV}"
        proc = self.cortex(project, "validate")
        self.assertEqual(proc.returncode, 2, proc.err)
        self.assertIn(why, proc.err)
        self.assertIs(self.settings(project)["too_new"], True)
        proc = self.cortex(project, "upgrade")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(why, proc.err)
        self.assertNotIn("move the project down", proc.err)     # to a version no teammate downloads

    def test_init_says_it_pins_its_own_version(self):
        # Pinned as any binary pins its own — not refused: CONTRIBUTING's loop makes throwaway projects from a checkout.
        project = self.tmp / "new"
        project.mkdir()
        proc = self.cortex(project, "init", "--tool", "claude")
        self.assertEqual(proc.returncode, 0, proc.out + proc.err)
        self.assertIn(f'version = "{OWN_DEV}"', (project / "cortex.toml").read_text(encoding="utf-8"))
        self.assertIn(f"warning: cortex.toml pins Cortex {OWN_DEV}, a build of no release, which no teammate can download "
                      "— pin a release: `cortex upgrade VERSION`\n", proc.err)
        project = self.project("1.7.0")                                    # a pin of its own: kept, and not said
        stored = self.home / "versions" / "1.7.0"
        for tree in ("agents", "templates", "docs"):
            shutil.copytree(harness.REPO / tree, stored / tree)
        proc = self.cortex(project, "init", "--tool", "claude")
        self.assertEqual(proc.returncode, 0, proc.out + proc.err)
        self.assertNotIn("build of no release", proc.err)

    @harness.outside_a_repository
    def test_its_version_is_written_where_no_teammate_reads_it_only(self):
        # The team's file and a committed cortex.toml are every teammate's: none can download this version.
        project = self.project("1.7.0")
        stored = self.home / "versions" / "1.7.0"
        for tree in ("agents", "templates", "docs"):
            shutil.copytree(harness.REPO / tree, stored / tree)
        (project / ".git").mkdir()
        before = (project / "cortex.toml").read_bytes()
        proc = self.cortex(project, "upgrade", OWN_DEV)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(f"Cortex {OWN_DEV} is a build of no release, which no teammate can download, and every teammate "
                      "reads its committed cortex.toml — nothing was changed. Pin a release there\n", proc.err)
        self.assertEqual((project / "cortex.toml").read_bytes(), before)
        (project / ".git").rmdir()                         # a throwaway project's: written, and said
        proc = self.cortex(project, "upgrade", OWN_DEV)
        self.assertEqual(proc.returncode, 0, proc.out + proc.err)
        self.assertIn(f"warning: cortex.toml pins Cortex {OWN_DEV}, a build of no release", proc.err)
        self.assertNotIn("/tag/", proc.out)
        # The team's file, at a pre-release of the release this build prepares, held by the store: never moved to it.
        pinned = "{}.{}.{}-alpha.1".format(*Version(OWN_DEV).core)
        for tree in ("agents", "templates", "docs"):
            shutil.copytree(harness.REPO / tree, self.home / "versions" / pinned / tree)
        team = self.tmp / "team"
        (team / "agents").mkdir(parents=True)
        (team / ".git").mkdir()
        (team / "cortex.toml").write_text('theme = "h2g2"\n', encoding="utf-8")
        (team / "agents" / "cortex.team.toml").write_text(f'version = "{pinned}"\n', encoding="utf-8")
        before = (team / "agents" / "cortex.team.toml").read_bytes()
        proc = self.cortex(team, "upgrade", "--team", OWN_DEV)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        # From a build that prepares 1.8.0, no release it serves is pinned there: this cortex is updated first.
        pin = "Pin a release there" if not store.prepares_before_team(Version(OWN_DEV)) else \
            ("Update this cortex to a release that reads the team's file — 1.8.0 or a candidate of it — then pin one: "
             "`cortex self-update VERSION`, `cortex upgrade --team VERSION`")
        self.assertIn(f"Cortex {OWN_DEV} is a build of no release, which no teammate can download, and every teammate "
                      f"reads agents/cortex.team.toml — nothing was changed. {pin}; tried on this machine: "
                      f"`cortex upgrade --local {OWN_DEV}`", proc.err)
        self.assertEqual((team / "agents" / "cortex.team.toml").read_bytes(), before)
        self.assertFalse((team / "agents" / "cortex.team.lock").exists())

    @unittest.skipIf(harness.BINARY, "a binary's version is its own")
    @harness.outside_a_repository
    def test_a_build_that_prepares_1_8_0_names_no_team_pin_for_a_committed_cortex_toml(self):
        # Bluspark's third pass: a committed cortex.toml is no team's file — from a build that prepares 1.8.0 or
        # below, its pin is moved plainly, never with `cortex upgrade --team VERSION`, whatever the changelog says.
        self.env["CORTEX_SOURCE_VERSION"] = "1.8.0-dev.5"
        self.assertTrue(store.prepares_before_team(Version("1.8.0-dev.5")))
        project = self.project("1.7.0")
        stored = self.home / "versions" / "1.7.0"
        for tree in ("agents", "templates", "docs"):
            shutil.copytree(harness.REPO / tree, stored / tree)
        (project / ".git").mkdir()
        before = (project / "cortex.toml").read_bytes()
        proc = self.cortex(project, "upgrade", "1.8.0-dev.5")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("Cortex 1.8.0-dev.5 is a build of no release, which no teammate can download, and every teammate "
                      "reads its committed cortex.toml — nothing was changed. Pin a release there\n", proc.err)
        self.assertNotIn("--team", proc.err)
        self.assertEqual((project / "cortex.toml").read_bytes(), before)

    @unittest.skipIf(harness.BINARY, "a source checkout's remedy")
    @NEEDS_GIT
    @harness.outside_a_repository
    def test_a_checkout_too_old_for_the_pin_is_told_to_update_itself(self):
        # #345: no self-update replaces a source checkout — the update it names is the checkout's own. A clone of this
        # one, its changelog behind a release this checkout prepares past: the command named, run, makes it serve it.
        pinned = "1.8.0"
        self.assertLess(Version(pinned), Version(OWN_DEV))
        git = [GIT, "-c", "user.name=Cortex Tests", "-c", "user.email=tests@example.invalid", "-c", "core.autocrlf=false"]
        upstream = self.tmp / "upstream"
        for tree in ("cli/cortex_cli", "core/cortex_core", "agents", "templates", "docs"):
            shutil.copytree(harness.REPO / tree, upstream / tree, ignore=shutil.ignore_patterns("__pycache__"))
        behind = b"# Changelog\n\n## [Unreleased]\n\n## [1.7.0] - 2026-01-01\n"
        (upstream / "CHANGELOG.md").write_bytes(behind)
        subprocess.run([*git, "init", "-q", str(upstream)], check=True, capture_output=True)
        subprocess.run([*git, "-C", str(upstream), "add", "-A"], check=True, capture_output=True)
        subprocess.run([*git, "-C", str(upstream), "commit", "-q", "-m", "behind"], check=True, capture_output=True)
        checkout = self.tmp / "checkout"
        subprocess.run([*git, "clone", "-q", str(upstream), str(checkout)], check=True, capture_output=True)
        shutil.copyfile(harness.REPO / "CHANGELOG.md", upstream / "CHANGELOG.md")
        subprocess.run([*git, "-C", str(upstream), "commit", "-q", "-am", "ahead"], check=True, capture_output=True)
        release = FakeRelease((pinned,))
        self.addCleanup(release.close)
        project = self.project(pinned)

        def sync():
            proc = subprocess.run([sys.executable, "-I", "-S", "-c", harness.SOURCE_CALL, str(checkout / "core"),
                                   str(checkout / "cli"), "sync"], cwd=project, capture_output=True,
                                  env={**self.env, "CORTEX_RELEASES_URL": release.url, "PWD": str(project)})
            proc.out, proc.err = proc.stdout.decode(), proc.stderr.decode()
            return proc

        proc = sync()
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(f"newer than this cortex, {development(behind.decode())} — update it:\n    git -C {display(checkout)} pull\n", proc.err)
        self.assertNotIn("self-update", proc.err)
        named = re.search(r"update it:\n    git (-C .+ pull)\n", proc.err).group(1)
        subprocess.run([*git, *shlex.split(named)], check=True, capture_output=True)     # the command it names
        self.assert_ok(sync())
        self.assertEqual(config.load(str(project)).synced["reads"], pinned)

    def test_upgrade_all_names_the_version_once(self):
        project = self.project("1.7.0")
        (self.home / "projects.toml").write_text(f'format = 1\n\n[[project]]\nname = "app"\npath = "{display(project)}"\n',
                                                 encoding="utf-8")
        before = (project / "cortex.toml").read_bytes()
        proc = self.cortex(self.tmp, "upgrade", "--all")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(f", {OWN_DEV}, the version its branch prepares, which no teammate can download — name the "
                      "version to move to: `cortex upgrade --all VERSION`. Nothing was changed", proc.err)
        self.assertNotIn("Project:", proc.out)
        self.assertEqual((project / "cortex.toml").read_bytes(), before)
        (project / "cortex.toml").write_text(f'version = "{OWN_DEV}"\ntheme = "h2g2"\n', encoding="utf-8")
        proc = self.cortex(self.tmp, "upgrade", "--all", "--dry-run")        # none to move: each one resumes
        self.assertEqual(proc.returncode, 0, proc.out + proc.err)
        self.assertIn(f"already on {OWN_DEV}", proc.out)

    def test_what_a_development_build_is(self):
        for text in ("0.0.0-dev", "1.8.0-dev", "1.8.0-dev.7", "2.0.0-dev.0", OWN_DEV):
            self.assertTrue(store.development(Version(text)), text)
        for text in ("1.8.0-dev.x", "1.8.0-dev.1.2", "1.8.0-devx", "1.8.0-rc.1", "1.8.0", "1.8.0-alpha.dev"):
            self.assertFalse(store.development(Version(text)), text)
        for version, own in (("1.8.0", "1.8.0-dev.7"), ("1.8.0-dev.8", "1.8.0-dev.7"), ("1.7.0", "0.0.0-dev"),
                             ("1.7.0", "1.6.0"), ("1.8.0-rc.1", "1.8.0-dev")):
            self.assertTrue(store.too_old_for(Version(version), Version(own)), (version, own))
        for version, own in (("1.7.0", "1.8.0-dev"), ("1.8.0-dev.7", "1.8.0-dev.7"), ("1.8.0", "1.8.1-dev.3"),
                             ("1.7.0", "1.7.0")):
            self.assertFalse(store.too_old_for(Version(version), Version(own)), (version, own))


def team_remedy(block: bool) -> str:
    """What a team's pin of no release is told: pin a release by --team — after a self-update, from a build that prepares
    1.8.0 or below, whose every --team VERSION is refused (ADR-009 §9)."""
    if store.prepares_before_team(Version(OWN)):
        first = "update this cortex to a release that reads the team's file — 1.8.0 or a candidate of it — then pin one"
        return (f"{first}:\n    cortex self-update VERSION\n    cortex upgrade --team VERSION" if block else
                f"{first}: `cortex self-update VERSION`, `cortex upgrade --team VERSION`")
    return "pin a release:\n    cortex upgrade --team VERSION" if block else "pin a release: `cortex upgrade --team VERSION`"


class DevelopmentPinTests(SettingsTestCase):
    """A project pinned to a build of no release's version — a CI artifact's, which `cortex init` pins —
    is told to pin a release: no self-update installs that version, no release serves it (ADR-009 §9)."""

    NEWER = "{}.{}.{}-dev.3".format(*Version(OWN).core[:2], Version(OWN).core[2] + 1)

    def test_newer_than_this_cortex(self):
        project = self.project(self.NEWER)
        for args in (("sync",), ("upgrade",)):
            with self.subTest(args=args):
                proc = self.cortex(project, *args)
                self.assertEqual(proc.returncode, 1, proc.err)
                self.assertIn(f"this project uses Cortex {self.NEWER}, which its cortex.toml pins — a build of no "
                              f"release, newer than this cortex, {OWN}", proc.err)
                self.assertIn("pin a release", proc.err.lower())
                self.assertIn("cortex upgrade VERSION", proc.err)
                self.assertNotIn("self-update", proc.err)
        proc = self.cortex(project, "config", "version")
        self.assert_ok(proc)
        self.assertIn(f"note: Cortex {self.NEWER} is a build of no release, newer than this cortex, {OWN} — pin a "
                      "release:\n    cortex upgrade VERSION\n", proc.err)
        proc = self.cortex(project, "config")
        self.assert_ok(proc)
        self.assertIn(f"cortex.toml — a build of no release, newer than this cortex, {OWN}: pin a release: "
                      "`cortex upgrade VERSION`", proc.out)

    def test_a_try_is_moved_by_local(self):
        project = self.project(extra="")
        (project / "cortex.local.toml").write_text(f'version = "{self.NEWER}"\n', encoding="utf-8")
        proc = self.cortex(project, "sync")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f"which its cortex.local.toml pins — a build of no release, newer than this cortex, {OWN}: pin a "
                      "release:\n    cortex upgrade --local VERSION", proc.err)

    def test_the_team_s_pin_is_moved_by_team(self):
        root = self.tmp / "team"
        (root / "agents").mkdir(parents=True)
        (root / "cortex.toml").write_text('theme = "h2g2"\n', encoding="utf-8")         # it follows the team's file
        (root / "agents" / "cortex.team.toml").write_text('version = "1.8.0-dev.3"\n', encoding="utf-8")
        for args in (("sync",), ("upgrade",)):                              # a follower resumes the team's pin
            with self.subTest(args=args):
                proc = self.cortex(root, *args)
                self.assertEqual(proc.returncode, 1, proc.err)
                self.assertIn(f"Cortex 1.8.0-dev.3 is a build of no release, which no release serves — {team_remedy(False)}",
                              proc.err)

    def test_config_names_the_command_of_the_file_that_pins_it(self):
        project = self.project(extra="")
        (project / "cortex.local.toml").write_text(f'version = "{self.NEWER}"\n', encoding="utf-8")
        team = self.tmp / "team"
        (team / "agents").mkdir(parents=True)
        (team / "cortex.toml").write_text('theme = "h2g2"\n', encoding="utf-8")
        (team / "agents" / "cortex.team.toml").write_text(f'version = "{self.NEWER}"\n', encoding="utf-8")
        local = ("pin a release:\n    cortex upgrade --local VERSION", "pin a release: `cortex upgrade --local VERSION`")
        for root, (block, inline) in ((project, local), (team, (team_remedy(True), team_remedy(False)))):
            with self.subTest(command=inline):
                proc = self.cortex(root, "config", "version")
                self.assert_ok(proc)
                self.assertIn(f"{block}\n", proc.err.replace("\r\n", "\n"))
                proc = self.cortex(root, "config")
                self.assert_ok(proc)
                self.assertIn(f"newer than this cortex, {OWN}: {inline}", proc.out)

    @harness.outside_a_repository
    def test_named_to_upgrade(self):
        project = self.project()
        before = (project / "cortex.toml").read_bytes()
        for held in (False, True):                                          # in the store too: newer all the same
            if held:
                for tree in ("agents", "templates", "docs"):
                    (self.home / "versions" / self.NEWER / tree).mkdir(parents=True)
            with self.subTest(held=held):
                proc = self.cortex(project, "upgrade", self.NEWER)
                self.assertEqual(proc.returncode, 1, proc.err)
                self.assertIn(f"Cortex {self.NEWER} is a build of no release, which no release serves — pin a "
                              "release: `cortex upgrade VERSION`", proc.err)
                self.assertNotIn("self-update", proc.err)
                self.assertEqual((project / "cortex.toml").read_bytes(), before)

    def test_a_move_from_it_says_no_remedy_in_its_note(self):
        project = self.project("1.7.1-dev.3")
        proc = self.cortex(project, "upgrade", OWN, "--dry-run")
        self.assertEqual(proc.returncode, 0, proc.out + proc.err)
        self.assertIn("note: what changes is not said — Cortex 1.7.1-dev.3 is a build of no release\n", proc.err)

    def test_older_than_this_cortex(self):
        project = self.project("1.7.1-dev.3")
        proc = self.cortex(project, "sync")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn("Cortex 1.7.1-dev.3 is a build of no release, which no release serves — pin a release: "
                      "`cortex upgrade VERSION`", proc.err)
        self.assertEqual(self.release.requested, [])                        # nothing asked of the releases


class NoHomeTests(unittest.TestCase):
    """A machine with no home to find — no CORTEX_HOME, no HOME, no account — reads no machine file."""

    def test_the_variable_is_read_first_and_no_home_only_warns(self):
        def no_home():
            raise RuntimeError("Could not determine home directory.")
        with mock.patch.dict(os.environ, {"CORTEX_RELEASES_URL": "https://mirror.example/r"}), \
                mock.patch.object(Path, "home", side_effect=no_home) as home:
            os.environ.pop("CORTEX_HOME", None)
            self.assertEqual(store.releases_url(), "https://mirror.example/r")
            home.assert_not_called()
        warned = io.StringIO()
        with mock.patch.dict(os.environ, {"CORTEX_RELEASES_URL": ""}), mock.patch.object(Path, "home", side_effect=no_home), \
                mock.patch("sys.stderr", warned):
            os.environ.pop("CORTEX_HOME", None)
            machine._warned.clear()
            self.assertEqual(store.releases_url(), store.DEFAULT_RELEASES_URL)
        self.assertIn("no Cortex home to read config.toml from", warned.getvalue())


class NoHomeCommandTests(SettingsTestCase):
    """cortex validate and cortex config build no store: with no home to find, they run (1.7.0's validate
    never needed one)."""

    def test_validate_and_config_run_with_no_home(self):
        from cortex_cli import settings, validate

        project = self.project()
        self.assert_ok(self.cortex(project, "sync", "--copy"))

        def no_home():
            raise RuntimeError("Could not determine home directory.")
        before = os.getcwd()
        self.addCleanup(os.chdir, before)
        os.chdir(project)
        environ = {key: value for key, value in os.environ.items() if key != "CORTEX_HOME"}
        with mock.patch.dict(os.environ, {**environ, "PWD": str(project)}, clear=True), \
                mock.patch.object(store, "VERSION", OWN), \
                mock.patch.object(Path, "home", side_effect=no_home), \
                mock.patch("sys.stderr", io.TextIOWrapper(io.BytesIO(), encoding="utf-8")), \
                mock.patch("sys.stdout", io.TextIOWrapper(io.BytesIO(), encoding="utf-8")):        # validate writes bytes
            self.assertEqual(validate.run([]), 0)
            for args in ((None, False, True), (None, False, False), ("theme", False, False), (None, True, True)):
                out = io.StringIO()
                self.assertEqual(settings.show(str(project), *args, out), 0, args)
            out = io.StringIO()
            self.assertEqual(settings.show(str(self.tmp), None, False, True, out), 0)
        self.assertEqual(list(json.loads(out.getvalue())["settings"]), ["releases_url", "update_check", "register"])
        self.assertIn("no Cortex home to read config.toml from", json.loads(out.getvalue())["warnings"][0])


class SameAsBeforeTests(SettingsTestCase):
    def test_a_second_sync_leaves_the_files_byte_for_byte(self):
        project = self.project(extra="claude_access = false\n")
        (project / "cortex.local.toml").write_text('# mine\ntheme = "none"\n', encoding="utf-8")
        self.assert_ok(self.cortex(project, "sync"))
        before = snapshot(project)
        self.assert_ok(self.cortex(project, "sync"))
        self.assertEqual(snapshot(project), before)
        self.assertEqual(set(before), {"cortex.toml", "cortex.local.toml", "cortex.lock"})
        local = before["cortex.local.toml"].decode()
        # synced, for a project whose team's version is 1.8.0 or later — this suite's (ADR-013 §3.1).
        self.assertEqual([line.split(" =")[0] for line in local.splitlines() if " = " in line],
                         ["theme", "spec", "synced"])


if __name__ == "__main__":
    unittest.main()
