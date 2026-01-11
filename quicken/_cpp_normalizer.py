"""
C++ source code normalization for content-aware hashing.

Normalizes C++ source files by removing unnecessary whitespace and comment content
while preserving semantic meaning, enabling cache hits on formatting changes.
"""

import hashlib
from pathlib import Path


def _skip_until(f, line, i, end, allow_escape=False):
    """Skip until `end` is found, across multiple lines if needed.
    Args:    f: File object for reading next lines
             line: Current line being processed
             i: Current index in line
             end: String to search for
             allow_escape: If True, backslash escapes the next character
    Returns: Tuple of (content_or_newline_count, updated line, index after end)
             - For string/char literals: returns (content_string, line, index)
             - For block comments: returns (newline_count, line, index)"""
    end_len = len(end)
    # For block comments, only track newlines; for strings/chars, collect content
    content = [] if allow_escape or end != '*/' else None
    newline_count = 0

    while True:
        while i < len(line):
            c = line[i]

            # Handle escapes in strings/char literals
            if allow_escape and c == '\\':
                if i + 1 < len(line):
                    content.append(line[i])
                    content.append(line[i+1])
                    i += 2
                else:
                    content.append(c)
                    i += 1
                continue

            # Check for end sequence
            if line[i:i+end_len] == end:
                if content is not None:
                    return ''.join(content), line, i + end_len
                return newline_count, line, i + end_len

            # Track newlines for comments, collect content for strings
            if content is None and c == '\n':
                newline_count += 1
            elif content is not None:
                content.append(c)

            i += 1

        # Move to next line
        line = next(f, "")
        if not line:
            if content is not None:
                return ''.join(content), line, 0
            return newline_count, line, 0
        i = 0


def _is_identifier_char(c: str) -> bool:
    """Check if character is part of an identifier (alphanumeric or underscore)."""
    return c.isalnum() or c == '_'


def hash_cpp_source(path: Path) -> str:
    """Calculate hash of C++ source with specific whitespace normalization rules.

    Whitespace handling:
    1. Preprocessor directives (lines starting with # after optional whitespace):
       - Preserve all internal spaces unchanged (to avoid preprocessing surprises)
       - Remove only trailing whitespace
    2. Regular code lines:
       - Remove all leading whitespace (indentation)
       - Normalize internal spaces (remove unnecessary ones, keep between identifiers)
       - Remove all trailing whitespace

    Produces same hash when:
    - Indentation is changed
    - Unnecessary spaces are changed (e.g., 'if (x)' vs 'if(x)')
    - Comment content is changed (but not added/removed lines)

    Still detects:
    - Line additions/removals (newlines are preserved)
    - Code changes
    - String literal changes (content is preserved exactly)
    - Preprocessor directive changes

    Args:    path: Path to C++ source file
    Returns: 16-character hex string (64-bit BLAKE2b hash)"""
    h = hashlib.blake2b(digest_size=8)  # Match existing 64-bit hash size

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()

            # Leave preprocessor directives unchanged except for leading and trailing whitespace
            if line.startswith('#'):
                h.update(line.encode("utf-8"))
                h.update(b"\n")
                continue

            # Process character by character with space normalization
            i = 0
            out = []

            while i < len(line):
                c = line[i]

                # Block comment /* ... */
                if line[i:i+2] == "/*":
                    out.append("/*")
                    newline_count, line, i = _skip_until(f, line, i + 2, "*/")
                    # Preserve newlines in comments to detect line count changes
                    for _ in range(newline_count):
                        out.append('\n')
                    out.append("*/")
                    continue

                # Line comment //
                if line[i:i+2] == "//":
                    out.append("//")
                    break

                # String literal - preserve content exactly
                if c == '"':
                    out.append('"')
                    content, line, i = _skip_until(f, line, i + 1, '"', allow_escape=True)
                    out.append(content)
                    out.append('"')
                    continue

                # Character literal - preserve content exactly
                if c == "'":
                    out.append("'")
                    content, line, i = _skip_until(f, line, i + 1, "'", allow_escape=True)
                    out.append(content)
                    out.append("'")
                    continue

                # Handle spaces/tabs: only keep if between two identifier characters
                if c == " " or c == "\t":
                    # Skip all consecutive spaces/tabs
                    while i < len(line) and (line[i] == " " or line[i] == "\t"):
                        i += 1

                    # Check if we need to keep a space
                    # Need space if: prev char is identifier char AND next char is identifier char
                    prev_char = out[-1] if out else ''
                    next_char = line[i] if i < len(line) else ''

                    if (_is_identifier_char(prev_char) and _is_identifier_char(next_char)):
                        out.append(" ")
                    # else: discard the space(s)
                    continue

                # Normal character
                out.append(c)
                i += 1

            # Remove trailing whitespace from output
            line_out = "".join(out).rstrip()
            h.update(line_out.encode("utf-8"))
            h.update(b"\n")

    return h.hexdigest()
