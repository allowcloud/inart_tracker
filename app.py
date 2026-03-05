import streamlit as st
import json
import os
import datetime
import base64
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import re
import uuid
import io
import tempfile
import hashlib
import zipfile
from decimal import Decimal
from PIL import Image

# ==========================================
# 核心架构：压缩引擎
# ==========================================
IMG_DIR = "img_assets"  # 仅保留供旧数据兼容读取，新数据不再写入

def compress_to_b64(img_data, max_size=(800, 800), quality=50):
    try:
        if isinstance(img_data, bytes): img = Image.open(io.BytesIO(img_data))
        else: img = Image.open(img_data)
        if img.mode in ("RGBA", "P"): img = img.convert("RGB")
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception: return ""

def render_image(img_str, **kwargs):
    if not img_str: return
    if img_str.startswith("FILE:"):
        file_path = os.path.join(IMG_DIR, img_str.replace("FILE:", ""))
        if os.path.exists(file_path): st.image(file_path, **kwargs)
        else: st.caption("⚠️ 图片为旧版本地文件，云端不可用，请重新上传。")
    else:
        try: st.image(base64.b64decode(img_str), **kwargs)
        except: pass

def save_uploaded_file_ref(file_obj, prefix="upload"):
    if file_obj is None:
        return ""
    if not os.path.exists(IMG_DIR):
        os.makedirs(IMG_DIR)
    ext = os.path.splitext(getattr(file_obj, 'name', '') or '')[1].lower() or '.jpg'
    fname = f"{prefix}_{uuid.uuid4().hex}{ext}"
    fpath = os.path.join(IMG_DIR, fname)
    with open(fpath, 'wb') as f:
        f.write(file_obj.read())
    return f"FILE:{fname}"

def norm_text(s):
    return re.sub(r'\s+', '', str(s or '').strip()).lower()

def resolve_alias_project(name, project_alias_map):
    n = norm_text(name)
    if not n:
        return name
    return project_alias_map.get(n, name)

def get_visible_projects(db_obj, current_pm):
    """按负责人过滤 + 别名去重：若A被映射到已存在的B，则默认隐藏A。"""
    alias_map = db_obj.get("系统配置", {}).get("项目别名", {})
    raw = [p for p, d in db_obj.items()
           if p != "系统配置" and
           (current_pm == "所有人" or str(d.get('负责人', '')).strip() == current_pm)]
    out = []
    for p in raw:
        canonical = resolve_alias_project(p, alias_map)
        if canonical != p and canonical in db_obj:
            continue
        out.append(p)

    def _latest_log_date(proj_name):
        latest = datetime.date.min
        for comp in db_obj.get(proj_name, {}).get("部件列表", {}).values():
            for lg in comp.get("日志流", []):
                if is_hidden_system_log(lg):
                    continue
                try:
                    dt = datetime.datetime.strptime(lg.get("日期", ""), "%Y-%m-%d").date()
                except:
                    continue
                if dt > latest:
                    latest = dt
        return latest

    def _is_paused(proj_name):
        pd = db_obj.get(proj_name, {})
        if str(pd.get("Milestone", "")).strip() == "暂停研发":
            return True
        comps = pd.get("部件列表", {})
        gk = next((k for k in comps.keys() if "全局" in k), "全局进度")
        return is_pause_stage(comps.get(gk, {}).get("主流程", ""))

    def _is_finished(proj_name):
        return str(db_obj.get(proj_name, {}).get("Milestone", "")).strip() in ["生产结束", "项目结束撒花🎉", "✅ 已完成(结束)"]

    # 项目列表排序：进行中在前，暂停在中，完结在后；同组按最近更新倒序
    out.sort(key=lambda p: (
        1 if _is_paused(p) else (2 if _is_finished(p) else 0),
        -_latest_log_date(p).toordinal(),
        p
    ))
    return out

def is_hidden_system_log(log_obj):
    evt = str((log_obj or {}).get("事件", ""))
    return "[系统自动追踪]" in evt

def collect_stage_activity(raw_logs, stages):
    """从日志提取阶段活跃/完成状态，降低主循环缩进复杂度（防 merge 缩进回归）。"""
    active_stages = set(); completed_stages = set()
    for log in raw_logs:
        stg = log.get('工序', ''); evt = log.get('事件', '')
        if stg in stages:
            active_stages.add(stg)
            if any(k in evt for k in ["彻底完成", "OK", "通过", "完结", "结束", "撒花"]):
                active_stages.discard(stg); completed_stages.add(stg)
    if active_stages or completed_stages:
        active_stages.discard("立项"); completed_stages.add("立项")
    return active_stages, completed_stages

def get_project_production_start_date(proj_data):
    """推断项目进入生产期（工厂复样/大货或里程碑设为生产中）的起始日期。"""
    comps = (proj_data or {}).get("部件列表", {})
    global_key = next((k for k in comps.keys() if "全局" in k), "全局进度")
    global_logs = comps.get(global_key, {}).get("日志流", [])
    date_candidates = []
    for log in global_logs:
        evt = str(log.get("事件", ""))
        stg = str(log.get("工序", ""))
        is_prod_hint = (
            stg in ["工厂复样(含胶件/上色等)", "大货"] or
            "阶段:生产中" in evt
        )
        if not is_prod_hint:
            continue
        try:
            dt = datetime.datetime.strptime(log.get("日期", ""), "%Y-%m-%d").date()
        except:
            continue
        date_candidates.append(dt)
    if not date_candidates:
        return None
    return min(date_candidates)

def is_late_added_component(comp_name, comp_info, production_start_date, factory_idx, stages):
    """区分生产期后新增零件：允许其独立从早期阶段重新走。"""
    if "全局" in str(comp_name):
        return False
    if not production_start_date:
        return False

    cur_stage = str((comp_info or {}).get("主流程", "")).strip()
    cur_idx = stages.index(cur_stage) if cur_stage in stages else 0
    if cur_idx >= factory_idx:
        return False

    logs = [lg for lg in (comp_info or {}).get("日志流", []) if not is_hidden_system_log(lg)]
    if not logs:
        return True

    first_dt = None
    for lg in logs:
        try:
            dt = datetime.datetime.strptime(lg.get("日期", ""), "%Y-%m-%d").date()
        except:
            continue
        first_dt = dt if first_dt is None else min(first_dt, dt)
    if first_dt is None:
        return False
    return first_dt >= production_start_date

# 防御式兜底：若后续 merge 冲突误删了函数定义，至少保证运行期不 NameError
if "is_hidden_system_log" not in globals():
    def is_hidden_system_log(log_obj):
        return False

# ==========================================
# 1. 页面基础配置与核心变量
# ==========================================
st.set_page_config(page_title="INART PM 系统", page_icon="🚀", layout="wide", initial_sidebar_state="expanded")

# 全局 CSS：减少白屏闪烁、优化表格渲染
st.markdown("""
<style>
:root {
  --pm-bg: #f8fafc;
  --pm-card: #ffffff;
  --pm-border: #dbe3ee;
  --pm-title: #0f172a;
  --pm-sub: #475569;
  --pm-accent: #0ea5e9;
  --pm-accent-soft: #e0f2fe;
}

.stApp {
  background: linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%);
}

/* 防止切页时整页白屏 */
.stSpinner > div { margin-top: 20vh; }

/* 表格行高压缩，显示更多数据 */
[data-testid="stDataFrame"] table td { padding: 4px 8px !important; font-size: 13px; }

/* 侧边栏按钮间距 */
section[data-testid="stSidebar"] .stButton button { width: 100%; border-radius: 8px; }

/* 隐藏 streamlit 页脚 */
footer { visibility: hidden; }

/* Tabs 视觉优化 */
.stTabs [data-baseweb="tab-list"] { gap: 8px; }
.stTabs [data-baseweb="tab"] {
  background: #ffffff;
  border: 1px solid var(--pm-border);
  border-radius: 10px;
  padding: 8px 14px;
}
.stTabs [aria-selected="true"] {
  background: var(--pm-accent-soft) !important;
  border-color: #7dd3fc !important;
}

/* Expander 视觉优化 */
.streamlit-expanderHeader {
  border: 1px solid var(--pm-border);
  border-radius: 10px;
  background: #ffffff;
  color: var(--pm-title);
}

/* 指标卡片 */
[data-testid="stMetric"] {
  background: var(--pm-card);
  border: 1px solid var(--pm-border);
  border-radius: 12px;
  padding: 10px 12px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
}

/* PM 页面分区标题 */
.pm-section-title {
  margin: 8px 0 10px 0;
  padding: 9px 12px;
  background: #0f172a;
  color: #f8fafc;
  border-radius: 10px;
  font-weight: 700;
}

.pm-subsection-title {
  margin: 6px 0 8px 0;
  padding: 6px 10px;
  background: #ffffff;
  border: 1px solid var(--pm-border);
  border-left: 4px solid var(--pm-accent);
  border-radius: 8px;
  color: var(--pm-title);
  font-weight: 600;
}

.pm-kv-note {
  color: var(--pm-sub);
  font-size: 12px;
  margin-top: -2px;
}

.pm-chip-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 4px 0 8px 0;
}

.pm-chip {
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 12px;
  border: 1px solid #d1d5db;
  background: #ffffff;
  color: #334155;
}

.pm-chip.done { background: #dcfce7; border-color: #86efac; }
.pm-chip.active { background: #dbeafe; border-color: #93c5fd; }
.pm-chip.pause { background: #e5e7eb; border-color: #9ca3af; }
.pm-chip.delay { background: #fef9c3; border-color: #facc15; }
.pm-chip.none { background: #f1f5f9; border-color: #cbd5e1; }
</style>
""", unsafe_allow_html=True)

MENU_DASHBOARD = "📊 全局大盘与甘特图"
MENU_SPECIFIC  = "🎯 PM 工作台"
MENU_FASTLOG   = "📝 手机 AI 速记"
MENU_PACKING   = "📦 包装与入库特殊领用"
MENU_COST      = "💰 专属成本台账"
MENU_HISTORY   = "🔍 历史溯源 (全局可编)"
MENU_SETTINGS  = "⚙️ 系统维护 (全局配置)"
MENU_GUIDE     = "📖 新手使用指南"

STD_MILESTONES  = ["待立项", "研发中", "暂停研发", "下模中", "生产中", "生产结束", "项目结束撒花🎉"]
HANDOFF_METHODS = ["内部正常推进", "微信", "飞书", "实物/打印件交接", "网盘链接", "当面沟通"]
STD_COSTS_LIST  = ["研发费", "模具费", "大货生产", "包装印刷", "物流运输", "外包设计", "杂项其他"]
QUOTE_ITEM_DEFAULTS = ["生产价", "衣服+皮布件", "头+装配", "模具费", "包装彩盒、吸塑", "手*9", "战衣版成品", "周转运费"]
REVIEW_TYPE_OPTIONS = ["(无)", "2D提审", "3D提审", "实物提审", "包装提审"]
REVIEW_RESULT_OPTIONS = ["(无)", "待反馈", "通过", "打回"]

DEFAULT_SYS_CFG = {
    "标准部件": ["头雕(表情)", "素体", "手型", "服装", "配件", "地台", "包装"],
    "标准阶段": ["立项", "建模(含打印/签样)", "涂装", "设计", "工程拆件", "手板/结构板", "官图", "工厂复样(含胶件/上色等)", "大货", "⏸️ 暂停/搁置", "✅ 已完成(结束)"],
    "宏观阶段": ["立项", "建模", "设计", "工程", "模具", "修模", "生产", "暂停", "结束"],
    "排期基线": {"立项": 7, "建模": 42, "设计": 35, "工程": 49, "模具": 28, "修模": 14, "生产": 30},
    "项目别名": {},
    "AI_COMP_KW":  {},
    "AI_STAGE_KW": {}
}
DEFAULT_DB = {"系统配置": DEFAULT_SYS_CFG}

# ==========================================
# 2. 核心数据层 (DAL) & 状态初始化
# ==========================================
class DatabaseManager:
    _instance = None  # 单例，避免每次 rerun 重新建立 TCP 连接

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        from pymongo import MongoClient
        from pymongo.errors import PyMongoError
        self.PyMongoError = PyMongoError
        try:
            uri = st.secrets["MONGO_URI"]
        except Exception:
            uri = os.environ.get("MONGO_URI", "")
        if not uri:
            st.error("❌ 未配置 MONGO_URI，请在 Streamlit Secrets 中添加。")
            st.stop()
        # maxPoolSize=5 足够小团队并发；connectTimeoutMS 给足时间
        self.client = MongoClient(
            uri,
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
            socketTimeoutMS=10000,
            maxPoolSize=5,
        )
        self.col = self.client["inart_pm"]["projects"]
        self._initialized = True

    @st.cache_data(ttl=60, show_spinner=False)
    def _load_cached(_self):
        """带60秒 TTL 缓存的读取，切模块不重复拉 MongoDB"""
        try:
            docs = list(_self.col.find({}, {"_id": 0}))
            if not docs:
                return None  # 触发迁移逻辑
            data = {}
            for doc in docs:
                key = doc.get("_doc_key")
                if key:
                    data[key] = doc.get("payload", {})
            return data if data else None
        except Exception as e:
            st.error(f"数据库读取失败: {e}")
            return None

    def load(self):
        cached = self._load_cached()
        if cached is not None:
            return cached
        return self._migrate_from_json()

    def save(self, data):
        """全量保存（备份恢复、系统配置变更时使用）"""
        try:
            from pymongo import UpdateOne
            ops = [
                UpdateOne({"_doc_key": key}, {"$set": {"_doc_key": key, "payload": value}}, upsert=True)
                for key, value in data.items()
            ]
            if ops:
                self.col.bulk_write(ops, ordered=False)  # bulk 写入，比逐条快 5-10x
            st.cache_data.clear()
        except self.PyMongoError as e:
            st.error(f"数据库保存失败: {e}")

    def save_one(self, key, value):
        """单条保存（项目级更新，三人并发写不同项目时完全不冲突）"""
        try:
            self.col.replace_one(
                {"_doc_key": key},
                {"_doc_key": key, "payload": value},
                upsert=True
            )
            st.cache_data.clear()
        except self.PyMongoError as e:
            st.error(f"保存失败 [{key}]: {e}")

    def _migrate_from_json(self):
        """首次启动自动把旧 JSON 导入 MongoDB"""
        json_path = "tracker_data_web_v20.json"
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.save(data)
                os.rename(json_path, json_path + ".migrated")
                st.toast("✅ 已自动从旧 JSON 迁移到 MongoDB！")
                return data
            except Exception as e:
                st.warning(f"JSON 迁移失败: {e}")
        return DEFAULT_DB.copy()

@st.cache_resource(show_spinner=False)
def get_db_manager():
    """全局单例，整个 Streamlit 进程只建立一次 MongoDB 连接池"""
    return DatabaseManager()

db_manager = get_db_manager()

