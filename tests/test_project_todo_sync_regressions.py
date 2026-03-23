from __future__ import annotations

import ast
import datetime
import re
import types
import unittest
import uuid
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = REPO_ROOT / "app.py"


def load_app_functions(*names: str) -> types.SimpleNamespace:
    source = APP_PATH.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(APP_PATH))
    wanted = set(names)
    namespace: dict[str, object] = {
        "datetime": datetime,
        "re": re,
        "uuid": uuid,
        "db": {},
        "get_recognition_keywords": lambda _key: [],
    }
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            mod = ast.Module(body=[node], type_ignores=[])
            exec(compile(mod, filename=str(APP_PATH), mode="exec"), namespace)
    missing = [name for name in names if name not in namespace]
    if missing:
        raise AssertionError(f"missing functions from app.py: {missing}")
    return types.SimpleNamespace(**namespace)


class ProjectTodoSyncRegressionTest(unittest.TestCase):
    def test_compute_stale_doc_keys_returns_removed_project_docs(self) -> None:
        ns = load_app_functions("compute_stale_doc_keys")

        stale = ns.compute_stale_doc_keys(
            ["系统配置", "1/6威龙", "6威龙", "6早川秋"],
            ["系统配置", "1/6威龙", "1/6早川秋"],
        )

        self.assertEqual(stale, ["6威龙", "6早川秋"])

    def test_has_live_todo_reference_ignores_deleted_or_hidden_todos(self) -> None:
        ns = load_app_functions(
            "todo_scope_of",
            "todo_visible_for_view",
            "get_live_linked_todo_ids",
            "has_live_todo_reference",
        )
        ns.db.update(
            {
                "系统配置": {
                    "PM_TODO_LIST": [
                        {"_id": "td_live", "所属视角": "袁", "创建者视角": "袁"},
                        {"_id": "td_other", "所属视角": "Mo", "创建者视角": "Mo"},
                    ]
                }
            }
        )

        self.assertTrue(ns.has_live_todo_reference({"关联待办": ["td_live"]}, pm_view="袁"))
        self.assertFalse(ns.has_live_todo_reference({"关联待办": ["td_live"]}, pm_view="Mo"))
        self.assertFalse(ns.has_live_todo_reference({"关联待办": ["td_missing"]}, pm_view="袁"))

    def test_purge_deleted_todo_standard_events_removes_stale_todo_reminders(self) -> None:
        ns = load_app_functions(
            "_is_todo_standard_event",
            "purge_deleted_todo_standard_events",
        )
        ns.db.update(
            {
                "系统配置": {
                    "标准事件流": [
                        {
                            "_id": "evt_todo",
                            "来源": "To-do",
                            "动作": "待办更新",
                            "关联待办": ["td1"],
                            "内容": "7/7待办：旧提醒",
                        },
                        {
                            "_id": "evt_progress",
                            "来源": "全局大盘",
                            "动作": "追加最新动态",
                            "关联待办": ["td1", "td2"],
                            "内容": "项目继续推进",
                        },
                    ]
                }
            }
        )

        removed = ns.purge_deleted_todo_standard_events(["td1"])

        self.assertEqual(removed, 1)
        remaining = ns.db["系统配置"]["标准事件流"]
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["_id"], "evt_progress")
        self.assertEqual(remaining[0]["关联待办"], ["td2"])

    def test_expand_workbench_segment_entries_preserves_split_stage_hints(self) -> None:
        ns = load_app_functions(
            "norm_text",
            "extract_todo_segment_hints",
            "expand_workbench_segment_entries",
        )

        rows = ns.expand_workbench_segment_entries(
            "头雕待打印确认效果；鞋子和手型已安排拆件",
            default_component="全局进度",
            default_stage="工程拆件",
            project_components=["头雕(表情)", "服装", "手型", "全局进度"],
            comp_kw={"头雕": "头雕(表情)", "鞋子": "服装", "手型": "手型", "手": "手型"},
            stage_kw_map={"拆件": "工程拆件", "打印": "建模(含打印/签样)"},
        )

        tuples = {(str(row.get("text", "")), str(row.get("component", "")), str(row.get("stage", ""))) for row in rows}
        self.assertIn(("头雕待打印确认效果", "头雕(表情)", "建模(含打印/签样)"), tuples)
        self.assertIn(("鞋子和手型已安排拆件", "服装", "工程拆件"), tuples)
        self.assertIn(("鞋子和手型已安排拆件", "手型", "工程拆件"), tuples)

    def test_is_stage_timeline_driver_log_excludes_todo_completion_logs(self) -> None:
        ns = load_app_functions("is_stage_timeline_driver_log")

        self.assertFalse(
            ns.is_stage_timeline_driver_log(
                {"流转": "待办", "事件": "[待办完成] 早川秋&玛奇玛 待确认提审结果"}
            )
        )
        self.assertTrue(
            ns.is_stage_timeline_driver_log(
                {"流转": "大盘动态", "事件": "版权已给反馈诗实物送审"}
            )
        )

    def test_build_project_todo_reminder_skips_todo_hidden_from_current_view(self) -> None:
        ns = load_app_functions(
            "parse_date_safe",
            "todo_cpddl_text",
            "todo_due_date",
            "todo_scope_of",
            "todo_visible_for_view",
            "build_project_todo_reminder",
        )

        hidden_todo = {
            "_id": "td_hidden",
            "任务": "版权已给反馈诗实物送审",
            "DDL": "2026-03-19",
            "完成": False,
            "所属视角": "Mo",
            "创建者视角": "Mo",
        }

        reminder = ns.build_project_todo_reminder(
            todo_event={
                "动作": "待办新建",
                "日期": "2026-03-19",
                "内容": "版权已给反馈诗实物送审",
                "关联待办": ["td_hidden"],
            },
            live_todo_binding={"todo": hidden_todo, "mode": "pending"},
        )

        self.assertEqual(reminder["action"], "待办提醒")

        reminder_hidden = ns.build_project_todo_reminder(
            todo_event={
                "动作": "待办新建",
                "日期": "2026-03-19",
                "内容": "版权已给反馈诗实物送审",
                "关联待办": ["td_hidden"],
            },
            live_todo_binding={"todo": hidden_todo, "mode": "pending"},
            pm_view="袁",
        )

        self.assertEqual(reminder_hidden, {})

    def test_format_todo_reminder_label_uses_completed_copy(self) -> None:
        ns = load_app_functions("parse_date_safe", "format_todo_reminder_label")

        label = ns.format_todo_reminder_label(
            "素体需要修改",
            "2026-03-17",
            "待办完成",
        )

        self.assertEqual(label, "3/17完成待办：素体需要修改")

    def test_extract_event_date_and_body_does_not_treat_decimal_like_text_as_date(self) -> None:
        ns = load_app_functions("extract_event_date_and_body")

        dt, body = ns.extract_event_date_and_body(
            "7.7的连接杆需要确认平视状态",
            ref_date=datetime.date(2026, 3, 19),
        )

        self.assertIsNone(dt)
        self.assertEqual(body, "7.7的连接杆需要确认平视状态")

    def test_extract_event_date_and_body_still_supports_explicit_month_day_dates(self) -> None:
        ns = load_app_functions("extract_event_date_and_body")

        dt, body = ns.extract_event_date_and_body(
            "7/7提审连接杆",
            ref_date=datetime.date(2026, 3, 19),
        )

        self.assertEqual(dt, datetime.date(2026, 7, 7))
        self.assertEqual(body, "提审连接杆")

    def test_normalize_todo_project_list_repairs_split_ratio_tokens_from_list(self) -> None:
        ns = load_app_functions(
            "norm_text",
            "resolve_alias_project",
            "_project_name_noise_variants",
            "infer_malformed_ratio_project_target",
            "canonicalize_project_name",
            "normalize_todo_project_list",
        )
        ns.db.update(
            {
                "系统配置": {"项目别名": {}},
                "1/6 Batman": {},
            }
        )

        repaired = ns.normalize_todo_project_list(["1", "6 Batman"])

        self.assertEqual(repaired, ["1/6 Batman"])

    def test_append_standard_event_entry_updates_todo_recent_linkage(self) -> None:
        ns = load_app_functions(
            "norm_text",
            "resolve_alias_project",
            "_project_name_noise_variants",
            "infer_malformed_ratio_project_target",
            "canonicalize_project_name",
            "split_people_text",
            "normalize_people_text",
            "_normalize_standard_event_component",
            "todo_link_module_label",
            "sync_todo_recent_linkage",
            "append_standard_event_entry",
        )
        ns.db.update(
            {
                "系统配置": {
                    "项目别名": {},
                    "PM_TODO_LIST": [
                        {
                            "_id": "td1",
                            "任务": "补官图说明",
                            "关联项目": "1/6 Batman",
                            "关联项目列表": ["1/6 Batman"],
                        }
                    ],
                    "标准事件流": [],
                },
                "1/6 Batman": {"部件列表": {}},
            }
        )

        changed = ns.append_standard_event_entry(
            source_module="PM工作台",
            action_type="工作台记录",
            project_name="1/6 Batman",
            event_date=datetime.date(2026, 3, 19),
            component_name="🌐 全局进度 (Overall)",
            stage_name="官图",
            content_text="官图已更新",
            todo_ids=["td1"],
        )

        self.assertTrue(changed)
        todo_row = ns.db["系统配置"]["PM_TODO_LIST"][0]
        self.assertEqual(todo_row["最近联动模块"], "交接工作台")
        self.assertEqual(todo_row["最近联动日期"], "2026-03-19")
        self.assertEqual(todo_row["最近联动项目"], "1/6 Batman")
        self.assertEqual(todo_row["最近联动部件"], "全局进度")
        self.assertEqual(todo_row["最近联动阶段"], "官图")


if __name__ == "__main__":
    unittest.main()
