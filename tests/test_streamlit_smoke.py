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

            detail_radio = next((r for r in at.radio if "项目详情标签" in str(r.label or "")), None)
            if detail_radio is not None:
                progress_option = next((o for o in detail_radio.options if "进展" in str(o)), None)
                if progress_option is not None:
                    detail_radio.set_value(progress_option)
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

            current_view = "Mo"
            for sel in at.selectbox:
                if "视角切换" in str(sel.label or ""):
                    current_view = str(sel.value or "Mo")
                    break
            if current_view == "所有人":
                current_view = "Mo"

            at.session_state["db"]["1/6 SMOKE"] = {{
                "负责人": current_view,
                "Milestone": "研发中",
                "Target": "TBD",
                "发货区间": "",
                "部件列表": {{
                    "全局进度": {{
                        "主流程": "建模(含打印/签样)",
                        "日志流": [
                            {{
                                "日期": "2026-04-09",
                                "流转": "测试",
                                "工序": "建模(含打印/签样)",
                                "事件": "smoke project ready",
                            }}
                        ],
                    }}
                }},
            }}

            jump_btn = None
            for btn in at.button:
                label = str(btn.label or "")
                if "全局大盘与甘特图" in label:
                    jump_btn = btn
                    break
            if jump_btn is None:
                raise SystemExit("home dashboard quick action not found")

            jump_btn.click()
            at.run()
            if at.exception:
                raise SystemExit("home dashboard navigation exceptions: " + " | ".join(str(x.value) for x in at.exception))

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

            nav_radio = None
            for r in at.radio:
                label = str(r.label or "")
                options = [str(o) for o in r.options]
                if "功能导航" in label or any("速记" in str(o) for o in options):
                    nav_radio = r
                    break
            if nav_radio is None:
                raise SystemExit("navigation radio not found")

            fastlog_option = next((o for o in nav_radio.options if "速记" in str(o)), None)
            if fastlog_option is None:
                raise SystemExit("fastlog option not found")

            nav_radio.set_value(fastlog_option)
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

            nav_radio = None
            for r in at.radio:
                label = str(r.label or "")
                options = [str(o) for o in r.options]
                if "功能导航" in label or any("我的待办" in o for o in options):
                    nav_radio = r
                    break
            if nav_radio is None:
                raise SystemExit("navigation radio not found")

            task_option = next((o for o in nav_radio.options if "我的待办" in str(o)), None)
            if task_option is None:
                raise SystemExit("task option not found")

            nav_radio.set_value(task_option)
            at.run()
            if at.exception:
                raise SystemExit("task page exceptions: " + " | ".join(str(x.value) for x in at.exception))

            title_input = next((x for x in at.text_input if "任务" in str(x.label or "")), None)
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

