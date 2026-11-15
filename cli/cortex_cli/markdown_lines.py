"""The lines of a changelog, as Markdown — CommonMark — reads them: which hold no Markdown, which are in a list item.

One rule for every reader of ``CHANGELOG.md``: the version a build of no release prepares (``cli/cortex_cli``'s
``devversion``) and the release actions (``.github/actions/lib``'s ``changelog``) read the same sections. This file
is kept byte for byte the same in both directories — the actions' tests check it — and imports the standard library
only, as the actions do.

What is read, as CommonMark has it:

- a line ends at a line feed, or a carriage return alone — not at the other breaks Python splits text at, which an
  entry may hold; a byte order mark is no part of the text;
- a fenced code block — three backticks or tildes, or more, indented up to three spaces from its container — an HTML
  comment, from ``<!--`` to the line that holds ``-->``, and an HTML block — from ``<pre``, ``<script``, ``<style`` or
  ``<textarea``, in any case, to the line that holds the end tag of any of them; from a block element's tag,
  ``<div>``, ``</details>``, to a blank line — hold no Markdown: their lines are ``code``. Opened in a list item, each
  ends with it — at the first line not blank indented less than the item's content — and holds what the item holds to
  the end of the file. One at the top level never closed hides nothing, where Markdown would hide the rest of the
  file: a stray fence never loses the changelog's sections;
- a list item — ``-``, ``*``, ``+``, or ``1.``, ``1)`` — holds the lines indented as far as its content, its first
  line's text after the marker, and the lines of a paragraph that continue it, lazily, indented less. A line in a
  list item is ``nested``; an item's own line is when it starts in another item. An item whose first line holds
  only its marker ends at a blank line below it. Past its marker, its first line is its content's first: an item
  nested on it — ``- * x`` — is one more item, and a heading, a thematic break, a quote, a block or code there is no
  paragraph of it. A line holds a ``paragraph``'s text, or none — an item's line, past its marker: its own;
- a heading — one to six ``#``, indented up to three spaces — ends every list: a changelog's headings are its own,
  never an item's, even indented as far as one's content. Indented further, as far as an item's content, or on an
  item's line, past its marker, it is the item's: no paragraph, which the line below would continue;
- a paragraph is interrupted by a list item, but not by an empty one nor an ordered one that does not start at 1, and
  not by a line indented as code: such a line continues it. Indented code — four spaces past its container's
  content — is no paragraph, which a less indented line would continue;
- a line of ``=`` or ``-`` under a paragraph, in its container, makes it a heading — never a list item;
- a block quote's line, ``>``, is text: it ends the lists it is not indented into — no list item's line continues
  in it lazily — and holds no entry nor section. The line below continues it lazily only when it holds a paragraph's
  text — not a heading, a fence, an HTML block, a thematic break nor indented code, quoted. Each of its lines is read
  with what the ones above left open in it — quotes, items, a paragraph, a fenced block, an HTML block: a quoted line
  indented as code goes on with a paragraph, a line of ``=`` under it makes it a heading, and a fence holds the
  quoted lines below it up to its end or the end of its container.

Not read: the other HTML blocks — ``<?``, ``<!X``, ``<![CDATA[``, and a line that holds only another tag,
``<span>`` — whose lines are read as Markdown: a section or an entry in one is read. A blank line holds spaces and tabs
only.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from typing import Dict, List, NamedTuple, Optional, Tuple

# Matched after the line's indentation, relative to its container.
FENCE = re.compile(r"(?P<fence>`{3,}|~{3,})(?P<info>.*)")
ITEM = re.compile(r"(?P<marker>[-*+]|[0-9]{1,9}[.)])(?P<gap> *)")     # its text: what follows
BREAK = re.compile(r"([-*_])(?: *\1){2,} *")              # a thematic break: `- - -` is no list item
HEADING = re.compile(r" {0,3}#{1,6}(?:[ \t].*)?")
HEADING_TEXT = re.compile(r"#{1,6}(?:[ \t].*)?")          # past its indentation: a heading in a list item
COMMENT, COMMENT_END = "<!--", "-->"
# HTML blocks, as CommonMark 0.31.2 starts them: what `<pre>`, `<script>`, `<style>` or `<textarea>` opens ends at the
# line that holds the end tag of any of them; a block element's tag, `<div>`, `</details>`, opens what a blank line ends.
RAW = re.compile(r"<(?:pre|script|style|textarea)(?:\s|>|$)", re.IGNORECASE)
RAW_END = re.compile(r"</(?:pre|script|style|textarea)>", re.IGNORECASE)
HTML = re.compile(r"</?(?:address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|"
                  r"dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|header|hr|"
                  r"html|iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|search|"
                  r"section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul)(?:\s|/?>|$)", re.IGNORECASE)
# A second-level section: `## [1.7.0] - 2026-11-06 — …`, `## [Unreleased]` — indented up to three spaces, a space or
# a tab after its ##.
SECTION = re.compile(r" {0,3}##[ \t]+\[(?P<label>[^\]]+)\].*")
QUOTE = ">"
QUOTE_MARK = re.compile(r" {0,3}> ?")                     # a quote's mark, and one space after it: its own
SPACES = re.compile(r" *")
UNDERLINE = re.compile(r"(?:=+|-+)[ \t]*")                # under a paragraph, it makes the paragraph a heading
PARAGRAPH = ("paragraph", "")                             # what a quote's line leaves open: its paragraph


class Line(NamedTuple):
    text: str               # the line, as written, without its line ending
    code: bool              # in a fenced code block or an HTML block, its fence or delimiters included
    nested: bool            # in a list item — a list item's own line: in another one
    item: bool = False      # the line starts a list item
    paragraph: bool = False  # it holds a paragraph's text — an item's line: its own, past its marker; not indented code


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
    # The block quote the line before is in: how many items it is in, and what is open in it — ``_quoted``'s.
    quote: Optional[Tuple[int, List[List[int]], Optional[Tuple[str, str]]]] = None
    closers = _closers(raw)         # what each line and those below it close, for a block opened at the top level
    index = 0
    while index < len(raw):
        line, held, quote = raw[index], quote, None             # a quote goes on only on a line it holds
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
        inner = bisect_right(items, width)                          # how many items this line is in, by its indent
        base = items[inner - 1] if inner else 0
        rest = _expand(line)[width:] if width - base < 4 else None   # a block's start, or None: code or text
        # As indented as the innermost item's content, the line is in the paragraph's own container: it may continue
        # the paragraph, which only some list items interrupt, or underline it.
        own = paragraph and not quoted and inner == len(items)
        if own and rest is not None and UNDERLINE.fullmatch(rest):
            read.append(Line(line, False, bool(items)))
            paragraph, index = False, index + 1
            continue
        content = None if rest is None else _item(rest, interrupts=own)
        quoting = rest is not None and rest.startswith(QUOTE)
        heading = rest is not None and HEADING_TEXT.fullmatch(rest) is not None   # in an item, indented past three
        opens = rest is not None and (_opens(rest) is not None or BREAK.fullmatch(rest) is not None or quoting or
                                      heading or content is not None)
        if inner < len(items) and (opens or not paragraph):
            del items[inner:]                                  # the items it is not indented into end
        # Otherwise, not indented into them, a line that opens nothing continues the paragraph lazily: still in them.
        if rest is None or not opens:
            quoted = quoted and paragraph                           # a block quote's paragraph, continued lazily
            quote = held if quoted else None
            paragraph, index = paragraph or rest is not None, index + 1   # indented code is no paragraph
            read.append(Line(line, False, bool(items), paragraph=paragraph))
            continue
        quoted = quoting
        if quoting:
            # The quote of the line above goes on, in the same items: what it left open is read on.
            path, leaf = held[1:] if held is not None and held[0] == len(items) else ([], None)
            leaf = _quoted(rest, path, leaf)
            read.append(Line(line, False, bool(items)))
            quote, paragraph, index = (len(items), path, leaf), leaf is PARAGRAPH, index + 1
            continue
        if heading or BREAK.fullmatch(rest):
            read.append(Line(line, False, bool(items)))
            paragraph, index = False, index + 1
            continue
        nested = bool(items)
        if content is not None:
            read.append(Line(line, False, nested, True))
            at, first, breaks = content, True, {}
            # Its text, past the marker, is read as its own container's first line: a block, an item nested in it — on
            # the same line, `- * x` — or a paragraph's text. Its own text is a paragraph's only if it is none of them.
            # Read once, from left to right, however many items it nests.
            while True:
                items.append(width + at)
                lead = SPACES.match(rest, at).end() - at
                char = rest[at + lead:at + lead + 1]
                block = _opens(rest[at:]) if char and char in "`~<" else None
                deeper, text = None, False
                if not char or lead >= 4 or block is not None:
                    paragraph = False                               # empty, code — five spaces after it — or a block
                elif char == "#" and HEADING_TEXT.fullmatch(rest, at) or _break(rest, at, breaks):
                    paragraph = False                               # a heading, a thematic break: no paragraph
                elif char == QUOTE:
                    path = []
                    leaf = _quoted(rest[at:], path, None)
                    quote, quoted, paragraph = (len(items), path, leaf), True, leaf is PARAGRAPH
                else:
                    deeper = _item(rest, at=at)
                    paragraph = text = deeper is None
                if deeper is None:
                    break
                at, first = at + deeper, False
            read[-1] = read[-1]._replace(paragraph=first and text)
            if block is None:
                index, empty = index + 1, not char
                continue
            index = _block(raw, index, block, items[-1], read, closers, first_line_read=True)
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
    """The fenced block or HTML block ``block`` opened at ``raw[index]``, in a container whose content is indented
    ``base`` columns — 0 at the top level: its lines appended to ``read``, as code; the index of the line after it.
    ``first_line_read``: the opening line is already in ``read`` — a list item's, which holds the block.
    ``closers``: ``_closers(raw)`` — a top-level block that no line below closes is known so without reading them, and
    the file is read once, however many such blocks it holds."""
    kind, fence = block
    opening = _expand(raw[index])
    start = opening.find(COMMENT) if kind == "comment" else -1
    end = None
    if kind == "comment" and COMMENT_END in opening[start + 2:] or kind == "raw" and RAW_END.search(opening):
        end = index + 1                                  # `<!-- … -->` on one line, `<!-->` too, `<pre>…</pre>`
    key = fence[:1]
    later = index + 1
    if end is None and not base and kind != "html" and closers[later].get(key, 0) < max(len(fence), 1):
        later = len(raw)                                 # no line below closes it
    while end is None and later < len(raw):
        line = raw[later]
        width = _indent(line)
        if base and not _blank(line) and width < base:
            end = later                                  # the item ends, and its block: this line is read
            break
        if kind == "comment" and COMMENT_END in line or kind == "raw" and RAW_END.search(line):
            end = later + 1
        elif kind == "html" and _blank(line):
            end = later                                  # a blank line ends it, and is none of it
        elif kind == "fence" and not _blank(line) and width - base < 4 and _closes(_expand(line)[width:], fence):
            end = later + 1
        later += 1
    if end is None:
        if not base and kind != "html":                  # at the top level, never closed: it hides nothing
            if not first_line_read:
                read.append(Line(raw[index], False, False, paragraph=True))
            return index + 1
        end = len(raw)                                   # in an item, to the end of the file
    if first_line_read:
        index += 1
    read.extend(Line(line, True, bool(base)) for line in raw[index:end])
    return end


def _closers(raw: List[str]) -> List[Dict[str, int]]:
    """For each index of ``raw``, and one past its end, what the lines from there to the end close at the top level:
    by fence character, the longest closing fence — ``""``, 1 when one holds ``-->``, and ``"<"``, 1 when one holds the
    end tag of ``<pre>`` or its kin."""
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
        if RAW_END.search(line):
            here["<"] = 1
        found.append(here)
    found.reverse()
    return found


def _quoted(rest: str, path: List[List[int]], leaf: Optional[Tuple[str, str]]) -> Optional[Tuple[str, str]]:
    """What a block quote's line, ``rest`` past its indentation — its tabs expanded — leaves open in the quote: a
    paragraph, ``PARAGRAPH``, which the line below may continue, a fenced block or a comment, as ``_opens`` names it,
    or nothing. ``path`` and ``leaf``: what the quote's lines above left open — ``[]`` and ``None`` on its first line;
    ``path`` is brought up to this line's, in place: the quotes open, the outermost first, each as the content columns
    of the items open in it, from the quote's content. Read once, from left to right, however many quotes and items
    it nests: a line of a thousand ``> `` is no deeper than one."""
    at = start = quotes = kept = 0                  # kept: the items matched in the last quote matched
    while quotes < len(path):                       # what is open, in order: a quote by its mark, an item by its indent
        mark = QUOTE_MARK.match(rest, at)
        if mark is None:
            break
        at = start = mark.end()
        run, quotes, kept = path[quotes], quotes + 1, 0
        if run:
            lead = SPACES.match(rest, at).end() - at
            kept = len(run) if at + lead == len(rest) else bisect_right(run, lead)   # a blank line is in every item
            at += run[kept - 1] if kept else 0
            if kept < len(run):
                break
    matched = quotes == len(path) and (not path or kept == len(path[-1]))
    if leaf is not None and leaf is not PARAGRAPH:
        if matched:                                 # in its container still: a block's line, or its end
            return None if _ends(leaf, rest, at) else leaf
        leaf = None                                 # its container ends, and the block with it
    paragraph, breaks = leaf is PARAGRAPH, {}
    lead = SPACES.match(rest, at).end() - at
    if paragraph and (lead >= 4 or at + lead < len(rest) and _text(rest, at + lead, matched, breaks)):
        return PARAGRAPH                            # it goes on — lazily, its containers open, when not matched
    del path[quotes:]                               # what the line is not in ends
    if path:
        del path[-1][kept:]
    if paragraph and matched and UNDERLINE.fullmatch(rest, at + lead):
        return None                                 # a line of = or - under it, in its container: a heading
    while True:
        lead = SPACES.match(rest, at).end() - at
        here = at + lead
        char = rest[here:here + 1]
        if not char or lead >= 4:
            return None                             # nothing — an empty item's past it — or indented code
        if char == QUOTE:                           # a quote, in the one it is in
            path.append([])
            at = start = QUOTE_MARK.match(rest, here).end()
            continue
        if char == "#" and HEADING_TEXT.fullmatch(rest, here) or _break(rest, here, breaks):
            return None
        block = _opens(rest[here:]) if char in "`~<" else None
        if block is not None:                       # closed on its line, or open
            return None if block[0] != "fence" and _ends(block, rest, here + 2 * (block[0] == "comment")) else block
        item = _item(rest, at=here)
        if item is None:
            return PARAGRAPH
        at = min(here + item, len(rest))            # the item's content, as its own container's
        path[-1].append(at - start)


def _text(rest: str, at: int, own: bool, breaks: Dict[str, int]) -> bool:
    """Whether ``rest`` from ``at`` — past its indentation, not blank, under a paragraph — goes on with it: no block it
    could be interrupted by starts there, nor, ``own`` — in the paragraph's own container — a line of ``=`` or ``-``
    that makes it a heading."""
    char = rest[at]
    if char == QUOTE or char == "#" and HEADING_TEXT.fullmatch(rest, at) or _break(rest, at, breaks):
        return False
    if own and UNDERLINE.fullmatch(rest, at) or char in "`~<" and _opens(rest[at:]) is not None:
        return False
    return _item(rest, interrupts=own, at=at) is None


def _ends(block: Tuple[str, str], rest: str, at: int) -> bool:
    """Whether a quote's line, ``rest`` from ``at`` — in the container of the fenced block or the HTML block ``block`` —
    ends it: closes the fence, holds ``-->`` or the end tag of ``<pre>`` and its kin, or, a block element's, is blank."""
    kind = block[0]
    if kind == "fence":
        lead = SPACES.match(rest, at).end() - at
        return lead < 4 and _closes(rest[at + lead:], block[1])
    if kind == "html":
        return _blank(rest[at:])
    return COMMENT_END in rest[at:] if kind == "comment" else RAW_END.search(rest, at) is not None


