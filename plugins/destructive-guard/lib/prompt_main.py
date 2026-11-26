"""Entry stub: checks the interpreter version, then hands over to the destructive_guard package.

Kept parsable by any Python >= 3.7 so an old interpreter prints the message below
instead of a SyntaxError. `python -I` leaves the script directory off sys.path, so
the package directory is added here.
"""

import os
import sys

if sys.version_info < (3, 14):
    sys.stderr.write(
        "needs Python >= 3.14 (found %d.%d at %s); set GATE_INFRA_PYTHON\n"
        % (sys.version_info[0], sys.version_info[1], sys.executable)
    )
    sys.exit(3)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from destructive_guard.prompt_gate import main

sys.exit(main())
