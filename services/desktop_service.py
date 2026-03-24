from __future__ import annotations

import datetime
from dataclasses import dataclass

from core.shared_logic import todo_alert_text, todo_due_date, todo_scope_of


@dataclass(frozen=True)
class DashboardStats:
    project_count: int
    projects_with_logs: int
    todo_total: int
    todo_pending: int
    todo_completed: int
    todo_overdue: int


@dataclass(frozen=True)
class ProjectSummary:
    name: str
    owner: str
    merchandiser: str
    milestone: str
    current_stage: str
    target: str
    ship_window: str
    component_count: int
    log_count: int
    latest_log_date: str
    latest_log_event: str
    pending_todo_count: int
    completed_todo_count: int


@dataclass(frozen=True)
class ComponentSnapshot:
    name: str
    stage: str
    log_count: int
    latest_log_date: str


@dataclass(frozen=True)
class ProjectLogSnapshot:
    date: str
    component: str
    stage: str
    flow: str
    event: str


@dataclass(frozen=True)
class ProjectDetail:
    summary: ProjectSummary
    note: str
    components: list[ComponentSnapshot]
    recent_logs: list[ProjectLogSnapshot]
    todo_lines: list[str]


class TrackerDesktopService:
    def __init__(self, repository):
        self.repository = repository

    def refresh(self):
        self.repository.refresh()

    def stage_options(self):
        return self.repository.stage_options()

    def component_options(self, project_name):
        return self.repository.project_component_names(project_name)

    def dashboard_stats(self):
        todos = self.repository.list_todos()
        pending = [todo for todo in todos if not bool((todo or {}).get("完成"))]
        overdue = []
        for todo in pending:
            due = todo_due_date(todo)
            if isinstance(due, datetime.date) and due < datetime.date.today():
                overdue.append(todo)
        projects_with_logs = 0
        for project_name in self.repository.project_names():
            if self.repository.collect_project_logs(project_name):
                projects_with_logs += 1
        return DashboardStats(
            project_count=len(self.repository.project_names()),
            projects_with_logs=projects_with_logs,
            todo_total=len(todos),
            todo_pending=len(pending),
            todo_completed=len(todos) - len(pending),
            todo_overdue=len(overdue),
        )

    def list_project_summaries(self, search_text=""):
        summaries = [self._build_project_summary(project_name) for project_name in self.repository.project_names()]
        token = str(search_text or "").strip().lower()
        if token:
            summaries = [
                summary
                for summary in summaries
                if token in summary.name.lower()
                or token in summary.owner.lower()
                or token in summary.current_stage.lower()
                or token in summary.milestone.lower()
            ]
        return sorted(
            summaries,
            key=lambda summary: (
                bool(summary.latest_log_date),
                summary.latest_log_date,
                summary.log_count,
                summary.name.lower(),
            ),
            reverse=True,
        )

    def get_project_detail(self, project_name):
        summary = self._build_project_summary(project_name)
        project = self.repository.get_project(project_name)
        components = self._build_component_snapshots(project)
        recent_logs = [
            ProjectLogSnapshot(
                date=str(log.get("日期", "")).strip(),
                component=str(log.get("_component", "")).strip() or "全局进度",
                stage=str(log.get("工序", "")).strip() or "-",
                flow=str(log.get("流转", "")).strip() or "-",
                event=str(log.get("事件", "")).strip() or "无事件",
            )
            for log in self.repository.collect_project_logs(project_name)[:12]
        ]
        todo_lines = self._build_project_todo_lines(project_name)
        return ProjectDetail(
            summary=summary,
            note=str(project.get("备忘录", "")).strip(),
            components=components,
            recent_logs=recent_logs,
            todo_lines=todo_lines,
        )

    def add_project_log(self, project_name, component_name, stage_name, event_text, event_date=None, flow="桌面工作台", sync_todos=True):
        result = self.repository.append_project_log(
            project_name=project_name,
            component_name=component_name,
            stage_name=stage_name,
            event_text=event_text,
            event_date=event_date,
            flow=flow,
            sync_todos=sync_todos,
        )
        return {
            "detail": self.get_project_detail(project_name),
            "log": result.get("log", {}),
            "todo_results": list(result.get("todo_results", []) or []),
            "todo_link_updates": int(result.get("todo_link_updates", 0) or 0),
        }

    def _build_project_summary(self, project_name):
        project = self.repository.get_project(project_name)
        logs = self.repository.collect_project_logs(project_name)
        latest_log = logs[0] if logs else {}
        todos = self.repository.find_project_todos(project_name)
        pending_todos = [todo for todo in todos if not bool((todo or {}).get("完成"))]
        completed_todos = [todo for todo in todos if bool((todo or {}).get("完成"))]
        return ProjectSummary(
            name=str(project_name).strip(),
            owner=str(project.get("负责人", "")).strip(),
            merchandiser=str(project.get("跟单", "")).strip(),
            milestone=str(project.get("Milestone", "")).strip() or "待立项",
            current_stage=self._infer_current_stage(project),
            target=str(project.get("Target", "")).strip() or "TBD",
            ship_window=str(project.get("发货区间", "")).strip(),
            component_count=len(project.get("部件列表", {})) if isinstance(project.get("部件列表", {}), dict) else 0,
            log_count=len(logs),
            latest_log_date=str(latest_log.get("日期", "")).strip(),
            latest_log_event=str(latest_log.get("事件", "")).strip() or "暂无日志",
            pending_todo_count=len(pending_todos),
            completed_todo_count=len(completed_todos),
        )

    def _infer_current_stage(self, project):
        components = project.get("部件列表", {})
        if isinstance(components, dict):
            global_key = next((name for name in components.keys() if "全局" in str(name)), "全局进度")
            global_component = components.get(global_key, {})
            if isinstance(global_component, dict):
                stage_name = str(global_component.get("主流程", "")).strip()
                if stage_name:
                    return stage_name
        return str(project.get("Milestone", "")).strip() or "待立项"

    def _build_component_snapshots(self, project):
        rows = []
        components = project.get("部件列表", {})
        if not isinstance(components, dict):
            return rows
        for component_name, component_info in components.items():
            info = component_info if isinstance(component_info, dict) else {}
            logs = info.get("日志流", []) if isinstance(info.get("日志流", []), list) else []
            latest_date = ""
            if logs:
                latest_row = max(
                    logs,
                    key=lambda row: (
                        (row or {}).get("日期", ""),
                        str((row or {}).get("_id", "")).strip(),
                    ),
                )
                latest_date = str((latest_row or {}).get("日期", "")).strip()
            rows.append(
                ComponentSnapshot(
                    name=str(component_name).strip() or "全局进度",
                    stage=str(info.get("主流程", "")).strip() or "-",
                    log_count=len(logs),
                    latest_log_date=latest_date,
                )
            )
        return sorted(rows, key=lambda row: (row.name != "全局进度", row.name))

    def _build_project_todo_lines(self, project_name):
        todos = self.repository.find_project_todos(project_name)
        if not todos:
            return ["当前没有关联 Todo。"]
        rows = []
        for todo in sorted(
            todos,
            key=lambda row: (
                bool((row or {}).get("完成")),
                str((row or {}).get("DDL", "")).strip(),
                str((row or {}).get("任务", "")).strip(),
            ),
        ):
            alert = todo_alert_text(todo)
            scope = todo_scope_of(todo)
            task = str((todo or {}).get("任务", "")).strip() or "未命名任务"
            due_text = str((todo or {}).get("DDL", "")).strip() or "-"
            rows.append(f"{alert} | {task} | DDL {due_text} | 视角 {scope}")
        return rows
