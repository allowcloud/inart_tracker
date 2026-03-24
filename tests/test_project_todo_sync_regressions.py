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

    def test_contains_print_tracking_signal_filters_weak_stage_sync_and_received_noise(self) -> None:
        ns = load_app_functions(
            "_has_strong_print_tracking_signal",
            "_has_weak_only_print_mentions",
            "_contains_print_tracking_signal",
        )

        self.assertFalse(ns._contains_print_tracking_signal("萨鲁曼头雕待打印确认效果；鞋子和手型已安排拆件"))
        self.assertFalse(ns._contains_print_tracking_signal("[系统自动同步] 跟随全局阶段 设计 -> 建模(含打印/签样)"))
        self.assertFalse(ns._contains_print_tracking_signal("早川秋发型打印件已收齐，待翻模；3D建模提审已交未有反馈"))
        self.assertFalse(ns._contains_print_tracking_signal("[待办完成] 小比例Neo尼奥安排打印并涂装"))
        self.assertTrue(ns._contains_print_tracking_signal("第一版头雕已拆眼睛已安排内部打印"))

    def test_get_print_tracking_status_payload_marks_cancelled_receipt_as_pending_today(self) -> None:
        ns = load_app_functions(
            "parse_date_safe",
            "_get_print_tracking_status_payload",
        )

        payload = ns._get_print_tracking_status_payload(
            {
                "日期": "2026-03-10",
                "描述": "第二版头雕",
                "打印地点": "内部",
                "已收到": False,
                "收到日期": "2026-03-20",
            },
            action_type="打印追踪取消收件",
        )

        self.assertEqual(payload["event_day"], datetime.date.today())
        self.assertIn("[取消收件]", payload["standard_content"])
        self.assertIn("重新待收件", payload["standard_content"])
        self.assertEqual(payload["place"], "内部")

    def test_cancel_received_print_log_becomes_latest_pending_history(self) -> None:
        ns = load_app_functions(
            "norm_text",
            "parse_date_safe",
            "event_attention_priority",
            "get_latest_project_log_binding",
            "_get_print_tracking_status_payload",
            "_append_print_tracking_status_log",
            "_append_print_unreceived_log",
        )
        globals_map = ns.get_latest_project_log_binding.__globals__
        globals_map["is_hidden_system_log"] = lambda log_obj: False
        ns.db.update(
            {
                "1/6超女": {
                    "部件列表": {
                        "头雕(表情)": {
                            "主流程": "建模(含打印/签样)",
                            "日志流": [
                                {
                                    "日期": "2026-03-20",
                                    "流转": "打印追踪",
                                    "工序": "建模(含打印/签样)",
                                    "事件": "[打印件已收到] 第二版头雕 | 来自：内部",
                                }
                            ],
                        }
                    }
                }
            }
        )

        appended = ns._append_print_unreceived_log(
            {
                "项目": "1/6超女",
                "部件": "头雕(表情)",
                "描述": "第二版头雕",
                "打印地点": "内部",
                "已收到": False,
            }
        )
        latest = ns.get_latest_project_log_binding("1/6超女")

        self.assertTrue(appended)
        self.assertEqual(latest["component"], "头雕(表情)")
        self.assertEqual(latest["log"]["日期"], str(datetime.date.today()))
        self.assertIn("[取消收件]", latest["log"]["事件"])
        self.assertIn("重新待收件", latest["log"]["事件"])

    def test_get_standard_event_display_people_hides_dashboard_followup_person(self) -> None:
        ns = load_app_functions(
            "norm_text",
            "_clean_project_name_identity_text",
            "_project_identity_key",
            "_project_identity_preference",
            "resolve_alias_project",
            "_project_name_noise_variants",
            "infer_malformed_ratio_project_target",
            "canonicalize_project_name",
            "split_people_text",
            "normalize_people_text",
            "get_standard_event_display_people",
        )
        ns.db.update(
            {
                "系统配置": {"项目别名": {}},
                "1/6萨鲁曼": {"跟单": "魏"},
            }
        )

        hidden = ns.get_standard_event_display_people(
            {"来源": "全局大盘", "项目": "1/6萨鲁曼", "关联人员": "魏"},
            "1/6萨鲁曼",
        )
        kept = ns.get_standard_event_display_people(
            {"来源": "To-do", "项目": "1/6萨鲁曼", "关联人员": "建模-猫老师"},
            "1/6萨鲁曼",
        )

        self.assertEqual(hidden, "")
        self.assertEqual(kept, "建模-猫老师")

    def test_normalize_project_name_for_write_reuses_hidden_char_variant(self) -> None:
        ns = load_app_functions(
            "norm_text",
            "_clean_project_name_identity_text",
            "_project_identity_key",
            "_project_identity_preference",
            "resolve_alias_project",
            "_project_name_noise_variants",
            "infer_malformed_ratio_project_target",
            "canonicalize_project_name",
            "is_invalid_project_name",
            "normalize_project_name_for_write",
        )
        ns.db.update(
            {
                "系统配置": {"项目别名": {}},
                "1/6早川秋": {"Milestone": "研发中"},
            }
        )

        normalized = ns.normalize_project_name_for_write("1／6早川秋\u200b")

        self.assertEqual(normalized, "1/6早川秋")

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
            "_clean_project_name_identity_text",
            "_project_identity_key",
            "_project_identity_preference",
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
            "_clean_project_name_identity_text",
            "_project_identity_key",
            "_project_identity_preference",
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

    def check_extract_dashboard_todo_segments_infers_component_and_stage_from_natural_text(self) -> None:
        ns = load_app_functions("extract_dashboard_todo_segments", "extract_todo_segment_hints")
        globals_map = ns.extract_dashboard_todo_segments.__globals__
        globals_map["db"].update(
            {
                "1/6马尔福": {
                    "部件列表": {
                        "头雕(表情)": {},
                        "包装": {},
                        "全局进度": {},
                    }
                }
            }
        )
        globals_map["norm_text"] = lambda text: str(text or "").strip().lower().replace(" ", "")
        globals_map["classify_temporal_event_route"] = (
            lambda text, ref_date=None, prefer_past=False: {
                "route": "todo",
                "date": None,
                "body": str(text or "").strip(),
                "intent": "todo",
                "date_bucket": "",
            }
        )

        def _extract_date(text, ref_date=None, prefer_past=False):
            raw = str(text or "").strip()
            m = re.search(r"(\d{1,2})/(\d{1,2})", raw)
            if not m:
                return None, raw
            mm = int(m.group(1))
            dd = int(m.group(2))
            body = (raw[:m.start()] + " " + raw[m.end():]).strip()
            return datetime.date(2026, mm, dd), body

        globals_map["extract_event_date_and_body"] = _extract_date
        globals_map["clean_auto_todo_task_text"] = lambda text: re.sub(r"\s+", " ", str(text or "").strip())
        globals_map["get_component_keyword_map"] = lambda: {
            "植发": "植发",
            "马海毛": "植发",
            "彩盒": "包装",
            "地台贴": "包装",
        }
        globals_map["get_stage_keyword_map"] = lambda: {
            "打样": "建模(含打印/签样)",
            "修改": "建模(含打印/签样)",
        }
        globals_map["infer_todo_handoff_prefill"] = lambda td, proj_name: {}

        rows = ns.extract_dashboard_todo_segments(
            "4/20马海毛到货开始植发；地台贴需修改、彩盒需修改烫色，已转交立宇待打样",
            project_name="1/6马尔福",
            ref_date=datetime.date(2026, 3, 23),
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["task"], "马海毛到货开始植发")
        self.assertEqual(rows[0]["component"], "头雕(表情)")
        self.assertEqual(rows[0]["stage"], "工厂复样(含胶件/上色等)")
        self.assertEqual(rows[0]["due_dt"], datetime.date(2026, 4, 20))
        self.assertEqual(rows[1]["task"], "地台贴需修改、彩盒需修改烫色，已转交立宇待打样")
        self.assertEqual(rows[1]["component"], "包装")
        self.assertEqual(rows[1]["stage"], "工厂复样(含胶件/上色等)")
        self.assertIsNone(rows[1]["due_dt"])
        self.assertTrue(rows[1]["allow_empty_due"])

    def test_extract_dashboard_todo_segments_infers_component_and_stage_from_natural_text_v2(self) -> None:
        ns = load_app_functions(
            "clean_auto_todo_task_text",
            "refine_dashboard_todo_task_text",
            "extract_dashboard_todo_segments",
            "extract_todo_segment_hints",
        )
        globals_map = ns.extract_dashboard_todo_segments.__globals__
        project_name = "1/6\u9a6c\u5c14\u798f"
        head_component = "\u5934\u96d5(\u8868\u60c5)"
        packaging_component = "\u5305\u88c5"
        globals_map["db"].update(
            {
                project_name: {
                    "\u90e8\u4ef6\u5217\u8868": {
                        head_component: {},
                        packaging_component: {},
                        "\u5168\u5c40\u8fdb\u5ea6": {},
                    }
                }
            }
        )
        globals_map["norm_text"] = lambda text: str(text or "").strip().lower().replace(" ", "")
        globals_map["classify_temporal_event_route"] = (
            lambda text, ref_date=None, prefer_past=False: {
                "route": "todo",
                "date": None,
                "body": str(text or "").strip(),
                "intent": "todo",
                "date_bucket": "",
            }
        )

        def _extract_date(text, ref_date=None, prefer_past=False):
            raw = str(text or "").strip()
            m = re.search(r"(\d{1,2})/(\d{1,2})", raw)
            if not m:
                return None, raw
            mm = int(m.group(1))
            dd = int(m.group(2))
            body = (raw[:m.start()] + " " + raw[m.end():]).strip()
            return datetime.date(2026, mm, dd), body

        globals_map["extract_event_date_and_body"] = _extract_date
        globals_map["clean_auto_todo_task_text"] = lambda text: re.sub(r"\s+", " ", str(text or "").strip())
        globals_map["get_component_keyword_map"] = lambda: {
            "\u690d\u53d1": "\u690d\u53d1",
            "\u9a6c\u6d77\u6bdb": "\u690d\u53d1",
            "\u5f69\u76d2": packaging_component,
            "\u5730\u53f0\u8d34": packaging_component,
        }
        globals_map["get_stage_keyword_map"] = lambda: {
            "\u6253\u6837": "\u5efa\u6a21(\u542b\u6253\u5370/\u7b7e\u6837)",
            "\u4fee\u6539": "\u5efa\u6a21(\u542b\u6253\u5370/\u7b7e\u6837)",
        }
        globals_map["infer_todo_handoff_prefill"] = lambda td, proj_name: {}

        rows = ns.extract_dashboard_todo_segments(
            "\u9a6c\u6d77\u6bdb\u9884\u8ba14/20\u5230\u8d27\u5f00\u59cb\u690d\u53d1\uff1b\u5730\u53f0\u8d34\u9700\u4fee\u6539\u3001\u5f69\u76d2\u9700\u4fee\u6539\u70eb\u8272\uff0c\u5df2\u8f6c\u4ea4\u7acb\u5b87\u5f85\u6253\u6837",
            project_name=project_name,
            ref_date=datetime.date(2026, 3, 23),
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["task"], "\u9a6c\u6d77\u6bdb\u5230\u8d27")
        self.assertEqual(rows[0]["component"], head_component)
        self.assertEqual(rows[0]["stage"], "\u5927\u8d27")
        self.assertEqual(rows[0]["due_dt"], datetime.date(2026, 4, 20))
        self.assertEqual(rows[1]["task"], "\u5730\u53f0\u8d34\u3001\u5f69\u76d2\u5f85\u7acb\u5b87\u6253\u6837")
        self.assertEqual(rows[1]["component"], packaging_component)
        self.assertEqual(rows[1]["stage"], "\u5de5\u5382\u590d\u6837(\u542b\u80f6\u4ef6/\u4e0a\u8272\u7b49)")
        self.assertIsNone(rows[1]["due_dt"])
        self.assertTrue(rows[1]["allow_empty_due"])

    def test_extract_dashboard_todo_segments_keeps_followup_todo_after_past_progress(self) -> None:
        ns = load_app_functions(
            "clean_auto_todo_task_text",
            "extract_event_date_and_body",
            "classify_text_intent",
            "classify_temporal_event_route",
            "extract_followup_todo_clause",
            "extract_dashboard_todo_segments",
            "extract_todo_segment_hints",
        )
        globals_map = ns.extract_dashboard_todo_segments.__globals__
        project_name = "1/6\u9a6c\u5c14\u798f"
        head_component = "\u5934\u96d5(\u8868\u60c5)"
        globals_map["db"].update(
            {
                project_name: {
                    "\u90e8\u4ef6\u5217\u8868": {
                        head_component: {},
                        "\u5168\u5c40\u8fdb\u5ea6": {},
                    }
                }
            }
        )
        globals_map["norm_text"] = lambda text: str(text or "").strip().lower().replace(" ", "")
        globals_map["get_recognition_keywords"] = lambda key: {
            "\u672a\u6765\u610f\u56fe\u8bcd": ["\u5f85", "\u5f85\u529e", "\u9700\u8981", "\u9700", "\u9884\u8ba1", "cp"],
            "\u8fc7\u53bb\u610f\u56fe\u8bcd": ["\u5df2", "\u5df2\u7ecf", "\u5b8c\u6210", "\u6536\u5230", "\u5b89\u6392\u4e86", "\u5df2\u5b89\u6392"],
            "\u65e5\u671f\u566a\u97f3\u8bcd": ["\u9884\u8ba1", "\u5de6\u53f3", "\u5927\u6982", "\u7ea6"],
        }.get(key, [])
        globals_map["get_component_keyword_map"] = lambda: {
            "\u5934\u96d5": head_component,
            "\u8138": head_component,
        }
        globals_map["get_stage_keyword_map"] = lambda: {
            "\u6253\u5370": "\u5efa\u6a21(\u542b\u6253\u5370/\u7b7e\u6837)",
        }
        globals_map["infer_todo_handoff_prefill"] = lambda td, proj_name: {}

        rows = ns.extract_dashboard_todo_segments(
            "\u7b2c\u4e8c\u7248\u5934\u96d53/23\u5df2\u5b89\u6392\u6253\u5370\uff0c\u5f85\u6536\u4ef6",
            project_name=project_name,
            ref_date=datetime.date(2026, 3, 24),
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task"], "\u5f85\u6536\u4ef6")
        self.assertEqual(rows[0]["route"], "todo")
        self.assertIsNone(rows[0]["due_dt"])
        self.assertTrue(rows[0]["allow_empty_due"])
        self.assertEqual(rows[0]["component"], head_component)
        self.assertEqual(rows[0]["stage"], "\u5efa\u6a21(\u542b\u6253\u5370/\u7b7e\u6837)")

    def test_extract_dashboard_todo_segments_strips_cp_tail_and_keeps_due_date(self) -> None:
        ns = load_app_functions(
            "clean_auto_todo_task_text",
            "refine_dashboard_todo_task_text",
            "extract_event_date_and_body",
            "classify_text_intent",
            "classify_temporal_event_route",
            "extract_followup_todo_clause",
            "extract_dashboard_todo_segments",
            "extract_todo_segment_hints",
        )
        globals_map = ns.extract_dashboard_todo_segments.__globals__
        project_name = "1/6\u9a6c\u5c14\u798f"
        globals_map["db"].update(
            {
                project_name: {
                    "\u90e8\u4ef6\u5217\u8868": {
                        "\u914d\u4ef6": {},
                        "\u5168\u5c40\u8fdb\u5ea6": {},
                    }
                }
            }
        )
        globals_map["norm_text"] = lambda text: str(text or "").strip().lower().replace(" ", "")
        globals_map["get_recognition_keywords"] = lambda key: {
            "\u672a\u6765\u610f\u56fe\u8bcd": ["\u5f85", "\u5f85\u529e", "\u9700\u8981", "\u9700", "\u9884\u8ba1", "cp"],
            "\u8fc7\u53bb\u610f\u56fe\u8bcd": ["\u5df2", "\u5df2\u7ecf", "\u5b8c\u6210", "\u6536\u5230", "\u5b89\u6392\u4e86", "\u5df2\u5b89\u6392"],
            "\u65e5\u671f\u566a\u97f3\u8bcd": ["\u9884\u8ba1", "\u5de6\u53f3", "\u5927\u6982", "\u7ea6"],
        }.get(key, [])
        globals_map["get_component_keyword_map"] = lambda: {
            "\u6263\u5b50": "\u914d\u4ef6",
            "\u516c\u6587\u5305": "\u914d\u4ef6",
        }
        globals_map["get_stage_keyword_map"] = lambda: {}
        globals_map["infer_todo_handoff_prefill"] = lambda td, proj_name: {}

        rows = ns.extract_dashboard_todo_segments(
            "\u516c\u6587\u5305\u6263\u5b50\u5df2\u7ecf17\uff0c\u5f85\u786e\u8ba4\u62c9\u4f4d\uff0cCP3/25",
            project_name=project_name,
            ref_date=datetime.date(2026, 3, 24),
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task"], "\u516c\u6587\u5305\u6263\u5b50\u5df2\u7ecf17\uff0c\u5f85\u786e\u8ba4\u62c9\u4f4d")
        self.assertEqual(rows[0]["route"], "todo")
        self.assertEqual(rows[0]["due_dt"], datetime.date(2026, 3, 25))
        self.assertFalse(rows[0]["allow_empty_due"])
        self.assertEqual(rows[0]["component"], "\u914d\u4ef6")
        self.assertEqual(rows[0]["stage"], "\u5de5\u5382\u590d\u6837(\u542b\u80f6\u4ef6/\u4e0a\u8272\u7b49)")

    def test_upsert_todo_from_event_text_keeps_undated_dashboard_todo_hints(self) -> None:
        ns = load_app_functions("upsert_todo_from_event_text")
        globals_map = ns.upsert_todo_from_event_text.__globals__
        globals_map["db"].update(
            {
                "系统配置": {"PM_TODO_LIST": []},
                "1/6马尔福": {"负责人": "袁", "跟单": "浪"},
            }
        )
        globals_map["extract_event_date_and_body"] = lambda text, ref_date=None, prefer_past=False: (None, str(text or "").strip())
        globals_map["clean_auto_todo_task_text"] = lambda text: str(text or "").strip()
        globals_map["normalize_people_text"] = lambda text: str(text or "").strip()
        globals_map["normalize_todo_cpddl_for_storage"] = lambda cpddl_text, task_text="", due_dt=None: str(cpddl_text or "").strip()
        globals_map["norm_text"] = lambda text: str(text or "").strip().lower()
        globals_map["todo_matches_project"] = lambda td, proj: proj in (td.get("关联项目列表", []) or []) or str(td.get("关联项目", "")).strip() == proj
        globals_map["todo_project_list"] = (
            lambda td: [str(x).strip() for x in (td.get("关联项目列表", []) or []) if str(x).strip()]
            or ([str(td.get("关联项目", "")).strip()] if str(td.get("关联项目", "")).strip() else [])
        )
        globals_map["todo_append_history_version"] = lambda td, actor="系统": None

        result = ns.upsert_todo_from_event_text(
            "1/6马尔福",
            "地台贴需修改、彩盒需修改烫色，已转交立宇待打样",
            forced_task_body="地台贴需修改、彩盒需修改烫色，已转交立宇待打样",
            allow_empty_due=True,
            return_payload=True,
            forced_component="包装",
            forced_stage="工厂复样(含胶件/上色等)",
        )

        self.assertEqual(result["status"], "created")
        todo_row = globals_map["db"]["系统配置"]["PM_TODO_LIST"][0]
        self.assertEqual(todo_row["任务"], "地台贴需修改、彩盒需修改烫色，已转交立宇待打样")
        self.assertEqual(todo_row["DDL"], "")
        self.assertEqual(todo_row["CPDDL"], "")
        self.assertEqual(todo_row["默认落地部件"], "包装")
        self.assertEqual(todo_row["默认落地阶段"], "工厂复样(含胶件/上色等)")


    def test_upsert_todo_from_event_text_can_skip_project_follow_people_fallback(self) -> None:
        ns = load_app_functions("upsert_todo_from_event_text")
        globals_map = ns.upsert_todo_from_event_text.__globals__
        project_name = "1/6\u9a6c\u5c14\u798f"
        globals_map["db"].update(
            {
                "\u7cfb\u7edf\u914d\u7f6e": {"PM_TODO_LIST": []},
                project_name: {"\u8d1f\u8d23\u4eba": "\u8881", "\u8ddf\u5355": "\u6d6a"},
            }
        )
        globals_map["extract_event_date_and_body"] = (
            lambda text, ref_date=None, prefer_past=False: (datetime.date(2026, 3, 25), "\u516c\u6587\u5305\u6263\u5b50\u5df2\u7ed917\uff0c\u5f85\u786e\u8ba4\u6869\u4f4d")
        )
        globals_map["clean_auto_todo_task_text"] = lambda text: str(text or "").strip()
        globals_map["normalize_people_text"] = lambda text: str(text or "").strip()
        globals_map["normalize_todo_cpddl_for_storage"] = lambda cpddl_text, task_text="", due_dt=None: str(cpddl_text or "").strip()
        globals_map["norm_text"] = lambda text: str(text or "").strip().lower()
        globals_map["todo_matches_project"] = lambda td, proj: proj in (td.get("\u5173\u8054\u9879\u76ee\u5217\u8868", []) or []) or str(td.get("\u5173\u8054\u9879\u76ee", "")).strip() == proj
        globals_map["todo_project_list"] = (
            lambda td: [str(x).strip() for x in (td.get("\u5173\u8054\u9879\u76ee\u5217\u8868", []) or []) if str(x).strip()]
            or ([str(td.get("\u5173\u8054\u9879\u76ee", "")).strip()] if str(td.get("\u5173\u8054\u9879\u76ee", "")).strip() else [])
        )
        globals_map["todo_append_history_version"] = lambda td, actor="\u7cfb\u7edf": None

        result = ns.upsert_todo_from_event_text(
            project_name,
            "\u516c\u6587\u5305\u6263\u5b50\u5df2\u7ed917\uff0c\u5f85\u786e\u8ba4\u6869\u4f4d\uff0cCP3/25",
            forced_due_dt=datetime.date(2026, 3, 25),
            forced_task_body="\u516c\u6587\u5305\u6263\u5b50\u5df2\u7ed917\uff0c\u5f85\u786e\u8ba4\u6869\u4f4d",
            return_payload=True,
            forced_component="\u914d\u4ef6",
            forced_stage="\u5de5\u5382\u590d\u6837(\u542b\u80f6\u4ef6/\u4e0a\u8272\u7b49)",
            fallback_project_people=False,
        )

        self.assertEqual(result["status"], "created")
        todo_row = globals_map["db"]["\u7cfb\u7edf\u914d\u7f6e"]["PM_TODO_LIST"][0]
        self.assertEqual(todo_row["\u5173\u8054\u4eba\u5458"], "")

    def test_get_latest_project_log_binding_prefers_pending_log_over_received_print_same_day(self) -> None:
        ns = load_app_functions(
            "norm_text",
            "parse_date_safe",
            "event_attention_priority",
            "get_latest_project_log_binding",
        )
        globals_map = ns.get_latest_project_log_binding.__globals__
        globals_map["is_hidden_system_log"] = lambda log_obj: False
        project_name = "1/6\u8d85\u5973"
        head_component = "\u5934\u96d5(\u8868\u60c5)"
        globals_map["db"].update(
            {
                project_name: {
                    "\u90e8\u4ef6\u5217\u8868": {
                        head_component: {
                            "\u65e5\u5fd7\u6d41": [
                                {
                                    "\u65e5\u671f": "2026-03-23",
                                    "\u6d41\u8f6c": "\u6253\u5370\u8ffd\u8e2a",
                                    "\u5de5\u5e8f": "\u5efa\u6a21(\u542b\u6253\u5370/\u7b7e\u6837)",
                                    "\u4e8b\u4ef6": "[\u6253\u5370\u4ef6\u5df2\u6536\u5230] \u7b2c\u4e00\u7248\u5934\u96d5\u5df2\u62c6\u773c\u775b\u5df2\u5b89\u6392\u5185\u90e8\u6253\u5370 | \u6765\u81ea\uff1a\u5185\u90e8",
                                },
                                {
                                    "\u65e5\u671f": "2026-03-23",
                                    "\u6d41\u8f6c": "\u5927\u76d8\u52a8\u6001",
                                    "\u5de5\u5e8f": "\u5efa\u6a21(\u542b\u6253\u5370/\u7b7e\u6837)",
                                    "\u4e8b\u4ef6": "\u7b2c\u4e8c\u7248\u5934\u96d53/23\u5df2\u5b89\u6392\u6253\u5370\uff0c\u5f85\u6536\u4ef6",
                                },
                            ]
                        }
                    }
                }
            }
        )

        latest = ns.get_latest_project_log_binding(project_name)

        self.assertEqual(latest["component"], head_component)
        self.assertEqual(latest["log"]["\u4e8b\u4ef6"], "\u7b2c\u4e8c\u7248\u5934\u96d53/23\u5df2\u5b89\u6392\u6253\u5370\uff0c\u5f85\u6536\u4ef6")

    def test_build_project_progress_matrix_rows_seeds_default_follow_components_from_global(self) -> None:
        ns = load_app_functions(
            "component_matrix_follows_global_progress",
            "build_project_progress_matrix_rows",
        )
        globals_map = ns.build_project_progress_matrix_rows.__globals__
        globals_map["STAGES_UNIFIED"] = [
            "\u7acb\u9879",
            "\u5efa\u6a21(\u542b\u6253\u5370/\u7b7e\u6837)",
            "\u5b98\u56fe",
            "\u5de5\u5382\u590d\u6837(\u542b\u80f6\u4ef6/\u4e0a\u8272\u7b49)",
            "\u5927\u8d27",
            "\u23f8\ufe0f \u6682\u505c/\u6401\u7f6e",
            "\u2705 \u5df2\u5b8c\u6210(\u7ed3\u675f)",
        ]
        globals_map["MATRIX_FOLLOW_COMPONENTS"] = [
            "\u5934\u96d5(\u8868\u60c5)",
            "\u7d20\u4f53",
            "\u624b\u578b",
            "\u914d\u4ef6",
            "\u5730\u53f0",
        ]
        globals_map["is_hidden_system_log"] = lambda log_obj: False

        rows = ns.build_project_progress_matrix_rows(
            {
                "\u90e8\u4ef6\u5217\u8868": {
                    "\u5168\u5c40\u8fdb\u5ea6": {
                        "\u4e3b\u6d41\u7a0b": "\u5b98\u56fe",
                        "\u65e5\u5fd7\u6d41": [{"\u65e5\u671f": "2026-03-24", "\u5de5\u5e8f": "\u5b98\u56fe", "\u4e8b\u4ef6": "\u9879\u76ee\u63a8\u8fdb"}],
                    }
                }
            }
        )

        self.assertEqual(
            [row["component"] for row in rows],
            [
                "\u5168\u5c40\u8fdb\u5ea6",
                "\u5934\u96d5(\u8868\u60c5)",
                "\u7d20\u4f53",
                "\u624b\u578b",
                "\u914d\u4ef6",
                "\u5730\u53f0",
            ],
        )
        follower = next(row for row in rows if row["component"] == "\u5934\u96d5(\u8868\u60c5)")
        self.assertTrue(follower["inherits_global"])
        self.assertEqual(follower["source_component"], "\u5168\u5c40\u8fdb\u5ea6")
        self.assertEqual(follower["info"]["\u4e3b\u6d41\u7a0b"], "\u5b98\u56fe")

    def test_component_matrix_follows_global_progress_keeps_unedited_seed_component_following_global(self) -> None:
        ns = load_app_functions("component_matrix_follows_global_progress")
        globals_map = ns.component_matrix_follows_global_progress.__globals__
        globals_map["STAGES_UNIFIED"] = [
            "\u7acb\u9879",
            "\u5efa\u6a21(\u542b\u6253\u5370/\u7b7e\u6837)",
            "\u5b98\u56fe",
        ]
        globals_map["is_hidden_system_log"] = lambda log_obj: False

        follows = ns.component_matrix_follows_global_progress(
            "\u624b\u578b",
            {"\u8d1f\u8d23\u4eba": "Mo", "\u4e3b\u6d41\u7a0b": "\u7acb\u9879", "\u65e5\u5fd7\u6d41": []},
            {"\u4e3b\u6d41\u7a0b": "\u5b98\u56fe", "\u65e5\u5fd7\u6d41": [{"\u65e5\u671f": "2026-03-24", "\u5de5\u5e8f": "\u5b98\u56fe"}]},
        )

        self.assertTrue(follows)

    def test_component_matrix_follows_global_progress_respects_explicit_component_override(self) -> None:
        ns = load_app_functions("component_matrix_follows_global_progress")
        globals_map = ns.component_matrix_follows_global_progress.__globals__
        globals_map["STAGES_UNIFIED"] = [
            "\u7acb\u9879",
            "\u5efa\u6a21(\u542b\u6253\u5370/\u7b7e\u6837)",
            "\u5b98\u56fe",
        ]
        globals_map["is_hidden_system_log"] = lambda log_obj: False

        follows = ns.component_matrix_follows_global_progress(
            "\u624b\u578b",
            {
                "\u4e3b\u6d41\u7a0b": "\u5efa\u6a21(\u542b\u6253\u5370/\u7b7e\u6837)",
                "\u65e5\u5fd7\u6d41": [{"\u65e5\u671f": "2026-03-24", "\u5de5\u5e8f": "\u5efa\u6a21(\u542b\u6253\u5370/\u7b7e\u6837)", "\u4e8b\u4ef6": "\u624b\u578b\u5f85\u786e\u8ba4"}],
            },
            {"\u4e3b\u6d41\u7a0b": "\u5b98\u56fe", "\u65e5\u5fd7\u6d41": [{"\u65e5\u671f": "2026-03-24", "\u5de5\u5e8f": "\u5b98\u56fe"}]},
        )

        self.assertFalse(follows)

    def test_build_project_review_matrix_state_seeds_follow_components_from_global_review(self) -> None:
        ns = load_app_functions(
            "component_matrix_follows_global_progress",
            "build_project_progress_matrix_rows",
            "build_project_review_matrix_state",
        )
        globals_map = ns.build_project_review_matrix_state.__globals__
        globals_map["db"].update(
            {
                "1/6\u9a6c\u5c14\u798f": {
                    "\u90e8\u4ef6\u5217\u8868": {
                        "\u5168\u5c40\u8fdb\u5ea6": {
                            "\u4e3b\u6d41\u7a0b": "\u5b98\u56fe",
                            "\u65e5\u5fd7\u6d41": [{"\u65e5\u671f": "2026-03-24", "\u5de5\u5e8f": "\u5b98\u56fe"}],
                        }
                    }
                }
            }
        )
        globals_map["STAGES_UNIFIED"] = [
            "\u7acb\u9879",
            "\u5efa\u6a21(\u542b\u6253\u5370/\u7b7e\u6837)",
            "\u5b98\u56fe",
        ]
        globals_map["MATRIX_FOLLOW_COMPONENTS"] = [
            "\u5934\u96d5(\u8868\u60c5)",
            "\u7d20\u4f53",
            "\u624b\u578b",
            "\u914d\u4ef6",
            "\u5730\u53f0",
        ]
        globals_map["is_hidden_system_log"] = lambda log_obj: False
        globals_map["parse_date_safe"] = lambda text: datetime.date(2026, 3, 24) if text == "2026-03-24" else None
        globals_map["normalize_review_round"] = lambda text: int(str(text or "0").strip() or "0")

        state = ns.build_project_review_matrix_state(
            "1/6\u9a6c\u5c14\u798f",
            review_rows=[
                {
                    "\u65e5\u671f": "2026-03-24",
                    "\u90e8\u4ef6": "\u5168\u5c40\u8fdb\u5ea6",
                    "\u63d0\u5ba1\u7c7b\u578b": "3D\u63d0\u5ba1",
                    "\u63d0\u5ba1\u7ed3\u679c": "\u5f85\u53cd\u9988",
                    "\u8f6e\u6b21": "1",
                    "\u4e8b\u4ef6": "\u5168\u5c40 3D \u63d0\u5ba1",
                }
            ],
        )

        component_rows = state["component_rows"]
        self.assertEqual(component_rows[0]["component"], "\u5168\u5c40\u8fdb\u5ea6")
        head_row = next(row for row in component_rows if row["component"] == "\u5934\u96d5(\u8868\u60c5)")
        self.assertTrue(head_row["inherits_global"])
        self.assertEqual(head_row["source_component"], "\u5168\u5c40\u8fdb\u5ea6")

    def test_pick_review_matrix_cell_row_prefers_explicit_component_review_over_global_fallback(self) -> None:
        ns = load_app_functions("pick_review_matrix_cell_row")

        latest_map = {
            ("\u5168\u5c40\u8fdb\u5ea6", "3D\u63d0\u5ba1"): {
                "row": {"\u63d0\u5ba1\u7ed3\u679c": "\u5f85\u53cd\u9988", "\u4e8b\u4ef6": "\u5168\u5c40\u63d0\u5ba1"}
            },
            ("\u5934\u96d5(\u8868\u60c5)", "3D\u63d0\u5ba1"): {
                "row": {"\u63d0\u5ba1\u7ed3\u679c": "\u901a\u8fc7", "\u4e8b\u4ef6": "\u5934\u96d5\u63d0\u5ba1"}
            },
        }

        row, inherited = ns.pick_review_matrix_cell_row(
            "\u5934\u96d5(\u8868\u60c5)",
            "3D\u63d0\u5ba1",
            latest_map,
            inherits_global=True,
            source_component="\u5168\u5c40\u8fdb\u5ea6",
        )

        self.assertFalse(inherited)
        self.assertEqual(row["\u4e8b\u4ef6"], "\u5934\u96d5\u63d0\u5ba1")

    def test_sync_save_db_system_config_skips_global_recompute(self) -> None:
        ns = load_app_functions("sync_save_db")

        class FakeSessionState(dict):
            def __getattr__(self, name):
                return self[name]

            def __setattr__(self, name, value):
                self[name] = value

        calls = {"all": 0, "project": [], "persist": []}
        fake_state = FakeSessionState(
            db={
                "系统配置": {"项目别名": {}},
                "1/6超女": {"Milestone": "官图"},
            }
        )
        globals_map = ns.sync_save_db.__globals__
        globals_map["st"] = types.SimpleNamespace(session_state=fake_state)
        globals_map["auto_cleanup_project_shells"] = lambda: None
        globals_map["sanitize_project_alias_map"] = lambda raw: raw if isinstance(raw, dict) else {}
        globals_map["canonicalize_all_project_references"] = lambda: None
        globals_map["recompute_project_derived_state"] = lambda proj: calls["project"].append(proj)
        globals_map["recompute_all_project_derived_states"] = lambda: calls.__setitem__("all", calls["all"] + 1)
        globals_map["persist_db_scope"] = lambda changed_proj=None: calls["persist"].append(changed_proj)

        ns.sync_save_db("系统配置")

        self.assertEqual(calls["all"], 0)
        self.assertEqual(calls["project"], [])
        self.assertEqual(calls["persist"], ["系统配置"])

    def test_build_history_day_scope_rows_limits_projects_and_seeds_missing_ids(self) -> None:
        ns = load_app_functions("build_history_day_scope_rows")
        globals_map = ns.build_history_day_scope_rows.__globals__
        globals_map["is_hidden_system_log"] = lambda log_obj: False
        globals_map["normalize_review_type"] = lambda value: str(value or "(无)") or "(无)"
        globals_map["normalize_review_round"] = lambda value: str(value or "").strip()
        ns.db.update(
            {
                "1/6超女": {
                    "部件列表": {
                        "头雕(表情)": {
                            "日志流": [
                                {"日期": "2026-03-24", "工序": "建模(含打印/签样)", "流转": "打印追踪", "事件": "第二版头雕待收件"}
                            ]
                        }
                    }
                },
                "1/6马尔福": {
                    "部件列表": {
                        "全局进度": {
                            "日志流": [
                                {"_id": "keep_me", "日期": "2026-03-24", "工序": "官图", "流转": "项目日志", "事件": "官图推进"}
                            ]
                        }
                    }
                },
            }
        )

        result = ns.build_history_day_scope_rows(["1/6超女"])

        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["项目"], "1/6超女")
        self.assertEqual(result["proj_map"][result["rows"][0]["_id"]], "1/6超女")
        self.assertEqual(result["comp_map"][result["rows"][0]["_id"]], "头雕(表情)")
        self.assertEqual(result["seeded_projects"], {"1/6超女"})

    def test_collect_history_project_todos_filters_and_sorts_related_items(self) -> None:
        ns = load_app_functions("collect_history_done_project_todos", "collect_history_project_todos")
        globals_map = ns.collect_history_project_todos.__globals__
        globals_map["todo_matches_project"] = lambda td, proj: proj in list(td.get("关联项目列表", []) or [td.get("关联项目", "")])
        ns.db.update(
            {
                "系统配置": {
                    "PM_TODO_LIST": [
                        {"任务": "先建旧记录", "创建": "2026-03-01", "完成": False, "关联项目列表": ["1/6超女"]},
                        {"任务": "后建已完成", "创建": "2026-03-10", "完成": True, "完成时间": "2026-03-22", "关联项目列表": ["1/6超女"]},
                        {"任务": "别的项目", "创建": "2026-03-20", "完成": True, "完成时间": "2026-03-23", "关联项目列表": ["1/6马尔福"]},
                    ]
                }
            }
        )

        all_rows = ns.collect_history_project_todos("1/6超女")
        done_rows = ns.collect_history_done_project_todos("1/6超女")

        self.assertEqual([row["任务"] for row in all_rows], ["后建已完成", "先建旧记录"])
        self.assertEqual([row["任务"] for row in done_rows], ["后建已完成"])

    def test_collect_history_project_standard_events_filters_current_project(self) -> None:
        ns = load_app_functions("collect_history_project_standard_events")
        ns.db.update(
            {
                "系统配置": {
                    "标准事件流": [
                        {"项目": "1/6超女", "内容": "头雕待收件"},
                        {"项目": "1/6马尔福", "内容": "官图推进"},
                    ]
                }
            }
        )

        rows = ns.collect_history_project_standard_events("1/6超女")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["内容"], "头雕待收件")

if __name__ == "__main__":
    unittest.main()
