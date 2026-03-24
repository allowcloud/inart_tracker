from __future__ import annotations

import datetime
from pathlib import Path

from core.constants import DEFAULT_SYS_CFG, SYSTEM_CONFIG_KEY, TODO_LIST_KEY
from core.project_ops import append_component_log_entry, auto_sync_milestone
from core.shared_logic import norm_text, parse_date_safe, todo_project_list
from core.todo_ops import refresh_project_todo_links, upsert_project_todos_from_log
from core.storage import LocalJsonStorageManager, build_storage_manager, ensure_db_shape


class TrackerRepository:
    def __init__(self, storage_manager):
        self.storage = storage_manager
        self._db = ensure_db_shape(self.storage.load())

    @classmethod
    def from_local_json(cls, data_path="tracker_data_web_v20.json"):
        return cls(LocalJsonStorageManager(path=data_path))

    @classmethod
    def from_default_storage(cls, data_path="tracker_data_web_v20.json"):
        return cls(build_storage_manager(json_path=data_path, prefer_local=True))

    @property
    def db(self):
        return self._db

    @property
    def backend_name(self):
        return getattr(self.storage, "backend_name", "Unknown")

    @property
    def data_path(self):
        raw_path = getattr(self.storage, "path", None)
        return Path(raw_path).resolve() if raw_path else None

    def refresh(self):
        self._db = ensure_db_shape(self.storage.load())
        return self._db

    def system_config(self):
        return self._db.get(SYSTEM_CONFIG_KEY, {})

    def project_names(self):
        return sorted([name for name in self._db.keys() if name != SYSTEM_CONFIG_KEY], key=lambda value: value.lower())

    def get_project(self, project_name):
        return self._db.get(str(project_name or "").strip(), {})

    def list_todos(self):
        todos = self.system_config().get(TODO_LIST_KEY, [])
        return todos if isinstance(todos, list) else []

    def stage_options(self):
        raw_stages = self.system_config().get("标准阶段", DEFAULT_SYS_CFG["标准阶段"])
        stage_list = [str(x).strip() for x in (raw_stages or []) if str(x).strip()]
        return stage_list or list(DEFAULT_SYS_CFG["标准阶段"])

    def project_component_names(self, project_name):
        project = self.get_project(project_name)
        components = project.get("部件列表", {}) if isinstance(project, dict) else {}
        if not isinstance(components, dict) or not components:
            return ["全局进度"]
        names = [str(name).strip() for name in components.keys() if str(name).strip()]
        if not any("全局" in name for name in names):
            names.insert(0, "全局进度")
        ordered = list(dict.fromkeys(names))
        return sorted(ordered, key=lambda name: (name != "全局进度", name))

    def alias_map(self):
        raw_alias = self.system_config().get("项目别名", {})
        alias = {}
        if isinstance(raw_alias, dict):
            for raw_key, raw_value in raw_alias.items():
                key = str(raw_key).strip()
                value = str(raw_value).strip()
                if not key or not value:
                    continue
                alias[key] = value
                alias[norm_text(key)] = value
        return alias

    def canonicalize_project_name(self, project_name, valid_projs=None, alias_map=None):
        token = str(project_name or "").strip()
        if not token:
            return ""
        valid_projects = valid_projs or self.project_names()
        alias_lookup = alias_map or self.alias_map()
        if token in valid_projects:
            return token
        mapped = alias_lookup.get(token) or alias_lookup.get(norm_text(token))
        if mapped:
            return mapped
        normalized = norm_text(token)
        matched = next((name for name in valid_projects if norm_text(name) == normalized), "")
        return matched or token

    def find_project_todos(self, project_name):
        project = str(project_name or "").strip()
        if not project:
            return []
        valid_projects = self.project_names()
        alias_map = self.alias_map()
        return [
            todo
            for todo in self.list_todos()
            if project in todo_project_list(
                todo,
                valid_projects=valid_projects,
                alias_map=alias_map,
                canonicalize=self.canonicalize_project_name,
            )
        ]

    def collect_project_logs(self, project_name):
        project = self.get_project(project_name)
        components = project.get("部件列表", {})
        if not isinstance(components, dict):
            return []

        rows = []
        for component_name, component_info in components.items():
            logs = component_info.get("日志流", []) if isinstance(component_info, dict) else []
            if not isinstance(logs, list):
                continue
            for index, log_entry in enumerate(logs):
                row = dict(log_entry or {})
                row["_component"] = str(component_name).strip() or "全局进度"
                row["_order"] = index
                rows.append(row)

        def sort_key(row):
            date_value = parse_date_safe(row.get("日期", "")) or datetime.date.min
            return (date_value.toordinal(), row.get("_order", 0), str(row.get("_id", "")).strip())

        return sorted(rows, key=sort_key, reverse=True)

    def save(self):
        self._db = ensure_db_shape(self._db)
        self.storage.save(self._db)

    def save_project(self, project_name):
        self._db = ensure_db_shape(self._db)
        project = str(project_name or "").strip()
        if not project or project not in self._db:
            self.save()
            return
        if hasattr(self.storage, "save_one"):
            self.storage.save_one(project, self._db[project])
            self.storage.save_one(SYSTEM_CONFIG_KEY, self.system_config())
            return
        self.save()

    def append_project_log(
        self,
        project_name,
        component_name,
        stage_name,
        event_text,
        event_date=None,
        flow="桌面工作台",
        sync_todos=True,
    ):
        project = str(project_name or "").strip()
        component = str(component_name or "").strip() or "全局进度"
        stage = str(stage_name or "").strip()
        event = str(event_text or "").strip()
        if not project:
            raise ValueError("项目不能为空。")
        if not event:
            raise ValueError("日志内容不能为空。")

        event_day = event_date if isinstance(event_date, datetime.date) else datetime.date.today()
        stage_list = self.stage_options()
        default_stage = stage or (stage_list[0] if stage_list else "立项")
        append_component_log_entry(
            self._db,
            project,
            component,
            {
                "日期": str(event_day),
                "流转": str(flow or "").strip() or "桌面工作台",
                "工序": default_stage,
                "事件": event,
            },
            resulting_stage=stage or None,
            default_stage=default_stage,
            stages=stage_list,
        )
        auto_sync_milestone(self._db.get(project), stage_list)
        todo_results = []
        todo_link_updates = 0
        if sync_todos:
            todo_results = upsert_project_todos_from_log(
                self._db,
                project_name=project,
                event_text=event,
                event_date=event_day,
                component_name=component,
                stage_name=stage,
                alias_map=self.alias_map(),
                canonicalize_project_name=self.canonicalize_project_name,
            )
            todo_link_updates = refresh_project_todo_links(
                self._db,
                project,
                alias_map=self.alias_map(),
                canonicalize_project_name=self.canonicalize_project_name,
            )
        self.save_project(project)
        logs = self.collect_project_logs(project)
        return {
            "log": logs[0] if logs else {},
            "todo_results": todo_results,
            "todo_link_updates": todo_link_updates,
        }
