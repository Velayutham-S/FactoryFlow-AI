"""``python -m decision <database-path> [max-recommendations]``."""

import sys

from decision.agent import main

if __name__ == "__main__":
    sys.exit(main())
