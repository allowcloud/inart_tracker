from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest
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

            nav_radio = next((r for r in at.radio if str(getattr(r, "key", "")) == "main_nav_menu"), None)
            if nav_radio is None:
                raise SystemExit("navigation radio not found")
            if len(nav_radio.options) < 3:
                raise SystemExit("navigation options incomplete")

            nav_radio.set_value(nav_radio.options[2])
            at.run()
            if at.exception:
                raise SystemExit("project space exceptions: " + " | ".join(str(x.value) for x in at.exception))

            detail_radio = next((r for r in at.radio if str(getattr(r, "key", "")).startswith("project_detail_view_")), None)
            if detail_radio is not None and len(detail_radio.options) >= 2:
                detail_radio.set_value(detail_radio.options[1])
                at.run()
                if at.exception:
                    raise SystemExit("project progress exceptions: " + " | ".join(str(x.value) for x in at.exception))

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

    def test_home_dashboard_quick_action_does_not_raise(self) -> None:
        script = textwrap.dedent(
            f"""
            from pathlib import Path
            from streamlit.testing.v1 import AppTest

            app_path = Path(r"{APP_PATH}")
            at = AppTest.from_file(str(app_path), default_timeout=30)
            at.run()
            if at.exception:
                raise SystemExit("initial run exceptions: " + " | ".join(str(x.value) for x in at.exception))

            jump_btn = next((b for b in at.button if str(getattr(b, "key", "")) == "home_jump_dashboard"), None)
            if jump_btn is None:
                raise SystemExit("home dashboard quick action not found")

            jump_btn.click()
            at.run()
            if at.exception:
                raise SystemExit("home dashboard navigation exceptions: " + " | ".join(str(x.value) for x in at.exception))

            nav_radio = next((r for r in at.radio if str(getattr(r, "key", "")) == "main_nav_menu"), None)
            if nav_radio is None:
                raise SystemExit("navigation radio missing after dashboard jump")
            if str(nav_radio.value) != str(nav_radio.options[1]):
                raise SystemExit("dashboard jump did not land on dashboard menu")

            print("HOME_DASHBOARD_JUMP_OK")
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
        self.assertIn("HOME_DASHBOARD_JUMP_OK", proc.stdout)

    def test_home_dashboard_recovers_legacy_workspace_value(self) -> None:
        script = textwrap.dedent(
            f"""
            from pathlib import Path
            from streamlit.testing.v1 import AppTest

            app_path = Path(r"{APP_PATH}")
            at = AppTest.from_file(str(app_path), default_timeout=30)
            at.session_state["home_workspace_mode"] = "今日总览"
            at.run()
            if at.exception:
                raise SystemExit("legacy home run exceptions: " + " | ".join(str(x.value) for x in at.exception))

            jump_btn = next((b for b in at.button if str(getattr(b, "key", "")) == "home_jump_dashboard"), None)
            if jump_btn is None:
                raise SystemExit("home dashboard content missing after legacy workspace value")

            print("HOME_LEGACY_WORKSPACE_OK")
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
        self.assertIn("HOME_LEGACY_WORKSPACE_OK", proc.stdout)

    def test_fastlog_renders_without_exceptions(self) -> None:
        script = textwrap.dedent(
            f"""
            from pathlib import Path
            from streamlit.testing.v1 import AppTest

            app_path = Path(r"{APP_PATH}")
            at = AppTest.from_file(str(app_path), default_timeout=30)
            at.run()
            if at.exception:
                raise SystemExit("initial run exceptions: " + " | ".join(str(x.value) for x in at.exception))

            home_workspace_radio = next((r for r in at.radio if str(getattr(r, "key", "")) == "home_workspace_mode"), None)
            if home_workspace_radio is None:
                raise SystemExit("home workspace radio not found")
            if len(home_workspace_radio.options) < 2:
                raise SystemExit("home workspace options incomplete")

            home_workspace_radio.set_value(home_workspace_radio.options[1])
            at.run()
            if at.exception:
                raise SystemExit("fastlog exceptions: " + " | ".join(str(x.value) for x in at.exception))

            print("FASTLOG_OK")
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
        self.assertIn("FASTLOG_OK", proc.stdout)

    def test_todo_queue_works_when_list_is_initially_empty(self) -> None:
        script = textwrap.dedent(
            f"""
            from pathlib import Path
            from streamlit.testing.v1 import AppTest

            app_path = Path(r"{APP_PATH}")
            at = AppTest.from_file(str(app_path), default_timeout=30)
            at.run()
            if at.exception:
                raise SystemExit("initial run exceptions: " + " | ".join(str(x.value) for x in at.exception))

            if "db" not in at.session_state:
                raise SystemExit("session db not found")
            at.session_state["db"].setdefault("系统配置", {{}})["PM_TODO_LIST"] = []
            at.session_state["todo_pending_drafts"] = []
            at.session_state["todo_manager_norm_sig"] = ""

            nav_radio = next((r for r in at.radio if str(getattr(r, "key", "")) == "main_nav_menu"), None)
            if nav_radio is None:
                raise SystemExit("navigation radio not found")
            if len(nav_radio.options) < 4:
                raise SystemExit("navigation options incomplete")

            nav_radio.set_value(nav_radio.options[3])
            at.run()
            if at.exception:
                raise SystemExit("task page exceptions: " + " | ".join(str(x.value) for x in at.exception))

            title_input = next((x for x in at.text_input if str(getattr(x, "key", "")) == "todo_title_global"), None)
            if title_input is None:
                raise SystemExit("todo title input not found")
            title_input.set_value("空列表首条草稿")

            queue_btn = next((b for b in at.button if "加入待保存列表" in str(b.label or "")), None)
            if queue_btn is None:
                raise SystemExit("queue button not found")
            queue_btn.click()
            at.run()
            if at.exception:
                raise SystemExit("queue from empty list exceptions: " + " | ".join(str(x.value) for x in at.exception))

            drafts = at.session_state["todo_pending_drafts"] if "todo_pending_drafts" in at.session_state else []
            if not drafts:
                raise SystemExit("draft not queued from empty list")

            print("TODO_EMPTY_QUEUE_OK")
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
        self.assertIn("TODO_EMPTY_QUEUE_OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
