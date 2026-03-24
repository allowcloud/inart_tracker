from __future__ import annotations

import datetime
import re
import uuid

from core.constants import DEFAULT_RECOGNITION_DICT, SYSTEM_CONFIG_KEY, TODO_LIST_KEY
from core.shared_logic import (
    norm_text,
    normalize_todo_project_list,
    todo_cpddl_text,
    todo_project_list,
)


DEFAULT_FUTURE_KEYWORDS = list(DEFAULT_RECOGNITION_DICT.get("未来意图词", []))
DEFAULT_PAST_KEYWORDS = list(DEFAULT_RECOGNITION_DICT.get("过去意图词", []))
DEFAULT_DATE_NOISE = list(DEFAULT_RECOGNITION_DICT.get("日期噪音词", []))


def clean_auto_todo_task_text(raw_text, noise_keywords=None):
    text = str(raw_text or "").strip()
    if not text:
        return ""
    text = re.sub(r"^(?:\[[^\]]+\]\s*){1,6}", "", text).strip()
    text = re.sub(r"\s+", " ", text).strip(" ，,;；|")
    text = re.sub(r"^(预计|计划|大概|约)\s*", "", text)
    text = re.sub(r"\s*(左右|前后)\s*", " ", text)
    text = re.sub(r"(^|[\s，,;；|])(?:CP|DDL)(?=[\s，,;；|]|$)", " ", text, flags=re.I)
    for noise in noise_keywords or DEFAULT_DATE_NOISE:
        if not noise:
            continue
        text = re.sub(rf"(^|[\s，,;；|]){re.escape(str(noise))}(?=[\s，,;；|]|$)", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ，,;；|")
    return text


def extract_deadline_from_text(text, ref_date=None):
    value = str(text or "").strip()
    if not value:
        return None
    ref = ref_date or datetime.date.today()

    full_match = re.search(r"(20\d{2})[-/／\.](\d{1,2})[-/／\.](\d{1,2})", value)
    if full_match:
        try:
            return datetime.date(int(full_match.group(1)), int(full_match.group(2)), int(full_match.group(3)))
        except Exception:
            pass

    md_match = re.search(r"(?<!\d)(\d{1,2})[\/／\-](\d{1,2})(?!\d)", value)
    if md_match:
        try:
            month = int(md_match.group(1))
            day = int(md_match.group(2))
            candidate = datetime.date(ref.year, month, day)
            if candidate < ref - datetime.timedelta(days=30):
                candidate = datetime.date(ref.year + 1, month, day)
            return candidate
        except Exception:
            pass

    cn_match = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})日?", value)
    if cn_match:
        try:
            month = int(cn_match.group(1))
            day = int(cn_match.group(2))
            candidate = datetime.date(ref.year, month, day)
            if candidate < ref - datetime.timedelta(days=30):
                candidate = datetime.date(ref.year + 1, month, day)
            return candidate
        except Exception:
            pass

    if "今天" in value:
        return ref
    if "明天" in value:
        return ref + datetime.timedelta(days=1)
    if "后天" in value:
        return ref + datetime.timedelta(days=2)
    return None


