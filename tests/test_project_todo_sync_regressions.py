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
