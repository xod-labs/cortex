"""This repository's release workflow signs SHA256SUMS before it drafts, and the draft and the guard
expect the same seven assets (ADR-015 §3.3, §3.4).

Read as text: the runner's Python has no YAML parser. Skipped where the action runs elsewhere.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
GUARD = ROOT / ".github" / "workflows" / "release-guard.yml"
SEVEN = ["SHA256SUMS", "SHA256SUMS.sig", "cortex-linux-aarch64.tar.gz", "cortex-linux-x86_64.tar.gz",
         "cortex-macos-arm64.tar.gz", "cortex-spec.tar.gz", "cortex-windows-x86_64.zip"]


def expected(text):
    block = re.search(r"(?m)^ {10}expected: \|\n((?: {12}\S.*\n?)+)", text)
    return [line.strip() for line in block.group(1).splitlines()] if block else None


def job(text, name):
    parts = re.split(r"(?m)^  ([A-Za-z0-9_-]+):\s*\n", text.split("\njobs:\n", 1)[1])
    return dict(zip(parts[1::2], parts[2::2]))[name]


@unittest.skipUnless(RELEASE.is_file(), "not in the repository that calls the action")
class ThisRepositoryTests(unittest.TestCase):
    def test_the_draft_and_the_guard_expect_the_seven_assets(self):
        self.assertEqual(expected(RELEASE.read_text(encoding="utf-8")), SEVEN)
        self.assertEqual(expected(GUARD.read_text(encoding="utf-8")), SEVEN)

    def test_the_draft_job_signs_in_its_environment_before_the_attestations(self):
        draft = job(RELEASE.read_text(encoding="utf-8"), "draft")
        self.assertRegex(draft, r"(?m)^    environment: release-signing$")
        sums = draft.index("python3 cli/build.py --checksums")
        signs = draft.index("uses: ./.github/actions/release-sign")
        attests = draft.index("uses: actions/attest-build-provenance")
        drafts = draft.index("uses: ./.github/actions/release-draft")
        self.assertLess(sums, signs)
        self.assertLess(signs, attests)   # SHA256SUMS.sig is attested like every asset
        self.assertLess(attests, drafts)
        step = draft[signs:attests]
        self.assertIn("key: ${{ secrets.RELEASE_SIGNING_KEY }}", step)
        self.assertIn("keys: cli/cortex_cli/release_keys.py", step)
        self.assertIn("assets: ${{ runner.temp }}/release-assets", step)
        self.assertIn("${{ runner.temp }}/release-assets/*", draft[attests:drafts])
        self.assertTrue((ROOT / "cli" / "cortex_cli" / "release_keys.py").is_file())

    def test_the_guard_checks_the_signature_against_the_same_file(self):
        self.assertIn("release-keys: cli/cortex_cli/release_keys.py", GUARD.read_text(encoding="utf-8"))
        self.assertIn('signed-from: "1.10.0"', GUARD.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