def extract_event_date_and_body(text, ref_date=None, prefer_past=False, noise_keywords=None):
    value = str(text or "").strip()
    if not value:
        return None, value
    ref = ref_date or datetime.date.today()

    def _clean_event_body(raw):
        body = str(raw or "").strip()
        body = re.sub(r"^(?:\[[^\]]+\]\s*){1,6}", "", body).strip()
        body = re.sub(r"\s+", " ", body).strip(" ，,;；|")
        for noise in noise_keywords or DEFAULT_DATE_NOISE:
            if not noise:
                continue
            body = re.sub(rf"(^|\s){re.escape(str(noise))}(?=\s|$)", " ", body)
        body = re.sub(r"\s*(左右|前后)\s*", " ", body)
        body = re.sub(r"^(大约|约)\s*", "", body)
        body = re.sub(r"\s+", " ", body).strip(" ，,;；|")
        return body

    full_patterns = [
        r"(20\d{2})[-/／\.](\d{1,2})[-/／\.](\d{1,2})",
        r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日?",
    ]
    for pattern in full_patterns:
        match = re.search(pattern, value)
        if not match:
            continue
        try:
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            dt_value = datetime.date(year, month, day)
            cleaned = _clean_event_body((value[: match.start()] + " " + value[match.end() :]))
            return dt_value, (cleaned or value)
        except Exception:
            pass

    md_patterns = [
        r"(?<!\d)(\d{1,2})[\/／](\d{1,2})(?!\d)",
        r"(?<!\d)(\d{1,2})-(\d{1,2})(?!\d)",
        r"(?<!\d)(\d{1,2})月(\d{1,2})日?",
    ]
    for pattern in md_patterns:
        match = re.search(pattern, value)
        if not match:
            continue
        try:
            month = int(match.group(1))
            day = int(match.group(2))
            candidate = datetime.date(ref.year, month, day)
            if prefer_past and candidate > ref + datetime.timedelta(days=30):
                candidate = datetime.date(ref.year - 1, month, day)
            if (not prefer_past) and candidate < ref - datetime.timedelta(days=30):
                candidate = datetime.date(ref.year + 1, month, day)
            cleaned = _clean_event_body((value[: match.start()] + " " + value[match.end() :]))
            return candidate, (cleaned or value)
        except Exception:
            pass

    relative_match = re.search(r"(今天|明天|后天)", value)
    if relative_match:
        token = relative_match.group(1)
        dt_value = extract_deadline_from_text(token, ref_date=ref)
        cleaned = _clean_event_body((value[: relative_match.start()] + " " + value[relative_match.end() :]))
        return dt_value, (cleaned or value)

    return None, (_clean_event_body(value) or value)


def classify_text_intent(text, future_keywords=None, past_keywords=None):
    raw_text = str(text or "")
    text_norm = norm_text(raw_text)
    todo_keys = list(future_keywords or DEFAULT_FUTURE_KEYWORDS)
    past_keys = list(past_keywords or DEFAULT_PAST_KEYWORDS)
    todo_score = sum(1 for key in todo_keys if (key in raw_text) or (norm_text(key) in text_norm))
    past_score = sum(1 for key in past_keys if (key in raw_text) or (norm_text(key) in text_norm))
    if todo_score > past_score and todo_score > 0:
        return "todo"
    if past_score > todo_score and past_score > 0:
        return "past"
    if todo_score > 0 and past_score > 0:
        if any(key in raw_text for key in ["待版权", "待审核", "待审", "待反馈", "待确认", "待回复", "待"]):
            return "todo"
        if any(key in raw_text for key in ["已", "已经", "完成", "通过", "提交", "收到", "看过", "on-hand", "done", "ok"]):
            return "past"
        return "past"
    return "neutral"


def classify_temporal_event_route(text, ref_date=None, prefer_past=False, future_keywords=None, past_keywords=None, noise_keywords=None):
    ref = ref_date or datetime.date.today()
    evt_date, cleaned_body = extract_event_date_and_body(text, ref_date=ref, prefer_past=prefer_past, noise_keywords=noise_keywords)
    intent = classify_text_intent(text, future_keywords=future_keywords, past_keywords=past_keywords)
    date_bucket = ""
    if isinstance(evt_date, datetime.date):
        if evt_date > ref:
            date_bucket = "future"
        elif evt_date < ref:
            date_bucket = "past"
        else:
            date_bucket = "today"
    if date_bucket == "future":
        route = "todo"
    elif date_bucket == "past":
        route = "past"
    elif date_bucket == "today":
        route = "past" if intent == "past" else "todo"
    else:
        route = "past" if intent == "past" else ("todo" if intent == "todo" else "neutral")
    return {
        "date": evt_date,
        "body": cleaned_body,
        "intent": intent,
        "date_bucket": date_bucket,
        "route": route,
    }


def extract_followup_todo_clause(text, route_hint=""):
    raw = str(text or "").strip()
    if not raw:
        return ""

    future_tokens = [
        "待确认",
        "待收件",
        "待回件",
        "待反馈",
        "待修改",
        "待补",
        "待回复",
        "待打样",
        "待打印",
        "待审",
        "待版权",
        "待做",
        "待处理",
        "待跟进",
        "待跟催",
        "需要",
        "需",
        "跟进",
        "跟催",
        "预计",
        "即将",
        "待",
    ]
    past_tokens = [
        "已于",
        "已经",
        "已安排",
        "安排了",
        "已转交",
        "已交接",
        "已提交",
        "已收到",
        "送去",
        "转交",
        "交接",
        "发给",
        "交给",
        "收到",
        "完成",
        "通过",
        "给了",
        "给出",
        "先出",
        "已出",
    ]

    matches = []
    for token in future_tokens:
        index = raw.find(token)
        if index > 0:
            matches.append((index, -len(token), token))
    if not matches:
        return ""

    index, _neg_len, _token = sorted(matches)[0]
    prefix = raw[:index]
    if not prefix.strip(" ，,;；|"):
        return ""

    has_past_context = any(token in prefix for token in past_tokens)
    has_clause_break = bool(re.search(r"[，,;；|、]", prefix))
    route_text = str(route_hint or "").strip()
    if (not has_past_context) and not (route_text == "past" and has_clause_break):
        return ""

    tail = raw[index:].strip(" ，,;；|")
    tail = re.sub(r"^[，,;；|、]+", "", tail)
    return tail