def _break(rest: str, at: int, breaks: Dict[str, int]) -> bool:
    """Whether ``rest`` — a line past its indentation, its tabs expanded — is a thematic break from ``at``: its
    character, and that character and spaces only to the end of the line — three of it at least. ``breaks``: by a
    break's character, where the line's run of it and spaces begins, measured once a line."""
    char = rest[at:at + 1]
    if not char or char not in "-*_":
        return False
    if char not in breaks:
        breaks[char] = len(rest.rstrip(char + " "))
    return at >= breaks[char] and rest.count(char, at) >= 3


def _opens(rest: str) -> Optional[Tuple[str, str]]:
    """``("fence", fence)``, ``("comment", "")``, ``("raw", "<")`` — ``<pre>`` and its kin — or ``("html", "")`` — a
    block element's tag — when ``rest`` — a line past its indentation — opens a fenced code block, an HTML comment or
    another HTML block; ``None`` otherwise. Backticks in a backtick fence's info string make it none."""
    found = FENCE.fullmatch(rest.rstrip())
    if found and not (found.group("fence")[0] == "`" and "`" in found.group("info")):
        return "fence", found.group("fence")
    if rest.startswith(COMMENT):
        return "comment", ""
    if RAW.match(rest):
        return "raw", "<"
    if HTML.match(rest):
        return "html", ""
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