def init_session():
    defaults = {
        'db': db_manager.load(),
        'parsed_logs': [], 'pasted_cache': {}, 'config_pasted_cache': {}, 'ai_pasted_cache': {},
        'exclude_imgs': set(), 'config_consumed_hashes': set(), 'ai_consumed_hashes': set(),
        'new_proj_mode': False, 'current_proj_context': None, 'form_key': 0
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    if "系统配置" not in st.session_state.db:
        st.session_state.db["系统配置"] = DEFAULT_SYS_CFG
    for k, v in DEFAULT_SYS_CFG.items():
        if k not in st.session_state.db["系统配置"]:
            st.session_state.db["系统配置"][k] = v

init_session()

SYS_CFG       = st.session_state.db["系统配置"]
STAGES_UNIFIED = SYS_CFG["标准阶段"]
STD_COMPONENTS = SYS_CFG["标准部件"]
MACRO_STAGES   = SYS_CFG["宏观阶段"]

# ==========================================
# 自动同步里程碑
# ==========================================
def auto_sync_milestone(proj_name):
    proj_data = st.session_state.db.get(proj_name)
    if not proj_data or "部件列表" not in proj_data:
        return
    comps = proj_data['部件列表']
    max_idx = -1
    max_stage = None
    for c_name, info in comps.items():
        if "全局" in c_name:
            continue
        s = str(info.get('主流程', '')).strip()
        s_idx = next((i for i, std_s in enumerate(STAGES_UNIFIED) if s in std_s or std_s in s), -1)
        if s_idx > max_idx and s_idx >= 0 and "暂停" not in STAGES_UNIFIED[s_idx]:
            max_idx = s_idx
            max_stage = STAGES_UNIFIED[s_idx]

    if max_idx >= 0 and max_stage:
        global_key = next((k for k in comps.keys() if "全局" in k), "全局进度")
        if global_key not in comps:
            comps[global_key] = {"主流程": STAGES_UNIFIED[0], "日志流": []}
        curr_global_stage = str(comps[global_key].get("主流程", "")).strip()
        curr_idx = next((i for i, std_s in enumerate(STAGES_UNIFIED) if curr_global_stage in std_s or std_s in curr_global_stage), -1)
        if curr_idx < max_idx and "暂停" not in curr_global_stage:
            # 自动对齐仅更新阶段，不再写入“系统自动追踪”日志，避免噪音
            comps[global_key]["主流程"] = max_stage

    sub_stages = [info.get('主流程', '') for c_name, info in comps.items() if "全局" not in c_name]
    stages = sub_stages if sub_stages else [comps.get("全局进度", {}).get("主流程", "")]
    cur_ms = proj_data.get('Milestone', '')
    if all(s == "✅ 已完成(结束)" for s in stages) and stages:
        proj_data['Milestone'] = "项目结束撒花🎉"
    elif any(s in ["工厂复样(含胶件/上色等)", "大货"] for s in stages):
        if cur_ms not in ["生产结束", "项目结束撒花🎉", "暂停研发"]:
            proj_data['Milestone'] = "生产中"
    elif any(s in ["建模(含打印/签样)", "涂装", "设计", "工程拆件", "手板/结构板", "官图"] for s in stages):
        if cur_ms == "待立项":
            proj_data['Milestone'] = "研发中"

def sync_save_db(changed_proj=None):
    """
    changed_proj: 传入项目名时只写该项目（并发安全）
                  不传时全量保存（备份恢复/系统配置变更时用）
    """
    if changed_proj and changed_proj in st.session_state.db and changed_proj != "系统配置":
        auto_sync_milestone(changed_proj)
    else:
        for p in st.session_state.db:
            if p != "系统配置":
                auto_sync_milestone(p)
    if changed_proj:
        db_manager.save_one(changed_proj, st.session_state.db[changed_proj])
        db_manager.save_one("系统配置", st.session_state.db["系统配置"])
    else:
        db_manager.save(st.session_state.db)

# ==========================================
# 3. 业务逻辑层
# ==========================================
from functools import lru_cache

@lru_cache(maxsize=128)
def get_macro_phase(detail_stage):
    s = str(detail_stage).strip()
    if "完成" in s or "结束" in s or "撒花" in s: return "结束"
    if "暂停" in s or "搁置" in s: return "暂停"
    if any(x in s for x in ["大货", "复样", "量产", "开定"]): return "生产"
    if any(x in s for x in ["拆件", "手板", "结构", "官图"]): return "工程"
    if "模具" in s: return "模具"
    if "设计" in s: return "设计"
    if "建模" in s or "打印" in s or "涂装" in s: return "建模"  # 涂装属于建模阶段
    if "立项" in s: return "立项"
    return "工程"

def is_pause_stage(stage_name):
    s = str(stage_name).strip()
    return ("暂停" in s) or ("搁置" in s)

def get_stage_index(stage_name, stages):
    s = str(stage_name).strip()
    if s in stages:
        return stages.index(s)
    return next((i for i, std_s in enumerate(stages) if s in std_s or std_s in s), -1)

def validate_review_with_stage(review_type, stage_name, comp_name, stages):
    """提审维度校验：仅提醒，不直接篡改主阶段。"""
    rt = str(review_type or "").strip()
    if not rt or rt == "(无)":
        return ""

    idx = get_stage_index(stage_name, stages)
    if idx < 0:
        return f"提审[{rt}]无法校验：阶段[{stage_name}]不在标准阶段中"

    li_idx = get_stage_index("立项", stages)
    design_idx = get_stage_index("设计", stages)
    eng_idx = get_stage_index("工程拆件", stages)
    struct_idx = get_stage_index("手板/结构板", stages)

    if rt == "2D提审":
        if li_idx >= 0 and idx < li_idx:
            return "2D提审建议在立项后出现"
    elif rt == "3D提审":
        min_idx = min([i for i in [design_idx, eng_idx] if i >= 0], default=-1)
        if min_idx >= 0 and idx < min_idx:
            return "3D提审建议在设计或工程阶段使用"
    elif rt == "实物提审":
        if struct_idx >= 0 and idx < struct_idx:
            return "实物提审建议在手板/结构板阶段及之后使用"
    elif rt == "包装提审":
        comp_txt = str(comp_name or "")
        stage_txt = str(stage_name or "")
        if ("包装" not in comp_txt) and (not any(k in stage_txt for k in ["包装", "彩盒", "灰箱", "物流箱"])):
            return "包装提审建议用于【包装】部件或包装相关阶段"
    return ""

def validate_transition_warning(curr_stage, next_stage, stages):
    """返回 warning 文案；空串表示无明显风险。"""
    if not next_stage or next_stage == "(维持原阶段)" or next_stage == curr_stage:
        return ""
    ci = get_stage_index(curr_stage, stages)
    ni = get_stage_index(next_stage, stages)
    if ci < 0 or ni < 0:
        return ""
    if next_stage == "✅ 已完成(结束)":
        min_finish_idx = get_stage_index("工厂复样(含胶件/上色等)", stages)
        if min_finish_idx >= 0 and ci < min_finish_idx:
            return f"当前阶段[{curr_stage}]过早完结，建议至少到工厂复样后再结束"
    if ni < ci and (not is_pause_stage(next_stage)):
        return f"阶段逆行：[{curr_stage}] -> [{next_stage}]，请确认是否需要强制提交"
    return ""

def infer_review_type_from_text(txt):
    s = str(txt or "").lower()
    has_review_signal = any(k in s for k in ["提审", "过审", "通过", "打回", "驳回", "待反馈", "review"])
    if not has_review_signal:
        return "(无)"
    if any(k in s for k in ["2d", "二维"]):
        return "2D提审"
    if any(k in s for k in ["3d", "三维"]):
        return "3D提审"
    if any(k in s for k in ["实物", "手板", "结构件"]):
        return "实物提审"
    if any(k in s for k in ["包装", "彩盒", "灰箱", "物流箱"]):
        return "包装提审"
    return "(无)"

def infer_review_result_from_text(txt):
    s = str(txt or "").lower()
    if any(k in s for k in ["通过", "ok", "pass", "过审"]):
        return "通过"
    if any(k in s for k in ["打回", "驳回", "退回"]):
        return "打回"
    if any(k in s for k in ["提审", "待反馈", "review"]):
        return "待反馈"
    return "(无)"

def normalize_review_round(val):
    s = str(val or "").strip()
    if not s:
        return ""
    try:
        n = int(float(s))
        return n if n > 0 else ""
    except:
        return ""

def infer_review_round_from_text(txt):
    s = str(txt or "")
    m_num = re.search(r'第?\s*([0-9]{1,2})\s*轮', s)
    if m_num:
        try:
            n = int(m_num.group(1))
            return n if n > 0 else ""
        except:
            pass
    m_zh = re.search(r'第?\s*([一二三四五六七八九十])\s*轮', s)
    if m_zh:
        zh_map = {"一":1, "二":2, "三":3, "四":4, "五":5, "六":6, "七":7, "八":8, "九":9, "十":10}
        return zh_map.get(m_zh.group(1), "")
    return ""

def quarter_to_deadline(q_str):
    """YYYY Qn -> 该季度最后一天日期"""
    m = re.match(r'^(\d{4})\s*Q([1-4])$', str(q_str or '').strip().upper())
    if not m:
        return None
    y = int(m.group(1)); q = int(m.group(2))
    month = q * 3
    if month == 12:
        return datetime.date(y, 12, 31)
    return datetime.date(y, month + 1, 1) - datetime.timedelta(days=1)

def is_due_soon(target_str, days=5):
    """开定/发货是否进入提前预警窗口"""
    s = str(target_str or '').strip()
    if not s or s.upper() == 'TBD':
        return False
    try:
        if re.match(r'^\d{4}\s*Q[1-4]$', s.upper()):
            dt = quarter_to_deadline(s)
        elif len(s) >= 10:
            dt = datetime.datetime.strptime(s[:10], "%Y-%m-%d").date()
        else:
            return False
    except:
        return False
    if not dt:
        return False
    d = (dt - datetime.date.today()).days
    return 0 <= d <= days

def get_stage_delay_set(raw_logs, baseline_days):
    """返回处于 delay 的阶段集合（按最近一次该阶段日志距今天数与基线比较）"""
    latest_by_stage = {}
    for lg in raw_logs:
        stg = lg.get('工序', '')
        try:
            dt = datetime.datetime.strptime(lg.get('日期', ''), "%Y-%m-%d").date()
        except:
            continue
        if stg not in latest_by_stage or dt > latest_by_stage[stg]:
            latest_by_stage[stg] = dt
    delayed = set()
    for stg, dt in latest_by_stage.items():
        macro = get_macro_phase(stg)
        limit = int((baseline_days or {}).get(macro, 99999))
        if (datetime.date.today() - dt).days > limit and limit < 99999:
            delayed.add(stg)
    return delayed

def parse_date_safe(date_str):
    try:
        return datetime.datetime.strptime(str(date_str), "%Y-%m-%d").date()
    except:
        return None


def extract_deadline_from_text(text, ref_date=None):
    """从 CP/DDL 合并文本提取日期，支持 YYYY-MM-DD / YYYY/MM/DD / M/D。"""
    s = str(text or "").strip()
    if not s:
        return None
    ref = ref_date or datetime.date.today()

    m_full = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m_full:
        try:
            return datetime.date(int(m_full.group(1)), int(m_full.group(2)), int(m_full.group(3)))
        except:
            pass

    m_md = re.search(r"(^|\D)(\d{1,2})/(\d{1,2})(\D|$)", s)
    if m_md:
        try:
            mm = int(m_md.group(2)); dd = int(m_md.group(3))
            y = ref.year
            cand = datetime.date(y, mm, dd)
            if cand < ref - datetime.timedelta(days=30):
                cand = datetime.date(y + 1, mm, dd)
            return cand
        except:
            pass
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

def todo_due_date(td):
    d = parse_date_safe((td or {}).get("DDL", ""))
    if d:
        return d
    return extract_deadline_from_text(todo_cpddl_text(td))
def get_risk_status(milestone, target_date_str="TBD"):
    ms = str(milestone).strip()
    target_date_str = str(target_date_str).strip()
    if ms == "暂停研发":
        return "⏸️ 暂停研发", "normal"
    is_finished = ms in ["生产结束", "项目结束撒花🎉", "✅ 已完成(结束)"]
    if target_date_str.upper() != "TBD" and target_date_str != "":
        try:
            today = datetime.date.today()
            if len(target_date_str) == 7 and "-" in target_date_str:
                t_year, t_month = int(target_date_str[:4]), int(target_date_str[5:7])
                if (today.year > t_year) or (today.year == t_year and today.month > t_month):
                    if not is_finished: return "🔴 逾期预警", "danger"
            elif len(target_date_str) >= 10:
                t_date = datetime.datetime.strptime(target_date_str[:10], "%Y-%m-%d").date()
                if today > t_date and not is_finished: return "🔴 逾期预警", "danger"
        except:
            pass
    if is_finished: return "🏁 已结案", "safe"
    if ms in ["生产中", "下模中"]: return "🟢 生产期", "safe"
    if "研发" in ms or ms in ["待开定", "已开定", "待立项"]: return "🟡 研发期", "warning"
    return "⚪ 未知阶段", "normal"


def render_pm_todo_manager(valid_projs):
    st.subheader("🗂️ To do List（CP/DDL 合并）")
    todo_list = db.setdefault("系统配置", {}).setdefault("PM_TODO_LIST", [])
    todo_proj_options = ["(不关联项目)"] + valid_projs

    st.caption("模板建议：任务必填；CP/DDL 合并填写，如“3/7 结构件确认”或“结构件-等工厂反馈”。")
    t1, t2, t3, t4 = st.columns([2.6, 2.0, 1.6, 0.9])
    with t1:
        todo_title = st.text_input("任务", key="todo_title_global", placeholder="如：金克丝 T2 结构件")
    with t2:
        todo_cpddl = st.text_input("CP/DDL(合并)", key="todo_cpddl_global", placeholder="如：3/7 结构件确认")
    with t3:
        todo_ref_proj = st.selectbox("关联项目(可选)", todo_proj_options, key="todo_ref_global")
    with t4:
        st.write("")
        if st.button("➕ 添加", key="todo_add_global", type="primary"):
            if todo_title.strip():
                due_dt = extract_deadline_from_text(todo_cpddl)
                todo_list.append({
                    "任务": todo_title.strip(),
                    "CPDDL": todo_cpddl.strip(),
                    "CP": todo_cpddl.strip(),
                    "DDL": str(due_dt) if due_dt else "",
                    "关联项目": "" if todo_ref_proj == "(不关联项目)" else todo_ref_proj,
                    "完成": False,
                    "创建": str(datetime.date.today())
                })
                sync_save_db("系统配置")
                st.rerun()

    title_hints = []
    if todo_title.strip():
        for p in valid_projs:
            p_short = re.sub(r'^(1/6|1/4|1/12|1/3|1/1)\s*', '', str(p)).strip()
            if not p_short:
                continue
            if p_short in todo_title:
                p_t = str(db.get(p, {}).get("Target", "")).strip()
                if p_t and p_t.upper() != "TBD":
                    title_hints.append(f"{p}→{p_t}")
        if title_hints:
            st.caption("🔎 关键节点联想：" + " | ".join(title_hints[:4]))

    hint_target = db.get(todo_ref_proj, {}).get("Target", "") if todo_ref_proj in db else ""
    if hint_target and str(hint_target).strip().upper() != "TBD":
        st.caption(f"🔎 提示：[{todo_ref_proj}] 当前预计开定为 {hint_target}")

    if todo_list:
        todo_sorted = sorted(
            todo_list,
            key=lambda x: (
                1 if x.get("完成") else 0,
                todo_due_date(x) or datetime.date.max,
                x.get("创建", "")
            )
        )
        for i, td in enumerate(todo_sorted):
            c1, c2, c3, c4, c5 = st.columns([0.7, 3.3, 2.0, 1.2, 1.0])
            due_dt = todo_due_date(td)
            tag = ""
            if due_dt and not td.get("完成"):
                dd = (due_dt - datetime.date.today()).days
                if dd <= 0:
                    tag = " 🔴今日/逾期"
                elif dd == 1:
                    tag = " 🟡明日到期"
            with c1:
                done = st.checkbox("", value=bool(td.get("完成")), key=f"todo_done_global_{i}")
                td["完成"] = done
            with c2:
                st.markdown(f"**{td.get('任务','')}**{tag}")
                ref_proj = td.get("关联项目", "")
                st.caption(ref_proj if ref_proj else "(未关联项目)")
            with c3:
                cpddl_txt = todo_cpddl_text(td)
                st.write(cpddl_txt if cpddl_txt else "-")
            with c4:
                st.write("✅ 已完成" if td.get("完成") else "⏳ 进行中")
            with c5:
                if st.button("🗑️", key=f"todo_del_global_{i}"):
                    todo_list.remove(td)
                    sync_save_db("系统配置")
                    st.rerun()
        if st.button("💾 保存To do状态", key="todo_save_global"):
            db.setdefault("系统配置", {})["PM_TODO_LIST"] = todo_list
            sync_save_db("系统配置")
            st.rerun()
    return todo_list



def render_pm_batch_fastlog_integrated(visible_projects, default_proj=""):
    st.subheader("📝 批量速记（多项目）")
    st.caption("用于晚间复盘：一次输入多项目进展，解析后统一校对并批量入库。")

    MANUAL_PICK = "⚠️请手动选择项目"
    PROJECT_ALIAS_MAP = SYS_CFG.get("项目别名", {})
    COMP_KW = {
        "头": "头雕(表情)", "眼": "头雕(表情)", "脸": "头雕(表情)", "手": "手型",
        "衣": "服装", "包": "包装", "盒": "包装", "地台": "地台",
        "扣": "配件", "法杖": "配件", "杯": "配件", "剑": "配件"
    }
    STAGE_KW = {
        "定价": "立项", "评估": "立项", "打印": "建模(含打印/签样)",
        "模型": "建模(含打印/签样)", "缩放": "建模(含打印/签样)", "建模": "建模(含打印/签样)",
        "涂": "涂装", "色": "涂装", "设计": "设计", "原画": "设计",
        "拆件": "工程拆件", "官图": "官图", "大货": "大货",
        "完成": "✅ 已完成(结束)", "结束": "✅ 已完成(结束)"
    }
    comp_kw = {**COMP_KW, **SYS_CFG.get("AI_COMP_KW", {})}
    stage_kw = {**STAGE_KW, **SYS_CFG.get("AI_STAGE_KW", {})}

    k_rows = "pm_batch_rows"
    d1, d2 = st.columns([1, 3])
    with d1:
        rec_date = st.date_input("记录日期", value=datetime.date.today(), key="pm_batch_date")
    with d2:
        raw_text = st.text_area(
            "批量输入（推荐：项目A & 项目B: 事件1；事件2）",
            key="pm_batch_text",
            height=130,
            placeholder="例：1/6金克丝: 头雕提审通过；包装刀线待补\n1/6里夫西装 & 里夫战衣: 官图提审待反馈"
        )

    def _norm(s):
        return re.sub(r"\s+", "", str(s or "")).lower()

    def _match_project(name):
        raw = str(name or "").strip()
        if not raw:
            return MANUAL_PICK
        direct = resolve_alias_project(raw, PROJECT_ALIAS_MAP)
        if direct in visible_projects:
            return direct
        q = _norm(raw)
        if not q:
            return MANUAL_PICK
        matched = []
        for p in visible_projects:
            p_full = _norm(p)
            p_core = _norm(re.sub(r'(1/6|1/4|1/12|1/3|1/1)\s*', '', str(p)))
            if q in p_full or (p_core and q in p_core):
                matched.append(p)
        matched = list(dict.fromkeys(matched))
        return matched[0] if len(matched) == 1 else MANUAL_PICK

    if st.button("✨ 智能拆解", key="pm_batch_parse", type="primary"):
        parsed = []
        for line in [x.strip() for x in str(raw_text).splitlines() if x.strip()]:
            txt = line.replace("：", ":").strip().rstrip("；;")
            if not txt:
                continue
            if ":" in txt:
                proj_part, content_part = txt.split(":", 1)
                proj_tokens = [x.strip() for x in re.split(r"&|和|,|，|、", proj_part) if x.strip()]
            else:
                proj_tokens = [default_proj] if default_proj else [MANUAL_PICK]
                content_part = txt
            content_segs = [x.strip() for x in re.split(r"[;；]+", content_part) if x.strip()]
            if not content_segs:
                content_segs = [content_part.strip()] if content_part.strip() else []
            projects = [_match_project(p) for p in proj_tokens] or [MANUAL_PICK]
            for p in projects:
                for seg in content_segs:
                    comp = next((v for k, v in comp_kw.items() if str(k).strip() and str(k) in seg), "全局进度")
                    stage = next((v for k, v in stage_kw.items() if str(k).strip() and str(k) in seg), "(维持原阶段)")
                    parsed.append({
                        "项目": p,
                        "部件": comp,
                        "阶段": stage,
                        "事件": seg,
                        "新词": "",
                        "提审类型": infer_review_type_from_text(seg),
                        "提审结果": infer_review_result_from_text(seg),
                        "提审轮次": infer_review_round_from_text(seg)
                    })
        st.session_state[k_rows] = parsed

    rows = st.session_state.get(k_rows, [])
    if not rows:
        st.info("输入批量速记后，点击【智能拆解】。")
        return

    all_existing_comps = set()
    for p in visible_projects:
        all_existing_comps.update(db.get(p, {}).get("部件列表", {}).keys())
    proj_opts = [MANUAL_PICK] + visible_projects
    comp_opts = ["全局进度"] + STD_COMPONENTS + sorted(all_existing_comps) + ["其他配件(系统自动创建)"]
    comp_opts = list(dict.fromkeys(comp_opts))
    stage_opts = ["(维持原阶段)"] + STAGES_UNIFIED

    edited_df = st.data_editor(
        pd.DataFrame(rows),
        num_rows="dynamic",
        use_container_width=True,
        key="pm_batch_editor",
        column_config={
            "项目": st.column_config.SelectboxColumn("项目", options=proj_opts, required=True),
            "部件": st.column_config.SelectboxColumn("部件", options=comp_opts, required=True),
            "阶段": st.column_config.SelectboxColumn("阶段", options=stage_opts, required=True),
            "提审类型": st.column_config.SelectboxColumn("提审类型", options=REVIEW_TYPE_OPTIONS, required=True),
            "提审结果": st.column_config.SelectboxColumn("提审结果", options=REVIEW_RESULT_OPTIONS, required=True),
            "提审轮次": st.column_config.NumberColumn("提审轮次", min_value=1, step=1),
        }
    )

    st.markdown("##### 🖼️ 附件图片")
    files = st.file_uploader("上传图片（可多张）", type=["png", "jpg", "jpeg"], accept_multiple_files=True, key="pm_batch_files")
    file_bind = {}
    if files:
        bind_opts = ["全部记录"] + [p for p in sorted(set(edited_df.get("项目", []))) if p in db and p != MANUAL_PICK]
        bind_opts = list(dict.fromkeys(bind_opts))
        cols = st.columns(min(3, len(files)))
        for i, f in enumerate(files):
            with cols[i % len(cols)]:
                file_bind[i] = st.selectbox(f"{f.name} 绑定", bind_opts, key=f"pm_batch_bind_{i}")

    auto_learn = st.checkbox("🤖 自动学习新词（留空时用事件前8字）", value=True, key="pm_batch_learn")
    force_submit = st.checkbox("⚠️ 强制提交（忽略阶段/提审 warning）", value=False, key="pm_batch_force")

    if st.button("💾 批量入库", key="pm_batch_save", type="primary"):
        images_by_target = {}
        if files:
            for i, f in enumerate(files):
                target = file_bind.get(i, "全部记录")
                b64 = compress_to_b64(f.getvalue())
                if b64:
                    images_by_target.setdefault(target, []).append(b64)

        saved_count = 0
        skipped_count = 0
        learned_count = 0
        changed_projects = set()

        for _, row in edited_df.iterrows():
            proj_raw = str(row.get("项目", "")).strip()
            proj = resolve_alias_project(proj_raw, PROJECT_ALIAS_MAP)
            if proj not in db or proj == "系统配置" or MANUAL_PICK in proj_raw:
                skipped_count += 1
                continue
            evt = str(row.get("事件", "")).strip()
            if not evt:
                skipped_count += 1
                continue

            comp = str(row.get("部件", "全局进度")).strip() or "全局进度"
            if comp == "其他配件(系统自动创建)":
                comp = "自定义配件"
            stage_in = str(row.get("阶段", "(维持原阶段)")).strip()
            rv_type = str(row.get("提审类型", "(无)")) or "(无)"
            rv_res = str(row.get("提审结果", "(无)")) or "(无)"
            rv_round = normalize_review_round(row.get("提审轮次", ""))

            if comp not in db[proj].setdefault("部件列表", {}):
                db[proj]["部件列表"][comp] = {"主流程": STAGES_UNIFIED[0], "日志流": []}

            curr_stage = db[proj]["部件列表"][comp].get("主流程", STAGES_UNIFIED[0])
            final_stage = curr_stage if stage_in in ["", "(维持原阶段)"] else stage_in
            stage_warn = validate_transition_warning(curr_stage, final_stage, STAGES_UNIFIED)
            review_warn = validate_review_with_stage(rv_type, final_stage, comp, STAGES_UNIFIED)
            if (stage_warn or review_warn) and not force_submit:
                warn_txt = "；".join([w for w in [stage_warn, review_warn] if w])
                st.warning(f"[{proj}/{comp}] {warn_txt}（如确认无误可勾选强制提交）")
                skipped_count += 1
                continue

            imgs = images_by_target.get("全部记录", []) + images_by_target.get(proj, [])
            db[proj]["部件列表"][comp].setdefault("日志流", []).append({
                "日期": str(rec_date),
                "流转": "PM批量速记",
                "工序": final_stage,
                "事件": evt,
                "图片": imgs,
                "提审类型": rv_type,
                "提审结果": rv_res,
                "提审轮次": rv_round
            })
            db[proj]["部件列表"][comp]["主流程"] = final_stage
            saved_count += 1
            changed_projects.add(proj)

            kw = str(row.get("新词", "")).strip()
            if (not kw) and auto_learn:
                kw = evt[:8] if len(evt) >= 2 else ""
            if auto_learn and kw and len(kw) >= 2:
                if comp != "全局进度":
                    SYS_CFG.setdefault("AI_COMP_KW", {})[kw] = comp
                    learned_count += 1
                if stage_in not in ["", "(维持原阶段)"]:
                    SYS_CFG.setdefault("AI_STAGE_KW", {})[kw] = final_stage
                    learned_count += 1

        if saved_count > 0:
            for p in sorted(changed_projects):
                sync_save_db(p)
            st.session_state[k_rows] = []
            st.success(f"已保存 {saved_count} 条，跳过 {skipped_count} 条。自动学习词条 {learned_count} 个。")
            st.rerun()
        else:
            st.warning("没有可保存的记录。")

def render_pm_fastlog_integrated(sel_proj):
    st.markdown("#### 📝 速记功能（已并入 PM）")
    st.caption("当前为【单项目速记】。AI 仅猜部件/阶段/提审，不自动跳到全局进度。")

    COMP_KW = {
        "头": "头雕(表情)", "眼": "头雕(表情)", "脸": "头雕(表情)", "手": "手型",
        "衣": "服装", "包": "包装", "盒": "包装", "地台": "地台",
        "扣": "配件", "法杖": "配件", "杯": "配件", "剑": "配件"
    }
    STAGE_KW = {
        "定价": "立项", "评估": "立项", "打印": "建模(含打印/签样)",
        "模型": "建模(含打印/签样)", "缩放": "建模(含打印/签样)", "建模": "建模(含打印/签样)",
        "涂": "涂装", "色": "涂装", "设计": "设计", "原画": "设计",
        "拆件": "工程拆件", "官图": "官图", "大货": "大货",
        "完成": "✅ 已完成(结束)", "结束": "✅ 已完成(结束)"
    }
    comp_kw = {**COMP_KW, **SYS_CFG.get("AI_COMP_KW", {})}
    stage_kw = {**STAGE_KW, **SYS_CFG.get("AI_STAGE_KW", {})}

    unresolved_comp = "(待选择部件)"
    rk = f"pm_fast_rows_{sel_proj}"
    fd1, fd2 = st.columns([1, 3])
    with fd1:
        rec_date = st.date_input("记录日期", value=datetime.date.today(), key=f"pm_fast_date_{sel_proj}")
    with fd2:
        raw_txt = st.text_area(
            "输入速记（分号/换行自动拆句）",
            key=f"pm_fast_text_{sel_proj}",
            height=110,
            placeholder="例：头雕提审通过；包装刀线已提供；工程拆件待确认"
        )

    if st.button("✨ 解析到部件/阶段", key=f"pm_fast_parse_{sel_proj}"):
        segs = [s.strip() for s in re.split(r"[;；\n]+", str(raw_txt)) if s.strip()]
        rows = []
        for seg in segs:
            comp = next((v for k, v in comp_kw.items() if str(k).strip() and str(k) in seg), unresolved_comp)
            stage = next((v for k, v in stage_kw.items() if str(k).strip() and str(k) in seg), "(维持原阶段)")
            rows.append({
                "部件": comp,
                "阶段": stage,
                "事件": seg,
                "新词": "",
                "提审类型": infer_review_type_from_text(seg),
                "提审结果": infer_review_result_from_text(seg),
                "提审轮次": infer_review_round_from_text(seg)
            })
        st.session_state[rk] = rows

    rows = st.session_state.get(rk, [])
    if not rows:
        st.info("输入速记后点击【解析到部件/阶段】即可批量入库。")
        return

    existing_comps = list(db[sel_proj].get("部件列表", {}).keys())
    comp_opts = [unresolved_comp, "全局进度"] + STD_COMPONENTS + existing_comps + ["其他配件(系统自动创建)"]
    comp_opts = list(dict.fromkeys(comp_opts))
    stage_opts = ["(维持原阶段)"] + STAGES_UNIFIED

    df_rows = pd.DataFrame(rows)
    edited_df = st.data_editor(
        df_rows,
        num_rows="dynamic",
        use_container_width=True,
        key=f"pm_fast_editor_{sel_proj}",
        column_config={
            "部件": st.column_config.SelectboxColumn("部件", options=comp_opts, required=True),
            "阶段": st.column_config.SelectboxColumn("阶段", options=stage_opts, required=True),
            "提审类型": st.column_config.SelectboxColumn("提审类型", options=REVIEW_TYPE_OPTIONS, required=True),
            "提审结果": st.column_config.SelectboxColumn("提审结果", options=REVIEW_RESULT_OPTIONS, required=True),
            "提审轮次": st.column_config.NumberColumn("提审轮次", min_value=1, step=1),
        }
    )

    st.markdown("##### 🖼️ 上传图片并绑定部件")
    files = st.file_uploader(
        "上传图片（可多张）",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key=f"pm_fast_files_{sel_proj}"
    )
    file_bind = {}
    if files:
        bind_opts = ["全部记录"] + [x for x in comp_opts if x not in ["其他配件(系统自动创建)", unresolved_comp]]
        bcols = st.columns(min(3, len(files)))
        for i, f in enumerate(files):
            with bcols[i % len(bcols)]:
                file_bind[i] = st.selectbox(
                    f"{f.name} 绑定",
                    bind_opts,
                    key=f"pm_fast_bind_{sel_proj}_{i}"
                )

    auto_learn = st.checkbox("🤖 自动学习新词（留空时用事件前6字）", value=True, key=f"pm_fast_learn_{sel_proj}")
    force_submit = st.checkbox("⚠️ 强制提交（忽略阶段/提审 warning）", value=False, key=f"pm_fast_force_{sel_proj}")

    if st.button("💾 保存速记到当前项目", type="primary", key=f"pm_fast_save_{sel_proj}"):
        images_by_target = {}
        if files:
            for i, f in enumerate(files):
                target = file_bind.get(i, "全部记录")
                b64 = compress_to_b64(f.getvalue())
                if b64:
                    images_by_target.setdefault(target, []).append(b64)

        saved_count = 0
        skipped_count = 0
        learned_count = 0
        for _, row in edited_df.iterrows():
            evt = str(row.get("事件", "")).strip()
            if not evt:
                skipped_count += 1
                continue

            comp = str(row.get("部件", unresolved_comp)).strip() or unresolved_comp
            if comp == unresolved_comp:
                st.warning(f"[{evt[:14]}] 未选择部件，已跳过。")
                skipped_count += 1
                continue
            if comp == "其他配件(系统自动创建)":
                comp = "自定义配件"
            stage_in = str(row.get("阶段", "(维持原阶段)")).strip()
            rv_type = str(row.get("提审类型", "(无)")) or "(无)"
            rv_res = str(row.get("提审结果", "(无)")) or "(无)"
            rv_round = normalize_review_round(row.get("提审轮次", ""))

            if comp not in db[sel_proj].setdefault("部件列表", {}):
                db[sel_proj]["部件列表"][comp] = {"主流程": STAGES_UNIFIED[0], "日志流": []}

            curr_stage = db[sel_proj]["部件列表"][comp].get("主流程", STAGES_UNIFIED[0])
            final_stage = curr_stage if stage_in in ["", "(维持原阶段)"] else stage_in

            stage_warn = validate_transition_warning(curr_stage, final_stage, STAGES_UNIFIED)
            review_warn = validate_review_with_stage(rv_type, final_stage, comp, STAGES_UNIFIED)
            if (stage_warn or review_warn) and not force_submit:
                warn_txt = "；".join([w for w in [stage_warn, review_warn] if w])
                st.warning(f"[{comp}] {warn_txt}（如确认无误可勾选强制提交）")
                skipped_count += 1
                continue

            imgs = images_by_target.get("全部记录", []) + images_by_target.get(comp, [])
            db[sel_proj]["部件列表"][comp].setdefault("日志流", []).append({
                "日期": str(rec_date),
                "流转": "PM速记",
                "工序": final_stage,
                "事件": evt,
                "图片": imgs,
                "提审类型": rv_type,
                "提审结果": rv_res,
                "提审轮次": rv_round
            })
            db[sel_proj]["部件列表"][comp]["主流程"] = final_stage
            saved_count += 1

            kw = str(row.get("新词", "")).strip()
            if (not kw) and auto_learn:
                kw = evt[:6] if len(evt) >= 2 else ""
            if auto_learn and kw and len(kw) >= 2:
                if comp not in ["全局进度", unresolved_comp]:
                    SYS_CFG.setdefault("AI_COMP_KW", {})[kw] = comp
                    learned_count += 1
                if stage_in not in ["", "(维持原阶段)"]:
                    SYS_CFG.setdefault("AI_STAGE_KW", {})[kw] = final_stage
                    learned_count += 1

        if saved_count > 0:
            sync_save_db(sel_proj)
            st.session_state[rk] = []
            st.success(f"已保存 {saved_count} 条速记，跳过 {skipped_count} 条。自动学习词条 {learned_count} 个。")
            st.rerun()
        else:
            st.warning("没有可保存的记录。")

def render_pm_packing_inventory_integrated(sel_proj):
    st.markdown("#### 📦 包装跟踪 + 入库台账（已并入 PM）")
    pack_data = db[sel_proj].get("包装专项", {})
    pack_items = [
        "实物寄厂", "提供刀线", "已称重", "彩盒设计", "灰箱设计", "物流箱设计", "说明书", "感谢信", "杂项纸品"
    ]
    labels = {
        "实物寄厂": "实物寄包装厂", "提供刀线": "提供刀线", "已称重": "内部已称重",
        "彩盒设计": "彩盒设计完毕", "灰箱设计": "灰箱设计完毕", "物流箱设计": "物流箱设计完毕",
        "说明书": "说明书定版", "感谢信": "感谢信定版", "杂项纸品": "杂项纸品确认"
    }
    pack_file_map = db[sel_proj].setdefault("包装物料附件", {})

    done_now = sum(1 for k in pack_items if pack_data.get(k, False))
    st.progress(done_now / max(1, len(pack_items)), text=f"包装进度：{done_now}/{len(pack_items)}")

    new_pack_vals = {}
    cols = st.columns(3)
    for i, k in enumerate(pack_items):
        with cols[i % 3]:
            with st.container(border=True):
                new_pack_vals[k] = st.checkbox(f"{i+1}. {labels[k]}", value=pack_data.get(k, False), key=f"pm2_pack_ck_{sel_proj}_{k}")
                up = st.file_uploader("关联文件", type=['png', 'jpg', 'jpeg', 'pdf'], key=f"pm2_pack_up_{sel_proj}_{k}")
                if up is not None and st.button("➕ 添加附件", key=f"pm2_pack_add_{sel_proj}_{k}"):
                    ref = save_uploaded_file_ref(up, prefix=f"pm2_pack_{norm_text(sel_proj)[:10]}_{i}")
                    if ref:
                        pack_file_map.setdefault(k, []).append(ref)
                        db[sel_proj]["包装物料附件"] = pack_file_map
                        sync_save_db(sel_proj)
                        st.success("已关联附件。")
                        st.rerun()
                st.caption(f"附件数：{len(pack_file_map.get(k, []))}")

    if st.button("💾 保存包装 Checklist", type="primary", key=f"pm2_pack_save_{sel_proj}"):
        db[sel_proj]["包装专项"] = new_pack_vals
        db[sel_proj]["包装物料附件"] = pack_file_map
        sync_save_db(sel_proj)
        st.success("包装清单已保存。")
        st.rerun()

    with st.expander("🗂️ 包装物料附件追溯", expanded=False):
        for k in pack_items:
            refs = pack_file_map.get(k, [])
            if not refs:
                continue
            st.markdown(f"**{labels[k]}**")
            gcols = st.columns(min(4, len(refs)))
            for j, ref in enumerate(refs):
                with gcols[j % len(gcols)]:
                    if str(ref).lower().endswith('.pdf'):
                        st.write(ref)
                    else:
                        render_image(ref, use_container_width=True)
                    if st.button("🗑️", key=f"pm2_pack_del_{sel_proj}_{k}_{j}"):
                        refs.pop(j)
                        db[sel_proj]["包装物料附件"][k] = refs
                        sync_save_db(sel_proj)
                        st.rerun()

    st.markdown("##### 🧮 工厂大货入库台账")
    inv_data = db[sel_proj].get("发货数据", {"总单量": 0, "批次明细": []})
    i1, i2 = st.columns([1, 2])
    with i1:
        total_qty = st.number_input("工厂生产总单量(PCS)", value=int(inv_data.get("总单量", 0)), step=100, key=f"pm2_total_qty_{sel_proj}")
        if st.button("保存总单量", key=f"pm2_save_total_{sel_proj}"):
            db[sel_proj].setdefault("发货数据", {})["总单量"] = int(total_qty)
            sync_save_db(sel_proj)
            st.rerun()

    in_qty = out_qty = 0
    rows = []
    for item in inv_data.get("批次明细", []):
        q = int(item.get("数量", 0))
        if item.get("类型") == "内部领用":
            out_qty += q
        else:
            in_qty += q
        rows.append({"日期": item.get("日期", ""), "类型": item.get("类型", ""), "数量": q, "用途": item.get("备注", "")})

    real_stock = in_qty - out_qty
    factory_left = int(total_qty) - in_qty
    st.write(f"**累计入库:** {in_qty} | **内部领用:** {out_qty} | **可用库存:** {real_stock} | **未交数量:** {factory_left}")

    with st.expander("➕ 登记入库/领用流水", expanded=False):
        r1, r2, r3, r4 = st.columns(4)
        with r1:
            typ = st.selectbox("类型", ["大货入库", "内部领用"], key=f"pm2_inv_typ_{sel_proj}")
        with r2:
            qty = st.number_input("数量", min_value=1, value=10, key=f"pm2_inv_qty_{sel_proj}")
        with r3:
            note = st.text_input("用途/备注", key=f"pm2_inv_note_{sel_proj}")
        with r4:
            st.write("")
            if st.button("登记", key=f"pm2_inv_add_{sel_proj}"):
                db[sel_proj].setdefault("发货数据", {}).setdefault("批次明细", []).append({
                    "日期": str(datetime.date.today()), "类型": typ, "数量": int(qty), "备注": note
                })
                sync_save_db(sel_proj)
                st.rerun()

    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True)


