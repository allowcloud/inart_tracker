from __future__ import annotations

import json


SYSTEM_CONFIG_KEY = "系统配置"
TODO_LIST_KEY = "PM_TODO_LIST"
STANDARD_EVENTS_KEY = "标准事件流"
PROJECT_RATIO_OPTIONS = ["1/1", "1/3", "1/4", "1/6", "1/12"]
PRINT_TRACK_LOCATION_DEFAULTS = ["内部", "博泰", "逸博", "小样儿"]

DEFAULT_RECOGNITION_DICT = {
    "未来意图词": [
        "待",
        "待办",
        "需要",
        "需",
        "跟进",
        "跟催",
        "补",
        "安排",
        "todo",
        "to do",
        "ddl",
        "cp",
        "待审",
        "待反馈",
        "待版权",
        "预计",
        "预计出",
        "预计给出",
        "即将",
    ],
    "过去意图词": [
        "已",
        "已经",
        "完成",
        "完毕",
        "通过",
        "收到",
        "确认了",
        "确认完成",
        "看过",
        "on-hand",
        "on hand",
        "done",
        "ok",
        "提交",
        "已提交",
        "已提审",
        "提审通过",
        "已发",
    ],
    "恢复推进词": ["resume", "恢复", "重启", "继续推进", "解除暂停", "复工"],
    "暂停信号词": [
        "暂停",
        "搁置",
        "冻结",
        "挂起",
        "叫停",
        "停止",
        "暂停研发",
        "项目暂停",
        "项目取消",
        "确认取消",
        "先停",
        "先暂停",
        "停做",
        "停一下",
    ],
    "打印正向词": ["打印", "开打", "安排打", "去打", "送打", "打件", "打印件", "打样件", "博泰", "逸博", "小样儿"],
    "打印排除词": ["打印件已收到"],
    "日期噪音词": ["预计", "左右", "大概", "约", "计划", "可", "能", "初版"],
}

DEFAULT_PROJECT_FIELDS = {
    "负责人": "",
    "跟单": "",
    "Milestone": "待立项",
    "Target": "TBD",
    "发货区间": "",
    "ratio_preset": "1/6",
    "ip_owner": "",
    "计划排期": [],
    "周会备注": [],
    "部件列表": {},
    "发货数据": {},
    "成本数据": {},
    "print_tracking": [],
    "garment_flow": {},
    "包装专项": {},
    "备忘录": "",
}

DEFAULT_SYS_CFG = {
    "标准部件": ["头雕(表情)", "素体", "手型", "服装", "配件", "地台", "包装"],
    "标准阶段": ["预研", "立项", "建模(含打印/签样)", "涂装", "设计", "工程拆件", "手板/结构板", "官图", "工厂复样(含胶件/上色等)", "大货", "⏸️ 暂停/搁置", "✅ 已完成(结束)"],
    "宏观阶段": ["预研", "立项", "建模", "打印", "涂装", "设计", "工程", "模具", "修模", "生产", "暂停", "结束"],
    "排期基线": {"预研": 14, "立项": 1, "建模": 42, "打印": 14, "涂装": 14, "设计": 35, "工程": 49, "模具": 28, "修模": 14, "生产": 30},
    "项目别名": {},
    "AI_COMP_KW": {},
    "AI_STAGE_KW": {},
    "AI_SPLIT_COMP_KW": {},
    "人员别名": {},
    "识别词典": json.loads(json.dumps(DEFAULT_RECOGNITION_DICT, ensure_ascii=False)),
    "打印追踪列表": [],
    "打印地点选项": PRINT_TRACK_LOCATION_DEFAULTS.copy(),
    "PROJECT_TEMPLATE": {"default_ratio": "1/6", "default_ip_owner": "", "ratio_options": PROJECT_RATIO_OPTIONS.copy()},
    TODO_LIST_KEY: [],
    STANDARD_EVENTS_KEY: [],
}

DEFAULT_DB = {SYSTEM_CONFIG_KEY: DEFAULT_SYS_CFG}


def deep_copy_obj(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False))
