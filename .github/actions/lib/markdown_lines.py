"""The lines of a changelog, as Markdown — CommonMark — reads them: which hold no Markdown, which are in a list item.

One rule for every reader of ``CHANGELOG.md``: the version a build of no release prepares (``cli/cortex_cli``'s
``devversion``) and the release actions (``.github/actions/lib``'s ``changelog``) read the same sections. This file
is kept byte for byte the same in both directories — the actions' tests check it — and imports the standard library
only, as the actions do.

What is read, as CommonMark has it:

- a line ends at a line feed, or a carriage return alone — not at the other breaks Python splits text at, which an
  entry may hold; a byte order mark is no part of the text;
- a fenced code block — three backticks or tildes, or more, indented up to three spaces from its container — and an
  HTML comment, from ``<!--`` to the line that holds ``-->``, hold no Markdown: their lines are ``code``. Opened in a
  list item, either ends with it — at the first line not blank indented less than the item's content — and holds
  what the item holds to the end of the file. One at the top level never closed hides nothing, where Markdown would
  hide the rest of the file: a stray fence never loses the changelog's sections;
- a list item — ``-``, ``*``, ``+``, or ``1.``, ``1)`` — holds the lines indented as far as its content, its first
  line's text after the marker, and the lines of a paragraph that continue it, lazily, indented less. A line in a
  list item is ``nested``; an item's own line is when it starts in another item;
- a heading — one to six ``#``, indented up to three spaces — ends every list: a changelog's headings are its own,
  never an item's, even indented as far as one's content.
"""

from __future__ import annotations

import re
from typing import List, NamedTuple, Optional, Tuple

# Matched after the line's indentation, relative to its container.
FENCE = re.compile(r"(?P<fence>`{3,}|~{3,})(?P<info>.*)")
ITEM = re.compile(r"(?P<marker>[-*+]|[0-9]{1,9}[.)])(?P<gap> *)(?P<text>.*)")
BREAK = re.compile(r"([-*_])(?: *\1){2,} *")              # a thematic break: `- - -` is no list item
HEADING = re.compile(r" {0,3}#{1,6}(?:[ \t].*)?")
COMMENT, COMMENT_END = "<!--", "-->"
# A second-level section: `## [1.7.0] - 2026-11-06 — …`, `## [Unreleased]` — indented up to three spaces, a space or
# a tab after its ##.
SECTION = re.compile(r" {0,3}##[ \t]+\[(?P<label>[^\]]+)\].*")


class Line(NamedTuple):
    text: str               # the line, as written, without its line ending
    code: bool              # in a fenced code block or an HTML comment, its fence or delimiters included
    nested: bool            # in a list item — a list item's own line: in another one
    item: bool = False      # the line starts a list item


def lines(text: str) -> List[Line]:
    """Every line of ``text``, in order, with what Markdown makes of it."""
    raw = re.split(r"\r\n|\r|\n", text.removeprefix("﻿"))
    read: List[Line] = []
    items: List[int] = []           # the content column of each list item open, the outermost first
    paragraph = False               # the line before is a paragraph's, which a less indented line continues
    index = 0
    while index < len(raw):
        line = raw[index]
        if not line.strip():
            read.append(Line(line, False, bool(items)))
            paragraph, index = False, index + 1
            continue
        width = _indent(line)
        if HEADING.fullmatch(line):
            items.clear()
            read.append(Line(line, False, False))
            paragraph, index = False, index + 1
            continue
        inner = [column for column in items if column <= width]     # the items this line is in, by its indent
        base = inner[-1] if inner else 0
        rest = _expand(line)[width:] if width - base < 4 else None   # a block's start, or None: code or text
        opens = rest is not None and (_opens(rest) is not None or BREAK.fullmatch(rest) is not None or
                                      _item(rest) is not None)
        if len(inner) < len(items) and (opens or not paragraph):
            del items[len(inner):]                                  # the items it is not indented into end
        elif len(inner) < len(items):                               # a lazy line of the paragraph: still in them
            read.append(Line(line, False, True))
            index += 1
            continue
        if rest is None or not opens:
            read.append(Line(line, False, bool(items)))
            paragraph, index = True, index + 1
            continue
        if BREAK.fullmatch(rest):
            read.append(Line(line, False, bool(items)))
            paragraph, index = False, index + 1
            continue
        nested = bool(items)
        content = _item(rest)
        if content is not None:
            column, text_after = width + content, rest[content:]
            items.append(column)
            block = _opens(text_after) if text_after.strip() else None
            read.append(Line(line, False, nested, True))
            if block is None:
                paragraph, index = bool(text_after.strip()), index + 1
                continue
            index = _block(raw, index, block, column, read, first_line_read=True)
            paragraph = False
            continue
        index = _block(raw, index, _opens(rest), base, read)
        paragraph = not read[-1].code                               # a block never closed is a paragraph's line
    return read


