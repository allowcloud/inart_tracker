from __future__ import annotations

import datetime
import re
import uuid

from core.constants import PROJECT_RATIO_OPTIONS, SYSTEM_CONFIG_KEY


def is_pause_stage(stage_name):
    stage_text = str(stage_name or "").strip()
    return ("暂停" in stage_text) or ("搁置" in stage_text)


def get_stage_index(stage_name, stages):
    stage_text = str(stage_name or "").strip()
    stage_list = [str(x).strip() for x in (stages or []) if str(x).strip()]
    if stage_text in stage_list:
        return stage_list.index(stage_text)
    return next((index for index, std_stage in enumerate(stage_list) if stage_text in std_stage or std_stage in stage_text), -1)


def build_project_shell(db_obj=None, owner_name="", ratio_preset="", ip_owner=""):
    cfg = db_obj.get(SYSTEM_CONFIG_KEY, {}) if isinstance(db_obj, dict) else {}
    tpl = cfg.get("PROJECT_TEMPLATE", {}) if isinstance(cfg, dict) else {}
    ratio_opts = tpl.get("ratio_options", PROJECT_RATIO_OPTIONS)
    if not isinstance(ratio_opts, list) or not ratio_opts:
        ratio_opts = PROJECT_RATIO_OPTIONS

    owner = str(owner_name or "").strip() or "Mo"
    ratio = str(ratio_preset or "").strip() or str(tpl.get("default_ratio", "1/6")).strip() or "1/6"
    if ratio not in ratio_opts:
        ratio = ratio_opts[0]
    ip_text = str(ip_owner or "").strip() or str(tpl.get("default_ip_owner", "")).strip()

    return {
        "负责人": owner,
        "跟单": "",
        "Milestone": "待立项",
        "Target": "TBD",
        "发货区间": "",
        "ratio_preset": ratio,
        "ip_owner": ip_text,
        "计划排期": [],
        "周会备注": [],
        "部件列表": {},
        "发货数据": {},
        "成本数据": {},
        "print_tracking": [],
        "garment_flow": {"stage": "Follow Global", "records": []},
        "包装专项": {},
        "备忘录": "",
    }


def ensure_project_component(
    db_obj,
    proj_name,
    comp_name,
    default_stage=None,
    stages=None,
    canonicalize_project_name=None,
    project_factory=None,
):
    if not isinstance(db_obj, dict):
        return {}

    project_name = str(proj_name or "").strip()
    component_name = str(comp_name or "").strip() or "全局进度"
    if (not project_name) or project_name == SYSTEM_CONFIG_KEY:
        return {}

    if callable(canonicalize_project_name):
        canonical = str(canonicalize_project_name(project_name) or "").strip()
        if canonical:
            project_name = canonical

    if project_name not in db_obj or not isinstance(db_obj.get(project_name), dict):
        if callable(project_factory):
            db_obj[project_name] = project_factory()
        else:
            db_obj[project_name] = build_project_shell(db_obj=db_obj)

    project_data = db_obj.setdefault(project_name, {})
    comp_map = project_data.setdefault("部件列表", {})
    if not isinstance(comp_map, dict):
        comp_map = {}
        project_data["部件列表"] = comp_map

    if component_name not in comp_map or not isinstance(comp_map.get(component_name), dict):
        stage_seed = str(default_stage or "").strip()
        if not stage_seed:
            stage_list = [str(x).strip() for x in (stages or []) if str(x).strip()]
            stage_seed = stage_list[0] if stage_list else "立项"
        comp_map[component_name] = {"主流程": stage_seed, "日志流": []}
    return comp_map[component_name]


