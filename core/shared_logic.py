from __future__ import annotations

import datetime
import re
from typing import Callable


def norm_text(value):
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def compute_stale_doc_keys(existing_keys, target_keys):
    existing = [str(x).strip() for x in (existing_keys or []) if str(x).strip()]
    target = {str(x).strip() for x in (target_keys or []) if str(x).strip()}
    return [key for key in existing if key not in target]


def parse_date_safe(date_str):
    try:
        return datetime.datetime.strptime(str(date_str), "%Y-%m-%d").date()
    except Exception:
        return None


def todo_cpddl_text(td):
    merged = str((td or {}).get("CPDDL", "")).strip()
    if merged:
        return merged
    cp = str((td or {}).get("CP", "")).strip()
    ddl = str((td or {}).get("DDL", "")).strip()
    if cp and ddl:
        return f"{ddl} | {cp}"
    return cp or ddl


def todo_due_date(td, deadline_extractor: Callable | None = None):
    due = parse_date_safe((td or {}).get("DDL", ""))
    if due:
        return due
    if callable(deadline_extractor):
        try:
            return deadline_extractor(todo_cpddl_text(td))
        except Exception:
            return None
    return None


def todo_alert_text(td, today=None, deadline_extractor: Callable | None = None):
    today = today or datetime.date.today()
    if bool((td or {}).get("完成")):
        return "✅ 已完成"
    due = todo_due_date(td, deadline_extractor=deadline_extractor)
    if not due:
        return "🟣 无DDL"
    diff = (due - today).days
    if diff < 0:
        return f"🔴 已逾期{abs(diff)}天"
    if diff == 0:
        return "🔴 今日到期"
    if diff == 1:
        return "🟧 明日到期"
    if diff <= 3:
        return "🟨 近期待办"
    return "🟢 正常"


def todo_sort_key(td, today=None, deadline_extractor: Callable | None = None):
    today = today or datetime.date.today()
    completed = bool((td or {}).get("完成"))
    due = todo_due_date(td, deadline_extractor=deadline_extractor)
    created = parse_date_safe((td or {}).get("创建", "")) or datetime.date.max
    completed_at = parse_date_safe((td or {}).get("完成时间", "")) or datetime.date.min
    task = str((td or {}).get("任务", "")).strip()
    if completed:
        return (1, 9, -completed_at.toordinal(), created.toordinal(), task)
    if due:
        diff = (due - today).days
        return (0, 0, diff, due.toordinal(), created.toordinal(), task)
    return (0, 1, 99999, datetime.date.max.toordinal(), created.toordinal(), task)


def todo_scope_of(td):
    scope = str((td or {}).get("所属视角", "")).strip()
    if scope and scope != "所有人":
        return scope
    creator_scope = str((td or {}).get("创建者视角", "")).strip()
    if creator_scope and creator_scope != "所有人":
        return creator_scope
    return "未分配"


def todo_visible_for_view(td, pm_view):
    if pm_view == "所有人":
        return todo_scope_of(td) == "未分配"
    return todo_scope_of(td) == pm_view


def todo_visible_for_sidebar(td, pm_view):
    return todo_visible_for_view(td, pm_view)


def normalize_todo_project_list(raw_value, valid_projects=None, alias_map=None, canonicalize=None):
    if isinstance(raw_value, list):
        text = " / ".join([str(x).strip() for x in raw_value if str(x).strip()])
    else:
        text = str(raw_value or "").strip()

    if not text:
        raw_tokens = []
    else:
        marker = "__RATIO_SLASH__"
        text_safe = re.sub(
            r"(?<!\d)(\d{1,2})\s*/\s*(\d{1,2})(?!\d)",
            lambda match: f"{match.group(1)}{marker}{match.group(2)}",
            text,
        )
        raw_tokens = [x.strip() for x in re.split(r"[,\uFF0C;\uFF1B\u3001|/\n]+", text_safe) if x.strip()]
        raw_tokens = [re.sub(r"\s*/\s*", "/", x.replace(marker, "/").strip()) for x in raw_tokens]

    valid_project_list = [str(x).strip() for x in (valid_projects or []) if str(x).strip()]
    alias_lookup = {}
    for raw_key, raw_alias_value in (alias_map or {}).items():
        key = str(raw_key).strip()
        value = str(raw_alias_value).strip()
        if not key or not value:
            continue
        alias_lookup[key] = value
        alias_lookup[norm_text(key)] = value

    out = []
    for token in raw_tokens:
        if token in ["(不关联项目)", "-"]:
            continue
        if re.fullmatch(r"\d{1,2}", token or ""):
            continue

        resolved = token
        if callable(canonicalize):
            try:
                resolved = canonicalize(token, valid_projs=valid_project_list, alias_map=dict(alias_map or {}))
            except TypeError:
                resolved = canonicalize(token)
        else:
            resolved = alias_lookup.get(token) or alias_lookup.get(norm_text(token), token)
            normalized = norm_text(resolved)
            matched = next((name for name in valid_project_list if norm_text(name) == normalized), "")
            if matched:
                resolved = matched

        resolved = str(resolved or "").strip()
        if not resolved or resolved == "系统配置":
            continue
        if valid_project_list and resolved not in valid_project_list:
            normalized = norm_text(resolved)
            matched = next((name for name in valid_project_list if norm_text(name) == normalized), "")
            if matched:
                resolved = matched
        if resolved not in out:
            out.append(resolved)
    return out


def todo_project_list(td_obj, valid_projects=None, alias_map=None, canonicalize=None):
    td = td_obj or {}
    project_list = normalize_todo_project_list(
        td.get("关联项目列表", []),
        valid_projects=valid_projects,
        alias_map=alias_map,
        canonicalize=canonicalize,
    )
    if project_list:
        return project_list
    legacy = str(td.get("关联项目", "")).strip()
    if legacy and legacy not in ["(不关联项目)", "-"]:
        legacy_list = normalize_todo_project_list(
            [legacy],
            valid_projects=valid_projects,
            alias_map=alias_map,
            canonicalize=canonicalize,
        )
        if legacy_list:
            return legacy_list
    return []


def todo_project_text(td_obj, valid_projects=None, alias_map=None, canonicalize=None):
    return " / ".join(
        todo_project_list(
            td_obj,
            valid_projects=valid_projects,
            alias_map=alias_map,
            canonicalize=canonicalize,
        )
    )
