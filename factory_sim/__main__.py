"""``python -m factory_sim <database-path> [hours] [seed]``."""

import sys

from factory_sim.simulator import main

if __name__ == "__main__":
    sys.exit(main())
