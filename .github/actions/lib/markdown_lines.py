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
  list item is ``nested``; an item's own line is when it starts in another item. An item whose first line holds
  only its marker ends at a blank line below it;
- a heading — one to six ``#``, indented up to three spaces — ends every list: a changelog's headings are its own,
  never an item's, even indented as far as one's content. Indented further, as far as an item's content, it is the
  item's: no paragraph, which the line below would continue;
- a paragraph is interrupted by a list item, but not by an empty one nor an ordered one that does not start at 1, and
  not by a line indented as code: such a line continues it. Indented code — four spaces past its container's
  content — is no paragraph, which a less indented line would continue;
- a line of ``=`` or ``-`` under a paragraph, in its container, makes it a heading — never a list item;
- a block quote's line, ``>``, is text: it ends the lists it is not indented into — no list item's line continues
  in it lazily — and holds no entry nor section. The line below continues it lazily only when it holds a paragraph's
  text — not a heading, a fence, a comment, a thematic break nor indented code, quoted.

Not read: HTML blocks other than comments — ``<details>``, ``<pre>`` — whose lines are read as Markdown: a section or
an entry in one is read. A blank line holds spaces and tabs only.
"""

from __future__ import annotations

import re
from typing import Dict, List, NamedTuple, Optional, Tuple

# Matched after the line's indentation, relative to its container.
FENCE = re.compile(r"(?P<fence>`{3,}|~{3,})(?P<info>.*)")
ITEM = re.compile(r"(?P<marker>[-*+]|[0-9]{1,9}[.)])(?P<gap> *)")     # its text: what follows
BREAK = re.compile(r"([-*_])(?: *\1){2,} *")              # a thematic break: `- - -` is no list item
HEADING = re.compile(r" {0,3}#{1,6}(?:[ \t].*)?")
HEADING_TEXT = re.compile(r"#{1,6}(?:[ \t].*)?")          # past its indentation: a heading in a list item
COMMENT, COMMENT_END = "<!--", "-->"
# A second-level section: `## [1.7.0] - 2026-11-06 — …`, `## [Unreleased]` — indented up to three spaces, a space or
# a tab after its ##.
SECTION = re.compile(r" {0,3}##[ \t]+\[(?P<label>[^\]]+)\].*")
QUOTE = ">"
UNDERLINE = re.compile(r"(?:=+|-+)[ \t]*")                # under a paragraph, it makes the paragraph a heading


class Line(NamedTuple):
    text: str               # the line, as written, without its line ending
    code: bool              # in a fenced code block or an HTML comment, its fence or delimiters included
    nested: bool            # in a list item — a list item's own line: in another one
    item: bool = False      # the line starts a list item


def is_unreleased(label: str) -> bool:
    """Whether a section's ``label`` names what is not released yet: ``[Unreleased]``, in any case."""
    return label.strip().lower() == "unreleased"


