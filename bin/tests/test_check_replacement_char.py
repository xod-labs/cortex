"""bin/check-replacement-char.sh in a throwaway repository: a tracked text file holding U+FFFD fails it (#314)."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check-replacement-char.sh"
LOST = "## \ufffd Anti-patterns\n"          # an emoji an encoding round trip lost: what the check must catch


class CheckReplacementCharTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo)
        (self.repo / "bin").mkdir()
        shutil.copy(SCRIPT, self.repo / "bin" / "check-replacement-char.sh")
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)

    def track(self, name, content):
        path = self.repo / name
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_bytes(content)
        subprocess.run(["git", "add", "--", name], cwd=self.repo, check=True)

    def check(self):
        out = subprocess.run(["bash", "bin/check-replacement-char.sh"], cwd=self.repo, capture_output=True,
                             text=True, encoding="utf-8", env=dict(os.environ, LC_ALL="C.UTF-8"))
        return out.returncode, out.stdout

    def test_a_tracked_file_holding_it_fails_and_is_named_with_its_line(self):
        self.track("card.md", "# Role\n\n" + LOST)
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("card.md:3:", out)

    def test_a_path_out_of_ascii_is_named_as_it_is(self):
        self.track("r\u00f4le.md", LOST)
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("r\u00f4le.md:1:", out)

    def test_clean_text_a_binary_file_and_an_untracked_one_pass(self):
        self.track("card.md", "## \U0001f6ab Anti-patterns\n")
        self.track("image.bin", b"\x00\x01" + "\ufffd".encode("utf-8") + b"\x00")     # any bytes, in a binary file
        (self.repo / "draft.md").write_text(LOST, encoding="utf-8")                 # not tracked
        self.assertEqual(self.check(), (0, "No replacement character in the tracked files.\n"))


if __name__ == "__main__":
    unittest.main()
