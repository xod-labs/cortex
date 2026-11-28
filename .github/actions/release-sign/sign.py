"""The signature of a release's ``SHA256SUMS`` (ADR-015 §3.3): made, then checked, before any draft.

The private key is the ``release-signing`` environment's secret, ``RELEASE_SIGNING_KEY``, an Ed25519
key in PEM, handed in the environment variable of that name. It is written to a file only this user
may read, in the runner's temporary directory, used by ``openssl pkeyutl -sign -rawin``, and removed
before the step ends, whatever happens; nothing of it is printed.

The signature becomes ``SHA256SUMS.sig``, beside ``SHA256SUMS``: ``ed25519 <key id> <base64>``.
It is then checked the way the guard and the binaries check it — against the public keys of the
release keys file of the commit built, never against the secret — and its key must be the file's
primary: a secret that does not match it fails the run before a draft exists. Standard library
only, and ``openssl``.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from signature import SIG, SignatureError, check, line, read_keys, sign, write_private  # noqa: E402

SUMS = "SHA256SUMS"
SECRET = "RELEASE_SIGNING_KEY"


class Refused(Exception):
    pass


def sign_release(assets: Path, keys_file: Path, key_text: str, temp: Path) -> str:
    """Write ``assets/SHA256SUMS.sig`` and check it; the id of the key that signed."""
    if not key_text.strip():
        raise Refused(f"{SECRET} is empty: the job runs outside the release-signing environment, or its secret is "
                      f"not set — bin/setup-release.sh says how to set it")
    sums = assets / SUMS
    if not sums.is_file():
        raise Refused(f"there is no {SUMS} in {assets} to sign")
    try:
        keys = read_keys(keys_file.read_text(encoding="utf-8"))
    except (OSError, SignatureError) as error:
        raise Refused(f"{keys_file}: {error}")
    primary = sorted(kid for kid, (role, _) in keys.items() if role == "primary")
    if len(primary) != 1:
        raise Refused(f"{keys_file} holds {len(primary)} primary key(s), and should hold one: no signature can be checked")
    private = write_private(key_text, temp)
    try:
        kid, signature = sign(private, sums)
    except SignatureError as error:
        raise Refused(str(error))
    finally:
        private.unlink()
    (assets / SIG).write_text(line(kid, signature), encoding="ascii", newline="\n")
    checked = check(sums.read_bytes(), (assets / SIG).read_text(encoding="ascii"), keys)
    if checked.key is None:
        raise Refused(f"{SECRET} does not match the primary key {primary[0]} of {keys_file}: {SIG} {checked.why}")
    if checked.key != primary[0]:
        raise Refused(f"{SECRET} is the {keys[checked.key][0]} key {checked.key}, not the primary key {primary[0]} of "
                      f"{keys_file}")
    return checked.key


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--assets", required=True, type=Path, help="the directory that holds SHA256SUMS")
    parser.add_argument("--keys", required=True, type=Path, help="the release keys file of the commit built")
    args = parser.parse_args(argv)
    temp = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir())
    try:
        kid = sign_release(args.assets, args.keys, os.environ.get(SECRET, ""), temp)
    except Refused as error:
        print(f"::error title=release signature::{error}")
        return 1
    print(f"{SUMS} signed by the primary key {kid}, and checked against {args.keys}: {args.assets / SIG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