def lines(text: str) -> List[Line]:
    """Every line of ``text``, in order, with what Markdown makes of it."""
    raw = re.split(r"\r\n|\r|\n", text.removeprefix("﻿"))
    read: List[Line] = []
    items: List[int] = []           # the content column of each list item open, the outermost first
    paragraph = False               # the line before is a paragraph's, which a less indented line continues
    quoted = False                  # that paragraph is a block quote's: no list item is refused for interrupting it
    empty = False                   # the line before opens an empty list item, which a blank line ends
    closers = _closers(raw)         # what each line and those below it close, for a block opened at the top level
    index = 0
    while index < len(raw):
        line = raw[index]
        if _blank(line):
            if empty:
                items.pop()                                         # an item starts with one blank line at most
            read.append(Line(line, False, bool(items)))
            paragraph, empty, index = False, False, index + 1
            continue
        empty = False
        width = _indent(line)
        if HEADING.fullmatch(line):
            items.clear()
            read.append(Line(line, False, False))
            paragraph, index = False, index + 1
            continue
        inner = [column for column in items if column <= width]     # the items this line is in, by its indent
        base = inner[-1] if inner else 0
        rest = _expand(line)[width:] if width - base < 4 else None   # a block's start, or None: code or text
        # As indented as the innermost item's content, the line is in the paragraph's own container: it may continue
        # the paragraph, which only some list items interrupt, or underline it.
        own = paragraph and not quoted and len(inner) == len(items)
        if own and rest is not None and UNDERLINE.fullmatch(rest):
            read.append(Line(line, False, bool(items)))
            paragraph, index = False, index + 1
            continue
        content = None if rest is None else _item(rest, interrupts=own)
        quote = rest is not None and rest.startswith(QUOTE)
        heading = rest is not None and HEADING_TEXT.fullmatch(rest) is not None   # in an item, indented past three
        opens = rest is not None and (_opens(rest) is not None or BREAK.fullmatch(rest) is not None or quote or
                                      heading or content is not None)
        if len(inner) < len(items) and (opens or not paragraph):
            del items[len(inner):]                                  # the items it is not indented into end
        # Otherwise, not indented into them, a line that opens nothing continues the paragraph lazily: still in them.
        if rest is None or not opens:
            read.append(Line(line, False, bool(items)))
            quoted = quoted and paragraph                           # a block quote's paragraph, continued lazily
            paragraph, index = paragraph or rest is not None, index + 1   # indented code is no paragraph
            continue
        quoted = quote
        if quote:
            read.append(Line(line, False, bool(items)))
            paragraph, index = _paragraph(rest), index + 1          # a heading, code, a break quoted: none
            continue
        if heading or BREAK.fullmatch(rest):
            read.append(Line(line, False, bool(items)))
            paragraph, index = False, index + 1
            continue
        nested = bool(items)
        if content is not None:
            column, text_after = width + content, rest[content:]
            items.append(column)
            block = None if _blank(text_after) else _opens(text_after)
            read.append(Line(line, False, nested, True))
            if block is None:
                # Its first line's text is a paragraph's — unless five spaces after the marker make it code.
                paragraph, index = not _blank(text_after) and _indent(text_after) < 4, index + 1
                empty = _blank(text_after)
                continue
            index = _block(raw, index, block, column, read, closers, first_line_read=True)
            paragraph = False
            continue
        index = _block(raw, index, _opens(rest), base, read, closers)
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
           closers: List[Dict[str, int]], first_line_read: bool = False) -> int:
    """The fenced block or HTML comment ``block`` opened at ``raw[index]``, in a container whose content is indented
    ``base`` columns — 0 at the top level: its lines appended to ``read``, as code; the index of the line after it.
    ``first_line_read``: the opening line is already in ``read`` — a list item's, which holds the block.
    ``closers``: ``_closers(raw)`` — a top-level block that no line below closes is known so without reading them, and
    the file is read once, however many such blocks it holds."""
    kind, fence = block
    opening = _expand(raw[index])
    start = opening.find(COMMENT) if kind == "comment" else -1
    end = None
    if kind == "comment" and COMMENT_END in opening[start + 2:]:
        end = index + 1                                  # `<!-- … -->` on one line, `<!-->` too
    key = fence[:1]
    later = index + 1
    if end is None and not base and closers[later].get(key, 0) < max(len(fence), 1):
        later = len(raw)                                 # no line below closes it
    while end is None and later < len(raw):
        line = raw[later]
        width = _indent(line)
        if base and not _blank(line) and width < base:
            end = later                                  # the item ends, and its block: this line is read
            break
        if kind == "comment" and COMMENT_END in line:
            end = later + 1
        elif kind == "fence" and not _blank(line) and width - base < 4 and _closes(_expand(line)[width:], fence):
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


