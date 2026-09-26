"""Manual end-to-end verification of the startup security rules.

Not part of the test suite (the suite covers the same ground); this exists so a
real process can be pointed at a real environment to confirm the failure modes
are legible to an operator.

    APP_ENV=production python -m tests.verify_startup
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

CHECK = textwrap.dedent(
    """
    from app.config import SecurityConfigurationError, settings
    print("APP_ENV =", settings.app_env)
    try:
        settings.validate_security()
    except SecurityConfigurationError as exc:
        print("REFUSED TO START:")
        print(exc)
        sys.exit(2)
    print("STARTED: configuration accepted")
    """
)


def main() -> int:
    script = f"import sys\n{CHECK}"
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
