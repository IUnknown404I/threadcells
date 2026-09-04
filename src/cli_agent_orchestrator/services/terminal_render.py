"""Bounded human rendering for raw terminal streams.

The durable pipe-pane log records what an application writes to the PTY, not
what a person finally sees.  Full-screen applications therefore leave many
intermediate repaint frames in the byte stream.  This module implements the
small VT subset used by managed providers so cursor movement, erasure and
overwriting collapse into a bounded screen plus scrollback representation.

It deliberately does not identify provider words or status phrases.  Ordinary
text such as ``Working`` is preserved; only terminal presentation semantics
can replace it.
"""

from __future__ import annotations

import re
import unicodedata
from collections import deque
from dataclasses import dataclass
from enum import IntEnum


class ParserState(IntEnum):
    NORMAL = 0
    ESCAPE = 1
    ESCAPE_INTERMEDIATE = 2
    CSI = 3
    CONTROL_STRING = 4
    CONTROL_STRING_ESCAPE = 5


_CSI_PATTERN = re.compile(r"(?:\x1b\[|\x9b)([0-9:;<=>?]*)([ -/]*)([@-~])")
_SCREEN_FINALS = frozenset("@ABCDEFGHJKLMPSTX`abcdefgrsu")
MAX_VIEWPORT_ROWS = 200
MAX_CURSOR_COLUMN = 4_096
MAX_RENDERED_CHARACTERS = 256 * 1024


def has_screen_semantics(value: str) -> bool:
    """Return whether a bounded stream contains cursor/screen mutations."""
    if "\b" in value or re.search(r"\r(?!\n)", value):
        return True
    return any(match.group(3) in _SCREEN_FINALS for match in _CSI_PATTERN.finditer(value))


def infer_viewport_rows(value: str, default: int = 24) -> int:
    """Infer the finite PTY height from addressing and scroll-region controls."""
    height = default
    for match in _CSI_PATTERN.finditer(value):
        parameters, _intermediates, final = match.groups()
        if final in {"H", "f", "d"}:
            first = parameters.lstrip("?<=>").split(";", 1)[0]
            if first.isdigit():
                height = max(height, int(first))
        elif final == "r":
            parts = parameters.lstrip("?<=>").split(";")
            if len(parts) > 1 and parts[1].isdigit():
                height = max(height, int(parts[1]))
    return max(2, min(height, MAX_VIEWPORT_ROWS))


def _params(raw: str, default: int = 1) -> list[int]:
    value = raw.lstrip("?<=>")
    if not value:
        return [default]
    result: list[int] = []
    for item in value.replace(":", ";").split(";"):
        try:
            result.append(int(item) if item else default)
        except ValueError:
            result.append(default)
    return result


def _cell_width(character: str) -> int:
    if unicodedata.combining(character):
        return 0
    return 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1


@dataclass(frozen=True)
class RenderedTerminal:
    output: str
    state_at_emit: ParserState
    final_state: ParserState
    orphan_string_terminator: bool


