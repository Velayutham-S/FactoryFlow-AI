"""``python -m master_data <database-path> <master-data-directory>``."""

import sys

from master_data.seeder import main

if __name__ == "__main__":
    sys.exit(main())
