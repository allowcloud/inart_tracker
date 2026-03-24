from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.repository import TrackerRepository
from services.desktop_service import TrackerDesktopService


def build_arg_parser():
    parser = argparse.ArgumentParser(description="INART PM Desktop Preview")
    parser.add_argument(
        "--data-file",
        default="tracker_data_web_v20.json",
        help="Path to the tracker JSON file.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a quick data summary without starting the GUI.",
    )
    parser.add_argument(
        "--auto-close-ms",
        type=int,
        default=0,
        help="Automatically close the GUI after the given milliseconds.",
    )
    return parser


def print_summary(service: TrackerDesktopService, repository: TrackerRepository):
    stats = service.dashboard_stats()
    print(f"backend={repository.backend_name}")
    print(f"data_file={repository.data_path or Path('tracker_data_web_v20.json').resolve()}")
    print(f"projects={stats.project_count}")
    print(f"projects_with_logs={stats.projects_with_logs}")
    print(f"todos_total={stats.todo_total}")
    first_items = service.list_project_summaries()[:5]
    for item in first_items:
        print(f"- {item.name} | {item.current_stage} | logs={item.log_count}")


def launch_qt_window(service: TrackerDesktopService, repository: TrackerRepository, auto_close_ms=0):
    try:
        from PySide6.QtCore import QDate, QTimer, Qt
        from PySide6.QtWidgets import (
            QApplication,
            QAbstractItemView,
            QCheckBox,
            QComboBox,
            QDateEdit,
            QFormLayout,
            QGroupBox,
            QHBoxLayout,
            QHeaderView,
            QLabel,
            QLineEdit,
            QListWidget,
            QListWidgetItem,
            QMainWindow,
            QMessageBox,
            QPushButton,
            QSplitter,
            QStatusBar,
            QTableWidget,
            QTableWidgetItem,
            QTextEdit,
            QVBoxLayout,
            QWidget,
        )
    except ImportError as exc:
        raise RuntimeError("PySide6 is not installed. Run `python -m pip install PySide6` first.") from exc

    class MainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.service = service
            self.repository = repository
            self.selected_project_name = ""
            self.setWindowTitle("INART PM Desktop Preview")
            self.resize(1500, 960)
            self._build_ui()
            self.reload_data()

        def _build_ui(self):
            central = QWidget(self)
            self.setCentralWidget(central)
            root_layout = QVBoxLayout(central)

            header_layout = QHBoxLayout()
            self.stats_label = QLabel("Loading...")
            self.stats_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.source_label = QLabel("")
            self.source_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            refresh_button = QPushButton("Refresh")
            refresh_button.clicked.connect(self.reload_data)
            header_layout.addWidget(self.stats_label, 1)
            header_layout.addWidget(self.source_label)
            header_layout.addWidget(refresh_button)
            root_layout.addLayout(header_layout)

            splitter = QSplitter(Qt.Horizontal)
            root_layout.addWidget(splitter, 1)

            left_panel = QWidget()
            left_layout = QVBoxLayout(left_panel)
            self.search_box = QLineEdit()
            self.search_box.setPlaceholderText("Search project / owner / stage")
            self.search_box.textChanged.connect(self.refresh_project_list)
            self.project_list = QListWidget()
            self.project_list.currentRowChanged.connect(self.show_selected_project)
            left_layout.addWidget(self.search_box)
            left_layout.addWidget(self.project_list, 1)
            splitter.addWidget(left_panel)

            right_panel = QWidget()
            right_layout = QVBoxLayout(right_panel)

            summary_group = QGroupBox("Project Detail")
            summary_form = QFormLayout(summary_group)
            self.project_name_label = QLabel("-")
            self.project_name_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.owner_label = QLabel("-")
            self.owner_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.merchandiser_label = QLabel("-")
            self.merchandiser_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.milestone_label = QLabel("-")
            self.stage_label = QLabel("-")
            self.target_label = QLabel("-")
            self.ship_label = QLabel("-")
            self.log_meta_label = QLabel("-")
            self.todo_meta_label = QLabel("-")
            self.latest_event_label = QLabel("-")
            self.latest_event_label.setWordWrap(True)
            self.latest_event_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            summary_form.addRow("Project", self.project_name_label)
            summary_form.addRow("Owner", self.owner_label)
            summary_form.addRow("Merchandiser", self.merchandiser_label)
            summary_form.addRow("Milestone", self.milestone_label)
            summary_form.addRow("Current Stage", self.stage_label)
            summary_form.addRow("Target", self.target_label)
            summary_form.addRow("Ship Window", self.ship_label)
            summary_form.addRow("Log Stats", self.log_meta_label)
            summary_form.addRow("Todo Stats", self.todo_meta_label)
            summary_form.addRow("Latest Event", self.latest_event_label)
            right_layout.addWidget(summary_group)

            entry_group = QGroupBox("PM Workspace: Add Log")
            entry_form = QFormLayout(entry_group)
            self.entry_date_edit = QDateEdit()
            self.entry_date_edit.setCalendarPopup(True)
            self.entry_date_edit.setDisplayFormat("yyyy-MM-dd")
            self.entry_date_edit.setDate(QDate.currentDate())
            self.entry_component_combo = QComboBox()
            self.entry_component_combo.setEditable(True)
            self.entry_stage_combo = QComboBox()
            self.entry_event_box = QTextEdit()
            self.entry_event_box.setPlaceholderText("Write the latest real project update here.")
            self.entry_event_box.setFixedHeight(110)
            self.entry_sync_todo_check = QCheckBox("Auto-create/update todo from future-action text")
            self.entry_sync_todo_check.setChecked(True)
            self.entry_save_button = QPushButton("Save Log")
            self.entry_save_button.clicked.connect(self.save_log_entry)
            entry_form.addRow("Date", self.entry_date_edit)
            entry_form.addRow("Component", self.entry_component_combo)
            entry_form.addRow("Stage", self.entry_stage_combo)
            entry_form.addRow("Event", self.entry_event_box)
            entry_form.addRow("", self.entry_sync_todo_check)
            entry_form.addRow("", self.entry_save_button)
            right_layout.addWidget(entry_group)

            component_group = QGroupBox("Component Snapshot")
            component_layout = QVBoxLayout(component_group)
            self.component_table = QTableWidget(0, 4)
            self.component_table.setHorizontalHeaderLabels(["Component", "Stage", "Log Count", "Latest Date"])
            self.component_table.verticalHeader().setVisible(False)
            self.component_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.component_table.setSelectionMode(QAbstractItemView.NoSelection)
            self.component_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            self.component_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
            self.component_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
            self.component_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
            component_layout.addWidget(self.component_table)
            right_layout.addWidget(component_group, 1)

            logs_group = QGroupBox("Recent Logs")
            logs_layout = QVBoxLayout(logs_group)
            self.logs_table = QTableWidget(0, 4)
            self.logs_table.setHorizontalHeaderLabels(["Date", "Component", "Stage", "Event"])
            self.logs_table.verticalHeader().setVisible(False)
            self.logs_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.logs_table.setSelectionMode(QAbstractItemView.NoSelection)
            self.logs_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
            self.logs_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
            self.logs_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
            self.logs_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
            logs_layout.addWidget(self.logs_table)
            right_layout.addWidget(logs_group, 1)

            bottom_split = QSplitter(Qt.Horizontal)

            todo_group = QGroupBox("Todo Overview")
            todo_layout = QVBoxLayout(todo_group)
            self.todo_list = QListWidget()
            todo_layout.addWidget(self.todo_list)
            bottom_split.addWidget(todo_group)

            note_group = QGroupBox("Project Notes")
            note_layout = QVBoxLayout(note_group)
            self.note_box = QTextEdit()
            self.note_box.setReadOnly(True)
            note_layout.addWidget(self.note_box)
            bottom_split.addWidget(note_group)

            right_layout.addWidget(bottom_split, 1)

            splitter.addWidget(right_panel)
            splitter.setStretchFactor(0, 2)
            splitter.setStretchFactor(1, 5)

            status_bar = QStatusBar(self)
            self.setStatusBar(status_bar)
            self.entry_save_button.setEnabled(False)

        def reload_data(self):
            try:
                self.service.refresh()
            except Exception as exc:
                QMessageBox.critical(self, "Load Failed", str(exc))
                return

            stats = self.service.dashboard_stats()
            data_path = self.repository.data_path or Path("tracker_data_web_v20.json").resolve()
            self.stats_label.setText(
                f"Projects {stats.project_count} | With Logs {stats.projects_with_logs} | Todos {stats.todo_total} | Pending {stats.todo_pending} | Overdue {stats.todo_overdue}"
            )
            self.source_label.setText(f"{self.repository.backend_name} | {data_path}")
            self.refresh_project_list()
            self.statusBar().showMessage("Desktop preview loaded real project data.", 5000)

        def refresh_project_list(self):
            current_name = self.selected_project_name
            self.project_list.blockSignals(True)
            self.project_list.clear()
            self.project_list.blockSignals(False)

            filtered = self.service.list_project_summaries(self.search_box.text())
            for summary in filtered:
                item_text = f"{summary.name} | {summary.current_stage} | {summary.owner or 'Unassigned'} | log {summary.log_count}"
                item = QListWidgetItem(item_text)
                item.setData(Qt.UserRole, summary.name)
                self.project_list.addItem(item)

            if self.project_list.count() == 0:
                self.selected_project_name = ""
                self.clear_detail()
                return

            selected_row = 0
            for row_index in range(self.project_list.count()):
                if self.project_list.item(row_index).data(Qt.UserRole) == current_name:
                    selected_row = row_index
                    break
            self.project_list.setCurrentRow(selected_row)

        def show_selected_project(self, _row):
            current_item = self.project_list.currentItem()
            if current_item is None:
                self.clear_detail()
                return
            project_name = str(current_item.data(Qt.UserRole) or "").strip()
            if not project_name:
                self.clear_detail()
                return

            self.selected_project_name = project_name
            detail = self.service.get_project_detail(project_name)
            summary = detail.summary
            self.project_name_label.setText(summary.name)
            self.owner_label.setText(summary.owner or "-")
            self.merchandiser_label.setText(summary.merchandiser or "-")
            self.milestone_label.setText(summary.milestone or "-")
            self.stage_label.setText(summary.current_stage or "-")
            self.target_label.setText(summary.target or "TBD")
            self.ship_label.setText(summary.ship_window or "-")
            self.log_meta_label.setText(f"{summary.log_count} logs / {summary.component_count} components")
            self.todo_meta_label.setText(f"Pending {summary.pending_todo_count} / Done {summary.completed_todo_count}")
            self.latest_event_label.setText(summary.latest_log_event or "No logs yet.")
            self.note_box.setPlainText(detail.note or "No project notes yet.")

            self._fill_component_table(detail)
            self._fill_logs_table(detail)
            self._fill_todo_list(detail)
            self._refresh_entry_form(detail)

        def save_log_entry(self):
            project_name = str(self.selected_project_name or "").strip()
            if not project_name:
                QMessageBox.warning(self, "No Project", "Select a project before saving a log.")
                return

            event_text = self.entry_event_box.toPlainText().strip()
            if not event_text:
                QMessageBox.warning(self, "Empty Event", "Log event text cannot be empty.")
                return

            component_name = self.entry_component_combo.currentText().strip() or "全局进度"
            stage_name = self.entry_stage_combo.currentText().strip()
            qdate = self.entry_date_edit.date()
            if hasattr(qdate, "toPython"):
                event_date = qdate.toPython()
            else:
                event_date = datetime.date(qdate.year(), qdate.month(), qdate.day())

            try:
                result = self.service.add_project_log(
                    project_name=project_name,
                    component_name=component_name,
                    stage_name=stage_name,
                    event_text=event_text,
                    event_date=event_date,
                    flow="桌面工作台",
                    sync_todos=self.entry_sync_todo_check.isChecked(),
                )
            except Exception as exc:
                QMessageBox.critical(self, "Save Failed", str(exc))
                return

            self.entry_event_box.clear()
            self.reload_data()
            todo_results = list(result.get("todo_results", []) or [])
            created = sum(1 for item in todo_results if str(item.get("status", "")).strip() == "created")
            updated = sum(1 for item in todo_results if str(item.get("status", "")).strip() == "updated")
            linked = int(result.get("todo_link_updates", 0) or 0)
            extra = ""
            if self.entry_sync_todo_check.isChecked():
                extra = f" Todo created {created}, updated {updated}, linked {linked}."
            self.statusBar().showMessage(f"Saved new log for {project_name}.{extra}", 5000)

        def clear_detail(self):
            for label in [
                self.project_name_label,
                self.owner_label,
                self.merchandiser_label,
                self.milestone_label,
                self.stage_label,
                self.target_label,
                self.ship_label,
                self.log_meta_label,
                self.todo_meta_label,
                self.latest_event_label,
            ]:
                label.setText("-")
            self.component_table.setRowCount(0)
            self.logs_table.setRowCount(0)
            self.todo_list.clear()
            self.note_box.setPlainText("")
            self.entry_component_combo.clear()
            self.entry_stage_combo.clear()
            self.entry_event_box.clear()
            self.entry_save_button.setEnabled(False)

        def _refresh_entry_form(self, detail):
            project_name = detail.summary.name
            component_options = self.service.component_options(project_name)
            stage_options = self.service.stage_options()
            current_component = self.entry_component_combo.currentText().strip()
            current_stage = detail.summary.current_stage or self.entry_stage_combo.currentText().strip()

            self.entry_component_combo.blockSignals(True)
            self.entry_component_combo.clear()
            self.entry_component_combo.addItems(component_options)
            if current_component and current_component not in component_options:
                self.entry_component_combo.addItem(current_component)
            if current_component:
                self.entry_component_combo.setCurrentText(current_component)
            elif "全局进度" in component_options:
                self.entry_component_combo.setCurrentText("全局进度")
            elif component_options:
                self.entry_component_combo.setCurrentIndex(0)
            self.entry_component_combo.blockSignals(False)

            self.entry_stage_combo.blockSignals(True)
            self.entry_stage_combo.clear()
            self.entry_stage_combo.addItems(stage_options)
            if current_stage and current_stage in stage_options:
                self.entry_stage_combo.setCurrentText(current_stage)
            elif stage_options:
                self.entry_stage_combo.setCurrentIndex(0)
            self.entry_stage_combo.blockSignals(False)

            self.entry_date_edit.setDate(QDate.currentDate())
            self.entry_save_button.setEnabled(True)

        def _fill_component_table(self, detail):
            self.component_table.setRowCount(len(detail.components))
            for row_index, component in enumerate(detail.components):
                values = [
                    component.name,
                    component.stage,
                    str(component.log_count),
                    component.latest_log_date or "-",
                ]
                for col_index, value in enumerate(values):
                    self.component_table.setItem(row_index, col_index, QTableWidgetItem(value))

        def _fill_logs_table(self, detail):
            self.logs_table.setRowCount(len(detail.recent_logs))
            for row_index, log_row in enumerate(detail.recent_logs):
                values = [log_row.date or "-", log_row.component or "-", log_row.stage or "-", log_row.event or "-"]
                for col_index, value in enumerate(values):
                    self.logs_table.setItem(row_index, col_index, QTableWidgetItem(value))

        def _fill_todo_list(self, detail):
            self.todo_list.clear()
            for line in detail.todo_lines:
                self.todo_list.addItem(line)

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.show()
    if isinstance(auto_close_ms, int) and auto_close_ms > 0:
        QTimer.singleShot(auto_close_ms, app.quit)
    return app.exec()


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    repository = TrackerRepository.from_default_storage(data_path=args.data_file)
    service = TrackerDesktopService(repository)

    if args.summary:
        print_summary(service, repository)
        return 0

    try:
        return launch_qt_window(service, repository, auto_close_ms=args.auto_close_ms)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
