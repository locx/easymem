#!/usr/bin/env python3
"""Strip the `## EasyMem (Easy Memory)` section from a CLAUDE.md.

Usage: _strip_memory_section.py <claude_md_path>

Deletes from the heading to the next `## ` (or EOF). Atomic rewrite.
"""
import os
import re
import sys


def strip(path: str) -> bool:
    with open(path, encoding="utf-8") as f:
        content = f.read()
    m = re.search(r"^## EasyMem \(Easy Memory\)", content, re.MULTILINE)
    if not m:
        return False
    start = m.start()
    end = content.find("\n## ", start + len(m.group(0)))
    old_section = content[start:] if end < 0 else content[start:end]
    new_content = content.replace(old_section, "").rstrip() + "\n"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: _strip_memory_section.py <claude_md>", file=sys.stderr)
        sys.exit(2)
    strip(sys.argv[1])
