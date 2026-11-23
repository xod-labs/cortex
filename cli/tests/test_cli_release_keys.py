"""The release keys a binary embeds (ADR-015 §3.1): one primary, one backup, each named by its id."""

import ast
import base64
import hashlib
import sys
import unittest
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "core")]
from cortex_cli import release_keys  # noqa: E402

KEYS_FILE = Path(release_keys.__file__)


def problems(keys):
    """What is wrong with a mapping shaped as ``release_keys.KEYS``, as text; none when it holds one
    primary and one backup key, each 32 bytes, each under its own id."""
    found = []
    roles = sorted(role for role, _ in keys.values())
    if roles != ["backup", "primary"]:
        found.append(f"the roles are {roles}, not one backup and one primary")
    for kid, (role, encoded) in keys.items():
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError:
            found.append(f"{kid}: its key is no base64")
            continue
        if len(raw) != 32:
            found.append(f"{kid}: its key is {len(raw)} bytes, not 32")
        if release_keys.key_id(raw) != kid:
            found.append(f"{kid}: its key's id is {release_keys.key_id(raw)}")
    return found


def entry(raw, role):
    return release_keys.key_id(raw), (role, base64.b64encode(raw).decode())


class ReleaseKeysTests(unittest.TestCase):
    # until the maintainer's keys are committed (ADR-015 §3.1)
    @unittest.expectedFailure
    def test_the_committed_keys_are_one_primary_and_one_backup_named_by_their_ids(self):
        self.assertEqual(problems(release_keys.KEYS), [])

    def test_a_key_s_id_is_the_start_of_its_sha256(self):
        raw = bytes(range(32))
        self.assertEqual(release_keys.key_id(raw), hashlib.sha256(raw).hexdigest()[:16])
        self.assertEqual(len(release_keys.key_id(raw)), 16)

    def test_what_the_check_of_the_committed_keys_finds(self):
        primary, backup = bytes(range(32)), bytes(range(1, 33))
        self.assertEqual(problems(dict([entry(primary, "primary"), entry(backup, "backup")])), [])
        self.assertIn("the roles are [], not one backup and one primary", problems({}))
        self.assertTrue(problems(dict([entry(primary, "primary"), entry(backup, "primary")])))
        wrong_id = {"0" * 16: ("primary", base64.b64encode(primary).decode()), **dict([entry(backup, "backup")])}
        self.assertIn(f"{'0' * 16}: its key's id is {release_keys.key_id(primary)}", problems(wrong_id))
        short = dict([entry(primary[:31], "primary"), entry(backup, "backup")])
        self.assertIn(f"{release_keys.key_id(primary[:31])}: its key is 31 bytes, not 32", problems(short))

    def test_public_keys_decodes_each_key(self):
        primary = bytes(range(32))
        self.assertEqual(release_keys.public_keys(dict([entry(primary, "primary")])),
                         {release_keys.key_id(primary): primary})

    def test_the_mapping_is_a_literal_the_release_actions_read_as_text(self):
        # The release workflow and the guard read the file with ast, without importing it.
        tree = ast.parse(KEYS_FILE.read_text(encoding="utf-8"))
        values = [node.value for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))
                  and any(isinstance(t, ast.Name) and t.id == "KEYS"
                          for t in (node.targets if isinstance(node, ast.Assign) else [node.target]))]
        self.assertEqual(len(values), 1)
        self.assertEqual(ast.literal_eval(values[0]), release_keys.KEYS)


if __name__ == "__main__":
    unittest.main()