class _Viewport:
    def __init__(self, height: int) -> None:
        self.height = height
        self.rows: list[list[str]] = [[] for _ in range(height)]
        self.row = 0
        self.column = 0
        self.saved = (0, 0)
        self.scroll_top = 0
        self.scroll_bottom = height - 1
        self.collect = False
        self.committed: deque[str] = deque()
        self.committed_characters = 0

    @staticmethod
    def _text(row: list[str]) -> str:
        return "".join(row).rstrip(" ")

    def begin_collecting(self) -> None:
        self.collect = True
        self.committed.clear()
        self.committed_characters = 0

    def _commit(self, row: list[str]) -> None:
        if self.collect:
            text = self._text(row)
            self.committed.append(text)
            self.committed_characters += len(text) + 1
            while self.committed and self.committed_characters > MAX_RENDERED_CHARACTERS:
                removed = self.committed.popleft()
                self.committed_characters -= len(removed) + 1

    def _scroll_up(self, count: int = 1) -> None:
        for _ in range(max(0, min(count, self.scroll_bottom - self.scroll_top + 1))):
            removed = self.rows.pop(self.scroll_top)
            self.rows.insert(self.scroll_bottom, [])
            self._commit(removed)

    def _scroll_down(self, count: int = 1) -> None:
        for _ in range(max(0, min(count, self.scroll_bottom - self.scroll_top + 1))):
            self.rows.pop(self.scroll_bottom)
            self.rows.insert(self.scroll_top, [])

    def linefeed(self) -> None:
        if self.row == self.scroll_bottom:
            self._scroll_up()
        else:
            self.row = min(self.height - 1, self.row + 1)

    def write(self, character: str) -> None:
        width = _cell_width(character)
        if width == 0:
            if self.column > 0 and self.rows[self.row]:
                index = min(self.column - 1, len(self.rows[self.row]) - 1)
                self.rows[self.row][index] += character
            return
        if self.column >= MAX_CURSOR_COLUMN:
            return
        row = self.rows[self.row]
        if len(row) < self.column:
            row.extend(" " for _ in range(self.column - len(row)))
        if self.column < len(row):
            row[self.column] = character
        else:
            row.append(character)
        if width == 2:
            if self.column + 1 < len(row):
                row[self.column + 1] = ""
            else:
                row.append("")
        self.column = min(MAX_CURSOR_COLUMN, self.column + width)

    def erase_line(self, mode: int) -> None:
        row = self.rows[self.row]
        if mode == 0:
            del row[min(self.column, len(row)) :]
        elif mode == 1:
            end = min(self.column + 1, len(row))
            row[:end] = [" "] * end
        elif mode == 2:
            row.clear()

    def erase_display(self, mode: int) -> None:
        if mode == 0:
            self.erase_line(0)
            for index in range(self.row + 1, self.height):
                self.rows[index] = []
        elif mode == 1:
            for index in range(0, self.row):
                self.rows[index] = []
            self.erase_line(1)
        elif mode in {2, 3}:
            self.rows = [[] for _ in range(self.height)]

    def csi(self, raw: str, final: str) -> None:
        values = _params(raw)
        amount = max(1, values[0])
        if final == "A":
            self.row = max(self.scroll_top, self.row - amount)
        elif final in {"B", "e"}:
            self.row = min(self.scroll_bottom, self.row + amount)
        elif final in {"C", "a"}:
            self.column = min(MAX_CURSOR_COLUMN, self.column + amount)
        elif final == "D":
            self.column = max(0, self.column - amount)
        elif final == "E":
            self.row = min(self.scroll_bottom, self.row + amount)
            self.column = 0
        elif final == "F":
            self.row = max(self.scroll_top, self.row - amount)
            self.column = 0
        elif final in {"G", "`"}:
            self.column = min(MAX_CURSOR_COLUMN, amount - 1)
        elif final in {"H", "f"}:
            row = values[0] if values else 1
            column = values[1] if len(values) > 1 else 1
            self.row = max(0, min(self.height - 1, row - 1))
            self.column = max(0, min(MAX_CURSOR_COLUMN, column - 1))
        elif final == "d":
            self.row = max(0, min(self.height - 1, amount - 1))
        elif final == "J":
            self.erase_display(_params(raw, default=0)[0])
        elif final == "K":
            self.erase_line(_params(raw, default=0)[0])
        elif final == "S":
            self._scroll_up(amount)
        elif final == "T":
            self._scroll_down(amount)
        elif final == "L" and self.scroll_top <= self.row <= self.scroll_bottom:
            for _ in range(min(amount, self.scroll_bottom - self.row + 1)):
                self.rows.pop(self.scroll_bottom)
                self.rows.insert(self.row, [])
        elif final == "M" and self.scroll_top <= self.row <= self.scroll_bottom:
            for _ in range(min(amount, self.scroll_bottom - self.row + 1)):
                self._commit(self.rows.pop(self.row))
                self.rows.insert(self.scroll_bottom, [])
        elif final == "P":
            row_buffer = self.rows[self.row]
            del row_buffer[self.column : self.column + amount]
        elif final == "@":
            row_buffer = self.rows[self.row]
            row_buffer[self.column : self.column] = [" "] * amount
        elif final == "X":
            row_buffer = self.rows[self.row]
            end = min(len(row_buffer), self.column + amount)
            row_buffer[self.column : end] = [" "] * max(0, end - self.column)
        elif final == "r":
            top = (values[0] if values else 1) - 1
            bottom = (values[1] if len(values) > 1 else self.height) - 1
            if 0 <= top < bottom < self.height:
                self.scroll_top, self.scroll_bottom = top, bottom
            else:
                self.scroll_top, self.scroll_bottom = 0, self.height - 1
            self.row, self.column = 0, 0
        elif final == "s":
            self.saved = (self.row, self.column)
        elif final == "u":
            self.row, self.column = self.saved

    def result(self, include_screen: bool) -> str:
        lines = list(self.committed)
        if include_screen:
            screen = [self._text(row) for row in self.rows]
            while screen and not screen[0]:
                screen.pop(0)
            while screen and not screen[-1]:
                screen.pop()
            lines.extend(screen)
        if not lines:
            return ""
        result = "\n".join(lines) + ("\n" if self.committed and not include_screen else "")
        return result[-MAX_RENDERED_CHARACTERS:]