def sections(text: str) -> List[Tuple[str, Line]]:
    """``(label, line)`` of every second-level section ``## [label]``, in order — none in code."""
    found = []
    for line in lines(text):
        match = None if line.code else SECTION.fullmatch(line.text.rstrip())
        if match:
            found.append((match.group("label"), line))
    return found


def _block(raw: List[str], index: int, block: Tuple[str, str], base: int, read: List[Line],
           first_line_read: bool = False) -> int:
    """The fenced block or HTML comment ``block`` opened at ``raw[index]``, in a container whose content is indented
    ``base`` columns — 0 at the top level: its lines appended to ``read``, as code; the index of the line after it.
    ``first_line_read``: the opening line is already in ``read`` — a list item's, which holds the block."""
    kind, fence = block
    opening = _expand(raw[index])
    start = opening.find(COMMENT) if kind == "comment" else -1
    end = None
    if kind == "comment" and COMMENT_END in opening[start + 2:]:
        end = index + 1                                  # `<!-- … -->` on one line, `<!-->` too
    later = index + 1
    while end is None and later < len(raw):
        line = raw[later]
        width = _indent(line)
        if base and line.strip() and width < base:
            end = later                                  # the item ends, and its block: this line is read
            break
        if kind == "comment" and COMMENT_END in line:
            end = later + 1
        elif kind == "fence" and line.strip() and width - base < 4 and _closes(_expand(line)[width:], fence):
            end = later + 1
        later += 1
    if end is None:
        if not base:                                     # at the top level, never closed: it hides nothing
            if not first_line_read:
                read.append(Line(raw[index], False, False))
            return index + 1
        end = len(raw)                                   # in an item, to the end of the file
    if first_line_read:
        index += 1
    read.extend(Line(line, True, bool(base)) for line in raw[index:end])
    return end


def _opens(rest: str) -> Optional[Tuple[str, str]]:
    """``("fence", fence)`` or ``("comment", "")`` when ``rest`` — a line past its indentation — opens a fenced code
    block or an HTML comment; ``None`` otherwise. Backticks in a backtick fence's info string make it none."""
    found = FENCE.fullmatch(rest.rstrip())
    if found and not (found.group("fence")[0] == "`" and "`" in found.group("info")):
        return "fence", found.group("fence")
    if rest.startswith(COMMENT):
        return "comment", ""
    return None


def _closes(rest: str, fence: str) -> bool:
    """Whether ``rest`` — a line past its indentation — closes a block ``fence`` opened: its character, as many or
    more, and nothing after it."""
    found = FENCE.fullmatch(rest.rstrip())
    return found is not None and found.group("fence")[0] == fence[0] and len(found.group("fence")) >= len(fence) \
        and not found.group("info").strip()


def _item(rest: str) -> Optional[int]:
    """The columns from a list item's marker to its content when ``rest`` — a line past its indentation, no thematic
    break — starts one; ``None`` otherwise. Five spaces or more after the marker make the content start one past it:
    code."""
    found = ITEM.fullmatch(rest)
    if found is None:
        return None
    marker, gap, text = found.group("marker"), len(found.group("gap")), found.group("text")
    if not text:
        return len(marker) + 1                           # an empty item, or one of spaces only
    if gap == 0:
        return None                                      # `-x`, `1.x`: no item
    return len(marker) + (1 if gap > 4 else gap)


def _expand(line: str) -> str:
    """``line`` with its tabs as spaces, to the next multiple of four columns: Markdown's indentation."""
    return line.expandtabs(4)


def _indent(line: str) -> int:
    wide = _expand(line)
    return len(wide) - len(wide.lstrip(" "))
