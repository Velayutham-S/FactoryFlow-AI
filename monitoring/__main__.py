"""``python -m monitoring <database-path> [cycles]``."""

import sys

from monitoring.agent import main

if __name__ == "__main__":
    sys.exit(main())
