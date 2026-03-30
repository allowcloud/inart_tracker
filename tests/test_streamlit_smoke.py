from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = REPO_ROOT / "app.py"


class StreamlitSmokeTest(unittest.TestCase):
    def test_project_space_renders_without_exceptions(self) -> None:
        script = textwrap.dedent(
            f"""
            from pathlib import Path
            from streamlit.testing.v1 import AppTest

            app_path = Path(r"{APP_PATH}")
            at = AppTest.from_file(str(app_path), default_timeout=30)
            at.run()
            if at.exception:
                raise SystemExit("initial run exceptions: " + " | ".join(str(x.value) for x in at.exception))

            nav_radio = None
            for r in at.radio:
                label = str(r.label or "")
                options = [str(o) for o in r.options]
                if "\u529f\u80fd\u5bfc\u822a" in label or any("\u9879\u76ee\u7a7a\u95f4" in o for o in options):
                    nav_radio = r
                    break
            if nav_radio is None:
                raise SystemExit("navigation radio not found")

            project_option = next((o for o in nav_radio.options if "\u9879\u76ee\u7a7a\u95f4" in str(o)), None)
            if project_option is None:
                raise SystemExit("Project space option not found")

            nav_radio.set_value(project_option)
            at.run()
            if at.exception:
                raise SystemExit("project space exceptions: " + " | ".join(str(x.value) for x in at.exception))

            print("PROJECT_SPACE_OK")
            """
        )

        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env={**os.environ, "INART_ALLOW_MEMORY_DB": "1"},
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
        self.assertIn("PROJECT_SPACE_OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()

