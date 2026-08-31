from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from typing import Iterator
from typing import TextIO


def duplicate_protocol_stdout() -> TextIO:
    """Return a line-buffered duplicate of stdout's original file description."""
    descriptor = os.dup(1)
    return os.fdopen(descriptor, "w", encoding="utf-8", buffering=1)


@contextmanager
def silence_process_stdio() -> Iterator[None]:
    """Suppress Python, native, and inherited child output on fd 1 and fd 2."""
    sys.stdout.flush()
    sys.stderr.flush()
    saved_stdout = os.dup(1)
    saved_stderr = os.dup(2)
    null_descriptor = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null_descriptor, 1)
        os.dup2(null_descriptor, 2)
        yield
    finally:
        try:
            # Flush while fd 1/2 still point at /dev/null, including when the
            # import or engine raised, so buffered third-party text cannot be
            # emitted after restoration.
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            os.dup2(saved_stdout, 1)
            os.dup2(saved_stderr, 2)
            os.close(saved_stdout)
            os.close(saved_stderr)
            os.close(null_descriptor)
