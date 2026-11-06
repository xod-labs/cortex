#!/usr/bin/env bash
# Fails when a tracked text file holds U+FFFD, the replacement character: what an encoding round
# trip leaves where it lost a character — an emoji, an accent — and what every host project then
# shows (#314). Binary files are left out: any byte sequence may sit in them.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 2

# Every path printed as it is, as check-english.sh prints it: core.quotePath off leaves one out of ASCII
# unquoted (not "caf\303\251.md"), and -z one that holds a \, a " or a control character. pipefail keeps
# git grep's status: 0 on a hit, 1 on none.
hits=$(git -c core.quotePath=false grep -z -n -I -F -e $'\xef\xbf\xbd' -- . | tr '\0' ':')
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
