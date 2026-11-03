"""python -m aion -- the framework's command line."""

import sys

for stream in (sys.stdout, sys.stderr):
    # Line-buffer: a flow run is long and usually watched through a pipe or a
    # log, where Python block-buffers and a running step looks like a hung one.
    try:
        stream.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass

from .cli import main  # noqa: E402

sys.exit(main())
