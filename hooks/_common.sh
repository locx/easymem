#!/usr/bin/env bash
# why: sourced not executed, with no side effects on source, so each hook
# keeps control of its own ordering and early-exit guards.

# why: macOS `date -r` and GNU `stat -c` disagree; fall through to python so
# the mtime read works on either host without the caller knowing which.
_file_mtime() {
    date -r "$1" +%s 2>/dev/null \
        || stat -c%Y "$1" 2>/dev/null \
        || python3 -c "import os,sys; print(int(os.path.getmtime(sys.argv[1])))" "$1" 2>/dev/null \
        || echo 0
}
