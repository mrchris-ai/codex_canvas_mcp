"""Launch this checkout without installing it or depending on the client's cwd.

Use a Python 3.11+ interpreter with the dependencies in pyproject.toml installed.
All normal canvas_mcp command-line arguments are forwarded unchanged.
"""

from pathlib import Path
import sys


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
    from canvas_mcp.server import main

    main()