def normalize_component_log_entry(log_entry, fallback_date=None, write_time=""):
    entry = dict(log_entry) if isinstance(log_entry, dict) else {}
    date_text = str(entry.get("日期", "")).strip()
    if (not date_text) and fallback_date:
        date_text = str(fallback_date)
        entry["日期"] = date_text
    if not str(entry.get("_id", "")).strip():
        entry["_id"] = uuid.uuid4().hex[:16]
    write_ts = str(entry.get("写入时间", "")).strip() or str(write_time or "").strip()
    if not write_ts:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
            write_ts = f"{date_text}T00:00:00"
        else:
            write_ts = datetime.datetime.now().isoformat(timespec="seconds")
    entry["写入时间"] = write_ts
    return entry


def append_component_log_entry(
    db_obj,
    proj_name,
    comp_name,
    log_entry,
    resulting_stage=None,
    default_stage=None,
    stages=None,
    canonicalize_project_name=None,
    project_factory=None,
):
    comp_data = ensure_project_component(
        db_obj,
        proj_name,
        comp_name,
        default_stage=default_stage,
        stages=stages,
        canonicalize_project_name=canonicalize_project_name,
        project_factory=project_factory,
    )
    if not isinstance(comp_data, dict):
        return {}
    comp_data.setdefault("日志流", []).append(normalize_component_log_entry(log_entry))
    if resulting_stage:
        comp_data["主流程"] = str(resulting_stage).strip()
    return comp_data


def auto_sync_milestone(project_data, stages):
    proj_data = project_data if isinstance(project_data, dict) else {}
    comps = proj_data.get("部件列表", {})
    if not isinstance(comps, dict):
        return

    stage_list = [str(x).strip() for x in (stages or []) if str(x).strip()]
    non_global_items = []
    for comp_name, info in comps.items():
        if "全局" in str(comp_name):
            continue
        if isinstance(info, dict):
            non_global_items.append((comp_name, info))

    max_idx = -1
    max_stage = ""
    for _, info in non_global_items:
        stage = str(info.get("主流程", "")).strip()
        if not stage or is_pause_stage(stage):
            continue
        stage_idx = get_stage_index(stage, stage_list)
        if stage_idx > max_idx and stage_idx < len(stage_list):
            max_idx = stage_idx
            max_stage = stage_list[stage_idx]

    if max_idx >= 0 and max_stage:
        global_key = next((key for key in comps.keys() if "全局" in str(key)), "全局进度")
        if global_key not in comps or not isinstance(comps.get(global_key), dict):
            default_stage = stage_list[0] if stage_list else "立项"
            comps[global_key] = {"主流程": default_stage, "日志流": []}
        current_global_stage = str(comps[global_key].get("主流程", "")).strip()
        current_idx = get_stage_index(current_global_stage, stage_list)
        if current_idx < max_idx and not is_pause_stage(current_global_stage):
            comps[global_key]["主流程"] = max_stage

    stages_for_milestone = [str(info.get("主流程", "")).strip() for _, info in non_global_items if str(info.get("主流程", "")).strip()]
    if not stages_for_milestone:
        global_key = next((key for key in comps.keys() if "全局" in str(key)), "全局进度")
        global_stage = str(comps.get(global_key, {}).get("主流程", "")).strip()
        if global_stage:
            stages_for_milestone = [global_stage]

    current_milestone = str(proj_data.get("Milestone", "")).strip()
    if stages_for_milestone and all(stage == "✅ 已完成(结束)" for stage in stages_for_milestone):
        proj_data["Milestone"] = "项目结束撒花🎉"
    elif any(stage in ["工厂复样(含胶件/上色等)", "大货"] for stage in stages_for_milestone):
        if current_milestone not in ["生产结束", "项目结束撒花🎉", "暂停研发"]:
            proj_data["Milestone"] = "生产中"
    elif any(stage == "开模" for stage in stages_for_milestone):
        if current_milestone not in ["生产结束", "项目结束撒花🎉", "暂停研发", "生产中"]:
            proj_data["Milestone"] = "下模中"
    elif any(stage in ["建模(含打印/签样)", "涂装", "设计", "工程拆件", "手板/结构板", "官图"] for stage in stages_for_milestone):
        if current_milestone in ["", "待立项"]:
            proj_data["Milestone"] = "研发中"
