"""``python -m notification <database-path> [cycles]``."""

import sys

from notification.notifier import main

if __name__ == "__main__":
    sys.exit(main())
