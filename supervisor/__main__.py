"""``python -m supervisor <database-path> [cycles]``."""

import sys

from supervisor.orchestrator import main

if __name__ == "__main__":
    sys.exit(main())