def render_pm_cost_integrated(sel_proj):
    st.markdown("#### 💰 成本分析（已并入 PM）")
    st.caption("支持按工厂/工艺建立多套预计方案；实际入账后自动计算分类占比与差异。")

    c_data = db[sel_proj].setdefault("成本数据", {})
    c1, c2, c3 = st.columns(3)
    with c1:
        orders = st.number_input("总订单数", value=int(c_data.get("总订单数", 0)), step=100, key=f"pmc_orders_{sel_proj}")
    with c2:
        price = st.number_input("目标单价(¥)", value=float(c_data.get("销售单价", 0.0)), step=100.0, key=f"pmc_price_{sel_proj}")
    with c3:
        st.write("")
        if st.button("💾 保存基础参数", key=f"pmc_save_base_{sel_proj}"):
            db[sel_proj].setdefault("成本数据", {})["总订单数"] = int(orders)
            db[sel_proj]["成本数据"]["销售单价"] = float(price)
            sync_save_db(sel_proj)
            st.rerun()

    st.markdown("##### 🧩 预计成本模板（按工艺/工厂）")
    scenario_list = db[sel_proj].setdefault("成本数据", {}).setdefault("预计报价方案", [])
    scenario_names = [x.get("方案名", f"方案{i+1}") for i, x in enumerate(scenario_list)]
    scenario_pick = st.selectbox("选择方案", scenario_names + ["➕ 新建方案"], key=f"pmc_pick_{sel_proj}")

    if scenario_pick == "➕ 新建方案":
        current = {
            "方案名": "", "头版类型": "啤件头版", "工厂": "", "工艺": "",
            "订单量": 0, "备注": "", "建议售价系数": 0.333333,
            "条目": [{"报价项目": nm, "核算工厂报价": 0.0, "备注": ""} for nm in QUOTE_ITEM_DEFAULTS]
        }
        s_idx = None
    else:
        s_idx = scenario_names.index(scenario_pick)
        current = scenario_list[s_idx]
        current.setdefault("条目", [{"报价项目": nm, "核算工厂报价": 0.0, "备注": ""} for nm in QUOTE_ITEM_DEFAULTS])

    fk = f"pmc_{sel_proj}_{'new' if s_idx is None else s_idx}"
    q1, q2, q3, q4, q5, q6 = st.columns([1.2, 1, 1, 1, 1, 1.2])
    with q1:
        sc_name = st.text_input("方案名", value=current.get("方案名", ""), key=f"pmc_name_{fk}")
    with q2:
        sc_head = st.selectbox("头版类型", ["啤件头版", "翻模头版", "其他"],
                               index=["啤件头版", "翻模头版", "其他"].index(current.get("头版类型", "啤件头版")) if current.get("头版类型", "啤件头版") in ["啤件头版", "翻模头版", "其他"] else 0,
                               key=f"pmc_head_{fk}")
    with q3:
        sc_factory = st.text_input("工厂", value=current.get("工厂", ""), key=f"pmc_factory_{fk}")
    with q4:
        sc_process = st.text_input("工艺", value=current.get("工艺", ""), key=f"pmc_process_{fk}")
    with q5:
        sc_qty = st.number_input("订单量", min_value=0, value=int(current.get("订单量", 0)), step=100, key=f"pmc_qty_{fk}")
    with q6:
        sc_coef = st.number_input("建议售价系数", min_value=0.05, max_value=1.0, value=float(current.get("建议售价系数", 0.333333)), step=0.01, key=f"pmc_coef_{fk}")

    sc_note = st.text_input("方案备注", value=current.get("备注", ""), key=f"pmc_note_{fk}")
    qdf = pd.DataFrame(current.get("条目", []))
    if qdf.empty:
        qdf = pd.DataFrame([{"报价项目": nm, "核算工厂报价": 0.0, "备注": ""} for nm in QUOTE_ITEM_DEFAULTS])
    qdf = st.data_editor(qdf, num_rows="dynamic", use_container_width=True, key=f"pmc_editor_{fk}")
    qdf["核算工厂报价"] = pd.to_numeric(qdf.get("核算工厂报价", 0.0), errors="coerce").fillna(0.0)

    total_est = float(qdf["核算工厂报价"].sum()) if "核算工厂报价" in qdf.columns else 0.0
    suggest_price = (total_est / sc_coef) if sc_coef > 0 else 0.0
    st.info(f"预计整套成本价：¥{total_est:,.2f} | 建议售价：¥{suggest_price:,.2f}")

    s1, s2 = st.columns(2)
    with s1:
        if st.button("💾 保存/更新预计方案", type="primary", key=f"pmc_save_scn_{fk}"):
            payload = {
                "方案名": sc_name or f"方案{len(scenario_list)+1}",
                "头版类型": sc_head,
                "工厂": sc_factory,
                "工艺": sc_process,
                "订单量": int(sc_qty),
                "备注": sc_note,
                "建议售价系数": float(sc_coef),
                "预计整套成本价": round(total_est, 2),
                "建议售价": round(suggest_price, 2),
                "条目": qdf.to_dict("records")
            }
            if s_idx is None:
                scenario_list.append(payload)
            else:
                scenario_list[s_idx] = payload
            sync_save_db(sel_proj)
            st.rerun()
    with s2:
        if scenario_pick != "➕ 新建方案" and st.button("🗑️ 删除当前方案", key=f"pmc_del_scn_{fk}"):
            scenario_list.pop(s_idx)
            sync_save_db(sel_proj)
            st.rerun()

    if scenario_list:
        comp_df = pd.DataFrame([
            {
                "方案名": x.get("方案名", ""), "头版": x.get("头版类型", ""), "工厂": x.get("工厂", ""),
                "工艺": x.get("工艺", ""), "订单量": x.get("订单量", 0),
                "预计整套成本价": x.get("预计整套成本价", 0.0), "建议售价": x.get("建议售价", 0.0)
            }
            for x in scenario_list
        ])
        st.dataframe(comp_df, use_container_width=True)

    st.markdown("##### ➕ 实际成本录入")
    a1, a2, a3, a4, a5 = st.columns([2, 2, 2, 1.2, 1.2])
    with a1:
        c_name = st.selectbox("成本分类", STD_COSTS_LIST, key=f"pmc_add_cat_{sel_proj}")
    with a2:
        vendor = st.text_input("供应商", key=f"pmc_add_vendor_{sel_proj}")
    with a3:
        c_unit = st.number_input("税后单价(¥)", min_value=0.0, step=100.0, key=f"pmc_add_unit_{sel_proj}")
    with a4:
        c_qty = st.number_input("数量", min_value=1.0, value=1.0, step=1.0, key=f"pmc_add_qty_{sel_proj}")
    with a5:
        tax_rate = st.selectbox("税点(%)", [0, 1, 3, 6, 9, 13], key=f"pmc_add_tax_{sel_proj}")
    if st.button("入账", key=f"pmc_add_btn_{sel_proj}"):
        db[sel_proj].setdefault("成本数据", {}).setdefault("动态明细", []).append({
            "分类": c_name,
            "供应商": vendor,
            "税后单价": float(c_unit),
            "数量": float(c_qty),
            "税后总成本": float(Decimal(str(c_unit)) * Decimal(str(c_qty))),
            "税点": f"{tax_rate}%",
            "税前总成本": float(round((Decimal(str(c_unit)) * Decimal(str(c_qty))) /
                                         (Decimal("1") + Decimal(str(tax_rate)) / Decimal("100")), 2))
        })
        sync_save_db(sel_proj)
        st.rerun()

    details = c_data.get("动态明细", [])
    if not details:
        st.info("暂无实际成本数据，录入后会自动展示成本占比与差异分析。")
        return

    for d in details:
        if '含税金额' in d and '税后总成本' not in d:
            d['税后总成本'] = d['含税金额']
            d['数量'] = 1.0
            d['税后单价'] = d['含税金额']
            if '税前金额' in d:
                d['税前总成本'] = d['税前金额']

    df_cost = pd.DataFrame(details)
    show_cols = ['分类', '供应商', '税后单价', '数量', '税后总成本', '税点', '税前总成本']
    df_cost = df_cost[[c for c in show_cols if c in df_cost.columns]]

    subtotals = df_cost.groupby('分类', dropna=False)['税后总成本'].sum().reset_index()
    total_sub = float(subtotals['税后总成本'].sum()) if not subtotals.empty else 0.0
    if total_sub > 0:
        share_df = subtotals.copy()
        share_df['成本占比'] = (share_df['税后总成本'] / total_sub * 100).round(2).astype(str) + '%'
        st.markdown("###### 🧮 各分类成本占比")
        st.dataframe(share_df.sort_values(by='税后总成本', ascending=False), use_container_width=True)

    edited_df = st.data_editor(df_cost, num_rows="dynamic", use_container_width=True, key=f"pmc_detail_editor_{sel_proj}")
    if st.button("💾 保存实际成本修改", key=f"pmc_detail_save_{sel_proj}"):
        for idx, row in edited_df.iterrows():
            try:
                qty_d = Decimal(str(row.get('数量', 1.0)))
                unit_d = Decimal(str(row.get('税后单价', 0.0)))
                tax_str = str(row.get('税点', '0%')).replace('%', '')
                rate_d = Decimal(tax_str) if tax_str else Decimal("0.0")
                tot_d = qty_d * unit_d
                tax_div = Decimal("1") + (rate_d / Decimal("100"))
                edited_df.at[idx, '税后总成本'] = float(tot_d)
                edited_df.at[idx, '税前总成本'] = float(round(tot_d / tax_div, 2))
            except:
                pass
        db[sel_proj]["成本数据"]["动态明细"] = edited_df.to_dict('records')
        sync_save_db(sel_proj)
        st.rerun()

    if scenario_list:
        st.markdown("###### 📉 实际成本 vs 预计成本")
        diff_pick = st.selectbox("选择预计方案", scenario_names, key=f"pmc_diff_pick_{sel_proj}")
        picked = next((x for x in scenario_list if x.get("方案名", "") == diff_pick), scenario_list[0])
        actual_total = float(pd.to_numeric(edited_df.get("税后总成本", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
        est_total = float(picked.get("预计整套成本价", 0.0))
        delta = actual_total - est_total
        delta_rate = (delta / est_total * 100) if est_total > 0 else 0.0

        m1, m2, m3 = st.columns(3)
        m1.metric("预计总成本", f"¥{est_total:,.2f}")
        m2.metric("实际总成本", f"¥{actual_total:,.2f}")
        m3.metric("差异(实际-预计)", f"¥{delta:,.2f}", delta=f"{delta_rate:.2f}%")

        act_by_cat = edited_df.groupby("分类", dropna=False)["税后总成本"].sum().reset_index().rename(columns={"税后总成本": "实际成本"})
        est_items = pd.DataFrame(picked.get("条目", []))
        if est_items.empty:
            est_by_cat = pd.DataFrame(columns=["分类", "预计成本"])
        else:
            est_items["核算工厂报价"] = pd.to_numeric(est_items.get("核算工厂报价", 0.0), errors="coerce").fillna(0.0)
            est_by_cat = est_items.rename(columns={"报价项目": "分类", "核算工厂报价": "预计成本"})[["分类", "预计成本"]]
        cmp_df = est_by_cat.merge(act_by_cat, on="分类", how="outer").fillna(0.0)
        cmp_df["差异"] = cmp_df["实际成本"] - cmp_df["预计成本"]
        cmp_df["差异率"] = cmp_df.apply(lambda r: f"{(r['差异'] / r['预计成本'] * 100):.2f}%" if r["预计成本"] > 0 else "-", axis=1)
        st.dataframe(cmp_df.sort_values(by="差异", ascending=False), use_container_width=True)

def render_pm_efficiency(sel_proj):
    st.subheader("⏱️ 团队效能与工时分析板")
    efficiency_data = []
    for c_name, info in db[sel_proj].get('部件列表', {}).items():
        if c_name == "全局进度":
            continue
        logs = info.get('日志流', [])
        owner_str = info.get('负责人', '未分配')
        stage_times = {}
        for log in logs:
            stg = log.get('工序', '')
            try:
                date_obj = datetime.datetime.strptime(log['日期'], "%Y-%m-%d").date()
            except:
                continue
            if stg not in stage_times:
                stage_times[stg] = {'start': date_obj, 'end': None}
            if "彻底完成" in log.get('事件', '') or "OK" in log.get('事件', ''):
                stage_times[stg]['end'] = date_obj
        for stg, times in stage_times.items():
            if times['end']:
                days_spent = max(1, (times['end'] - times['start']).days)
                efficiency_data.append({
                    "部件": c_name,
                    "工序": stg,
                    "耗时(天)": days_spent,
                    "参与人员": owner_str
                })
    if efficiency_data:
        df_eff = pd.DataFrame(efficiency_data)
        top_cols = st.columns(3)
        top_cols[0].metric("闭环记录数", len(df_eff))
        top_cols[1].metric("平均耗时(天)", round(float(df_eff['耗时(天)'].mean()), 2))
        top_cols[2].metric("中位耗时(天)", round(float(df_eff['耗时(天)'].median()), 2))
        st.dataframe(df_eff.sort_values(by=["耗时(天)", "工序"], ascending=[False, True]), use_container_width=True)
    else:
        st.info("💡 暂无完整闭环的工时记录。勾选【彻底完成】后即可激活此工时排行榜！")
# ==========================================
# 4. 视图控制层
# ==========================================
st.sidebar.title("🚀 INART PM 系统")
pm_list    = ["所有人", "Mo", "越", "袁"]
current_pm = st.sidebar.selectbox("👤 视角切换", pm_list)

db          = st.session_state.db
valid_projs = get_visible_projects(db, current_pm)

menu = st.sidebar.radio("模块导航", [
    MENU_DASHBOARD, MENU_SPECIFIC,
    MENU_HISTORY, MENU_SETTINGS, MENU_GUIDE
])
st.sidebar.caption("说明：原【速记/包装入库/成本台账】已并入【🎯 PM 工作台】")

# 备份与恢复
st.sidebar.divider()
st.sidebar.markdown("### ⚙️ 数据备份与恢复")
try:
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        json_bytes = json.dumps(st.session_state.db, ensure_ascii=False, indent=4).encode('utf-8')
        zf.writestr("database.json", json_bytes)
        if os.path.exists("img_assets"):
            for img_name in os.listdir("img_assets"):
                img_path = os.path.join("img_assets", img_name)
                if os.path.isfile(img_path):
                    zf.write(img_path, arcname=f"img_assets/{img_name}")
    zip_buffer.seek(0)
    st.sidebar.download_button(
        "💾 下载全量备份 (数据+图片)", data=zip_buffer,
        file_name=f"inart_pm_full_backup_{datetime.date.today()}.zip",
        mime="application/zip"
    )
except Exception as e:
    st.sidebar.warning(f"备份生成失败: {e}")

restore_file = st.sidebar.file_uploader("📂 上传备份以恢复", type=['zip', 'json'])
if restore_file is not None and st.sidebar.button("⚠️ 确认覆盖恢复", type="primary"):
    try:
        if restore_file.name.endswith(".zip"):
            with zipfile.ZipFile(restore_file, "r") as zf:
                if "database.json" in zf.namelist():
                    with zf.open("database.json") as f:
                        restored_data = json.load(f)
                else:
                    st.sidebar.error("❌ 压缩包内未找到 database.json！")
                    st.stop()
                if not os.path.exists("img_assets"):
                    os.makedirs("img_assets")
                for item in zf.namelist():
                    if item.startswith("img_assets/") and not item.endswith('/'):
                        zf.extract(item, path=".")
        else:
            restored_data = json.load(restore_file)
        db_manager.save(restored_data)
        st.session_state.db = restored_data
        st.sidebar.success("🎉 恢复成功！请手动刷新网页！")
    except Exception as e:
        st.sidebar.error(f"解析失败: {e}")

# ==========================================
# 模块 1：大盘与甘特图
# ==========================================
if menu == MENU_DASHBOARD:
    st.title(f"📊 全局大盘与进度甘特图 ({current_pm} 的视角)")

    with st.expander("📥 批量导入/更新研发总表 (CSV)"):
        st.info("💡 支持自动识别含有【项目名称】、【负责人】、【当前阶段】、【开定时间】、【发货区间】、【跟单】等列的 CSV 文件。")
        rd_csv = st.file_uploader("选择研发总表 CSV 文件", type=['csv'], key="rd_csv_uploader")
        if rd_csv and st.button("🚀 开始解析导入", type="primary"):
            try:
                try:
                    df_rd = pd.read_csv(rd_csv)
                except UnicodeDecodeError:
                    rd_csv.seek(0)
                    df_rd = pd.read_csv(rd_csv, encoding='gbk')

                col_proj = next((c for c in df_rd.columns if any(k in str(c) for k in ['项目', '名称', '产品'])), None)
                col_pm   = next((c for c in df_rd.columns if any(k in str(c) for k in ['负责', 'PM'])), None)
                col_ms   = next((c for c in df_rd.columns if any(k in str(c) for k in ['阶段', '状态', 'Milestone'])), None)
                col_tgt  = next((c for c in df_rd.columns if any(k in str(c) for k in ['开定', 'Target', '目标'])), None)
                col_ship = next((c for c in df_rd.columns if any(k in str(c) for k in ['发货', '出货'])), None)
                col_gd   = next((c for c in df_rd.columns if any(k in str(c) for k in ['跟单'])), None)

                if not col_proj:
                    st.error("❌ 未能找到【项目名称】列，请检查表头！")
                else:
                    count_new = count_update = 0
                    for _, row in df_rd.iterrows():
                        p_name = str(row[col_proj]).strip()
                        if not p_name or p_name.lower() in ['nan', 'none', '']:
                            continue
                        pm_val   = str(row[col_pm]).strip()   if col_pm   and not pd.isna(row[col_pm])   else ""
                        ms_val   = str(row[col_ms]).strip()   if col_ms   and not pd.isna(row[col_ms])   else "待立项"
                        tgt_val  = str(row[col_tgt]).strip()  if col_tgt  and not pd.isna(row[col_tgt])  else "TBD"
                        ship_val = str(row[col_ship]).strip() if col_ship and not pd.isna(row[col_ship]) else ""
                        gd_val   = str(row[col_gd]).strip()   if col_gd   and not pd.isna(row[col_gd])   else ""
                        if pm_val.lower()   == 'nan': pm_val   = ""
                        if ms_val.lower()   == 'nan': ms_val   = "待立项"
                        if tgt_val.lower()  == 'nan': tgt_val  = "TBD"
                        if ship_val.lower() == 'nan': ship_val = ""
                        ship_val = ship_val.upper().replace('-', ' ').strip()
                        m_q = re.match(r'^(\d{4})\s*Q([1-4])$', ship_val)
                        ship_val = f"{m_q.group(1)} Q{m_q.group(2)}" if m_q else ""
                        if gd_val.lower()   == 'nan': gd_val   = ""
                        if p_name not in db:
                            db[p_name] = {"负责人": pm_val, "跟单": gd_val, "Milestone": ms_val,
                                          "Target": tgt_val, "发货区间": ship_val,
                                          "部件列表": {}, "发货数据": {}, "成本数据": {}}
                            count_new += 1
                        else:
                            if col_pm   and not pd.isna(row[col_pm]):   db[p_name]["负责人"]   = pm_val
                            if col_ms   and not pd.isna(row[col_ms]):   db[p_name]["Milestone"] = ms_val
                            if col_tgt  and not pd.isna(row[col_tgt]):  db[p_name]["Target"]    = tgt_val
                            if col_ship and not pd.isna(row[col_ship]): db[p_name]["发货区间"]  = ship_val
                            if col_gd   and not pd.isna(row[col_gd]):   db[p_name]["跟单"]      = gd_val
                            count_update += 1
                    sync_save_db()
                    st.success(f"🎉 导入完毕！新增: {count_new} 个，更新: {count_update} 个。")
                    st.rerun()
            except Exception as e:
                st.error(f"解析失败: {e}")

    gantt_cat_orders = MACRO_STAGES.copy()
    combined_color_map = {
        "立项": "#FFB84C", "建模": "#2CD3E1", "设计": "#A555EC",
        "工程": "#4D96FF", "模具": "#F47C7C", "修模": "#FF7B54",
        "生产": "#6BCB77", "暂停": "#B2B2B2", "结束": "#1A1A2E"
    }

    # ── 缓存大盘计算结果（TTL=30s，切模块不重算）──
    @st.cache_data(ttl=30, show_spinner=False)
    def _build_dash(proj_list_key: str, db_hash: str):
        _table = []; _gantt = []; _ppr = []; _sx = []; _sy = []; _meta = []
        for proj in valid_projs:
            data = db[proj]
            if not data.get('部件列表') and not data.get('Milestone') and not data.get('Target'):
                _table.append({"状态":"⚪ 未知阶段","项目":proj,"跟单":"","项目当前阶段":"待立项",
                    "开定时间":"TBD","预计发货":"-","断更":"-","最新全盘动态":"无数据"}); continue
            gd=data.get('跟单',''); ms=data.get('Milestone',''); tgt=data.get('Target','TBD')
            ship_itv=data.get('发货区间','-'); r_txt,_=get_risk_status(ms,tgt)
            comps=data.get('部件列表',{})
            proj_y_label=f"{proj} 📦[{ship_itv}]" if ship_itv and ship_itv!='-' else proj
            if not comps:
                _table.append({"状态":r_txt,"项目":proj,"跟单":gd,"项目当前阶段":ms,
                    "开定时间":tgt,"预计发货":ship_itv,"断更":"-","最新全盘动态":"无数据"}); continue
            latest_date_obj=None; latest_event_str="无数据"; latest_comp_name="-"; grouped={}
            for c_name,info in comps.items():
                for pair in re.split(r'[,，|]',str(info.get('负责人','')).strip()):
                    pair=pair.strip()
                    if not pair or pair=='未分配': continue
                    if '-' in pair: rp,pp=pair.split('-',1); _ppr.append((proj,pp.strip(),rp.strip()))
                    elif ':' in pair: rp,pp=pair.split(':',1); _ppr.append((proj,pp.strip(),rp.strip()))
                    else: _ppr.append((proj,pair,"综合"))
                logs=[lg for lg in info.get('日志流',[]) if not is_hidden_system_log(lg)]
                if logs:
                    try:
                        l_dt=datetime.datetime.strptime(logs[-1]['日期'],"%Y-%m-%d").date()
                        if latest_date_obj is None or l_dt>latest_date_obj:
                            latest_date_obj=l_dt; latest_event_str=logs[-1]['事件']; latest_comp_name=c_name
                    except: pass
                for log in logs:
                    log_stage = log.get('工序', info.get('主流程', '未知'))
                    macro_stage = get_macro_phase(log_stage)
                    try:
                        dt_obj = datetime.datetime.strptime(log['日期'], "%Y-%m-%d")
                        evt    = log['事件']
                        k = (dt_obj, macro_stage, evt)
                        if k not in grouped:
                            grouped[k] = {"日期_obj": dt_obj, "日期_str": log['日期'],
                                          "工序": macro_stage, "事件": evt,
                                          "部件": [c_name], "is_pause": is_pause_stage(macro_stage),
                                          "提审类型": log.get("提审类型", ""), "提审结果": log.get("提审结果", "")}
                        elif c_name not in grouped[k]["部件"]:
                            grouped[k]["部件"].append(c_name)
                    except: pass
            dt_txt=f"{(datetime.date.today()-latest_date_obj).days} 天" if latest_date_obj else "-"
            ce=latest_event_str
            if "补充:" in ce: ce=ce.split("补充:")[-1].split("[系统]")[0].strip()
            elif "】" in ce: ce=ce.split("】")[-1].split("[系统]")[0].strip()
            _table.append({"状态":r_txt,"项目":proj,"跟单":gd,"项目当前阶段":ms,
                "开定时间":tgt,"预计发货":ship_itv,"断更":dt_txt,"最新全盘动态":f"[{latest_comp_name}] {ce}"})
            _meta.append({
                "项目": proj,
                "项目标签": proj_y_label,
                "最近更新": latest_date_obj.strftime("%Y-%m-%d") if latest_date_obj else "0001-01-01",
                "是否暂停": 1 if str(ms).strip() == "暂停研发" else 0,
                "是否完结": 1 if str(ms).strip() in ["生产结束", "项目结束撒花🎉", "✅ 已完成(结束)"] else 0
            })
            try:
                if tgt and tgt.upper()!='TBD':
                    pt=datetime.datetime.strptime(f"{tgt}-01" if len(tgt)==7 else tgt[:10],"%Y-%m-%d")
                    _sx.append(pt.strftime("%Y-%m-%d")); _sy.append(proj_y_label)
            except: pass
            all_logs = sorted(grouped.values(), key=lambda x: x["日期_obj"])
            if all_logs:
                # 找出暂停时间段：[pause_start, resume_start) 之间的普通日志不产生甘特色块
                # 暂停节点：工序=="暂停" 的日志日期
                # 恢复节点：暂停之后第一条非暂停日志日期
                pause_intervals = []  # list of (pause_dt, resume_dt or None)
                in_pause = False; pause_start = None
                for lg in all_logs:
                    if lg["is_pause"] and not in_pause:
                        in_pause = True; pause_start = lg["日期_obj"]
                    elif not lg["is_pause"] and in_pause:
                        pause_intervals.append((pause_start, lg["日期_obj"]))
                        in_pause = False; pause_start = None
                if in_pause and pause_start:
                    pause_intervals.append((pause_start, None))  # 还未恢复

                def in_pause_period(dt):
                    """日志日期是否落在某个暂停区间内（暂停后、恢复前）"""
                    for ps, pe in pause_intervals:
                        if dt > ps and (pe is None or dt < pe):
                            return True
                    return False

                cs = all_logs[0]["工序"]; sd = all_logs[0]["日期_obj"]; buf = []
                for i, log in enumerate(all_logs):
                    # 暂停区间内的非暂停日志（系统自动追踪等）跳过，不产生甘特色块
                    if log["工序"] != "暂停" and in_pause_period(log["日期_obj"]):
                        continue
                    rv_type = str(log.get("提审类型", "")).strip()
                    rv_res = str(log.get("提审结果", "")).strip()
                    rv_txt = ""
                    if rv_type and rv_type != "(无)":
                        rv_txt = f" | 提审:{rv_type}"
                        if rv_res and rv_res != "(无)":
                            rv_txt += f"/{rv_res}"
                        rv_round = str(log.get("提审轮次", "")).strip()
                        if rv_round:
                            rv_txt += f"/第{rv_round}轮"
                    buf.append(f"[{log['日期_str']}] [{', '.join(log['部件'])}] {log['事件']}{rv_txt}")
                    is_last  = (i == len(all_logs) - 1)
                    # 看下一条有效日志（同样跳过暂停区间内的）
                    ns = None
                    for j in range(i + 1, len(all_logs)):
                        nxt = all_logs[j]
                        if nxt["工序"] == "暂停" or not in_pause_period(nxt["日期_obj"]):
                            ns = nxt["工序"]; break
                    if is_last or ns != cs:
                        # 暂停在甘特图里只占 1 天体量：暂停当天有色块，后续保持留白直到恢复
                        if cs == "暂停":
                            ed = sd + datetime.timedelta(days=1)
                        else:
                            ed = log["日期_obj"]
                            if sd == ed: ed += datetime.timedelta(days=1)
                        _gantt.append({"项目": proj_y_label, "工序阶段": cs,
                                       "Start": sd.strftime("%Y-%m-%d"),
                                       "Finish": ed.strftime("%Y-%m-%d"),
                                       "详情": "<br>".join([f"• {e}" for e in buf])})
                        if not is_last:
                            cs = ns if ns else log["工序"]
                            sd = log["日期_obj"]; buf = []
        return _table, _gantt, _ppr, _sx, _sy, _meta

    # cache key：项目列表 + 数据指纹（只用非图片字段的哈希）
    import hashlib as _hl
    _db_sig = _hl.md5(json.dumps(
        {k:{fk:fv for fk,fv in v.items() if fk not in ("配件清单长图",)}
         for k,v in db.items() if k!="系统配置"},
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    table_data, gantt_data, _ppr_list, star_x, star_y, _meta = _build_dash(",".join(valid_projs), _db_sig)
    project_person_roles = set(map(tuple, _ppr_list))


    st.divider()
    st.subheader("📈 全局进展甘特图")
    st.markdown("💡 支持按时间区间筛选；并统计建模/设计/工程平均耗时（可选去极值）。")
    if gantt_data:
        df_g = pd.DataFrame(gantt_data).sort_values(by=["项目", "Start"])
        df_g["Start_dt"] = pd.to_datetime(df_g["Start"], errors="coerce")
        df_g["Finish_dt"] = pd.to_datetime(df_g["Finish"], errors="coerce")
        d1, d2 = st.columns(2)
        with d1:
            gantt_start = st.date_input("甘特开始日期", value=df_g["Start_dt"].min().date() if not df_g["Start_dt"].isna().all() else datetime.date.today(), key="gantt_start")
        with d2:
            gantt_end = st.date_input("甘特结束日期", value=df_g["Finish_dt"].max().date() if not df_g["Finish_dt"].isna().all() else datetime.date.today(), key="gantt_end")
        m = (df_g["Finish_dt"] >= pd.to_datetime(gantt_start)) & (df_g["Start_dt"] <= pd.to_datetime(gantt_end))
        df_g = df_g[m]

        if _meta:
            df_meta = pd.DataFrame(_meta).drop_duplicates(subset=["项目标签"])
            df_meta["最近更新_dt"] = pd.to_datetime(df_meta["最近更新"], errors="coerce").fillna(pd.Timestamp.min)
            df_meta["有更新"] = (df_meta["最近更新"] != "0001-01-01").astype(int)
            df_meta = df_meta.sort_values(by=["有更新", "最近更新_dt", "项目标签"], ascending=[False, False, True])
            y_order = df_meta["项目标签"].tolist()
        else:
            y_order = sorted(df_g["项目"].unique().tolist())
        fig = px.timeline(
            df_g, x_start="Start", x_end="Finish", y="项目",
            color="工序阶段", hover_name="详情",
            category_orders={"工序阶段": gantt_cat_orders, "项目": y_order},
            color_discrete_map=combined_color_map
        )
        if star_x:
            fig.add_trace(go.Scatter(
                x=star_x, y=star_y, mode='markers',
                marker=dict(symbol='star', size=24, color='#FFD700',
                            line=dict(width=2, color='#FF4500')),
                name='📅 目标开定',
                hovertemplate='目标开定: %{x}<extra></extra>'
            ))
        fig.update_yaxes(autorange="reversed")
        fig.update_layout(height=max(400, len(df_g['项目'].unique()) * 45))
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("#### ⏱️ 建模/设计/工程平均耗时（天）")
        trim_outlier = st.checkbox("去掉最大值和最小值（样本>=3时）", value=False, key="trim_stage_avg")
        df_dur = df_g.copy()
        df_dur["天数"] = (df_dur["Finish_dt"] - df_dur["Start_dt"]).dt.days.clip(lower=1)
        focus = df_dur[df_dur["工序阶段"].isin(["建模", "设计", "工程"])]
        rows = []
        for stg in ["建模", "设计", "工程"]:
            vals = focus[focus["工序阶段"] == stg]["天数"].dropna().tolist()
            vals = [int(v) for v in vals if v >= 1]
            if trim_outlier and len(vals) >= 3:
                vals = sorted(vals)[1:-1]
            avg = round(sum(vals)/len(vals), 2) if vals else 0
            rows.append({"阶段": stg, "样本数": len(vals), "平均耗时(天)": avg})
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    else:
        st.warning("无足够日志数据生成甘特图。")

    st.subheader("📋 大盘状态明细表")
    if table_data:
        df_table = pd.DataFrame(table_data)
        df_table["开定延迟预警"] = ""
        df_table["发货延迟预警"] = ""
        df_table["状态组"] = 2
        df_table.loc[df_table["状态"].str.contains("研发|生产", na=False), "状态组"] = 0
        df_table.loc[df_table["状态"].str.contains("暂停", na=False), "状态组"] = 1
        df_table.loc[df_table["状态"].str.contains("未知", na=False), "状态组"] = 2
        df_table.loc[df_table["状态"].str.contains("结案", na=False), "状态组"] = 3
        df_table["开定排序"] = pd.to_datetime(df_table["开定时间"], errors="coerce")
        df_table["发货排序"] = df_table["预计发货"].apply(lambda x: quarter_to_deadline(x) or datetime.date.max)
        df_table["发货排序"] = pd.to_datetime(df_table["发货排序"], errors="coerce")
        df_table["断更天"] = df_table["断更"].str.extract(r'(\d+)').fillna('99999').astype(int)
        df_table["主排序时间"] = pd.Timestamp.max
        dev_mask = df_table["状态"].str.contains("研发", na=False)
        prod_mask = df_table["状态"].str.contains("生产", na=False)
        df_table.loc[dev_mask, "主排序时间"] = df_table.loc[dev_mask, "开定排序"].fillna(pd.Timestamp.max)
        df_table.loc[prod_mask, "主排序时间"] = df_table.loc[prod_mask, "发货排序"].fillna(pd.Timestamp.max)
        for i, r in df_table.iterrows():
            stt = str(r.get("状态", ""))
            if "研发" in stt and is_due_soon(r.get("开定时间", ""), 5):
                df_table.at[i, "开定延迟预警"] = "⚠️ +5天临期"
            if "生产" in stt and is_due_soon(r.get("预计发货", ""), 5):
                df_table.at[i, "发货延迟预警"] = "⚠️ +5天临期"

        df_table = df_table.sort_values(by=["状态组", "主排序时间", "断更天", "项目"], ascending=[True, True, True, True])
        show_df = df_table.drop(columns=["状态组", "开定排序", "发货排序", "断更天", "主排序时间"])

        def _hl_warn(v):
            return 'background-color: #fef08a; color: #111827; font-weight: 600' if str(v).strip() else ''

        st.caption("提示：开定/发货 +5 天临期会高亮黄色，仍可点击表头二次排序。")
        st.dataframe(
            show_df.style.map(_hl_warn, subset=["开定延迟预警", "发货延迟预警"]),
            use_container_width=True
        )

        with st.expander("✏️ 大盘状态快速更新（项目状态/开定/发货）", expanded=False):
            st.caption("建议用于紧急修正；日常详细过程仍建议在 PM 工作台更新。")
            edit_proj = st.selectbox("项目", valid_projs, key="dash_quick_edit_proj")
            cur_d = db.get(edit_proj, {})
            e1, e2, e3, e4 = st.columns(4)
            with e1:
                edit_ms = st.selectbox("项目状态", STD_MILESTONES,
                                       index=STD_MILESTONES.index(cur_d.get("Milestone", "待立项")) if cur_d.get("Milestone", "待立项") in STD_MILESTONES else 0,
                                       key="dash_quick_edit_ms")
            with e2:
                edit_target = st.text_input("预计开定", value=str(cur_d.get("Target", "TBD")), key="dash_quick_edit_target")
            with e3:
                edit_ship = st.text_input("预计发货区间", value=str(cur_d.get("发货区间", "")), key="dash_quick_edit_ship")
            with e4:
                edit_pm = st.selectbox("负责人", ["Mo", "越", "袁"],
                                       index=["Mo", "越", "袁"].index(cur_d.get("负责人", "Mo")) if cur_d.get("负责人", "Mo") in ["Mo", "越", "袁"] else 0,
                                       key="dash_quick_edit_pm")

            if st.button("💾 保存大盘快速更新", key="dash_quick_save", type="primary"):
                db[edit_proj]["Milestone"] = edit_ms
                db[edit_proj]["Target"] = edit_target
                db[edit_proj]["发货区间"] = edit_ship
                db[edit_proj]["负责人"] = edit_pm
                td = str(datetime.date.today())
                comps = db[edit_proj].setdefault("部件列表", {})
                gk = "全局进度" if "全局进度" in comps else (next(iter(comps.keys()), "全局进度"))
                if gk not in comps:
                    comps[gk] = {"主流程": STAGES_UNIFIED[0], "日志流": []}
                comps[gk].setdefault("日志流", []).append({
                    "日期": td,
                    "流转": "系统更新",
                    "工序": comps[gk].get("主流程", STAGES_UNIFIED[0]),
                    "事件": f"[大盘快速更新] 状态:{edit_ms} | 开定:{edit_target} | 发货:{edit_ship} | PM:{edit_pm}"
                })
                sync_save_db(edit_proj)
                st.success("已更新。")
                st.rerun()

    st.divider()
    if project_person_roles:
        df_ppr   = pd.DataFrame(list(project_person_roles), columns=["项目", "人员", "职务"])
        df_owner = df_ppr.groupby(["人员", "职务"]).size().reset_index(name='积压项目数')
        df_owner["积压项目数"] = df_owner["积压项目数"].astype(int)
        fig_owner = px.bar(df_owner, x='人员', y='积压项目数', color='职务',
                           title="👤 团队&责任人 Loading", text='积压项目数')
        fig_owner.update_yaxes(dtick=1)
        st.plotly_chart(fig_owner, use_container_width=True)

        st.markdown("#### 📌 Function 去重项目数（进行中）")
        table_df = pd.DataFrame(table_data)
        ongoing = table_df[table_df["状态"].str.contains("研发|生产", na=False)][["项目"]]
        role_df = df_ppr.merge(ongoing, on="项目", how="inner").drop_duplicates(subset=["项目", "职务"])
        focus_roles = ["监修", "建模", "设计", "工程"]
        if role_df.empty:
            fn_stats = pd.DataFrame({"职能": focus_roles, "进行中项目数": [0] * len(focus_roles)})
        else:
            role_df["职能"] = role_df["职务"].apply(lambda x: next((r for r in focus_roles if r in str(x)), None))
            fn_stats = role_df.dropna(subset=["职能"]).groupby("职能")["项目"].nunique().reindex(focus_roles, fill_value=0).reset_index(name="进行中项目数")
        st.dataframe(fn_stats, use_container_width=True)

# ==========================================
# 模块 2：特定项目管控台
# ==========================================
elif menu == MENU_SPECIFIC:
    st.title("🎯 PM 工作台")

    if st.button("➕ 手动建档新项目"):
        st.session_state.new_proj_mode = not st.session_state.get('new_proj_mode', False)
    if st.session_state.get('new_proj_mode', False):
        with st.container(border=True):
            c_n1, c_n2, c_n3 = st.columns(3)
            with c_n1: new_p  = st.text_input("新项目名称 (如: 1/6 新蝙蝠侠)")
            with c_n2: new_pm = st.selectbox("分配负责人", ["Mo", "越", "袁"], index=0)
            with c_n3:
                st.write("")
                if st.button("✅ 确认创建", type="primary"):
                    if new_p and new_p not in db:
                        db[new_p] = {"负责人": new_pm, "跟单": "", "Milestone": "待立项",
                                     "Target": "TBD", "发货区间": "",
                                     "部件列表": {}, "发货数据": {}, "成本数据": {}}
                        sync_save_db(new_p)
                        st.success(f"建档成功！已分配给 {new_pm}")
                        st.toast(f"📌 已创建项目：{new_p}")
                        st.session_state.new_proj_mode = False
                        st.rerun()
                    elif new_p in db:
                        st.warning("项目已存在，请更换名称。")
                    else:
                        st.error("项目名称不能为空。")

    st.markdown("<div class='pm-section-title'>📌 PM 工作台入口</div>", unsafe_allow_html=True)
    st.markdown("<div class='pm-kv-note'>To do 与批量速记并列；建议先复盘再落项目。</div>", unsafe_allow_html=True)
    tab_todo, tab_batch = st.tabs(["🗂️ To do", "📝 批量速记"])
    with tab_todo:
        todo_list = render_pm_todo_manager(valid_projs)
    with tab_batch:
        render_pm_batch_fastlog_integrated(valid_projs)

    st.divider()

    if not valid_projs:
        st.warning("当前视角下暂无项目，可先维护 To do List。")
        st.stop()

    st.markdown("<div class='pm-section-title'>🎯 特定项目操作</div>", unsafe_allow_html=True)
    if 'current_proj_context' not in st.session_state:
        st.session_state.current_proj_context = valid_projs[0] if valid_projs else None
    sel_proj = st.selectbox("📌 选择要透视与操作的项目 (💡支持键盘打字模糊搜索)", valid_projs)
    if sel_proj != st.session_state.current_proj_context:
        st.session_state.pasted_cache        = {}
        st.session_state.config_pasted_cache = {}
        st.session_state.exclude_imgs        = set()
        st.session_state.config_consumed_hashes = set()
        st.session_state.current_proj_context   = sel_proj

    # PM 工作区：单屏单任务，减少滚动
    proj_obj = db.get(sel_proj, {})
    comps_obj = proj_obj.get("部件列表", {})
    latest_dt = None
    for _c in comps_obj.values():
        for _lg in _c.get("日志流", []):
            if is_hidden_system_log(_lg):
                continue
            try:
                d0 = datetime.datetime.strptime(_lg.get("日期", ""), "%Y-%m-%d").date()
            except:
                continue
            latest_dt = d0 if latest_dt is None else max(latest_dt, d0)
    break_days = "-" if latest_dt is None else f"{(datetime.date.today() - latest_dt).days}天"
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("当前里程碑", str(proj_obj.get("Milestone", "待立项")))
    k2.metric("部件数", len(comps_obj))
    k3.metric("预计开定", str(proj_obj.get("Target", "TBD")))
    k4.metric("预计发货", str(proj_obj.get("发货区间", "-")) or "-")
    k5.metric("断更", break_days)

    st.caption("工作区已改为折叠面板：顶部项目摘要 + 中部进度更新 + 侧栏待办。")

    with st.sidebar.expander("🗂️ To do / 提审待办", expanded=True):
        pending_todos = [x for x in todo_list if not x.get("完成")]
        if pending_todos:
            pending_todos = sorted(pending_todos, key=lambda x: (todo_due_date(x) or datetime.date.max, x.get("创建", "")))
            st.markdown("**To do（未完成）**")
            for td in pending_todos[:8]:
                due = todo_due_date(td)
                due_txt = f"{due}" if due else "无DDL"
                st.write(f"- {td.get('任务','')} ｜ {due_txt}")
        else:
            st.caption("To do 暂无未完成项")

        st.markdown("**提审待办（当前项目）**")
        review_pending = []
        for _cname, _cinfo in db.get(sel_proj, {}).get("部件列表", {}).items():
            for _lg in _cinfo.get("日志流", []):
                if is_hidden_system_log(_lg):
                    continue
                _rt = str(_lg.get("提审类型", "")).strip()
                _rr = str(_lg.get("提审结果", "")).strip()
                if _rt and _rt != "(无)" and _rr in ["待反馈", "打回"]:
                    review_pending.append({
                        "日期": str(_lg.get("日期", "")),
                        "部件": _cname,
                        "提审": _rt,
                        "结果": _rr,
                        "事件": str(_lg.get("事件", ""))
                    })
        if review_pending:
            review_pending = sorted(review_pending, key=lambda x: x["日期"], reverse=True)
            for r in review_pending[:8]:
                st.write(f"- [{r['日期']}] {r['部件']} {r['提审']} / {r['结果']}")
        else:
            st.caption("当前项目暂无提审待办")

    st.divider()
    st.markdown("<div class='pm-subsection-title'>🔬 项目透视矩阵（并行连消追踪）</div>", unsafe_allow_html=True)
    st.markdown("""
    <div class="pm-chip-row">
      <span class="pm-chip done">🟩 已完成</span>
      <span class="pm-chip active">🟦 进行中/生产中</span>
      <span class="pm-chip pause">⬛ 暂停前已流转</span>
      <span class="pm-chip delay">🟨 Delay</span>
      <span class="pm-chip none">⬜ 未流转</span>
    </div>
    """, unsafe_allow_html=True)
    comps = db[sel_proj].get('部件列表', {})
    if not comps:
        st.warning("暂无录入部件明细。请在下方录入。")
    else:
        z_data = []
        y_labels = list(comps.keys())
        y_labels_display = []
        hover_text = []
        global_comp_key = next((k for k in comps.keys() if "全局" in k), "全局进度")
        global_is_paused = is_pause_stage(comps.get(global_comp_key, {}).get("主流程", ""))
        for comp_name in y_labels:
            owner_str    = comps[comp_name].get('负责人', '').strip()
            display_name = f"{comp_name} 👤 {owner_str}" if owner_str and owner_str != '未分配' else comp_name
            y_labels_display.append(display_name)
            cur_stage = comps[comp_name].get('主流程', STAGES_UNIFIED[0])
            c_idx     = STAGES_UNIFIED.index(cur_stage) if cur_stage in STAGES_UNIFIED else 0
            active_stages = set()
            completed_stages = set()
            stage_recent_logs = {}
            raw_logs = [log for log in comps[comp_name].get('日志流', []) if not is_hidden_system_log(log)]
            sorted_logs_desc = sorted(
                raw_logs,
                key=lambda x: x.get('日期', ''),
                reverse=True
            )
            for log in sorted_logs_desc:
                stg = log.get('工序', '')
                if stg in STAGES_UNIFIED:
                    stage_recent_logs.setdefault(stg, [])
                    if len(stage_recent_logs[stg]) < 2:
                        stage_recent_logs[stg].append(f"[{log.get('日期','')}] {log.get('事件','')}")

            active_stages, completed_stages = collect_stage_activity(raw_logs, STAGES_UNIFIED)
            delayed_stages = get_stage_delay_set(raw_logs, SYS_CFG.get("排期基线", {}))
            # 领头羊规则：全局进入暂停时，子部件展示态也进入暂停（仅展示层，不覆盖原日志）
            cur_is_paused = is_pause_stage(cur_stage) or (global_is_paused and "全局" not in comp_name)
            if cur_is_paused:
                pause_anchor_idx = None
                parsed_logs = []
                for lg in raw_logs:
                    stg = lg.get('工序', '')
                    if stg not in STAGES_UNIFIED:
                        continue
                    try:
                        lg_dt = datetime.datetime.strptime(lg['日期'], "%Y-%m-%d").date()
                    except:
                        continue
                    parsed_logs.append((lg_dt, stg))
                parsed_logs.sort(key=lambda x: x[0])
                for _, stg in parsed_logs:
                    if not is_pause_stage(stg) and stg != "✅ 已完成(结束)":
                        pause_anchor_idx = STAGES_UNIFIED.index(stg)

                if pause_anchor_idx is None:
                    active_idxs = [STAGES_UNIFIED.index(s) for s in active_stages
                                   if s in STAGES_UNIFIED and not is_pause_stage(s)]
                    pause_anchor_idx = max(active_idxs) if active_idxs else c_idx
                real_c_idx = pause_anchor_idx
            else:
                real_c_idx = c_idx
            row_vals = []; row_hover = []
            for i in range(len(STAGES_UNIFIED)):
                stg        = STAGES_UNIFIED[i]
                hover_base = f"部件: {comp_name}<br>负责人: {owner_str or '未分配'}<br>工序: {stg}"
                recent = stage_recent_logs.get(stg, [])
                if recent:
                    hover_base += "<br>最近日志:<br>• " + "<br>• ".join(recent)
                if cur_is_paused:
                    if is_pause_stage(stg):
                        row_vals.append(2); row_hover.append(f"{hover_base}<br>状态: ⏸️ <b>暂停中</b>")
                    elif i <= real_c_idx and not is_pause_stage(stg):
                        row_vals.append(3); row_hover.append(f"{hover_base}<br>状态: ⏸️ 暂停前已流转")
                    else:
                        row_vals.append(0); row_hover.append(f"{hover_base}<br>状态: ⏳ 未流转")
                    continue
                if cur_stage == "✅ 已完成(结束)" and stg == "✅ 已完成(结束)":
                    row_vals.append(1); row_hover.append(f"{hover_base}<br>状态: ✅ 全部结束")
                elif (stg in completed_stages) and not is_pause_stage(stg):
                    row_vals.append(1); row_hover.append(f"{hover_base}<br>状态: ✅ 已彻底完成")
                elif i < real_c_idx and "暂停" not in stg:
                    row_vals.append(1); row_hover.append(f"{hover_base}<br>状态: ✅ 已流转完成")
                elif i == real_c_idx and "暂停" not in stg:
                    if (stg in delayed_stages) and not cur_is_paused:
                        row_vals.append(4); row_hover.append(f"{hover_base}<br>状态: ⚠️ <b>Delay</b>")
                    else:
                        row_vals.append(2); row_hover.append(f"{hover_base}<br>状态: 🚀 <b>进行中</b>")
                elif stg in active_stages:
                    if (stg in delayed_stages) and not cur_is_paused:
                        row_vals.append(4); row_hover.append(f"{hover_base}<br>状态: ⚠️ <b>Delay</b>")
                    else:
                        row_vals.append(2); row_hover.append(f"{hover_base}<br>状态: 🚀 <b>进行中</b>")
                else:
                    row_vals.append(0); row_hover.append(f"{hover_base}<br>状态: ⏳ 未流转")
            z_data.append(row_vals); hover_text.append(row_hover)
        # 0=未流转(浅灰), 1=完成(绿), 2=进行中/暂停(蓝), 3=暂停前已流转(深灰), 4=delay(黄)
        colorscale = [
            [0.00, '#f1f5f9'], [0.19, '#f1f5f9'],
            [0.20, '#2ecc71'], [0.39, '#2ecc71'],
            [0.40, '#3b82f6'], [0.59, '#3b82f6'],
            [0.60, '#4b5563'], [0.79, '#4b5563'],
            [0.80, '#facc15'], [1.00, '#facc15']
        ]
        fig_grid = go.Figure(data=go.Heatmap(
            z=z_data, x=STAGES_UNIFIED, y=y_labels_display,
            colorscale=colorscale, showscale=False, xgap=4, ygap=4,
            text=hover_text, hoverinfo='text'
        ))
        fig_grid.update_layout(
            xaxis=dict(side='top', tickangle=-45),
            yaxis=dict(autorange='reversed', automargin=True),
            plot_bgcolor='white',
            height=max(250, len(y_labels) * 45),
            margin=dict(t=120, b=20, r=20)
        )
        st.plotly_chart(fig_grid, use_container_width=True)

    with st.expander("🔧 进度更新（主面板）", expanded=True):
        st.divider()
        st.subheader("🔧 进度明细与流转交接工作台")

        with st.expander("📝 单项目速记", expanded=False):
            render_pm_fastlog_integrated(sel_proj)
        cur_pm     = db[sel_proj].get('负责人', 'Mo')
        cur_ms     = db[sel_proj].get('Milestone', '')
        cur_target = db[sel_proj].get('Target', 'TBD')
        cur_ship   = db[sel_proj].get('发货区间', '')
    
        st.markdown("**1. 全局大盘与发货目标设定**")
        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        with col_m1:
            new_pm = st.selectbox("👤 负责人分配", ["Mo", "越", "袁"],
                                  index=["Mo", "越", "袁"].index(cur_pm) if cur_pm in ["Mo", "越", "袁"] else 0)
        with col_m2:
            new_ms = st.selectbox("项目当前阶段", STD_MILESTONES,
                                  index=STD_MILESTONES.index(cur_ms) if cur_ms in STD_MILESTONES else 0)
        with col_m3:
            new_target = st.text_input("📅 预计开定时间", value=cur_target)
        with col_m4:
            new_ship = st.text_input("📦 预计发货区间 (例: 2026 Q2)", value=cur_ship)
    
        if st.button("💾 更新大盘基础信息", type="primary", key="btn_global"):
            db[sel_proj]['负责人']  = new_pm
            db[sel_proj]['Milestone'] = new_ms
            db[sel_proj]['Target']    = new_target
            db[sel_proj]['发货区间']  = new_ship
            td        = str(datetime.date.today())
            comps_list = list(db[sel_proj].get('部件列表', {}).keys())
            t_c        = "全局进度" if "全局进度" in comps_list else (comps_list[0] if comps_list else "全局进度")
            cur_macro_state = db[sel_proj].get("部件列表", {}).get(t_c, {}).get("主流程", STAGES_UNIFIED[0])
            if t_c not in db[sel_proj].setdefault("部件列表", {}):
                db[sel_proj]["部件列表"][t_c] = {"主流程": STAGES_UNIFIED[0], "日志流": []}
            db[sel_proj]["部件列表"][t_c]['日志流'].append({
                "日期": td, "流转": "系统更新",
                "工序": db[sel_proj]["部件列表"][t_c]["主流程"],
                "事件": f"[属性更新] 阶段:{new_ms} | 开定:{new_target} | 发货:{new_ship}"
            })
            sync_save_db(sel_proj)
            st.rerun()
    
        with st.expander("🧾 审核信息", expanded=False):
            review_rows = []
            for _cn, _ci in db.get(sel_proj, {}).get("部件列表", {}).items():
                for _lg in _ci.get("日志流", []):
                    if is_hidden_system_log(_lg):
                        continue
                    _rt = str(_lg.get("提审类型", "")).strip()
                    _rr = str(_lg.get("提审结果", "")).strip()
                    _rd = normalize_review_round(_lg.get("提审轮次", ""))
                    if (_rt and _rt != "(无)") or (_rr and _rr != "(无)"):
                        review_rows.append({
                            "日期": str(_lg.get("日期", "")),
                            "部件": _cn,
                            "阶段": str(_lg.get("工序", "")),
                            "提审类型": _rt if _rt else "(无)",
                            "提审结果": _rr if _rr else "(无)",
                            "轮次": _rd if _rd else "",
                            "事件": str(_lg.get("事件", ""))
                        })
            if review_rows:
                df_rv = pd.DataFrame(review_rows).sort_values(by=["日期", "部件"], ascending=[False, True])
                st.dataframe(df_rv, use_container_width=True, hide_index=True)
            else:
                st.caption("当前项目暂无提审记录。")

        with st.expander("📄 产品配置清单 (图文长图底稿)"):
            curr_link = db[sel_proj].get("配件清单链接", "")
            new_link  = st.text_input("🔗 在线文档链接 (如飞书/腾讯文档，输入即自动保存)", value=curr_link)
            if new_link != curr_link:
                db[sel_proj]["配件清单链接"] = new_link
                sync_save_db(sel_proj)
                st.rerun()
            if curr_link:
                st.markdown(f"👉 **[点击此处，在新标签页打开产品配置清单]({curr_link})**")
            st.divider()
            st.markdown("**🖼️ 上传清单截图 (按 Ctrl+V 直接粘贴)**")
            try:
                from streamlit_paste_button import paste_image_button
                config_paste_result = paste_image_button(
                    "📋 专属底稿截图捕获区",
                    background_color="#f8f9fa", hover_background_color="#e2e8f0",
                    key=f"paste_cfg_{sel_proj}"
                )
                if config_paste_result is not None and hasattr(config_paste_result, 'image_data') \
                        and config_paste_result.image_data is not None:
                    buffered = io.BytesIO()
                    config_paste_result.image_data.save(buffered, format="PNG")
                    h_key = hashlib.md5(buffered.getvalue()).hexdigest()
                    if h_key not in st.session_state.config_pasted_cache \
                            and h_key not in st.session_state.config_consumed_hashes:
                        st.session_state.config_pasted_cache[h_key] = config_paste_result.image_data
            except ImportError:
                pass
    
            config_files = st.file_uploader("或直接拖拽长图/截图", type=['png', 'jpg', 'jpeg'],
                                            accept_multiple_files=True, key=f"cfg_up_{sel_proj}")
            if st.session_state.config_pasted_cache:
                st.markdown("**👀 待存底稿池**")
                cfg_p_cols = st.columns(min(len(st.session_state.config_pasted_cache), 4) or 1)
                keys_to_del = []
                for i, (k, img) in enumerate(st.session_state.config_pasted_cache.items()):
                    with cfg_p_cols[i % 4]:
                        st.image(img, use_container_width=True)
                        if st.button("🗑️ 移除", key=f"del_cfg_paste_{k}", use_container_width=True):
                            keys_to_del.append(k)
                if keys_to_del:
                    for k in keys_to_del:
                        del st.session_state.config_pasted_cache[k]
                        st.session_state.config_consumed_hashes.add(k)
                    st.rerun()
    
            if config_files or st.session_state.config_pasted_cache:
                if st.button("💾 保存图片为底稿", type="secondary"):
                    b64_drafts = []
                    if config_files:
                        for f in config_files:
                            b64_drafts.append(compress_to_b64(f.read()))
                    for k, img_obj in st.session_state.config_pasted_cache.items():
                        b64_drafts.append(compress_to_b64(img_obj))
                        st.session_state.config_consumed_hashes.add(k)
                    db[sel_proj]["配件清单长图"] = db[sel_proj].get("配件清单长图", []) + b64_drafts
                    st.session_state.config_pasted_cache = {}
                    sync_save_db(sel_proj)
                    st.success("✅ 保存成功！")
                    st.rerun()
    
            saved_drafts = db[sel_proj].get("配件清单长图", [])
            if saved_drafts:
                st.markdown("**🖼️ 当前图文底稿画廊**")
                draft_cols = st.columns(min(len(saved_drafts), 2) or 1)
                for idx, b64_str in enumerate(saved_drafts):
                    with draft_cols[idx % 2]:
                        render_image(b64_str, use_container_width=True)
                        if st.button("🗑️ 移除此底稿", key=f"del_draft_{sel_proj}_{idx}"):
                            saved_drafts.pop(idx)
                            db[sel_proj]["配件清单长图"] = saved_drafts
                            sync_save_db(sel_proj)
                            st.rerun()
    
        st.markdown("**2. 细分配件交接工作台**")
        st.caption("说明：提审是独立维度，不会自动改变主阶段；仅做一致性校验提醒。")
        fk = st.session_state.form_key
        existing_comps = list(db[sel_proj].get('部件列表', {}).keys())
        custom_comps   = sorted([c for c in existing_comps if c not in STD_COMPONENTS and "全局" not in c])
        all_comps      = ["➕ 新增细分配件...", "🌐 全局进度 (Overall)"] + STD_COMPONENTS + custom_comps
    
        with st.container(border=True):
            st.markdown("**(1) 基础流转信息**")
            c1, c2, c3, c4, c5, c6, c7 = st.columns([1, 1, 1, 1, 1, 1, 0.9])
            with c1: selected_comps_raw = st.multiselect("操作部件", all_comps, default=[], key=f"ms_{fk}")
            with c2: evt_type  = st.selectbox("记录类型", ["🔄 内部进展/正常流转", "⬅️ 收到反馈/被打回"], key=f"evt_{fk}")
            with c3: new_stage = st.selectbox("🎯 目标工序阶段", STAGES_UNIFIED, key=f"stg_{fk}")
            with c4: handoff   = st.selectbox("关联媒介", HANDOFF_METHODS, key=f"hd_{fk}")
            with c5: review_type = st.selectbox("🧾 提审类型", REVIEW_TYPE_OPTIONS, key=f"rv_type_{fk}")
            with c6: review_result = st.selectbox("🧾 提审结果", REVIEW_RESULT_OPTIONS, key=f"rv_res_{fk}")
            with c7: review_round = st.number_input("提审轮次", min_value=1, value=1, step=1, key=f"rv_round_{fk}")
    
            comps_to_process = selected_comps_raw if selected_comps_raw else ["🌐 全局进度 (Overall)"]
            new_comp_name    = ""
            if "➕ 新增细分配件..." in comps_to_process:
                sub_cat       = st.selectbox("所属主分类", STD_COMPONENTS, key=f"ncat_{fk}")
                sub_name      = st.text_input("细分名称", key=f"nname_{fk}")
                new_comp_name = f"{sub_cat} - {sub_name}" if sub_name else ""
    
            st.markdown("**(2) 细分角色分配**")
            all_historical_names = set()
            for p_data in db.values():
                if not isinstance(p_data, dict): continue
                for c_data in p_data.get('部件列表', {}).values():
                    for pair in re.split(r'[,，|]', c_data.get('负责人', '')):
                        pair = pair.strip()
                        if pair and pair != '未分配':
                            if '-' in pair:   all_historical_names.add(pair.split('-', 1)[-1].strip())
                            elif ':' in pair: all_historical_names.add(pair.split(':', 1)[-1].strip())
                            else:             all_historical_names.add(pair)
            base_options = ["(留空/暂不分配)"] + sorted(list(all_historical_names)) + ["➕ 手动输入新成员..."]
    
            ref_c         = comps_to_process[0] if comps_to_process[0] != "🌐 全局进度 (Overall)" else "全局进度"
            old_owner_str = db[sel_proj].get('部件列表', {}).get(ref_c, {}).get('负责人', '')
            old_dict      = {}
            for pair in old_owner_str.split(','):
                if '-' in pair:   parts = pair.split('-', 1)
                elif ':' in pair: parts = pair.split(':', 1)
                else: continue
                if len(parts) == 2: old_dict[parts[0].strip()] = parts[1].strip()
    
            role_list = ["建模", "设计", "工程", "监修", "打印", "涂装"]
            role_vals = {}
            r_cols    = st.columns(6)
            for idx, r in enumerate(role_list):
                with r_cols[idx]:
                    old_v      = old_dict.get(r, "")
                    temp_opts  = base_options.copy()
                    if old_v and old_v not in temp_opts and old_v != "(留空/暂不分配)":
                        temp_opts.insert(1, old_v)
                    sel_val = st.selectbox(f"{r}", temp_opts,
                                           index=temp_opts.index(old_v) if old_v in temp_opts else 0,
                                           key=f"role_{r}_{fk}")
                    if sel_val == "➕ 手动输入新成员...":
                        final_val = st.text_input("👉 新姓名", key=f"rnew_{r}_{fk}")
                    elif sel_val == "(留空/暂不分配)":
                        final_val = ""
                    else:
                        final_val = sel_val
                    role_vals[r] = final_val
    
            st.markdown("**(3) 日期与进展**")
            d_col, t_col = st.columns([1, 3])
            with d_col: detail_record_date = st.date_input("🕒 发生日期", datetime.date.today(), key=f"date_{fk}")
            with t_col: log_txt = st.text_area("📝 详细进展 (按需写打回原因)", height=80, key=f"txt_{fk}")
    
            st.markdown("**(4) 参考图 (支持连按 Ctrl+V 缓存)**")
            try:
                from streamlit_paste_button import paste_image_button
                paste_result = paste_image_button(
                    "📋 剪贴板捕获区",
                    background_color="#f1f5f9", hover_background_color="#e2e8f0",
                    key=f"paste_log_{sel_proj}_{fk}"
                )
                if paste_result is not None and hasattr(paste_result, 'image_data') \
                        and paste_result.image_data is not None:
                    buffered = io.BytesIO()
                    paste_result.image_data.save(buffered, format="PNG")
                    h_key = hashlib.md5(buffered.getvalue()).hexdigest()
                    if h_key not in st.session_state.pasted_cache:
                        st.session_state.pasted_cache[h_key] = paste_result.image_data
            except ImportError:
                pass
    
            img_files = st.file_uploader("或选择文件上传", type=['png', 'jpg', 'jpeg'],
                                         accept_multiple_files=True, key=f"up_log_{sel_proj}_{fk}")
            preview_imgs = []
            if img_files:
                for f in img_files:
                    preview_imgs.append({"type": "file", "id": f.name, "data": f})
            for h_key, img_obj in st.session_state.pasted_cache.items():
                preview_imgs.append({"type": "paste", "id": h_key, "data": img_obj})
            preview_imgs = [img for img in preview_imgs if img["id"] not in st.session_state.exclude_imgs]
    
            if preview_imgs:
                st.markdown("**👀 待上传池**")
                p_cols = st.columns(min(len(preview_imgs), 6) or 1)
                for idx, img_info in enumerate(preview_imgs):
                    with p_cols[idx % 6]:
                        if img_info["type"] == "paste":
                            st.image(img_info["data"], use_container_width=True)
                        else:
                            img_info["data"].seek(0)
                            st.image(img_info["data"], use_container_width=True)
                        if st.button("🗑️ 移除", key=f"del_{img_info['id']}_{idx}",
                                     use_container_width=True, type="primary"):
                            st.session_state.exclude_imgs.add(img_info["id"])
                            st.rerun()
    
            st.markdown("---")
            is_completed = st.checkbox(
                f"✅ 标记所选部件的【{new_stage}】阶段已彻底完成 (矩阵变绿)",
                value=False, key=f"comp_{fk}"
            )
            force_submit_detail = st.checkbox("⚠️ 强制提交（忽略阶段/提审 warning）", value=False, key=f"force_detail_{fk}")
    
            if st.button("🚀 批量保存交接与进度", type="primary", use_container_width=True):
                if "➕ 新增细分配件..." in comps_to_process and not new_comp_name:
                    st.error("❌ 新增名称为空！")
                else:
                    new_owner_final = ", ".join([f"{k}-{v}" for k, v in role_vals.items() if v])
                    img_b64_list    = []
                    for img_info in preview_imgs:
                        if img_info["type"] == "paste":
                            img_b64_list.append(compress_to_b64(img_info["data"]))
                        else:
                            img_info["data"].seek(0)
                            img_b64_list.append(compress_to_b64(img_info["data"].read()))
    
                    global_pause_cascade = ("🌐 全局进度 (Overall)" in comps_to_process and is_pause_stage(new_stage))
                    saved_records = 0
    
                    for c_raw in comps_to_process:
                        if c_raw == "🌐 全局进度 (Overall)":
                            actual_c = "全局进度"
                        elif c_raw == "➕ 新增细分配件...":
                            actual_c = new_comp_name
                        else:
                            actual_c = c_raw
    
                        if actual_c not in db[sel_proj].setdefault("部件列表", {}):
                            db[sel_proj]["部件列表"][actual_c] = {"主流程": STAGES_UNIFIED[0], "日志流": []}
                        if new_owner_final:
                            db[sel_proj]["部件列表"][actual_c]['负责人'] = new_owner_final
    
                        base_log = (f"【{evt_type} | {handoff}】补充: {log_txt}"
                                    if log_txt else f"【{evt_type} | {handoff}】")
                        if is_completed:
                            base_log += " [系统]彻底完成"
                        curr_stage_detail = db[sel_proj]["部件列表"][actual_c].get("主流程", STAGES_UNIFIED[0])
                        stage_warn = validate_transition_warning(curr_stage_detail, new_stage, STAGES_UNIFIED)
                        review_warn = validate_review_with_stage(review_type, new_stage, actual_c, STAGES_UNIFIED)
                        if (stage_warn or review_warn) and not force_submit_detail:
                            warn_txt = "；".join([w for w in [stage_warn, review_warn] if w])
                            st.warning(f"[{actual_c}] {warn_txt}（如确认无误可勾选强制提交）")
                            continue
                        if (stage_warn or review_warn) and force_submit_detail:
                            warn_txt = "；".join([w for w in [stage_warn, review_warn] if w])
                            st.warning(f"[{actual_c}] {warn_txt}（已强制提交）")
    
                        if new_stage == "立项":
                            db[sel_proj]["部件列表"][actual_c]['日志流'].append({
                                "日期": str(detail_record_date), "流转": evt_type,
                                "工序": "立项", "事件": base_log, "图片": img_b64_list,
                                "提审类型": review_type, "提审结果": review_result, "提审轮次": int(review_round) if review_type != "(无)" else ""
                            })
                            saved_records += 1
                            db[sel_proj]["部件列表"][actual_c]['日志流'].append({
                                "日期": str(detail_record_date + datetime.timedelta(days=1)),
                                "流转": "系统自动", "工序": "建模(含打印/签样)",
                                "事件": "[系统] 立项完成自动推演"
                            })
                            db[sel_proj]["部件列表"][actual_c]['主流程'] = "建模(含打印/签样)"
                        else:
                            db[sel_proj]["部件列表"][actual_c]['日志流'].append({
                                "日期": str(detail_record_date), "流转": evt_type,
                                "工序": new_stage, "事件": base_log, "图片": img_b64_list,
                                "提审类型": review_type, "提审结果": review_result, "提审轮次": int(review_round) if review_type != "(无)" else ""
                            })
                            saved_records += 1
                            db[sel_proj]["部件列表"][actual_c]['主流程'] = new_stage
    
                    if global_pause_cascade:
                        db[sel_proj]["Milestone"] = "暂停研发"
                        for sub_c, sub_info in db[sel_proj].get("部件列表", {}).items():
                            if "全局" in sub_c:
                                continue
                            if sub_info.get("主流程") != new_stage:
                                sub_info.setdefault('日志流', []).append({
                                    "日期": str(detail_record_date), "流转": "系统自动",
                                    "工序": new_stage, "事件": "[系统] 全局已暂停，子部件自动同步为暂停"
                                })
                                sub_info["主流程"] = new_stage
    
                    if saved_records <= 0:
                        st.warning("未写入任何记录：请检查提审/阶段 warning，或勾选强制提交。")
                    else:
                        st.session_state.form_key    += 1
                        st.session_state.pasted_cache = {}
                        st.session_state.exclude_imgs = set()
                        sync_save_db(sel_proj)
                        st.success(f"🎉 记录成功！本次写入 {saved_records} 条。")
                        st.rerun()

        with st.expander("⏱️ 团队效能与工时", expanded=False):
            render_pm_efficiency(sel_proj)

        with st.expander("📦 3. 包装&入库", expanded=False):
            render_pm_packing_inventory_integrated(sel_proj)

        with st.expander("💰 4. 成本面板", expanded=False):
            render_pm_cost_integrated(sel_proj)
# ==========================================
# 模块 3：AI 速记
# ==========================================
elif menu == MENU_FASTLOG:
    st.title("🚀 移动端 智能速记引擎")
    st.caption("最低成本建议：先用系统自动学习新词，持续沉淀部件/阶段关键词；无需接入外部模型也能逐周变准。")
    MANUAL_PICK = "⚠️冲突: 请手动选择"
    def is_manual_pick_project(name):
        ss = str(name or "").strip()
        return ss in [MANUAL_PICK, "⚠️请手动选择项目", "未知/请手动修改"]


    with st.expander("💡 点击查看【标准速记语法模板】", expanded=False):
        st.markdown("""
**标准模板**：`[比例前缀] 项目A & 项目B：部件关键字 + 进度描述 ； 部件关键字 + 进度描述`
* ✅ **多项目联动**：`1/6里夫西装 & 里夫战衣：官图提审` -> 自动拆分为 2 个项目的全局进度
* ✅ **多管线并发**：`1/6萨鲁曼：头雕打样中；目标定价2980；法杖需要修改` -> 自动拆分为 3 笔独立记录
        """)

    global_ai_date = st.date_input("🕒 本次批量记录发生日期", datetime.date.today())
    raw_text       = st.text_area("✍️ 输入进展 (按模板语法输入)：", height=150)

    COMP_KW  = {"头": "头雕(表情)", "眼": "头雕(表情)", "脸": "头雕(表情)", "手": "手型",
                "衣": "服装", "包": "包装", "盒": "包装", "地台": "地台",
                "扣": "配件", "法杖": "配件", "杯": "配件", "剑": "配件"}
    STAGE_KW = {"定价": "立项", "评估": "立项", "打印": "建模(含打印/签样)",
                "模型": "建模(含打印/签样)", "缩放": "建模(含打印/签样)",
                "修": "建模(含打印/签样)", "建模": "建模(含打印/签样)",
                "涂": "涂装", "色": "涂装", "设计": "设计", "原画": "设计",
                "拆件": "工程拆件", "官图": "官图", "大货": "大货",
                "完成": "✅ 已完成(结束)", "结束": "✅ 已完成(结束)"}
    RESUME_KWS = ["resume", "恢复", "重启", "继续推进", "解除暂停", "复工"]

    PROJECT_ALIAS_MAP = SYS_CFG.get("项目别名", {})
    DYNAMIC_COMP_KW  = {**COMP_KW,  **SYS_CFG.get("AI_COMP_KW",  {})}
    DYNAMIC_STAGE_KW = {**STAGE_KW, **SYS_CFG.get("AI_STAGE_KW", {})}

    # 避免短词误伤（如“甘”几乎必然误判到甘道夫）
    DYNAMIC_COMP_KW = {k: v for k, v in DYNAMIC_COMP_KW.items() if len(str(k).strip()) >= 2}
    DYNAMIC_STAGE_KW = {k: v for k, v in DYNAMIC_STAGE_KW.items() if len(str(k).strip()) >= 1}

    if st.button("✨ 智能拆解", type="primary"):
        if not raw_text.strip():
            st.warning("内容为空！")
        else:
            parsed = []
            prefix_pat = re.compile(r'(1/6|1/4|1/12|1/3|1/1)')

            def smart_match_proj(query):
                """
                四层递进智能匹配，返回最佳匹配项目名或None：
                1. 精确包含匹配（去空格）
                2. 缩写匹配：query是项目名各词首字的缩写
                3. 跨语言匹配：query出现在项目名的任何位置（含英文/数字）
                4. 容错匹配：query与项目名编辑距离<=2
                """
                q = query.replace(" ", "").lower()
                if not q: return None

                def edit_dist(a, b):
                    if abs(len(a)-len(b)) > 3: return 99
                    dp = list(range(len(b)+1))
                    for i, ca in enumerate(a):
                        ndp = [i+1]
                        for j, cb in enumerate(b):
                            ndp.append(min(dp[j]+(ca!=cb), dp[j+1]+1, ndp[j]+1))
                        dp = ndp
                    return dp[-1]

                candidates = []
                for vp in valid_projs:
                    vp_cl   = vp.replace(" ", "").lower()
                    vp_core = re.sub(r"(1/6|1/4|1/12|1/3|1/1)\s*", "", vp).strip()
                    vp_core_cl = vp_core.replace(" ", "").lower()

                    # 层1：精确包含（query在项目名里，或项目名在query里）
                    if q in vp_cl or vp_core_cl in q or q in vp_core_cl:
                        candidates.append((0, len(vp_cl), vp)); continue

                    # 层2：缩写匹配（query是项目核心词各字首字母/首字拼在一起）
                    # 支持中文首字缩写：哈波火焰杯 → 哈火杯 / hb
                    chars = [c for c in vp_core_cl]
                    if len(q) >= 2 and len(q) <= len(chars):
                        # 中文首字缩写
                        abbr_zh = "".join(chars[::max(1, len(chars)//len(q))])[:len(q)]
                        if q == abbr_zh[:len(q)]:
                            candidates.append((1, len(vp_cl), vp)); continue

                    # 层3：跨语言 - 英文词或数字出现在项目名里
                    en_tokens = re.findall(r"[a-zA-Z0-9]+", query)
                    if en_tokens:
                        vp_lower = vp.lower()
                        if all(tok.lower() in vp_lower for tok in en_tokens):
                            candidates.append((2, len(vp_cl), vp)); continue

                    # 层4：容错匹配（编辑距离）
                    dist = edit_dist(q, vp_core_cl)
                    threshold = 1 if len(q) <= 3 else 2
                    if dist <= threshold:
                        candidates.append((3+dist, len(vp_cl), vp))

                if not candidates:
                    return None
                candidates.sort(key=lambda x: (x[0], -x[1]))
                if len(candidates) >= 2 and candidates[0][0] == candidates[1][0]:
                    return "⚠️冲突: 请手动选择"
                return candidates[0][2]

            def find_best_proj(text):
                """从文本开头贪心匹配最长项目名，返回(项目名或None, 剩余文本)"""
                text_cl = text.replace(" ", "").lower()
                best_proj = None; best_len = 0
                for vp in sorted(valid_projs, key=len, reverse=True):
                    vp_cl      = vp.replace(" ", "").lower()
                    vp_core    = re.sub(r"(1/6|1/4|1/12|1/3|1/1)\s*", "", vp).strip()
                    vp_core_cl = vp_core.replace(" ", "").lower()
                    if text_cl.startswith(vp_cl) and len(vp_cl) > best_len:
                        best_proj = vp; best_len = len(vp_cl)
                    elif vp_core_cl and text_cl.startswith(vp_core_cl) and len(vp_core_cl) > best_len:
                        best_proj = vp; best_len = len(vp_core_cl)
                # 如果前缀匹配失败，用智能模糊匹配整段文字
                if not best_proj:
                    best_proj = smart_match_proj(text)
                    if best_proj:
                        return best_proj, ""  # 整段都是项目名，无剩余内容
                    return None, text
                cut = 0; no_sp = 0
                for ch in text:
                    if no_sp >= best_len: break
                    cut += 1
                    if ch != ' ': no_sp += 1
                return best_proj, text[cut:].strip()

            def parse_line(line):
                line = line.replace('：', ':').rstrip('；; 	').strip()
                if not line: return []
                # 格式1：有冒号，冒号前是项目区，后是内容区
                if ':' in line:
                    proj_raw, content_raw = line.split(':', 1)
                    proj_segs = [s.strip() for s in re.split(r'&|和', proj_raw) if s.strip()]
                    contents  = [c.strip() for c in re.split(r'[;；]', content_raw) if c.strip()] or [content_raw.strip()]
                    cur_pfx = ""; resolved = []
                    for seg in proj_segs:
                        m = prefix_pat.search(seg)
                        if m: cur_pfx = m.group(1)
                        seg2 = seg if prefix_pat.search(seg) else f"{cur_pfx} {seg}".strip()
                        proj, _ = find_best_proj(seg2)
                        resolved.append(proj or MANUAL_PICK)
                    return [(p, c) for p in resolved for c in contents]
                # 格式2：无冒号，按&逐段贪心提取项目名，剩余作为内容
                amp_parts = re.split(r'&', line)
                proj_parts = []; content_parts = []; switched = False; cur_pfx = ""
                for part in amp_parts:
                    part = part.strip()
                    if switched:
                        content_parts.append(part); continue
                    m = prefix_pat.search(part)
                    if m: cur_pfx = m.group(1)
                    candidate = part if prefix_pat.search(part) else f"{cur_pfx} {part}".strip()
                    proj, leftover = find_best_proj(candidate)
                    if proj:
                        proj_parts.append(proj)
                        if leftover:
                            content_parts.append(leftover); switched = True
                    else:
                        content_parts.append(part); switched = True
                if not proj_parts: proj_parts = [MANUAL_PICK]
                raw_content = "&".join(content_parts)
                contents = [c.strip() for c in re.split(r'[;；]', raw_content) if c.strip()] or [raw_content or "(无内容)"]
                return [(p, c) for p in proj_parts for c in contents]

            for line in raw_text.splitlines():
                line = line.strip()
                if not line: continue
                for proj, content in parse_line(line):
                    detected_comp  = next((comp for kw, comp in DYNAMIC_COMP_KW.items()  if kw in content), "全局进度")
                    detected_stage = next((stg  for kw, stg  in DYNAMIC_STAGE_KW.items() if kw in content), "(维持原阶段)")
                    proj = resolve_alias_project(proj, PROJECT_ALIAS_MAP)
                    parsed.append({"识别项目": proj, "推测部件": detected_comp,
                                   "推测阶段": detected_stage, "待写入事件": content})

            st.session_state.parsed_logs = parsed
            st.success(f"🎉 拆解完成！共识别 {len(parsed)} 条记录。")

    if st.session_state.parsed_logs:
        st.divider()
        st.subheader("👀 核对与入库")
        edited_logs     = []
        project_options = [MANUAL_PICK] + valid_projs
        comp_options    = ["全局进度"] + STD_COMPONENTS + ["其他配件(系统自动创建)"]

        for i, item in enumerate(st.session_state.parsed_logs):
            is_unknown = is_manual_pick_project(item['识别项目'])
            c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([1.2, 1, 1, 1.6, 1, 1, 1, 0.9])
            with c1:
                sel_proj_ai = st.selectbox(
                    "归属项目", project_options,
                    index=project_options.index(item['识别项目']) if item['识别项目'] in project_options else 0,
                    key=f"sel_p_{i}"
                )
                # 识别失败时，显示快速新建项目入口
                if is_unknown or is_manual_pick_project(sel_proj_ai):
                    with st.expander("➕ 直接新建此项目", expanded=False):
                        new_p_name = st.text_input("项目名称", key=f"new_pname_{i}",
                                                    placeholder="如: 1/6 威龙")
                        new_p_pm   = st.selectbox("负责人", ["Mo", "越", "袁"], key=f"new_ppm_{i}")
                        if st.button("✅ 建档并选中", key=f"new_pbtn_{i}", type="primary"):
                            if new_p_name and new_p_name not in db:
                                db[new_p_name] = {
                                    "负责人": new_p_pm, "跟单": "", "Milestone": "待立项",
                                    "Target": "TBD", "发货区间": "",
                                    "部件列表": {}, "发货数据": {}, "成本数据": {}
                                }
                                sync_save_db(new_p_name)
                                # 更新识别结果为新建的项目
                                st.session_state.parsed_logs[i]['识别项目'] = new_p_name
                                st.success(f"✅ 已建档：{new_p_name}")
                                st.rerun()
                            elif new_p_name in db:
                                st.warning("项目已存在，直接在上方下拉选择即可。")
            with c2:
                sel_comp = st.selectbox(
                    "归属部件", comp_options,
                    index=comp_options.index(item['推测部件']) if item['推测部件'] in comp_options else 0,
                    key=f"sel_c_{i}"
                )
            with c3:
                options_stages = ["(维持原阶段)"] + STAGES_UNIFIED
                sel_stage = st.selectbox(
                    "部件阶段", options_stages,
                    index=options_stages.index(item['推测阶段']) if item.get('推测阶段') in options_stages else 0,
                    key=f"stg_{i}"
                )
            with c4:
                sel_event = st.text_input("📝 写入事件", value=item['待写入事件'], key=f"evt_{i}")
            with c5:
                ai_kw = st.text_input("🧠 新词(可选，留空自动学习)", placeholder="如: 法杖", key=f"kw_{i}")
            with c6:
                rv_type_default = infer_review_type_from_text(item['待写入事件'])
                rv_type = st.selectbox("提审类型", REVIEW_TYPE_OPTIONS,
                                       index=REVIEW_TYPE_OPTIONS.index(rv_type_default) if rv_type_default in REVIEW_TYPE_OPTIONS else 0,
                                       key=f"rv_ai_type_{i}")
            with c7:
                rv_res_default = infer_review_result_from_text(item['待写入事件'])
                rv_res = st.selectbox("提审结果", REVIEW_RESULT_OPTIONS,
                                      index=REVIEW_RESULT_OPTIONS.index(rv_res_default) if rv_res_default in REVIEW_RESULT_OPTIONS else 0,
                                      key=f"rv_ai_res_{i}")
            with c8:
                rv_round_default = infer_review_round_from_text(item['待写入事件'])
                rv_round = st.text_input("提审轮次", value=str(rv_round_default) if rv_round_default else "", key=f"rv_ai_round_{i}")
            edited_logs.append({"项目": sel_proj_ai, "部件": sel_comp, "事件": sel_event,
                                 "推测阶段": sel_stage, "新词汇": ai_kw,
                                 "提审类型": rv_type, "提审结果": rv_res, "提审轮次": rv_round})

        st.markdown("**🖼️ 附件图片**")
        st.caption("附件会跟随每条入库记录自动关联到其对应项目/部件，后续可在【历史溯源】按项目追溯。")
        try:
            from streamlit_paste_button import paste_image_button
            ai_paste_result = paste_image_button(
                "📋 专属剪贴板捕获区",
                background_color="#f1f5f9", hover_background_color="#e2e8f0",
                key="ai_paste_btn"
            )
            if ai_paste_result is not None and hasattr(ai_paste_result, 'image_data') \
                    and ai_paste_result.image_data is not None:
                buffered = io.BytesIO()
                ai_paste_result.image_data.save(buffered, format="PNG")
                h_key = hashlib.md5(buffered.getvalue()).hexdigest()
                if h_key not in st.session_state.ai_pasted_cache \
                        and h_key not in st.session_state.ai_consumed_hashes:
                    st.session_state.ai_pasted_cache[h_key] = ai_paste_result.image_data
        except ImportError:
            pass

        ai_files = st.file_uploader("或直接拖拽图片", type=['png', 'jpg', 'jpeg'],
                                    accept_multiple_files=True, key="ai_up_files")
        if st.session_state.ai_pasted_cache:
            cfg_p_cols  = st.columns(min(len(st.session_state.ai_pasted_cache), 6) or 1)
            keys_to_del = []
            for i, (k, img) in enumerate(st.session_state.ai_pasted_cache.items()):
                with cfg_p_cols[i % 6]:
                    st.image(img, use_container_width=True)
                    if st.button("🗑️ 移除", key=f"del_ai_{k}", use_container_width=True):
                        keys_to_del.append(k)
            if keys_to_del:
                for k in keys_to_del:
                    del st.session_state.ai_pasted_cache[k]
                    st.session_state.ai_consumed_hashes.add(k)
                st.rerun()

        auto_learn_kw = st.checkbox("🤖 自动学习新词（留空时从事件前8字提取）", value=True, key="ai_auto_learn")
        force_ai_submit = st.checkbox("⚠️ 允许强制提交（忽略阶段/提审 warning）", value=False, key="ai_force_submit")
        if st.button("💾 确认入库", type="primary"):
            td          = str(global_ai_date)
            ai_b64_list = []
            if ai_files:
                for f in ai_files:
                    ai_b64_list.append(compress_to_b64(f.read()))
            for k, img_obj in st.session_state.ai_pasted_cache.items():
                ai_b64_list.append(compress_to_b64(img_obj))
                st.session_state.ai_consumed_hashes.add(k)

            learned_count = 0
            for log in edited_logs:
                p = log['项目']
                p = resolve_alias_project(p, PROJECT_ALIAS_MAP)
                if p not in db or "未知" in p or "冲突" in p:
                    st.error(f"跳过无效项目: {p}")
                    continue
                target_comp = log["部件"] if log["部件"] != "其他配件(系统自动创建)" else "自定义配件"
                snippet     = log.get("新词汇", "").strip()
                if (not snippet) and auto_learn_kw:
                    evt = str(log.get("事件", "")).strip()
                    snippet = evt[:8] if len(evt) >= 2 else ""
                if snippet:
                    if target_comp != "全局进度":
                        SYS_CFG.setdefault("AI_COMP_KW", {})[snippet]  = target_comp
                        learned_count += 1
                    if log["推测阶段"] != "(维持原阶段)":
                        SYS_CFG.setdefault("AI_STAGE_KW", {})[snippet] = log["推测阶段"]
                        learned_count += 1
                if target_comp not in db[p].setdefault("部件列表", {}):
                    db[p]["部件列表"][target_comp] = {"主流程": STAGES_UNIFIED[0], "日志流": []}
                curr_stage = db[p]["部件列表"][target_comp].get("主流程", STAGES_UNIFIED[0])
                final_stage = (curr_stage if log["推测阶段"] == "(维持原阶段)" else log["推测阶段"])
                stage_warn = validate_transition_warning(curr_stage, final_stage, STAGES_UNIFIED)
                review_warn = validate_review_with_stage(log.get("提审类型", "(无)"), final_stage, target_comp, STAGES_UNIFIED)
                if (stage_warn or review_warn) and not force_ai_submit:
                    merged_warn = "；".join([w for w in [stage_warn, review_warn] if w])
                    st.warning(f"[{p}/{target_comp}] {merged_warn}。如确认无误请勾选强制提交后再次保存")
                    continue
                # 防呆：当前处于暂停时，除非明确写了恢复关键词或手动改了阶段，否则保持暂停
                evt_text = str(log.get('事件', '')).lower()
                has_resume_signal = any(kw in evt_text for kw in RESUME_KWS)
                if is_pause_stage(curr_stage) and log["推测阶段"] == "(维持原阶段)" and not has_resume_signal:
                    final_stage = curr_stage
                db[p]["部件列表"][target_comp]['日志流'].append({
                    "日期": td, "流转": "AI速记",
                    "工序": final_stage, "事件": log['事件'], "图片": ai_b64_list,
                    "提审类型": log.get("提审类型", "(无)"), "提审结果": log.get("提审结果", "(无)"),
                    "提审轮次": normalize_review_round(log.get("提审轮次", ""))
                })
                db[p]["部件列表"][target_comp]["主流程"] = final_stage

            # AI速记可能涉及多个项目，逐项目保存
            changed_projs = list(set(
                resolve_alias_project(log["项目"], PROJECT_ALIAS_MAP)
                for log in edited_logs
                if resolve_alias_project(log["项目"], PROJECT_ALIAS_MAP) in db
                and "未知" not in log["项目"] and "冲突" not in log["项目"]
            ))
            for cp in changed_projs:
                sync_save_db(cp)
            db_manager.save_one("系统配置", st.session_state.db["系统配置"])
            st.session_state.parsed_logs    = []
            st.session_state.ai_pasted_cache = {}
            msg = "🎉 入库成功！" if learned_count == 0 else f"🎉 入库成功！AI 已学会了 {learned_count} 个新词汇！"
            st.success(msg)
            st.rerun()

# ==========================================
# 模块 4：包装与入库
# ==========================================
elif menu == MENU_PACKING:
    st.title("📦 包装与入库特殊领用记录")
    if not valid_projs: st.stop()
    sel_proj = st.selectbox("📌 追踪项目", valid_projs)

    st.divider()
    st.markdown("### 📝 项目全局备忘录")
    memo_txt = st.text_area("记录跨部门叮嘱等杂项", value=db[sel_proj].get("备忘录", ""), height=100)
    if st.button("💾 保存备忘录"):
        db[sel_proj]["备忘录"] = memo_txt
        sync_save_db(sel_proj)
        st.success("已保存！")
        st.rerun()

    st.divider()
    st.markdown("### 🎁 包装 Checklist（卡片版 + 物料附件追溯）")
    pack_data = db[sel_proj].get("包装专项", {})
    pack_items = [
        "实物寄厂", "提供刀线", "已称重", "彩盒设计", "灰箱设计", "物流箱设计", "说明书", "感谢信", "杂项纸品"
    ]
    labels = {
        "实物寄厂": "1. 实物寄包装厂", "提供刀线": "2. 提供刀线", "已称重": "3. 内部已称重",
        "彩盒设计": "4. 彩盒设计完毕", "灰箱设计": "5. 灰箱设计完毕", "物流箱设计": "6. 物流箱已设计",
        "说明书": "7. 说明书定版", "感谢信": "8. 感谢信定版", "杂项纸品": "9. 杂项纸品"
    }
    pack_file_map = db[sel_proj].setdefault("包装物料附件", {})
    new_pack_vals = {}
    cols = st.columns(3)
    for i, key in enumerate(pack_items):
        with cols[i % 3]:
            with st.container(border=True):
                new_pack_vals[key] = st.checkbox(labels[key], value=pack_data.get(key, False), key=f"pack_ck_{sel_proj}_{key}")
                up = st.file_uploader("上传附件(可选)", type=['png', 'jpg', 'jpeg', 'pdf'], key=f"pack_file_{sel_proj}_{key}")
                if up is not None:
                    ref = save_uploaded_file_ref(up, prefix=f"pack_{norm_text(sel_proj)[:12]}_{i}")
                    if ref:
                        pack_file_map.setdefault(key, []).append(ref)
                        st.success("附件已缓存，点击【保存包装进度】后统一落库。")
                refs = pack_file_map.get(key, [])
                if refs:
                    st.caption(f"已关联附件：{len(refs)}")

    if st.button("💾 保存包装进度", type="primary"):
        db[sel_proj]["包装专项"] = new_pack_vals
        db[sel_proj]["包装物料附件"] = pack_file_map
        sync_save_db(sel_proj)
        st.success("已存档！")
        st.rerun()

    with st.expander("🗂️ 查看包装物料附件"):
        for key in pack_items:
            refs = pack_file_map.get(key, [])
            if not refs:
                continue
            st.markdown(f"**{labels[key]}**")
            pcols = st.columns(min(len(refs), 4))
            for j, ref in enumerate(refs):
                with pcols[j % 4]:
                    if str(ref).lower().endswith('.pdf'):
                        st.write(ref)
                    else:
                        render_image(ref, use_container_width=True)

    st.divider()
    st.markdown("### 🧮 工厂大货入库与特殊领用台账")
    inv_data  = db[sel_proj].get("发货数据", {"总单量": 0, "批次明细": []})
    c1, c2    = st.columns([1, 2])
    with c1:
        total_qty = st.number_input("工厂生产总单量 (PCS)", value=int(inv_data.get("总单量", 0)), step=100)
        if st.button("保存单量"):
            db[sel_proj].setdefault("发货数据", {})["总单量"] = total_qty
            sync_save_db(sel_proj)
            st.rerun()
    in_a = out_a = 0
    records = []
    for item in inv_data.get("批次明细", []):
        q = int(item.get('数量', 0))
        if item.get('类型') == '内部领用': out_a += q
        else: in_a += q
        records.append({"日期": item['日期'], "类型": item['类型'],
                         "数量": q, "用途": item.get('备注', '无')})
    fac_left   = total_qty - in_a
    real_stock = in_a - out_a
    st.write(f"**累计入库:** {in_a} | **内部领用:** {out_a} | **📦 可用:** {real_stock} | **🏭 未交:** {fac_left}")
    with st.expander("➕ 登记新流水"):
        rc1, rc2, rc3, rc4 = st.columns(4)
        with rc1: typ  = st.selectbox("类型", ["大货入库", "内部领用"])
        with rc2: q    = st.number_input("数量", min_value=1, value=10)
        with rc3: note = st.text_input("用途")
        with rc4:
            st.write("")
            if st.button("登记"):
                db[sel_proj].setdefault("发货数据", {}).setdefault("批次明细", []).append({
                    "日期": str(datetime.date.today()), "类型": typ,
                    "数量": int(q), "备注": note
                })
                sync_save_db(sel_proj)
                st.rerun()
    if records:
        st.dataframe(pd.DataFrame(records), use_container_width=True)

# ==========================================
# 模块 5：成本台账
# ==========================================
elif menu == MENU_COST:
    st.title("💰 纯净动态成本控制台")
    if not valid_projs: st.stop()
    sel_proj = st.selectbox("📌 核算项目", valid_projs)
    c_data   = db[sel_proj].get("成本数据", {})

    c1, c2, c3 = st.columns(3)
    with c1: orders = st.number_input("总订单数",      value=int(c_data.get("总订单数", 0)),      step=100)
    with c2: price  = st.number_input("目标单价 (¥)", value=float(c_data.get("销售单价", 0.0)), step=100.0)
    with c3:
        st.write("")
        if st.button("💾 保存基础单量"):
            db[sel_proj].setdefault("成本数据", {})["总订单数"] = orders
            db[sel_proj]["成本数据"]["销售单价"] = price
            sync_save_db(sel_proj)
            st.success("已保存")
            st.rerun()

    st.divider()
    st.subheader("🧩 预计报价模板（按工艺/工厂/头版方案）")
    scenario_list = db[sel_proj].setdefault("成本数据", {}).setdefault("预计报价方案", [])
    scenario_names = [x.get("方案名", f"方案{i+1}") for i, x in enumerate(scenario_list)]
    scenario_pick = st.selectbox("选择方案", scenario_names + ["➕ 新建方案"], key=f"quote_pick_{sel_proj}")

    if scenario_pick == "➕ 新建方案":
        current = {
            "方案名": "", "头版类型": "啤件头版", "工厂": "", "工艺": "",
            "订单量": 0, "备注": "", "建议售价系数": 0.333333,
            "条目": [{"报价项目": nm, "核算工厂报价": 0.0, "备注": ""} for nm in QUOTE_ITEM_DEFAULTS]
        }
        s_idx = None
    else:
        s_idx = scenario_names.index(scenario_pick)
        current = scenario_list[s_idx]
        current.setdefault("条目", [{"报价项目": nm, "核算工厂报价": 0.0, "备注": ""} for nm in QUOTE_ITEM_DEFAULTS])

    form_key = f"{sel_proj}_{'new' if s_idx is None else s_idx}"
    q1, q2, q3, q4, q5, q6 = st.columns([1.4, 1, 1, 1, 1, 1.2])
    with q1: sc_name = st.text_input("方案名", value=current.get("方案名", ""), key=f"q_name_{form_key}")
    with q2: sc_head = st.selectbox("头版类型", ["啤件头版", "翻模头版", "其他"], index=["啤件头版", "翻模头版", "其他"].index(current.get("头版类型", "啤件头版")) if current.get("头版类型", "啤件头版") in ["啤件头版", "翻模头版", "其他"] else 0, key=f"q_head_{form_key}")
    with q3: sc_factory = st.text_input("工厂", value=current.get("工厂", ""), key=f"q_factory_{form_key}")
    with q4: sc_process = st.text_input("工艺", value=current.get("工艺", ""), key=f"q_process_{form_key}")
    with q5: sc_qty = st.number_input("订单量", min_value=0, value=int(current.get("订单量", 0)), step=100, key=f"q_qty_{form_key}")
    with q6: sc_coef = st.number_input("建议售价系数(成本/系数)", min_value=0.05, max_value=1.0, value=float(current.get("建议售价系数", 0.333333)), step=0.01, key=f"q_coef_{form_key}")

    sc_note = st.text_input("方案备注", value=current.get("备注", ""), key=f"q_note_{form_key}")
    quote_df = pd.DataFrame(current.get("条目", []))
    if quote_df.empty:
        quote_df = pd.DataFrame([{"报价项目": nm, "核算工厂报价": 0.0, "备注": ""} for nm in QUOTE_ITEM_DEFAULTS])
    quote_df = st.data_editor(quote_df, num_rows="dynamic", use_container_width=True, key=f"q_editor_{form_key}")
    if "核算工厂报价" in quote_df.columns:
        quote_df["核算工厂报价"] = pd.to_numeric(quote_df["核算工厂报价"], errors="coerce").fillna(0.0)
    total_est = float(quote_df["核算工厂报价"].sum()) if "核算工厂报价" in quote_df.columns else 0.0
    suggest_price = (total_est / sc_coef) if sc_coef > 0 else 0.0
    st.info(f"预计整套成本价：¥{total_est:,.2f} | 建议售价：¥{suggest_price:,.2f}")

    qa, qb, qc = st.columns([1,1,2])
    with qa:
        if st.button("💾 保存/更新报价方案", key=f"q_save_{form_key}", type="primary"):
            payload = {
                "方案名": sc_name or f"方案{len(scenario_list)+1}", "头版类型": sc_head,
                "工厂": sc_factory, "工艺": sc_process, "订单量": int(sc_qty), "备注": sc_note,
                "建议售价系数": float(sc_coef), "预计整套成本价": round(total_est, 2), "建议售价": round(suggest_price, 2),
                "条目": quote_df.to_dict("records")
            }
            if s_idx is None:
                scenario_list.append(payload)
            else:
                scenario_list[s_idx] = payload
            sync_save_db(sel_proj); st.success("已保存报价方案"); st.rerun()
    with qb:
        if scenario_pick != "➕ 新建方案" and st.button("🗑️ 删除当前方案", key=f"q_del_{form_key}"):
            scenario_list.pop(s_idx)
            sync_save_db(sel_proj); st.success("已删除方案"); st.rerun()

    if scenario_list:
        st.markdown("#### 📌 方案对比")
        comp_df = pd.DataFrame([
            {"方案名": x.get("方案名", ""), "头版": x.get("头版类型", ""), "工厂": x.get("工厂", ""), "工艺": x.get("工艺", ""),
             "订单量": x.get("订单量", 0), "预计整套成本价": x.get("预计整套成本价", 0.0), "建议售价": x.get("建议售价", 0.0)}
            for x in scenario_list
        ])
        st.dataframe(comp_df, use_container_width=True)

        st.markdown("#### 📉 实际成本 vs 预计成本差异")
        diff_pick = st.selectbox("选择预计方案用于对比", scenario_names, key=f"cost_diff_pick_{sel_proj}")
        picked = next((x for x in scenario_list if x.get("方案名", "") == diff_pick), scenario_list[0])

        actual_rows = c_data.get("动态明细", [])
        df_actual = pd.DataFrame(actual_rows) if actual_rows else pd.DataFrame()
        if not df_actual.empty and "税后总成本" not in df_actual.columns and "含税金额" in df_actual.columns:
            df_actual["税后总成本"] = pd.to_numeric(df_actual["含税金额"], errors="coerce").fillna(0.0)
        actual_total = float(pd.to_numeric(df_actual.get("税后总成本", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()) if not df_actual.empty else 0.0
        est_total_pick = float(picked.get("预计整套成本价", 0.0))
        diff_val = actual_total - est_total_pick
        diff_rate = (diff_val / est_total_pick * 100) if est_total_pick > 0 else 0.0

        d1, d2, d3 = st.columns(3)
        d1.metric("预计总成本", f"¥{est_total_pick:,.2f}")
        d2.metric("实际总成本", f"¥{actual_total:,.2f}")
        d3.metric("差异(实际-预计)", f"¥{diff_val:,.2f}", delta=f"{diff_rate:.2f}%")

        if not df_actual.empty:
            act_by_cat = df_actual.groupby("分类", dropna=False)["税后总成本"].sum().reset_index().rename(columns={"税后总成本": "实际成本"})
            est_items = pd.DataFrame(picked.get("条目", []))
            if est_items.empty:
                est_by_cat = pd.DataFrame(columns=["分类", "预计成本"])
            else:
                est_items["核算工厂报价"] = pd.to_numeric(est_items.get("核算工厂报价", 0.0), errors="coerce").fillna(0.0)
                est_by_cat = est_items.rename(columns={"报价项目": "分类", "核算工厂报价": "预计成本"})[["分类", "预计成本"]]
            cmp_df = est_by_cat.merge(act_by_cat, on="分类", how="outer").fillna(0.0)
            cmp_df["差异"] = cmp_df["实际成本"] - cmp_df["预计成本"]
            cmp_df["差异率"] = cmp_df.apply(lambda r: f"{(r['差异'] / r['预计成本'] * 100):.2f}%" if r["预计成本"] > 0 else "-", axis=1)
            st.dataframe(cmp_df.sort_values(by="差异", ascending=False), use_container_width=True)
            st.caption("说明：分类差异按【报价项目】与【成本分类】同名匹配，不同命名会分开显示。")
        else:
            st.caption("暂无实际成本明细，导入或手动入账后会自动生成差异分析。")

    st.divider()
    st.subheader("📥 批量导入成本明细 (CSV)")
    cost_csv = st.file_uploader("选择成本 CSV 文件", type=['csv'], key="cost_csv")
    if cost_csv and st.button("🚀 开始解析导入", type="primary"):
        try:
            try:
                df_cost = pd.read_csv(cost_csv)
            except UnicodeDecodeError:
                cost_csv.seek(0)
                df_cost = pd.read_csv(cost_csv, encoding='gbk')
            col_cat    = next((c for c in df_cost.columns if any(k in str(c) for k in ['分类', '项目', '名称'])), None)
            col_vendor = next((c for c in df_cost.columns if any(k in str(c) for k in ['供应商', '收款', '公司'])), None)
            col_price  = next((c for c in df_cost.columns if any(k in str(c) for k in ['单价'])), None)
            col_qty    = next((c for c in df_cost.columns if any(k in str(c) for k in ['数量', '件数'])), None)
            col_amt    = next((c for c in df_cost.columns if any(k in str(c) for k in ['金额', '价', '总计'])), None)
            col_tax    = next((c for c in df_cost.columns if '税' in str(c)), None)
            count = 0
            for _, row in df_cost.iterrows():
                if not col_amt and not col_price: continue
                if col_amt and pd.isna(row[col_amt]): continue
                cat     = str(row[col_cat])    if col_cat    else "未分类"
                vendor  = str(row[col_vendor]) if col_vendor else "未知"
                raw_qty = float(row[col_qty])  if col_qty and not pd.isna(row[col_qty]) else 1.0
                if col_price and not pd.isna(row[col_price]):
                    raw_price = float(str(row[col_price]).replace(',', '').replace('¥', '').replace('￥', '').strip())
                    tot_after = raw_price * raw_qty
                elif col_amt:
                    tot_after = float(str(row[col_amt]).replace(',', '').replace('¥', '').replace('￥', '').strip())
                    raw_price = tot_after
                    raw_qty   = 1.0
                else:
                    continue
                tax_str = str(row[col_tax]).replace('%', '') if col_tax else "0"
                try:    tax_rate = float(tax_str)
                except: tax_rate = 0.0
                db[sel_proj].setdefault("成本数据", {}).setdefault("动态明细", []).append({
                    "分类": cat, "供应商": vendor, "税后单价": raw_price, "数量": raw_qty,
                    "税后总成本": tot_after, "税点": f"{tax_rate}%",
                    "税前总成本": round(tot_after / (1 + tax_rate / 100), 2)
                })
                count += 1
            sync_save_db(sel_proj)
            if count > 0: st.success(f"🎉 导入 {count} 条明细！"); st.balloons()
            else:          st.warning("⚠️ 未能识别金额数据。")
        except Exception as e:
            st.error(f"解析失败: {e}")

    st.divider()
    st.subheader("➕ 手动录入单笔成本")
    ac1, ac2, ac3, ac4, ac5 = st.columns([2, 2, 2, 1.5, 1.5])
    with ac1: c_name   = st.selectbox("成本分类", STD_COSTS_LIST)
    with ac2: vendor   = st.text_input("供应商", placeholder="例：志昇")
    with ac3: c_unit   = st.number_input("税后单价(¥)", min_value=0.0, step=100.0)
    with ac4: c_qty    = st.number_input("数量", min_value=1.0, value=1.0, step=1.0)
    with ac5: tax_rate = st.selectbox("税点(%)", [0, 1, 3, 6, 9, 13])
    if st.button("入账"):
        db[sel_proj].setdefault("成本数据", {}).setdefault("动态明细", []).append({
            "分类": c_name, "供应商": vendor,
            "税后单价": float(c_unit), "数量": float(c_qty),
            "税后总成本": float(Decimal(str(c_unit)) * Decimal(str(c_qty))),
            "税点": f"{tax_rate}%",
            "税前总成本": float(round(
                (Decimal(str(c_unit)) * Decimal(str(c_qty))) /
                (Decimal("1") + Decimal(str(tax_rate)) / Decimal("100")), 2
            ))
        })
        sync_save_db(sel_proj)
        st.rerun()

    details = c_data.get("动态明细", [])
    if details:
        for d in details:
            if '含税金额' in d and '税后总成本' not in d:
                d['税后总成本'] = d['含税金额']; d['数量'] = 1.0; d['税后单价'] = d['含税金额']
                if '税前金额' in d: d['税前总成本'] = d['税前金额']
        df_cost_show  = pd.DataFrame(details)
        display_cols  = ['分类', '供应商', '税后单价', '数量', '税后总成本', '税点', '税前总成本']
        df_cost_show  = df_cost_show[[c for c in display_cols if c in df_cost_show.columns]]

        st.divider()
        st.markdown("### 📊 各分类成本总计")
        subtotals = df_cost_show.groupby('分类')['税后总成本'].sum().reset_index()
        num_cols  = min(len(subtotals), 6)
        if num_cols > 0:
            metric_cols = st.columns(num_cols)
            for i, row in subtotals.iterrows():
                metric_cols[i % num_cols].metric(label=row['分类'], value=f"¥ {row['税后总成本']:,.2f}")

        total_sub = float(subtotals['税后总成本'].sum()) if not subtotals.empty else 0.0
        if total_sub > 0:
            share_df = subtotals.copy()
            share_df['成本占比'] = (share_df['税后总成本'] / total_sub * 100).round(2).astype(str) + '%'
            st.markdown("#### 🧮 各分类成本占比")
            st.dataframe(share_df.sort_values(by='税后总成本', ascending=False), use_container_width=True)

        st.divider()
        st.markdown("### 📝 动态明细管理")
        edited_df = st.data_editor(df_cost_show, num_rows="dynamic", use_container_width=True)
        if st.button("💾 确认并保存修改", type="primary"):
            for idx, row in edited_df.iterrows():
                try:
                    qty_d  = Decimal(str(row.get('数量', 1.0)))
                    unit_d = Decimal(str(row.get('税后单价', 0.0)))
                    tax_str = str(row.get('税点', '0%')).replace('%', '')
                    rate_d  = Decimal(tax_str) if tax_str else Decimal("0.0")
                    tot_d   = qty_d * unit_d
                    tax_div = Decimal("1") + (rate_d / Decimal("100"))
                    edited_df.at[idx, '税后总成本'] = float(tot_d)
                    edited_df.at[idx, '税前总成本'] = float(round(tot_d / tax_div, 2))
                except:
                    pass
            db[sel_proj]["成本数据"]["动态明细"] = edited_df.to_dict('records')
            sync_save_db(sel_proj)
            st.success("✅ 成本明细已更新！")
            st.rerun()

        st.divider()
        total_c      = sum(edited_df['税后总成本']) if not edited_df.empty else 0
        saved_orders = int(c_data.get("总订单数", orders))
        saved_price  = float(c_data.get("销售单价", price))
        unit_c       = total_c / saved_orders if saved_orders > 0 else 0
        st.info(
            f"**💰 累计税后总成本:** ¥{total_c:,.2f} | "
            f"**单体核算成本:** ¥{unit_c:,.2f} | "
            f"**单体毛利:** ¥{saved_price - unit_c:,.2f} | "
            f"**预测毛利率:** {(saved_price - unit_c) / saved_price * 100 if saved_price > 0 else 0:.2f}%"
        )

# ==========================================
# 模块 6：历史溯源
# ==========================================
elif menu == MENU_HISTORY:
    st.title("🔍 图文交接溯源档案 (全局/可编辑)")
    valid_p = [p for p in db.keys() if p != "系统配置"]
    if not valid_p: st.stop()
    sel_proj = st.selectbox("📌 选择溯源项目", valid_p)

    for c_name, comp in db[sel_proj].get("部件列表", {}).items():
        for log in comp.get("日志流", []):
            if is_hidden_system_log(log):
                continue
            if "_id" not in log:
                log["_id"] = str(uuid.uuid4())

    comps_in_proj = ["🌐 全部展示"] + list(db[sel_proj].get("部件列表", {}).keys())
    sel_comp      = st.selectbox("📌 筛选特定部件 (默认全览)", comps_in_proj)

    grouped_logs = {}
    log_ref_map  = {}
    for c_name, comp in db[sel_proj].get("部件列表", {}).items():
        if sel_comp != "🌐 全部展示" and c_name != sel_comp:
            continue
        for log in comp.get("日志流", []):
            if is_hidden_system_log(log):
                continue
            log_ref_map[log["_id"]] = log
            key = (log.get("日期",""), log.get("工序",""), log.get("流转",""), log.get("事件",""), log.get("提审类型",""), log.get("提审结果",""), log.get("提审轮次",""))
            if key not in grouped_logs:
                grouped_logs[key] = {"_ids": [log["_id"]], "部件": [c_name], "log": log}
            else:
                grouped_logs[key]["_ids"].append(log["_id"])
                grouped_logs[key]["部件"].append(c_name)

    flat_data = []
    for g in grouped_logs.values():
        all_imgs   = []
        seen_imgs  = set()
        for log_id in g["_ids"]:
            if log_id in log_ref_map:
                for img in log_ref_map[log_id].get("图片", []):
                    if img and img not in seen_imgs:
                        seen_imgs.add(img)
                        all_imgs.append(img)
        flat_data.append({
            "_ids": g["_ids"], "部件": ", ".join(g["部件"]),
            "日期": g["log"]["日期"], "工序": g["log"]["工序"],
            "类型": g["log"]["流转"], "事件": g["log"]["事件"],
            "提审类型": g["log"].get("提审类型", "(无)"),
            "提审结果": g["log"].get("提审结果", "(无)"),
            "提审轮次": g["log"].get("提审轮次", ""),
            "图片": all_imgs
        })

    if flat_data:
        df_logs = pd.DataFrame(flat_data).sort_values(by="日期", ascending=False).reset_index(drop=True)
        df_logs.insert(0, '序号', range(len(df_logs), 0, -1))

        st.info("💡 下方为历史日志。直接**双击修改文字**，或选中整行后按 **Delete** 删除。")
        edited_df = st.data_editor(
            df_logs.drop(columns=["_ids", "图片"]),
            column_config={
                "序号":  st.column_config.NumberColumn(disabled=True),
                "部件":  st.column_config.TextColumn(disabled=True),
                "工序":  st.column_config.SelectboxColumn("工序", options=STAGES_UNIFIED, required=True),
                "提审类型": st.column_config.SelectboxColumn("提审类型", options=REVIEW_TYPE_OPTIONS, required=True),
                "提审结果": st.column_config.SelectboxColumn("提审结果", options=REVIEW_RESULT_OPTIONS, required=True),
                "提审轮次": st.column_config.NumberColumn("提审轮次", min_value=1, step=1)
            },
            num_rows="dynamic", use_container_width=True
        )

        if st.button("💾 确认并覆盖保存历史记录", type="primary"):
            new_logs_by_comp = {}
            for i, row in edited_df.iterrows():
                if pd.isna(row.get("部件")) or str(row.get("部件")).strip() in ["", "None", "nan"]:
                    continue
                c_list = [x.strip() for x in str(row["部件"]).split(",")]
                for c in c_list:
                    if not c: c = "全局进度"
                    if c not in new_logs_by_comp:
                        new_logs_by_comp[c] = []
                    old_ids    = df_logs.at[i, "_ids"] if i in df_logs.index else []
                    old_images = log_ref_map[old_ids[0]].get("图片", []) if old_ids and old_ids[0] in log_ref_map else []
                    if not isinstance(old_images, list):
                        old_images = [old_images] if old_images else []
                    new_logs_by_comp[c].append({
                        "_id": str(uuid.uuid4()),
                        "日期": str(row["日期"]), "工序": str(row["工序"]),
                        "流转": str(row["类型"]), "事件": str(row["事件"]),
                        "提审类型": str(row.get("提审类型", "(无)")),
                        "提审结果": str(row.get("提审结果", "(无)")),
                        "提审轮次": normalize_review_round(row.get("提审轮次", "")),
                        "图片": old_images
                    })
            comps_in_scope = ([sel_comp] if sel_comp != "🌐 全部展示"
                              else list(db[sel_proj].get("部件列表", {}).keys()))
            for c in comps_in_scope:
                if c in db[sel_proj].get("部件列表", {}):
                    db[sel_proj]["部件列表"][c]["日志流"] = sorted(
                        new_logs_by_comp.get(c, []), key=lambda x: x.get("日期", "")
                    )
            sync_save_db(sel_proj)
            st.success("✅ 历史记录已更新！")
            st.rerun()

        st.divider()
        st.subheader("🖼️ 历史参考图画廊 (时间倒序)")
        has_images = False
        img_groups = [(g, g["图片"] if isinstance(g["图片"], list) else ([g["图片"]] if g["图片"] else [])) 
                      for g in flat_data]
        img_groups = [(g, imgs) for g, imgs in img_groups if imgs]
        
        if not img_groups:
            st.caption("该过滤条件下暂无历史参考图片。")
        else:
            has_images = True
            st.caption(f"共 {len(img_groups)} 组参考图，点击展开查看")
            for g_data, images in img_groups:
                raw_evt = g_data['事件']
                clean_detail = raw_evt
                if "补充:" in raw_evt: clean_detail = raw_evt.split("补充:")[-1].split("[系统]")[0].strip()
                elif "】" in raw_evt: clean_detail = raw_evt.split("】")[-1].split("[系统]")[0].strip()
                label = f"📅 {g_data['日期']} | 📍 {g_data['工序']} | 🧩 {g_data['部件']} — {clean_detail[:30]}{'…' if len(clean_detail)>30 else ''}"
                with st.expander(label, expanded=False):
                    cols = st.columns(min(len(images), 4))
                    for i, img_b64 in enumerate(images):
                        with cols[i % 4]:
                            render_image(img_b64, use_container_width=True)
    else:
        st.info("该过滤条件下暂无记录。")

# ==========================================
# 模块 7：系统维护
# ==========================================
elif menu == MENU_SETTINGS:
    st.title("⚙️ 系统维护 (全局参数与词库管理)")

    with st.expander("🧭 项目管理（重命名 / 合并同类 / 别名学习）", expanded=True):
        all_proj_names = [p for p in db.keys() if p != "系统配置"]
        if not all_proj_names:
            st.info("暂无项目可管理。")

        else:
            st.markdown("**A. 重命名项目**")
            c_r1, c_r2, c_r3 = st.columns([1.2, 1.2, 1])
            with c_r1:
                src_proj = st.selectbox("选择项目", all_proj_names, key="rename_src")
            with c_r2:
                new_proj_name = st.text_input("新名称", value=src_proj, key="rename_dst")
            with c_r3:
                st.write("")
                if st.button("✏️ 确认重命名", type="primary", key="btn_rename"):
                    if not new_proj_name.strip():
                        st.error("新名称不能为空。")
                    elif new_proj_name == src_proj:
                        st.warning("名称未变化，无需重命名。")
                    elif new_proj_name in db:
                        st.error("目标名称已存在，请先使用“合并同类项目”。")
                    else:
                        db[new_proj_name] = db.pop(src_proj)
                        alias_map = st.session_state.db["系统配置"].setdefault("项目别名", {})
                        alias_map[norm_text(src_proj)] = new_proj_name
                        sync_save_db()
                        st.success(f"✅ 已重命名：{src_proj} → {new_proj_name}")
                        st.rerun()

            st.markdown("---")
            st.markdown("**B. 合并同类项目 + 自动学习别名**")
            c_m1, c_m2, c_m3 = st.columns([1, 1, 1.2])
            with c_m1:
                merge_src = st.selectbox("并入来源项目", all_proj_names, key="merge_src")
            with c_m2:
                merge_dst = st.selectbox("目标项目", all_proj_names, key="merge_dst")
            with c_m3:
                alias_input = st.text_input("附加别名（逗号分隔）", placeholder="如: 1/6超女, 1/6 supergirl, 1/6超级女孩")

            if st.button("🔀 执行合并并学习别名", type="primary", key="btn_merge"):
                if merge_src == merge_dst:
                    st.error("来源项目与目标项目不能相同。")
                else:
                    src_data = db.get(merge_src, {})
                    dst_data = db.get(merge_dst, {})
                    st.session_state.db["系统配置"].setdefault("最近合并回滚", {})
                    rollback_payload = {
                        "id": str(uuid.uuid4()),
                        "时间": str(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                        "merge_src": merge_src,
                        "merge_dst": merge_dst,
                        "src_data": json.loads(json.dumps(src_data, ensure_ascii=False)),
                        "dst_data_before": json.loads(json.dumps(dst_data, ensure_ascii=False)),
                        "alias_map_before": json.loads(json.dumps(st.session_state.db["系统配置"].get("项目别名", {}), ensure_ascii=False))
                    }
                    st.session_state.db["系统配置"]["最近合并回滚"] = rollback_payload
                    st.session_state.db["系统配置"].setdefault("合并回滚历史", []).append(rollback_payload)
                    dst_data.setdefault("部件列表", {})
                    for comp_name, comp_data in src_data.get("部件列表", {}).items():
                        if comp_name not in dst_data["部件列表"]:
                            dst_data["部件列表"][comp_name] = comp_data
                        else:
                            dst_data["部件列表"][comp_name].setdefault("日志流", [])
                            dst_data["部件列表"][comp_name]["日志流"].extend(comp_data.get("日志流", []))
                    for bucket in ["发货数据", "成本数据"]:
                        dst_data.setdefault(bucket, {})
                        for k, v in src_data.get(bucket, {}).items():
                            if k not in dst_data[bucket]:
                                dst_data[bucket][k] = v

                    db[merge_dst] = dst_data
                    if merge_src in db:
                        del db[merge_src]

                    alias_map = st.session_state.db["系统配置"].setdefault("项目别名", {})
                    learned_aliases = {merge_src, merge_dst}
                    if alias_input.strip():
                        learned_aliases.update(x.strip() for x in re.split(r'[,，]', alias_input) if x.strip())
                    for a in learned_aliases:
                        alias_map[norm_text(a)] = merge_dst

                    sync_save_db()
                    st.success(f"✅ 合并完成：{merge_src} → {merge_dst}，并已学习 {len(learned_aliases)} 个别名。")
                    st.rerun()

            alias_map = st.session_state.db["系统配置"].get("项目别名", {})
            if alias_map:
                alias_df = pd.DataFrame([
                    {"别名(归一化)": k, "映射项目": v} for k, v in sorted(alias_map.items(), key=lambda x: x[0])
                ])
                st.markdown("**当前别名词典**")
                st.dataframe(alias_df, use_container_width=True)
                c_a1, c_a2 = st.columns([1.2, 1])
                with c_a1:
                    del_alias = st.selectbox("删除某个别名映射", [""] + sorted(alias_map.keys()), key="del_alias_key")
                    if st.button("🧯 删除该别名", key="btn_del_alias") and del_alias:
                        st.session_state.db["系统配置"].setdefault("项目别名", {}).pop(del_alias, None)
                        sync_save_db()
                        st.success(f"已删除别名：{del_alias}")
                        st.rerun()
                with c_a2:
                    if st.button("🧹 清空全部别名映射", key="btn_clear_alias"):
                        st.session_state.db["系统配置"]["项目别名"] = {}
                        sync_save_db()
                        st.success("已清空全部别名映射。")
                        st.rerun()

            rollback = st.session_state.db["系统配置"].get("最近合并回滚", {})
            if rollback and rollback.get("merge_src"):
                st.markdown("---")
                st.markdown(f"**后悔药（最近一次合并）**：{rollback.get('merge_src')} → {rollback.get('merge_dst')}")
                if st.button("↩️ 撤销最近一次合并", key="btn_undo_merge"):
                    src_name = rollback.get("merge_src")
                    dst_name = rollback.get("merge_dst")
                    if dst_name in db:
                        db[dst_name] = rollback.get("dst_data_before", db.get(dst_name, {}))
                    db[src_name] = rollback.get("src_data", {})
                    st.session_state.db["系统配置"]["项目别名"] = rollback.get(
                        "alias_map_before", st.session_state.db["系统配置"].get("项目别名", {})
                    )
                    st.session_state.db["系统配置"].setdefault("最近合并回滚", {})
                    st.session_state.db["系统配置"]["最近合并回滚"] = {}
                    sync_save_db()
                    st.success("✅ 已撤销最近一次合并。")
                    st.rerun()

            hist = st.session_state.db["系统配置"].setdefault("合并回滚历史", [])
            if hist:
                st.markdown("---")
                st.markdown("**合并回滚历史（可多选删除，单条恢复）**")
                hist_df = pd.DataFrame([
                    {
                        "ID": h.get("id", ""),
                        "时间": h.get("时间", ""),
                        "来源": h.get("merge_src", ""),
                        "目标": h.get("merge_dst", "")
                    }
                    for h in hist
                ]).sort_values(by=["时间"], ascending=False)
                st.dataframe(hist_df, use_container_width=True)
                id_list = hist_df["ID"].tolist()
                sel_restore = st.selectbox("选择要恢复的历史记录（单选）", ["(不选择)"] + id_list, key="merge_hist_restore")
                c_h1, c_h2 = st.columns(2)
                with c_h1:
                    if st.button("↩️ 按历史记录恢复", key="btn_restore_hist") and sel_restore != "(不选择)":
                        tar = next((x for x in hist if x.get("id") == sel_restore), None)
                        if tar:
                            src_name = tar.get("merge_src")
                            dst_name = tar.get("merge_dst")
                            if dst_name in db:
                                db[dst_name] = tar.get("dst_data_before", db.get(dst_name, {}))
                            db[src_name] = tar.get("src_data", {})
                            st.session_state.db["系统配置"]["项目别名"] = tar.get(
                                "alias_map_before", st.session_state.db["系统配置"].get("项目别名", {})
                            )
                            sync_save_db()
                            st.success("✅ 已按历史记录恢复。")
                            st.rerun()
                with c_h2:
                    del_ids = st.multiselect("多选删除历史记录", id_list, key="merge_hist_delete")
                    if st.button("🗑️ 删除选中历史", key="btn_del_hist") and del_ids:
                        st.session_state.db["系统配置"]["合并回滚历史"] = [x for x in hist if x.get("id") not in set(del_ids)]
                        sync_save_db()
                        st.success(f"已删除 {len(del_ids)} 条历史记录。")
                        st.rerun()

    with st.expander("🛠️ 团队成员清洗 (支持按职能/姓名替换)", expanded=True):
        st.info("替换某个人的特定职能（如：将 `建模-雨萱` 替换为 `设计-雨萱`），留空即彻底抹除。")
        all_names = set()
        for p_data in db.values():
            if not isinstance(p_data, dict) or "部件列表" not in p_data: continue
            for c_data in p_data['部件列表'].values():
                for pair in re.split(r'[,，|]', c_data.get('负责人', '')):
                    pair = pair.strip()
                    if pair and pair != '未分配':
                        all_names.add(pair)
        c_old, c_new, c_btn = st.columns([1.5, 1.5, 1])
        with c_old: old_n = st.selectbox("1. 选中要清洗的组合", [""] + sorted(list(all_names)))
        with c_new: new_n = st.text_input("2. 替换为新组合 (留空即删除)")
        with c_btn:
            st.write("")
            if st.button("🚨 确认全库替换", type="primary") and old_n:
                count_fixed = 0
                for p_data in db.values():
                    if not isinstance(p_data, dict) or "部件列表" not in p_data: continue
                    for c_data in p_data['部件列表'].values():
                        owner_str = c_data.get('负责人', '')
                        if not owner_str: continue
                        pairs     = [x.strip() for x in re.split(r'[,，]', owner_str) if x.strip()]
                        new_pairs = []; changed = False
                        for p in pairs:
                            if p == old_n:
                                if new_n.strip(): new_pairs.append(new_n.strip())
                                changed = True
                            else:
                                new_pairs.append(p)
                        if changed:
                            c_data['负责人'] = ", ".join(new_pairs)
                            count_fixed += 1
                sync_save_db()
                st.success(f"✅ 清洗完成！全库共修正 {count_fixed} 处记录。")
                st.rerun()

    with st.expander("⏱️ 全局计划排期默认基线"):
        st.info("设定各阶段的默认目标天数。")
        cols     = st.columns(len(SYS_CFG["排期基线"]))
        new_days = {}
        for i, (k, v) in enumerate(SYS_CFG["排期基线"].items()):
            new_days[k] = cols[i].number_input(k, value=int(v), step=1, key=f"bd_{k}")
        if st.button("💾 保存默认基线天数"):
            st.session_state.db["系统配置"]["排期基线"] = new_days
            sync_save_db()
            st.success("已更新默认计划基线！")
            st.rerun()

    with st.expander("🧹 数据库瘦身 & Base64 图片迁移工具", expanded=True):
        st.warning("⚠️ 如果 JSON 文件很大，说明旧版 Base64 图片还留在数据库里。点击下方按钮一键迁移，JSON 将大幅缩小。")
        json_str     = json.dumps(st.session_state.db, ensure_ascii=False)
        json_size_mb = len(json_str.encode('utf-8')) / 1024 / 1024
        b64_count = 0; file_count = 0
        for p_name, p_data in st.session_state.db.items():
            if p_name == "系统配置" or not isinstance(p_data, dict): continue
            for c_data in p_data.get("部件列表", {}).values():
                for log in c_data.get("日志流", []):
                    for img in log.get("图片", []):
                        if isinstance(img, str):
                            if img.startswith("FILE:"): file_count += 1
                            elif len(img) > 100:        b64_count  += 1
            for img in p_data.get("配件清单长图", []):
                if isinstance(img, str):
                    if img.startswith("FILE:"): file_count += 1
                    elif len(img) > 100:        b64_count  += 1
        col_s1, col_s2, col_s3 = st.columns(3)
        col_s1.metric("📦 JSON 当前体积",      f"{json_size_mb:.1f} MB")
        col_s2.metric("🖼️ 待迁移 Base64 图片", f"{b64_count} 张")
        col_s3.metric("✅ 已迁移文件图片",      f"{file_count} 张")

        if b64_count > 0:
            if st.button("🚀 一键迁移：将所有 Base64 图片转存为本地文件", type="primary"):
                migrated = 0; errors = 0
                progress = st.progress(0, text="迁移中...")
                all_refs = []
                for p_name, p_data in st.session_state.db.items():
                    if p_name == "系统配置" or not isinstance(p_data, dict): continue
                    for c_data in p_data.get("部件列表", {}).values():
                        for log in c_data.get("日志流", []):
                            imgs = log.get("图片", [])
                            for idx, img in enumerate(imgs):
                                if isinstance(img, str) and not img.startswith("FILE:") and len(img) > 100:
                                    all_refs.append((imgs, idx))
                    for idx, img in enumerate(p_data.get("配件清单长图", [])):
                        if isinstance(img, str) and not img.startswith("FILE:") and len(img) > 100:
                            all_refs.append((p_data["配件清单长图"], idx))
                total = len(all_refs)
                if not os.path.exists(IMG_DIR): os.makedirs(IMG_DIR)
                for i, (container, idx) in enumerate(all_refs):
                    try:
                        b64_str    = container[idx]
                        img_bytes  = base64.b64decode(b64_str)
                        file_name  = f"migrated_{uuid.uuid4().hex}.jpg"
                        file_path  = os.path.join(IMG_DIR, file_name)
                        img        = Image.open(io.BytesIO(img_bytes))
                        if img.mode in ("RGBA", "P"): img = img.convert("RGB")
                        img.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
                        img.save(file_path, format="JPEG", quality=70)
                        container[idx] = f"FILE:{file_name}"
                        migrated += 1
                    except:
                        errors += 1
                    progress.progress((i + 1) / total, text=f"迁移中... {i+1}/{total}")
                sync_save_db()
                new_json_str  = json.dumps(st.session_state.db, ensure_ascii=False)
                new_size_mb   = len(new_json_str.encode('utf-8')) / 1024 / 1024
                saved_mb      = json_size_mb - new_size_mb
                st.success(f"🎉 迁移完成！成功 {migrated} 张，失败 {errors} 张。"
                           f"JSON 从 {json_size_mb:.1f}MB → {new_size_mb:.1f}MB，节省 {saved_mb:.1f}MB！")
                st.rerun()
        else:
            st.success("✅ 数据库已是最优状态，无需迁移！")

        st.divider()
        st.markdown("#### 🗜️ 图片重新压缩（进一步缩小 img_assets 目录）")
        col_q1, col_q2 = st.columns(2)
        with col_q1: recomp_quality = st.slider("压缩质量", 30, 85, 60)
        with col_q2: recomp_size    = st.selectbox("最大尺寸", ["800x800", "600x600", "1000x1000"], index=0)
        if st.button("🗜️ 对所有本地图片执行二次压缩"):
            max_dim      = int(recomp_size.split("x")[0])
            recomp_count = 0; recomp_errors = 0
            if os.path.exists(IMG_DIR):
                files = [f for f in os.listdir(IMG_DIR) if f.endswith(('.jpg', '.jpeg', '.png'))]
                prog2 = st.progress(0, text="压缩中...")
                for i, fname in enumerate(files):
                    fpath = os.path.join(IMG_DIR, fname)
                    try:
                        img = Image.open(fpath)
                        if img.mode in ("RGBA", "P"): img = img.convert("RGB")
                        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                        img.save(fpath, format="JPEG", quality=recomp_quality, optimize=True)
                        recomp_count += 1
                    except:
                        recomp_errors += 1
                    prog2.progress((i+1)/len(files), text=f"压缩中... {i+1}/{len(files)}")
                st.success(f"✅ 二次压缩完成！处理 {recomp_count} 张，失败 {recomp_errors} 张。")

# ==========================================
# 模块 8：新手指南
# ==========================================
elif menu == MENU_GUIDE:
    st.title("📖 INART PM 系统 (v49) 快速上手指南")
    st.info("👋 新同事建议按下面顺序操作：先 PM 工作台录入，再看全局大盘，再用历史溯源校验。")
    st.markdown("---")

    with st.expander("🚀 5分钟上手路径", expanded=True):
        st.markdown(
            "1. 在 **【🎯 PM 工作台】** 先创建/选择项目。\n"
            "2. 在 To do 中录入任务（任务必填，CP/DDL 合并字段可选）。\n"
            "3. 在细分配件工作台更新阶段、提审类型、提审结果并保存。\n"
            "4. 去 **【📊 全局大盘与甘特图】** 检查断更、临期预警和甘特时段。"
        )

    with st.expander("🎯 PM 工作台：To do + 透视矩阵", expanded=True):
        st.markdown(
            "1. **To do 排序规则**：未完成在上，已完成自动下沉；CP/DDL 中识别到日期时会做临期提醒。\n"
            "2. **透视矩阵颜色**：绿色=完成，蓝色=进行中/暂停点，深灰=暂停前流转，黄色=Delay，浅灰=未到阶段。\n"
            "3. **包装快捷跟踪**：可在 PM 工作台直接勾选包装状态；附件追溯在包装模块维护。"
        )

    with st.expander("📝 AI 速记：最低成本自动学习", expanded=False):
        st.markdown(
            "1. 先点击 **智能拆解**，逐条校对项目/部件/阶段。\n"
            "2. 勾选 **自动学习新词**，系统会把新词沉淀到本地词库。\n"
            "3. 上传的附件会自动跟随每条记录写入对应项目，后续可在历史溯源直接查图。"
        )

    with st.expander("💰 成本：预计 vs 实际", expanded=False):
        st.markdown(
            "1. 在预计报价模板按 **工厂/工艺/头版方案** 建多个场景。\n"
            "2. 在动态明细持续入账实际成本。\n"
            "3. 系统会显示 **实际-预计差异**（总额与分类）。分类差异按同名匹配。"
        )

    with st.expander("🧯 系统维护与风险提示", expanded=False):
        st.markdown(
            "1. 合并同类项目支持历史恢复与多选删除。\n"
            "2. **数据库瘦身**建议保留：当图片多时可显著降低库体积与加载时间。\n"
            "3. 若当前体积很小，可暂时不执行迁移，仅保留备份策略。"
        )

    with st.expander("💾 备份与恢复", expanded=False):
        st.markdown(
            "每次收工建议下载全量备份（数据+图片）；换设备后通过上传备份一键恢复。"
        )













































