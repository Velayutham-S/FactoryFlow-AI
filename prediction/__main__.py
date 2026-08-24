"""``python -m prediction <database-path> train|predict``."""

import sys

from prediction.agent import main

if __name__ == "__main__":
    sys.exit(main())
