from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = REPO_ROOT / "app.py"


class StreamlitSmokeTest(unittest.TestCase):
    def test_pm_workspace_renders_without_exceptions(self) -> None:
        script = textwrap.dedent(
            f"""
            from pathlib import Path
            from streamlit.testing.v1 import AppTest

            app_path = Path(r"{APP_PATH}")
            at = AppTest.from_file(str(app_path), default_timeout=30)
            at.run()
            if at.exception:
                raise SystemExit("initial run exceptions: " + " | ".join(str(x.value) for x in at.exception))

            at.radio[0].set_value(at.radio[0].options[1])
            at.run()
            if at.exception:
                raise SystemExit("pm workspace exceptions: " + " | ".join(str(x.value) for x in at.exception))

            button_labels = [w.label for w in at.button]
            if "\u2795 \u6dfb\u52a0" not in button_labels:
                raise SystemExit("todo add button not found in PM workspace")

            print("PM_WORKSPACE_OK")
            """
        )

        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        self.assertEqual(
            proc.returncode,
            0,
            f"stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}",
        )
        self.assertIn("PM_WORKSPACE_OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()