def normalize_todo_cpddl_for_storage(cpddl_text, task_text="", due_dt=None):
    raw = str(cpddl_text or "").strip()
    task = clean_auto_todo_task_text(task_text)
    if not raw:
        return f"{due_dt.month}/{due_dt.day}" if isinstance(due_dt, datetime.date) else ""
    due = due_dt if isinstance(due_dt, datetime.date) else extract_deadline_from_text(raw)
    if not due:
        cleaned_raw = clean_auto_todo_task_text(raw)
        if task and (cleaned_raw == task or cleaned_raw in task or task in cleaned_raw):
            return ""
        return cleaned_raw
    body = re.sub(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", " ", raw)
    body = re.sub(r"(?<!\d)(\d{1,2})[\/\-](\d{1,2})(?!\d)", " ", body)
    body = re.sub(r"(?<!\d)(\d{1,2})月(\d{1,2})日?", " ", body)
    body = clean_auto_todo_task_text(body)
    if not body:
        return f"{due.month}/{due.day}"
    if task and (body == task or body in task or task in body):
        return f"{due.month}/{due.day}"
    return f"{due.month}/{due.day} {body}".strip()


def todo_matches_project(td, proj_name, alias_map=None, canonicalize_project_name=None):
    td_obj = td or {}
    project = str(proj_name or "").strip()
    if not project:
        return False

    alias_lookup = alias_map or {}
    if callable(canonicalize_project_name):
        project_canon = str(canonicalize_project_name(project) or "").strip() or project
    else:
        project_canon = str(alias_lookup.get(project) or alias_lookup.get(norm_text(project)) or project).strip()

    ref_projects = todo_project_list(td_obj)
    for ref_proj in ref_projects:
        ref_text = str(ref_proj or "").strip()
        if not ref_text:
            continue
        if callable(canonicalize_project_name):
            ref_canon = str(canonicalize_project_name(ref_text) or "").strip() or ref_text
        else:
            ref_canon = str(alias_lookup.get(ref_text) or alias_lookup.get(norm_text(ref_text)) or ref_text).strip()
        if ref_text in [project, project_canon] or ref_canon in [project, project_canon]:
            return True

    linked_projects = normalize_todo_project_list(
        td_obj.get("最近联动项目", ""),
        alias_map=alias_lookup,
        canonicalize=canonicalize_project_name,
    )
    for linked_proj in linked_projects:
        linked_text = str(linked_proj or "").strip()
        if not linked_text:
            continue
        if callable(canonicalize_project_name):
            linked_canon = str(canonicalize_project_name(linked_text) or "").strip() or linked_text
        else:
            linked_canon = str(alias_lookup.get(linked_text) or alias_lookup.get(norm_text(linked_text)) or linked_text).strip()
        if linked_text in [project, project_canon] or linked_canon in [project, project_canon]:
            return True

    text = f"{str(td_obj.get('任务', '')).strip()} {todo_cpddl_text(td_obj)}".strip()
    text_norm = norm_text(text)

    candidates = {project, project_canon}

    def _add_project_forms(name):
        item = str(name or "").strip()
        if not item:
            return
        candidates.add(item)
        short = re.sub(r"^(1/6|1/4|1/12|1/3|1/1)\s*", "", item).strip()
        if short:
            candidates.add(short)

    _add_project_forms(project)
    _add_project_forms(project_canon)

    for alias_name in alias_lookup.keys():
        alias_text = str(alias_name or "").strip()
        if not alias_text:
            continue
        alias_canon = str(alias_lookup.get(alias_text) or alias_lookup.get(norm_text(alias_text)) or "").strip()
        if alias_canon in [project, project_canon]:
            _add_project_forms(alias_text)

    for token in candidates:
        item = str(token or "").strip()
        if not item:
            continue
        if item in text:
            return True
        item_norm = norm_text(item)
        if item_norm and item_norm in text_norm:
            return True
    return False


def refresh_project_todo_links(db_obj, proj_name, alias_map=None, canonicalize_project_name=None):
    if not isinstance(db_obj, dict):
        return 0
    project = str(proj_name or "").strip()
    if not project or project == SYSTEM_CONFIG_KEY or project not in db_obj:
        return 0

    todo_all = db_obj.get(SYSTEM_CONFIG_KEY, {}).get(TODO_LIST_KEY, [])
    todo_items = [
        td
        for td in todo_all
        if todo_matches_project(td, project, alias_map=alias_map, canonicalize_project_name=canonicalize_project_name)
        and str((td or {}).get("任务", "")).strip()
    ]
    if not todo_items:
        return 0

    logs = []
    for comp_name, comp_info in db_obj.get(project, {}).get("部件列表", {}).items():
        component_info = comp_info if isinstance(comp_info, dict) else {}
        for log_row in component_info.get("日志流", []):
            log_obj = log_row if isinstance(log_row, dict) else {}
            event = str(log_obj.get("事件", "")).strip()
            if not event:
                continue
            date_text = str(log_obj.get("日期", "")).strip()
            try:
                date_obj = datetime.datetime.strptime(date_text, "%Y-%m-%d").date()
            except Exception:
                date_obj = datetime.date.min
            logs.append(
                {
                    "dt": date_obj,
                    "date": date_text,
                    "component": str(comp_name),
                    "stage": str(log_obj.get("工序", "")).strip(),
                    "event": event,
                    "event_norm": norm_text(event),
                }
            )

    if not logs:
        return 0

    logs.sort(key=lambda item: (item.get("dt") or datetime.date.min, item.get("date", ""), item.get("component", "")), reverse=True)
    write_ts = datetime.datetime.now().isoformat(timespec="seconds")
    updated = 0

    for todo_obj in todo_items:
        task = str((todo_obj or {}).get("任务", "")).strip()
        task_norm = norm_text(task)
        if len(task_norm) < 2:
            continue

        short_task = task[:8].strip()
        short_norm = norm_text(short_task)
        hit = None
        for item in logs:
            event = item["event"]
            event_norm = item["event_norm"]
            matched = False
            if ("[关联To do]" in event or "[关联待办]" in event) and (task in event or (task_norm and task_norm in event_norm)):
                matched = True
            elif len(task_norm) >= 4 and task_norm in event_norm:
                matched = True
            elif short_norm and len(short_norm) >= 4 and short_norm in event_norm:
                matched = True
            if matched:
                hit = item
                break

        if not hit:
            continue

        current_dt = None
        try:
            current_dt = datetime.datetime.strptime(str(todo_obj.get("最近联动日期", "")).strip(), "%Y-%m-%d").date()
        except Exception:
            current_dt = None
        hit_dt = hit.get("dt") if hit.get("dt") != datetime.date.min else None
        if current_dt and hit_dt and hit_dt < current_dt:
            continue

        desired = {
            "最近联动模块": "日志联动回填",
            "最近联动日期": hit.get("date", ""),
            "最近联动项目": project,
            "最近联动部件": hit.get("component", ""),
            "最近联动阶段": hit.get("stage", ""),
            "最近联动写入时间": write_ts,
        }
        changed = False
        for key, value in desired.items():
            if str(todo_obj.get(key, "")) != str(value):
                todo_obj[key] = value
                changed = True
        if changed:
            updated += 1
    return updated


def infer_todo_segments_from_log_text(text, ref_date=None):
    raw_text = str(text or "").strip()
    if not raw_text:
        return []

    ref = ref_date or datetime.date.today()
    segments = [item.strip() for item in re.split(r"[;；\n]+", raw_text) if item.strip()]
    if not segments:
        segments = [raw_text]

    out = []
    for segment in segments:
        route_info = classify_temporal_event_route(segment, ref_date=ref, prefer_past=False)
        route = str(route_info.get("route", "")).strip() or "neutral"
        due_dt, body_without_date = extract_event_date_and_body(segment, ref_date=ref, prefer_past=False)
        task_seed = body_without_date or segment
        followup_clause = extract_followup_todo_clause(task_seed, route_hint=route)
        if followup_clause:
            follow_due_dt, follow_body_without_date = extract_event_date_and_body(followup_clause, ref_date=ref, prefer_past=False)
            task_seed = follow_body_without_date or followup_clause
            if isinstance(follow_due_dt, datetime.date):
                due_dt = follow_due_dt
            elif route == "past":
                due_dt = None
            route = "todo"

        task = clean_auto_todo_task_text(task_seed)
        if not task:
            continue
        if route != "todo":
            continue

        out.append(
            {
                "raw": segment,
                "task": task,
                "due_dt": due_dt if isinstance(due_dt, datetime.date) else None,
                "allow_empty_due": True,
            }
        )
    return out


def upsert_project_todos_from_log(
    db_obj,
    project_name,
    event_text,
    event_date=None,
    people_text="",
    scope_override="",
    component_name="",
    stage_name="",
    alias_map=None,
    canonicalize_project_name=None,
):
    if not isinstance(db_obj, dict):
        return []

    project = str(project_name or "").strip()
    if (not project) or project == SYSTEM_CONFIG_KEY or project not in db_obj:
        return []

    ref_date = event_date if isinstance(event_date, datetime.date) else datetime.date.today()
    segments = infer_todo_segments_from_log_text(event_text, ref_date=ref_date)
    if not segments:
        return []

    cfg = db_obj.setdefault(SYSTEM_CONFIG_KEY, {})
    todo_all = cfg.setdefault(TODO_LIST_KEY, [])
    project_data = db_obj.get(project, {})
    owner = str(project_data.get("负责人", "")).strip()
    people_seed = str(people_text or "").strip() or str(project_data.get("跟单", "")).strip()
    scope = str(scope_override or "").strip() or (owner if owner and owner != "所有人" else "未分配")
    component_text = str(component_name or "").strip()
    stage_text = str(stage_name or "").strip()
    alias_lookup = alias_map or {}
    results = []

    for segment in segments:
        task = str(segment.get("task", "")).strip()
        if not task:
            continue
        due_dt = segment.get("due_dt") if isinstance(segment.get("due_dt"), datetime.date) else None
        due_text = str(due_dt) if isinstance(due_dt, datetime.date) else ""
        cpddl_seed = f"{due_dt.month}/{due_dt.day} {task}" if isinstance(due_dt, datetime.date) else task
        cpddl_text = normalize_todo_cpddl_for_storage(cpddl_seed, task_text=task, due_dt=due_dt)
        task_norm = norm_text(task)

        hit = None
        for todo_obj in todo_all:
            if bool((todo_obj or {}).get("完成")):
                continue
            if not todo_matches_project(todo_obj, project, alias_map=alias_lookup, canonicalize_project_name=canonicalize_project_name):
                continue
            if norm_text(str((todo_obj or {}).get("任务", "")).strip()) == task_norm:
                hit = todo_obj
                break

        status = "created"
        if hit is not None:
            status = "exists"
            changed = False
            old_projects = todo_project_list(hit)
            new_projects = list(dict.fromkeys(old_projects + [project]))
            desired = {
                "任务": task,
                "DDL": due_text,
                "CPDDL": cpddl_text,
                "CP": cpddl_text,
                "关联人员": str(hit.get("关联人员", "")).strip() or people_seed,
                "默认落地部件": component_text,
                "默认落地阶段": stage_text,
                "关联项目列表": new_projects,
                "关联项目": new_projects[0] if new_projects else project,
            }
            for key, value in desired.items():
                if value == "" and key in ["默认落地部件", "默认落地阶段"]:
                    continue
                if key == "关联人员" and not str(value).strip():
                    continue
                if key == "关联项目列表":
                    if desired[key] != old_projects:
                        hit[key] = value
                        changed = True
                    continue
                if str(hit.get(key, "")) != str(value):
                    hit[key] = value
                    changed = True
            if changed:
                status = "updated"
            todo_obj = hit
        else:
            todo_obj = {
                "_id": str(uuid.uuid4()),
                "任务": task,
                "CPDDL": cpddl_text,
                "CP": cpddl_text,
                "DDL": due_text,
                "完成": False,
                "关联项目": project,
                "关联项目列表": [project],
                "关联人员": people_seed,
                "所属视角": scope,
                "创建者视角": scope,
                "创建": str(ref_date),
                "完成时间": "",
                "历史版本": [],
                "默认落地部件": component_text,
                "默认落地阶段": stage_text,
            }
            todo_all.append(todo_obj)

        results.append(
            {
                "status": status,
                "todo_id": str(todo_obj.get("_id", "")).strip(),
                "task": task,
            }
        )

    return results