def _closers(raw: List[str]) -> List[Dict[str, int]]:
    """For each index of ``raw``, and one past its end, what the lines from there to the end close at the top level:
    by fence character, the longest closing fence — and ``""``, 1 when one holds ``-->``."""
    found: List[Dict[str, int]] = [{}]
    for line in reversed(raw):
        here = dict(found[-1])
        width = _indent(line)
        fence = FENCE.fullmatch(_expand(line)[width:].rstrip(" \t")) if width < 4 else None
        if fence is not None and _blank(fence.group("info")):
            key = fence.group("fence")[0]
            here[key] = max(here.get(key, 0), len(fence.group("fence")))
        if COMMENT_END in line:
            here[""] = 1
        found.append(here)
    found.reverse()
    return found


def _paragraph(rest: str) -> bool:
    """Whether a block quote's line, ``rest`` past its indentation — its tabs expanded — holds a paragraph's text,
    which a less indented line would continue, and not a heading, a fenced block or a comment opened, a thematic
    break, indented code, or nothing: its content as a list item's or a nested quote's too. Read once, from left to
    right, however many quotes and items it nests: a line of a thousand ``> `` is no deeper than one."""
    at = len(QUOTE) + (1 if rest.startswith(" ", len(QUOTE)) else 0)
    breaks: Dict[str, int] = {}                     # by a break's character, where the line's run of it begins
    while True:
        start = at
        while at < len(rest) and rest[at] == " ":
            at += 1
        if at >= len(rest) or at - start >= 4:
            return False                            # nothing — an empty item's past it — or indented code
        char = rest[at]
        if char == QUOTE:                           # a nested quote
            at += len(QUOTE) + (1 if rest.startswith(" ", at + len(QUOTE)) else 0)
            continue
        if char == "#" and HEADING_TEXT.fullmatch(rest, at):
            return False
        if char in "-*_":
            # A thematic break holds that character and spaces only, to the end of the line — three of it at least.
            if char not in breaks:
                breaks[char] = len(rest.rstrip(char + " "))
            if at >= breaks[char] and rest.count(char, at) >= 3:
                return False
        if char in "`~<" and _opens(rest[at:]) is not None:
            return False
        item = _item(rest, at=at)
        if item is None:
            return True
        at += item                                  # the item's content, as its own container's


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
    found = FENCE.fullmatch(rest.rstrip(" \t"))
    return found is not None and found.group("fence")[0] == fence[0] and len(found.group("fence")) >= len(fence) \
        and _blank(found.group("info"))


def _item(rest: str, interrupts: bool = False, at: int = 0) -> Optional[int]:
    """The columns from a list item's marker to its content when ``rest`` from ``at`` — a line past its indentation,
    its tabs expanded, no thematic break — starts one; ``None`` otherwise. Five spaces or more after the marker make the content start
    one past it: code. ``interrupts``: the line would otherwise continue a paragraph, which neither an empty item nor
    an ordered one that does not start at 1 interrupts."""
    found = ITEM.match(rest, at)
    if found is None:
        return None
    # Past the spaces after its marker — the line's tabs expanded — its text is blank only when it is empty.
    marker, gap, text = found.group("marker"), len(found.group("gap")), found.end()   # text: where it starts
    if interrupts and (text == len(rest) or (marker[-1] in ".)" and int(marker[:-1]) != 1)):
        return None
    if text == len(rest):
        return len(marker) + 1                           # an empty item, or one of spaces only
    if gap == 0:
        return None                                      # `-x`, `1.x`: no item
    return len(marker) + (1 if gap > 4 else gap)


def _blank(line: str) -> bool:
    """Whether ``line`` is blank: spaces and tabs only — no other white space."""
    return not line.strip(" \t")


def _expand(line: str) -> str:
    """``line`` with its tabs as spaces, to the next multiple of four columns: Markdown's indentation."""
    return line.expandtabs(4)


def _indent(line: str) -> int:
    wide = _expand(line)
    return len(wide) - len(wide.lstrip(" "))
