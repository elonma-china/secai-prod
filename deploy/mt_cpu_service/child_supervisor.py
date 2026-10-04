"""Race-safe Linux parent-death coupling before exec of the pinned model binary.

The parent passes its flock descriptor with Popen(pass_fds=...). The descriptor
stays open after this exec, so SIGKILL of the parent cannot free the reservation
while this process (or its exec'd model) is still alive.
"""
from __future__ import annotations

import ctypes
import os
import signal
import sys


def main() -> int:
    if len(sys.argv) < 4:
        return 2
    try:
        parent_pid = int(sys.argv[1])
    except ValueError:
        return 2
    if os.getppid() != parent_pid:
        return 2
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
            return 2
    except (AttributeError, OSError):
        return 2
    # Parent can die between fork and prctl. The second check closes that race.
    if os.getppid() != parent_pid:
        return 2
    os.execv(sys.argv[2], sys.argv[2:])
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
