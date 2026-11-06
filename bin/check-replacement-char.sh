#!/usr/bin/env bash
# Fails when a tracked text file holds U+FFFD, the replacement character: what an encoding round
# trip leaves where it lost a character — an emoji, an accent — and what every host project then
# shows (#314). Binary files are left out: any byte sequence may sit in them.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 2

# core.quotePath off: a path out of ASCII is printed as it is, not quoted as "caf\303\251.md".
hits=$(git -c core.quotePath=false grep -n -I -F -e $'\xef\xbf\xbd' -- .)
case $? in
  0)
    printf '%s\n' "$hits"
    echo "U+FFFD, the replacement character, in the lines above: put back the character it replaced." >&2
    exit 1
    ;;
  1)
    echo "No replacement character in the tracked files."
    ;;
  *)
    exit 2
    ;;
esac
