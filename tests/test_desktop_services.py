from __future__ import annotations

import datetime
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core.repository import TrackerRepository
from core.storage import LocalJsonStorageManager
from services.desktop_service import TrackerDesktopService


class DesktopServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_path = Path(self.temp_dir.name) / "tracker.json"
        self.data_path.write_text(
            json.dumps(
                {
                    "系统配置": {
                        "项目别名": {"Alpha Alias": "Project Alpha"},
                        "PM_TODO_LIST": [
                            {
                                "_id": "td1",
                                "任务": "补打印确认",
                                "DDL": "2026-03-28",
                                "完成": False,
                                "所属视角": "Mo",
                                "关联项目列表": ["Alpha Alias"],
                            },
                            {
                                "_id": "td2",
                                "任务": "归档记录",
                                "DDL": "2026-03-20",
                                "完成": True,
                                "所属视角": "Mo",
                                "关联项目列表": ["Project Alpha"],
                            },
                        ],
                    },
                    "Project Alpha": {
                        "负责人": "Mo",
                        "跟单": "June",
                        "Milestone": "研发中",
                        "Target": "2026-06",
                        "发货区间": "2026-Q3",
                        "备忘录": "优先观察头雕进度",
                        "部件列表": {
                            "全局进度": {
                                "主流程": "建模(含打印/签样)",
                                "日志流": [
                                    {
                                        "日期": "2026-03-24",
                                        "流转": "项目日志",
                                        "工序": "建模(含打印/签样)",
                                        "事件": "头雕等待最终确认",
                                        "_id": "log2",
                                    },
                                    {
                                        "日期": "2026-03-10",
                                        "流转": "项目日志",
                                        "工序": "立项",
                                        "事件": "项目启动",
                                        "_id": "log1",
                                    },
                                ],
                            },
                            "头雕(表情)": {
                                "主流程": "建模(含打印/签样)",
                                "日志流": [
                                    {
                                        "日期": "2026-03-23",
                                        "流转": "项目日志",
                                        "工序": "建模(含打印/签样)",
                                        "事件": "头雕微调中",
                                        "_id": "log3",
                                    }
                                ],
                            },
                        },
                    },
                    "Project Beta": {
                        "负责人": "Ana",
                        "Milestone": "待立项",
                        "部件列表": {},
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        repository = TrackerRepository(LocalJsonStorageManager(path=self.data_path))
        self.service = TrackerDesktopService(repository)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dashboard_stats_and_project_summary_use_real_data_shapes(self):
        stats = self.service.dashboard_stats()
        self.assertEqual(stats.project_count, 2)
        self.assertEqual(stats.todo_total, 2)
        self.assertEqual(stats.todo_pending, 1)
        self.assertEqual(stats.projects_with_logs, 1)

        summaries = {summary.name: summary for summary in self.service.list_project_summaries()}
        alpha = summaries["Project Alpha"]
        self.assertEqual(alpha.current_stage, "建模(含打印/签样)")
        self.assertEqual(alpha.log_count, 3)
        self.assertEqual(alpha.pending_todo_count, 1)
        self.assertEqual(alpha.completed_todo_count, 1)
        self.assertEqual(alpha.latest_log_event, "头雕等待最终确认")

    def test_project_detail_exposes_components_logs_and_todo_lines(self):
        detail = self.service.get_project_detail("Project Alpha")

        self.assertEqual(detail.summary.name, "Project Alpha")
        self.assertEqual(detail.note, "优先观察头雕进度")
        self.assertEqual(len(detail.components), 2)
        self.assertEqual(detail.components[0].name, "全局进度")
        self.assertEqual(detail.recent_logs[0].event, "头雕等待最终确认")
        self.assertTrue(any("补打印确认" in line for line in detail.todo_lines))

    def test_add_project_log_persists_and_updates_project_state(self):
        result = self.service.add_project_log(
            project_name="Project Beta",
            component_name="全局进度",
            stage_name="立项",
            event_text="桌面版录入了第一条项目日志",
            event_date=datetime.date(2026, 3, 24),
            sync_todos=False,
        )
        detail = result["detail"]

        self.assertEqual(detail.summary.name, "Project Beta")
        self.assertEqual(detail.summary.current_stage, "立项")
        self.assertEqual(detail.summary.log_count, 1)
        self.assertEqual(detail.summary.latest_log_event, "桌面版录入了第一条项目日志")
        self.assertEqual(result["todo_results"], [])
        self.assertEqual(result["todo_link_updates"], 0)

        stored = json.loads(self.data_path.read_text(encoding="utf-8"))
        beta = stored["Project Beta"]
        self.assertEqual(beta["Milestone"], "待立项")
        self.assertEqual(beta["部件列表"]["全局进度"]["主流程"], "立项")
        self.assertEqual(beta["部件列表"]["全局进度"]["日志流"][-1]["事件"], "桌面版录入了第一条项目日志")

    def test_add_project_log_can_auto_create_and_link_todo(self):
        result = self.service.add_project_log(
            project_name="Project Alpha",
            component_name="全局进度",
            stage_name="建模(含打印/签样)",
            event_text="3/28 待打印确认头雕效果",
            event_date=datetime.date(2026, 3, 24),
            sync_todos=True,
        )
        detail = result["detail"]

        self.assertEqual(detail.summary.pending_todo_count, 2)
        self.assertEqual(len(result["todo_results"]), 1)
        self.assertEqual(result["todo_results"][0]["status"], "created")

        stored = json.loads(self.data_path.read_text(encoding="utf-8"))
        todos = stored["系统配置"]["PM_TODO_LIST"]
        created = next(td for td in todos if td["任务"] == "待打印确认头雕效果")
        self.assertEqual(created["DDL"], "2026-03-28")
        self.assertEqual(created["默认落地部件"], "全局进度")
        self.assertEqual(created["默认落地阶段"], "建模(含打印/签样)")
        self.assertEqual(created["最近联动项目"], "Project Alpha")
        self.assertEqual(created["最近联动部件"], "全局进度")


    def test_desktop_summary_mode_runs_as_script(self):
        repo_root = Path(__file__).resolve().parents[1]
        main_path = repo_root / "desktop_app" / "main.py"
        result = subprocess.run(
            [sys.executable, str(main_path), "--summary", "--data-file", str(self.data_path)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=20,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("backend=Local JSON", result.stdout)
        self.assertIn("projects=2", result.stdout)

if __name__ == "__main__":
    unittest.main()