def render_terminal_stream(
    value: str,
    *,
    emit_from: int = 0,
    include_screen: bool = True,
    initial_state: ParserState = ParserState.NORMAL,
) -> RenderedTerminal:
    """Apply bounded terminal semantics and emit only the requested page window."""
    if not 0 <= emit_from <= len(value):
        raise ValueError("emit_from is outside the terminal-render input")
    viewport = _Viewport(infer_viewport_rows(value))
    state = initial_state
    state_at_emit: ParserState | None = None
    orphan = False
    csi = ""
    index = 0

    while index < len(value):
        if index == emit_from:
            state_at_emit = state
            viewport.begin_collecting()
        character = value[index]
        codepoint = ord(character)

        if state == ParserState.CONTROL_STRING:
            if character in {"\n", "\r"}:
                state = ParserState.NORMAL
                continue
            if character in {"\x07", "\x9c"}:
                state = ParserState.NORMAL
                index += 1
                continue
            if character == "\x1b":
                state = ParserState.CONTROL_STRING_ESCAPE
                index += 1
                continue
            index += 1
            continue
        if state == ParserState.CONTROL_STRING_ESCAPE:
            if character == "\\":
                state = ParserState.NORMAL
                index += 1
            else:
                state = ParserState.CONTROL_STRING
            continue
        if state == ParserState.CSI:
            if 0x40 <= codepoint <= 0x7E:
                viewport.csi(csi, character)
                csi = ""
                state = ParserState.NORMAL
                index += 1
                continue
            if 0x20 <= codepoint <= 0x3F or codepoint < 0x20 or codepoint == 0x7F:
                csi += character
                index += 1
                continue
            csi = ""
            state = ParserState.NORMAL
            continue
        if state == ParserState.ESCAPE_INTERMEDIATE:
            if 0x20 <= codepoint <= 0x2F:
                index += 1
                continue
            state = ParserState.NORMAL
            index += 1 if 0x30 <= codepoint <= 0x7E else 0
            continue
        if state == ParserState.ESCAPE:
            if character == "[":
                csi = ""
                state = ParserState.CSI
                index += 1
                continue
            if character in "]PX^_":
                state = ParserState.CONTROL_STRING
                index += 1
                continue
            if character == "\\":
                orphan = True
                state = ParserState.NORMAL
                index += 1
                continue
            if character == "7":
                viewport.saved = (viewport.row, viewport.column)
            elif character == "8":
                viewport.row, viewport.column = viewport.saved
            elif character == "D":
                viewport.linefeed()
            elif character == "E":
                viewport.column = 0
                viewport.linefeed()
            elif character == "M":
                if viewport.row == viewport.scroll_top:
                    viewport._scroll_down()
                else:
                    viewport.row = max(0, viewport.row - 1)
            if 0x20 <= codepoint <= 0x2F:
                state = ParserState.ESCAPE_INTERMEDIATE
            else:
                state = ParserState.NORMAL
            index += 1
            continue

        if character == "\x1b":
            state = ParserState.ESCAPE
            index += 1
        elif character == "\x9b":
            csi = ""
            state = ParserState.CSI
            index += 1
        elif character in {"\x90", "\x98", "\x9d", "\x9e", "\x9f"}:
            state = ParserState.CONTROL_STRING
            index += 1
        elif character in {"\x07", "\x9c"}:
            orphan = True
            index += 1
        elif character == "\r":
            viewport.column = 0
            index += 1
        elif character in {"\n", "\x0b", "\x0c"}:
            # PTY output normally carries ONLCR, but retained/synthetic logs
            # also contain bare LF. Human text treats either form as a fresh
            # line rather than preserving a terminal column indentation.
            viewport.column = 0
            viewport.linefeed()
            index += 1
        elif character == "\b":
            viewport.column = max(0, viewport.column - 1)
            index += 1
        elif character == "\t":
            viewport.write("\t")
            index += 1
        elif codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            index += 1
        else:
            viewport.write(character)
            index += 1

    if state_at_emit is None:
        state_at_emit = state
        viewport.begin_collecting()
    return RenderedTerminal(
        output=viewport.result(include_screen),
        state_at_emit=state_at_emit,
        final_state=state,
        orphan_string_terminator=orphan,
    )
