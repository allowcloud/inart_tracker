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
from collections import Counter
from decimal import Decimal
from functools import lru_cache
from PIL import Image
st.set_page_config(
    page_title="INART PM 系统",
    page_icon="📌",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .stApp [data-testid="stAppViewContainer"] .main .block-container {
        max-width: 1680px;
        padding-top: 1.5rem;
        padding-left: 2rem;
        padding-right: 2rem;
    }
    footer { visibility: hidden; }
    </style>
    """,
    unsafe_allow_html=True,
)


# --- Bootstrap fallback (for cloud/runtime safety) ---
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
                evt = str((lg or {}).get("事件", "")).strip()
                if "[属性更新]" in evt:
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
# 全局 CSS：减少白屏闪烁、优化表格渲染
st.markdown("""
<style>
:root {
    --pm-border: #dbe3ef;
    --pm-bg-start: #f8fbff;
    --pm-bg-end: #f5f8fc;
    --pm-sidebar-start: #f7fafc;
    --pm-sidebar-end: #edf4ff;
    --pm-card: #ffffff;
    --pm-text-soft: #475569;
    --pm-accent: #0f766e;
}
@media (prefers-color-scheme: dark) {
    :root {
        --pm-border: #334155;
        --pm-bg-start: #0b1220;
        --pm-bg-end: #111827;
        --pm-sidebar-start: #0f172a;
        --pm-sidebar-end: #111827;
        --pm-card: #0f172a;
        --pm-text-soft: #cbd5e1;
        --pm-accent: #22c55e;
    }
}
/* prevent blank flash while switching pages */
.stSpinner > div { margin-top: 20vh; }
/* app background follows system light/dark */
[data-testid="stAppViewContainer"] {
    background: linear-gradient(180deg, var(--pm-bg-start) 0%, var(--pm-bg-end) 100%);
}
/* compact table cells for denser information */
[data-testid="stDataFrame"] table td { padding: 4px 8px !important; font-size: 13px; }
[data-testid="stDataEditor"] [role="gridcell"] { font-size: 13px; }
/* sidebar visual tuning with same theme variables */
section[data-testid="stSidebar"] { background: linear-gradient(180deg, var(--pm-sidebar-start) 0%, var(--pm-sidebar-end) 100%); }
section[data-testid="stSidebar"] .stButton button { width: 100%; border-radius: 8px; }
/* metric cards */
[data-testid="stMetric"] { border: 1px solid var(--pm-border); border-radius: 10px; padding: 6px 10px; background: var(--pm-card); }
[data-testid="stMetric"] [data-testid="stMetricLabel"] { color: var(--pm-text-soft); }
/* hide streamlit footer */
footer { visibility: hidden; }
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

def _deep_copy_obj(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False))


class _LocalJsonDBManager:
    backend_name = "Local JSON"
    attachment_mode = "local-file"

    def __init__(self, path="tracker_data_web_v20.json"):
        self.path = path

    def load(self):
        if os.path.exists(self.path):
            for enc in ["utf-8", "utf-8-sig", "gbk"]:
                try:
                    with open(self.path, "r", encoding=enc) as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        return data
                except Exception:
                    continue
        return {"系统配置": _deep_copy_obj(DEFAULT_SYS_CFG)}

    def save(self, data):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def save_one(self, key, value):
        data = self.load()
        data[key] = value
        self.save(data)

    def save_file_bytes(self, file_bytes, filename="", prefix="upload"):
        if not os.path.exists(IMG_DIR):
            os.makedirs(IMG_DIR)
        ext = os.path.splitext(filename or "")[1].lower() or ".jpg"
        fname = f"{prefix}_{uuid.uuid4().hex}{ext}"
        fpath = os.path.join(IMG_DIR, fname)
        with open(fpath, "wb") as f:
            f.write(file_bytes)
        return f"FILE:{fname}"

    def read_file_bytes(self, ref):
        if not isinstance(ref, str) or not ref.startswith("FILE:"):
            return None
        file_path = os.path.join(IMG_DIR, ref.replace("FILE:", "", 1))
        if not os.path.exists(file_path):
            return None
        with open(file_path, "rb") as f:
            return f.read()

    def import_file_bytes(self, ref, file_bytes, filename=""):
        if not os.path.exists(IMG_DIR):
            os.makedirs(IMG_DIR)
        if isinstance(ref, str) and ref.startswith("FILE:"):
            fname = ref.replace("FILE:", "", 1)
        else:
            ext = os.path.splitext(filename or "")[1].lower() or ".jpg"
            fname = f"restore_{uuid.uuid4().hex}{ext}"
        fpath = os.path.join(IMG_DIR, fname)
        with open(fpath, "wb") as f:
            f.write(file_bytes)
        return f"FILE:{fname}"


class _MongoDBManager:
    backend_name = "MongoDB"
    attachment_mode = "gridfs"

    def __init__(self, uri):
        self.local_json_path = "tracker_data_web_v20.json"
        self.use_local_json = False
        self.client = None
        self.col = None
        self.fs = None
        self.PyMongoError = Exception
        self.NoFile = FileNotFoundError
        self.ObjectId = lambda raw: raw

        try:
            from pymongo import MongoClient
            from pymongo.errors import PyMongoError
            from gridfs import GridFS, NoFile
            from bson import ObjectId
        except Exception as e:
            self.use_local_json = True
            st.warning(f"Mongo 初始化失败，已回退本地 JSON：{e}")
            return

        self.PyMongoError = PyMongoError
        self.NoFile = NoFile
        self.ObjectId = ObjectId
        uri = uri or _get_mongo_uri()
        if not uri:
            self.use_local_json = True
            st.info("未检测到 MONGO_URI，使用本地 JSON 存储。")
            return

        try:
            self.client = MongoClient(
                uri,
                serverSelectionTimeoutMS=10000,
                connectTimeoutMS=10000,
                socketTimeoutMS=10000,
                maxPoolSize=5,
            )
            self.db = self.client["inart_pm"]
            self.col = self.db["projects"]
            self.fs = GridFS(self.db, collection="attachments")
        except Exception as e:
            self.use_local_json = True
            self.client = None
            self.col = None
            self.fs = None
            st.warning(f"Mongo 连接失败，已回退本地 JSON：{e}")

    def _local_manager(self):
        return _LocalJsonDBManager(self.local_json_path)

    def _load_cached(self):
        if getattr(self, "use_local_json", False) or getattr(self, "col", None) is None:
            return None
        try:
            docs = list(self.col.find({}, {"_id": 0}))
            data = {}
            for doc in docs:
                key = doc.get("_doc_key")
                if key:
                    data[key] = doc.get("payload", {})
            return data if data else None
        except Exception as e:
            st.warning(f"Mongo 读取失败，回退 JSON 缓存：{e}")
            return None

    def _migrate_from_json(self):
        """尝试将本地 JSON 数据迁移到 MongoDB。"""
        json_path = self.local_json_path
        if os.path.exists(json_path):
            for enc in ["utf-8", "utf-8-sig", "gbk"]:
                try:
                    with open(json_path, "r", encoding=enc) as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        self.save(data)
                        try:
                            os.rename(json_path, json_path + ".migrated")
                        except OSError:
                            pass
                        return data
                except Exception:
                    continue
        return DEFAULT_DB.copy()

    def load(self):
        if getattr(self, "use_local_json", False):
            return self._local_manager().load()
        try:
            cached = self._load_cached()
            if cached is not None:
                return cached
            return self._migrate_from_json()
        except Exception as e:
            st.warning(f"数据库加载失败，回退本地 JSON：{e}")
            self.use_local_json = True
            return self._local_manager().load()

    def save(self, data):
        """保存全量数据（Mongo 异常时由调用方回退）。"""
        if self.use_local_json:
            self._local_manager().save(data)
            return
        try:
            from pymongo import UpdateOne
            ops = [
                UpdateOne({"_doc_key": key}, {"$set": {"_doc_key": key, "payload": value}}, upsert=True)
                for key, value in data.items()
            ]
            if ops:
                self.col.bulk_write(ops, ordered=False)
        except self.PyMongoError as e:
            st.warning(f"Mongo 保存失败: {e}")

    def save_one(self, key, value):
        """保存单个 key（减少并发覆盖风险）。"""
        if self.use_local_json:
            self._local_manager().save_one(key, value)
            return
        try:
            self.col.replace_one(
                {"_doc_key": key},
                {"_doc_key": key, "payload": value},
                upsert=True,
            )
        except self.PyMongoError as e:
            st.warning(f"Mongo 保存失败 [{key}]: {e}")

    def save_file_bytes(self, file_bytes, filename="", prefix="upload"):
        if self.use_local_json or self.fs is None:
            return self._local_manager().save_file_bytes(file_bytes, filename=filename, prefix=prefix)
        try:
            safe_name = filename or f"{prefix}_{uuid.uuid4().hex}.jpg"
            file_id = self.fs.put(
                file_bytes,
                filename=safe_name,
                contentType="image/jpeg",
                createdAt=datetime.datetime.utcnow(),
            )
            return f"GRIDFS:{file_id}"
        except self.PyMongoError as e:
            st.warning(f"Mongo 附件保存失败: {e}")
            return ""

    def read_file_bytes(self, ref):
        if self.use_local_json or self.fs is None:
            return self._local_manager().read_file_bytes(ref)
        if not isinstance(ref, str) or not ref.startswith("GRIDFS:"):
            return None
        raw_id = ref.replace("GRIDFS:", "", 1)
        try:
            file_id = self.ObjectId(raw_id)
        except Exception:
            file_id = raw_id
        try:
            return self.fs.get(file_id).read()
        except self.NoFile:
            return None
        except self.PyMongoError as e:
            st.warning(f"Mongo 附件读取失败: {e}")
            return None

    def import_file_bytes(self, ref, file_bytes, filename=""):
        if self.use_local_json or self.fs is None:
            return self._local_manager().import_file_bytes(ref, file_bytes, filename=filename)
        if not isinstance(ref, str) or not ref.startswith("GRIDFS:"):
            return self.save_file_bytes(file_bytes, filename=filename, prefix="restore")
        raw_id = ref.replace("GRIDFS:", "", 1)
        try:
            file_id = self.ObjectId(raw_id)
        except Exception:
            return self.save_file_bytes(file_bytes, filename=filename, prefix="restore")
        try:
            if not self.fs.exists(file_id):
                self.fs.put(
                    file_bytes,
                    _id=file_id,
                    filename=filename or f"{raw_id}.jpg",
                    contentType="image/jpeg",
                    createdAt=datetime.datetime.utcnow(),
                )
            return ref
        except self.PyMongoError as e:
            st.warning(f"Mongo 附件恢复失败: {e}")
            return self.save_file_bytes(file_bytes, filename=filename, prefix="restore")


def _get_mongo_uri():
    try:
        return st.secrets.get("MONGO_URI", "") or os.environ.get("MONGO_URI", "")
    except Exception:
        return os.environ.get("MONGO_URI", "")


DB_MANAGER_CACHE_BUSTER = "20260309_bootfix_1"


@st.cache_resource(show_spinner=False)
def _get_cached_mongo_manager(uri, cache_buster=DB_MANAGER_CACHE_BUSTER):
    return _MongoDBManager(uri)


def _build_db_manager(force_local=False):
    local_path = os.environ.get("INART_DATA_FILE", "tracker_data_web_v20.json")
    if force_local:
        return _LocalJsonDBManager(local_path)

    mongo_uri = _get_mongo_uri()
    if not mongo_uri:
        return _LocalJsonDBManager(local_path)

    manager = _get_cached_mongo_manager(mongo_uri, DB_MANAGER_CACHE_BUSTER)
    required_methods = ["load", "save", "save_one", "save_file_bytes", "read_file_bytes", "import_file_bytes"]
    missing = [name for name in required_methods if not hasattr(manager, name)]
    if missing:
        st.warning(f"数据库管理器缺少必要方法，已重建实例：{', '.join(missing)}")
        manager = _MongoDBManager(mongo_uri)

    if isinstance(manager, _MongoDBManager):
        manager.PyMongoError = getattr(manager, "PyMongoError", Exception)
        manager.NoFile = getattr(manager, "NoFile", FileNotFoundError)
        manager.ObjectId = getattr(manager, "ObjectId", (lambda raw: raw))
        manager.local_json_path = getattr(manager, "local_json_path", local_path)
        manager.use_local_json = bool(getattr(manager, "use_local_json", False))
        manager.client = getattr(manager, "client", None)
        manager.col = getattr(manager, "col", None)
        manager.fs = getattr(manager, "fs", None)
        if not manager.use_local_json and manager.col is None:
            manager.use_local_json = True

    return manager
def _ensure_db_shape(db_obj):
    if not isinstance(db_obj, dict):
        db_obj = {}
    cfg_key = "系统配置"
    cfg = db_obj.get(cfg_key)
    if not isinstance(cfg, dict):
        db_obj[cfg_key] = {}
        cfg = db_obj[cfg_key]
    for k, v in DEFAULT_SYS_CFG.items():
        if k not in cfg:
            cfg[k] = _deep_copy_obj(v)
    for p, d in list(db_obj.items()):
        if p == cfg_key:
            continue
        if not isinstance(d, dict):
            db_obj[p] = {}
            d = db_obj[p]
        d.setdefault("负责人", "")
        d.setdefault("跟单", "")
        d.setdefault("Milestone", "待立项")
        d.setdefault("Target", "TBD")
        d.setdefault("发货区间", "")
        if not isinstance(d.get("部件列表"), dict):
            d["部件列表"] = {}
        if not isinstance(d.get("发货数据"), dict):
            d["发货数据"] = {}
        if not isinstance(d.get("成本数据"), dict):
            d["成本数据"] = {}
    return db_obj




def _load_db_or_fallback():
    global db_manager
    db_manager = _build_db_manager(force_local=False)
    try:
        loaded = db_manager.load()
    except Exception as e:
        st.warning(f"数据库加载失败，回退本地 JSON：{e}")
        db_manager = _build_db_manager(force_local=True)
        try:
            loaded = db_manager.load()
        except Exception as inner:
            st.error(f"本地 JSON 加载也失败：{inner}")
            loaded = DEFAULT_DB.copy()
    return _ensure_db_shape(loaded)


# db_manager 全局实例（用于附件与持久化接口）
db_manager = _build_db_manager(force_local=False)

if "db" not in st.session_state:
    st.session_state.db = _load_db_or_fallback()
else:
    st.session_state.db = _ensure_db_shape(st.session_state.db)

def _ensure_runtime_state_defaults():
    defaults = {
        "parsed_logs": [],
        "pasted_cache": {},
        "config_pasted_cache": {},
        "ai_pasted_cache": {},
        "exclude_imgs": set(),
        "config_consumed_hashes": set(),
        "ai_consumed_hashes": set(),
        "new_proj_mode": False,
        "current_proj_context": None,
        "form_key": 0,
        "todo_handoff_prefill": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_ensure_runtime_state_defaults()


SYS_CFG = st.session_state.db.setdefault("系统配置", {})
STAGES_UNIFIED = list(SYS_CFG.get("标准阶段", DEFAULT_SYS_CFG["标准阶段"]))
STD_COMPONENTS = list(SYS_CFG.get("标准部件", DEFAULT_SYS_CFG["标准部件"]))
MACRO_STAGES = list(SYS_CFG.get("宏观阶段", DEFAULT_SYS_CFG["宏观阶段"]))


def ensure_ordered_value(seq, value, after=None, before=None):
    arr = list(seq or [])
    if value in arr:
        return arr
    if after and after in arr:
        arr.insert(arr.index(after) + 1, value)
        return arr
    if before and before in arr:
        arr.insert(arr.index(before), value)
        return arr
    arr.append(value)
    return arr


STAGES_UNIFIED = ensure_ordered_value(STAGES_UNIFIED, "开模", after="官图")
MACRO_STAGES = ["开模" if str(x) == "模具" else str(x) for x in MACRO_STAGES]
MACRO_STAGES = ensure_ordered_value(list(dict.fromkeys(MACRO_STAGES)), "开模", before="修模")
SYS_CFG.setdefault("排期基线", DEFAULT_SYS_CFG["排期基线"].copy())
if "模具" in SYS_CFG["排期基线"] and "开模" not in SYS_CFG["排期基线"]:
    SYS_CFG["排期基线"]["开模"] = SYS_CFG["排期基线"].get("模具", 28)
SYS_CFG["排期基线"].setdefault("开模", 28)

def infer_review_round_from_text(text):
    s = str(text or "")
    m = re.search(r"第\s*(\d+)\s*轮", s)
    if m:
        return int(m.group(1))
    m2 = re.search(r"\b(\d+)\s*轮\b", s)
    if m2:
        return int(m2.group(1))
    return ""


def normalize_review_round(v):
    try:
        iv = int(v)
        return iv if iv > 0 else ""
    except Exception:
        return ""



# --- End bootstrap fallback ---
# ==========================================
# 核心架构：压缩引擎
# ==========================================
IMG_DIR = "img_assets"  # 仅保留供旧数据兼容读取，新数据默认走引用


def compress_to_image_bytes(img_data, max_size=(1400, 1400), quality=68):
    try:
        if isinstance(img_data, Image.Image):
            img = img_data.copy()
        elif isinstance(img_data, bytes):
            img = Image.open(io.BytesIO(img_data))
        elif hasattr(img_data, "read"):
            if hasattr(img_data, "seek"):
                img_data.seek(0)
            raw = img_data.read()
            if hasattr(img_data, "seek"):
                img_data.seek(0)
            img = Image.open(io.BytesIO(raw))
        else:
            img = Image.open(img_data)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    except Exception:
        return b""


def compress_to_b64(img_data, max_size=(800, 800), quality=50):
    raw = compress_to_image_bytes(img_data, max_size=max_size, quality=quality)
    return base64.b64encode(raw).decode() if raw else ""


def is_attachment_ref(value):
    return isinstance(value, str) and (value.startswith("FILE:") or value.startswith("GRIDFS:"))


def read_binary_ref(ref):
    if not ref:
        return None
    if isinstance(ref, str) and ref.startswith("GRIDFS:") and hasattr(db_manager, "read_file_bytes"):
        return db_manager.read_file_bytes(ref)
    if isinstance(ref, str) and ref.startswith("FILE:"):
        file_path = os.path.join(IMG_DIR, ref.replace("FILE:", "", 1))
        if os.path.exists(file_path):
            with open(file_path, "rb") as f:
                return f.read()
        return None
    if isinstance(ref, str):
        try:
            return base64.b64decode(ref)
        except Exception:
            return None
    return None


def render_image(img_str, **kwargs):
    if not img_str:
        return
    raw = read_binary_ref(img_str)
    if raw is not None:
        st.image(raw, **kwargs)
        return
    if isinstance(img_str, str) and img_str.startswith("FILE:"):
        st.caption("⚠️ 图片为本地文件引用，当前环境未找到对应文件。")
    elif isinstance(img_str, str) and img_str.startswith("GRIDFS:"):
        st.caption("⚠️ 持久附件引用存在，但当前无法读取。")


def save_image_ref_data(img_data, filename="", prefix="upload"):
    raw = compress_to_image_bytes(img_data)
    if not raw:
        return ""
    safe_name = filename or f"{prefix}.jpg"
    if hasattr(db_manager, "save_file_bytes"):
        return db_manager.save_file_bytes(raw, filename=safe_name, prefix=prefix)
    if not os.path.exists(IMG_DIR):
        os.makedirs(IMG_DIR)
    ext = os.path.splitext(safe_name)[1].lower() or ".jpg"
    fname = f"{prefix}_{uuid.uuid4().hex}{ext}"
    with open(os.path.join(IMG_DIR, fname), "wb") as f:
        f.write(raw)
    return f"FILE:{fname}"


def save_uploaded_file_ref(file_obj, prefix="upload"):
    if file_obj is None:
        return ""
    file_name = getattr(file_obj, "name", "") or f"{prefix}.jpg"
    return save_image_ref_data(file_obj, filename=file_name, prefix=prefix)


def get_storage_backend_name():
    return getattr(db_manager, "backend_name", "Unknown")


def get_storage_attachment_mode():
    return getattr(db_manager, "attachment_mode", "legacy")


def derive_attachment_filename(ref):
    if isinstance(ref, str) and ref.startswith("FILE:"):
        return ref.replace("FILE:", "", 1)
    if isinstance(ref, str) and ref.startswith("GRIDFS:"):
        return f"{ref.replace('GRIDFS:', '', 1)}.jpg"
    return f"attachment_{uuid.uuid4().hex}.jpg"


def iter_attachment_refs_in_db(db_obj):
    seen = set()
    for p_name, p_data in db_obj.items():
        if p_name == "系统配置" or not isinstance(p_data, dict):
            continue
        for c_data in p_data.get("部件列表", {}).values():
            for log in c_data.get("日志流", []):
                imgs = log.get("图片", [])
                if isinstance(imgs, str):
                    imgs = [imgs] if imgs else []
                for img in imgs:
                    if is_attachment_ref(img) and img not in seen:
                        seen.add(img)
                        yield img
        drafts = p_data.get("配件清单长图", [])
        if isinstance(drafts, str):
            drafts = [drafts] if drafts else []
        for img in drafts:
            if is_attachment_ref(img) and img not in seen:
                seen.add(img)
                yield img


def attachment_backup_path(ref):
    if isinstance(ref, str) and ref.startswith("FILE:"):
        return f"img_assets/{ref.replace('FILE:', '', 1)}"
    if isinstance(ref, str) and ref.startswith("GRIDFS:"):
        return f"attachments/gridfs/{ref.replace('GRIDFS:', '', 1)}.jpg"
    return f"attachments/misc/{uuid.uuid4().hex}.bin"


def iter_attachment_backup_candidates(ref):
    if isinstance(ref, str) and ref.startswith("FILE:"):
        fname = ref.replace("FILE:", "", 1)
        return [f"img_assets/{fname}", f"attachments/file/{fname}"]
    if isinstance(ref, str) and ref.startswith("GRIDFS:"):
        fid = ref.replace("GRIDFS:", "", 1)
        return [f"attachments/gridfs/{fid}.jpg"]
    return []


def import_attachment_ref(ref, file_bytes, filename=""):
    safe_name = filename or derive_attachment_filename(ref)
    if get_storage_attachment_mode() == "gridfs":
        if isinstance(ref, str) and ref.startswith("GRIDFS:") and hasattr(db_manager, "import_file_bytes"):
            return db_manager.import_file_bytes(ref, file_bytes, filename=safe_name)
        return db_manager.save_file_bytes(file_bytes, filename=safe_name, prefix="restore")
    if hasattr(db_manager, "import_file_bytes"):
        return db_manager.import_file_bytes(ref, file_bytes, filename=safe_name)
    return save_image_ref_data(file_bytes, filename=safe_name, prefix="restore")


def replace_attachment_refs_in_db(db_obj, ref_map):
    if not ref_map:
        return db_obj
    for p_name, p_data in db_obj.items():
        if p_name == "系统配置" or not isinstance(p_data, dict):
            continue
        for c_data in p_data.get("部件列表", {}).values():
            for log in c_data.get("日志流", []):
                imgs = log.get("图片", [])
                if isinstance(imgs, list):
                    log["图片"] = [ref_map.get(img, img) for img in imgs]
                elif isinstance(imgs, str):
                    log["图片"] = ref_map.get(imgs, imgs)
        drafts = p_data.get("配件清单长图", [])
        if isinstance(drafts, list):
            p_data["配件清单长图"] = [ref_map.get(img, img) for img in drafts]
        elif isinstance(drafts, str):
            p_data["配件清单长图"] = ref_map.get(drafts, drafts)
    return db_obj


def restore_attachments_from_zip(db_obj, zf):
    ref_map = {}
    restored = 0
    missing = 0
    for ref in list(iter_attachment_refs_in_db(db_obj)):
        file_bytes = None
        for arcname in iter_attachment_backup_candidates(ref):
            if arcname in zf.namelist():
                file_bytes = zf.read(arcname)
                break
        if file_bytes is None:
            missing += 1
            continue
        new_ref = import_attachment_ref(ref, file_bytes, filename=derive_attachment_filename(ref))
        if new_ref:
            if new_ref != ref:
                ref_map[ref] = new_ref
            restored += 1
        else:
            missing += 1
    if ref_map:
        replace_attachment_refs_in_db(db_obj, ref_map)
    return db_obj, restored, missing


def refresh_project_todo_links(proj_name):
    proj = str(proj_name or "").strip()
    db_obj = st.session_state.db if isinstance(st.session_state.get("db"), dict) else {}
    if not proj or proj == "\u7cfb\u7edf\u914d\u7f6e" or proj not in db_obj:
        return 0

    todo_all = db_obj.get("\u7cfb\u7edf\u914d\u7f6e", {}).get("PM_TODO_LIST", [])
    todo_items = [
        td for td in todo_all
        if str((td or {}).get("\u5173\u8054\u9879\u76ee", "")).strip() == proj and str((td or {}).get("\u4efb\u52a1", "")).strip()
    ]
    if not todo_items:
        return 0

    logs = []
    for comp_name, comp_info in db_obj.get(proj, {}).get("\u90e8\u4ef6\u5217\u8868", {}).items():
        for lg in (comp_info or {}).get("\u65e5\u5fd7\u6d41", []):
            if is_hidden_system_log(lg):
                continue
            evt = str((lg or {}).get("\u4e8b\u4ef6", "")).strip()
            if not evt:
                continue
            d_txt = str((lg or {}).get("\u65e5\u671f", "")).strip()
            try:
                d_obj = datetime.datetime.strptime(d_txt, "%Y-%m-%d").date()
            except Exception:
                d_obj = datetime.date.min
            logs.append({
                "dt": d_obj,
                "date": d_txt,
                "component": str(comp_name),
                "stage": str((lg or {}).get("\u5de5\u5e8f", "")).strip(),
                "event": evt,
                "event_norm": norm_text(evt),
            })

    if not logs:
        return 0

    logs.sort(key=lambda x: (x.get("dt") or datetime.date.min, x.get("date", ""), x.get("component", "")), reverse=True)

    def _safe_date(s):
        try:
            return datetime.datetime.strptime(str(s or "").strip(), "%Y-%m-%d").date()
        except Exception:
            return None

    write_ts = datetime.datetime.now().isoformat(timespec="seconds")
    updated = 0

    for td in todo_items:
        task = str((td or {}).get("\u4efb\u52a1", "")).strip()
        task_norm = norm_text(task)
        if len(task_norm) < 2:
            continue

        short_task = task[:8].strip()
        short_norm = norm_text(short_task)
        hit = None
        for item in logs:
            evt = item["event"]
            evt_norm = item["event_norm"]
            matched = False
            if "[\u5173\u8054To do]" in evt and (task in evt or (task_norm and task_norm in evt_norm)):
                matched = True
            elif len(task_norm) >= 4 and task_norm in evt_norm:
                matched = True
            elif short_norm and len(short_norm) >= 4 and short_norm in evt_norm:
                matched = True
            if matched:
                hit = item
                break

        if not hit:
            continue

        cur_dt = _safe_date(td.get("\u6700\u8fd1\u8054\u52a8\u65e5\u671f", ""))
        hit_dt = hit.get("dt") if hit.get("dt") != datetime.date.min else None
        if cur_dt and hit_dt and hit_dt < cur_dt:
            continue

        desired = {
            "\u6700\u8fd1\u8054\u52a8\u6a21\u5757": "\u65e5\u5fd7\u8054\u52a8\u56de\u586b",
            "\u6700\u8fd1\u8054\u52a8\u65e5\u671f": hit.get("date", ""),
            "\u6700\u8fd1\u8054\u52a8\u9879\u76ee": proj,
            "\u6700\u8fd1\u8054\u52a8\u90e8\u4ef6": hit.get("component", ""),
            "\u6700\u8fd1\u8054\u52a8\u9636\u6bb5": hit.get("stage", ""),
            "\u6700\u8fd1\u8054\u52a8\u5199\u5165\u65f6\u95f4": write_ts,
        }
        changed = False
        for k, v in desired.items():
            if str(td.get(k, "")) != str(v):
                td[k] = v
                changed = True
        if changed:
            updated += 1

    return updated


def auto_sync_milestone(proj_name):
    proj_data = st.session_state.db.get(proj_name)
    if not isinstance(proj_data, dict):
        return
    comps = proj_data.get("部件列表", {})
    if not isinstance(comps, dict):
        return

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
        stage_idx = next((i for i, std_stage in enumerate(STAGES_UNIFIED) if stage == std_stage or stage in std_stage or std_stage in stage), -1)
        if stage_idx > max_idx:
            max_idx = stage_idx
            max_stage = STAGES_UNIFIED[stage_idx]

    if max_idx >= 0 and max_stage:
        global_key = next((k for k in comps.keys() if "全局" in str(k)), "全局进度")
        if global_key not in comps or not isinstance(comps.get(global_key), dict):
            comps[global_key] = {"主流程": STAGES_UNIFIED[0], "日志流": []}
        curr_global_stage = str(comps[global_key].get("主流程", "")).strip()
        curr_idx = next((i for i, std_stage in enumerate(STAGES_UNIFIED) if curr_global_stage == std_stage or curr_global_stage in std_stage or std_stage in curr_global_stage), -1)
        if curr_idx < max_idx and not is_pause_stage(curr_global_stage):
            comps[global_key]["主流程"] = max_stage

    stages = [str(info.get("主流程", "")).strip() for _, info in non_global_items if str(info.get("主流程", "")).strip()]
    if not stages:
        global_key = next((k for k in comps.keys() if "全局" in str(k)), "全局进度")
        global_stage = str(comps.get(global_key, {}).get("主流程", "")).strip()
        if global_stage:
            stages = [global_stage]

    cur_ms = str(proj_data.get("Milestone", "")).strip()
    if stages and all(stage == "✅ 已完成(结束)" for stage in stages):
        proj_data["Milestone"] = "项目结束撒花🎉"
    elif any(stage in ["工厂复样(含胶件/上色等)", "大货"] for stage in stages):
        if cur_ms not in ["生产结束", "项目结束撒花🎉", "暂停研发"]:
            proj_data["Milestone"] = "生产中"
    elif any(stage == "开模" for stage in stages):
        if cur_ms not in ["生产结束", "项目结束撒花🎉", "暂停研发", "生产中"]:
            proj_data["Milestone"] = "下模中"
    elif any(stage in ["建模(含打印/签样)", "涂装", "设计", "工程拆件", "手板/结构板", "官图"] for stage in stages):
        if cur_ms in ["", "待立项"]:
            proj_data["Milestone"] = "研发中"


def sync_save_db(changed_proj=None):
    """
    changed_proj: save one project key when provided.
    otherwise save all projects.
    """
    if changed_proj and changed_proj in st.session_state.db and changed_proj != "\u7cfb\u7edf\u914d\u7f6e":
        auto_sync_milestone(changed_proj)
        refresh_project_todo_links(changed_proj)
    else:
        for p in st.session_state.db:
            if p != "\u7cfb\u7edf\u914d\u7f6e":
                auto_sync_milestone(p)
                refresh_project_todo_links(p)
    if changed_proj:
        db_manager.save_one(changed_proj, st.session_state.db[changed_proj])
        db_manager.save_one("\u7cfb\u7edf\u914d\u7f6e", st.session_state.db["\u7cfb\u7edf\u914d\u7f6e"])
    else:
        db_manager.save(st.session_state.db)
def get_macro_phase(detail_stage):
    s = str(detail_stage).strip()
    if "完成" in s or "结束" in s or "撒花" in s: return "结束"
    if "暂停" in s or "搁置" in s: return "暂停"
    if any(x in s for x in ["大货", "复样", "量产", "开定"]): return "生产"
    if any(x in s for x in ["拆件", "手板", "结构", "官图"]): return "工程"
    if "模具" in s: return "模具"
    if "设计" in s or "官图" in s: return "设计"
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
    """返回空字符串表示合法，否则返回 warning 文案。"""
    rt = str(review_type).strip()
    if not rt or rt == "(无)":
        return ""
    idx = get_stage_index(stage_name, stages)
    if idx < 0:
        return f"提审[{rt}]无法校验：阶段[{stage_name}]不在标准阶段中"
    design_idx = get_stage_index("设计", stages)
    eng_idx = get_stage_index("工程拆件", stages)
    struct_idx = get_stage_index("手板/结构板", stages)
    if rt == "2D提审":
        if idx < get_stage_index("立项", stages):
            return "2D提审应在立项后再使用"
    elif rt == "3D提审":
        min_idx = min([i for i in [design_idx, eng_idx] if i >= 0], default=-1)
        if min_idx >= 0 and idx < min_idx:
            return "3D提审建议在设计或工程阶段使用"
    elif rt == "实物提审":
        if struct_idx >= 0 and idx < struct_idx:
            return "实物提审建议在手板/结构板阶段及之后使用"
    elif rt == "包装提审":
        if "包装" not in str(comp_name):
            return "包装提审建议用于【包装】部件"
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
    s = str(txt).lower()
    if "2d" in s and "提审" in s:
        return "2D提审"
    if "3d" in s and "提审" in s:
        return "3D提审"
    if any(k in s for k in ["实物提审", "手板提审", "结构件提审"]):
        return "实物提审"
    if "包装" in s and "提审" in s:
        return "包装提审"
    return "(无)"

def infer_review_result_from_text(txt):
    s = str(txt).lower()
    if any(k in s for k in ["通过", "ok", "pass"]):
        return "通过"
    if any(k in s for k in ["打回", "驳回", "退回"]):
        return "打回"
    if "提审" in s:
        return "待反馈"
    return "(无)"

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
def todo_alert_text(td, today=None):
    today = today or datetime.date.today()
    if bool((td or {}).get("完成")):
        return "✅ 已完成"
    due = todo_due_date(td)
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

def todo_sort_key(td, today=None):
    today = today or datetime.date.today()
    completed = bool((td or {}).get("完成"))
    due = todo_due_date(td)
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
    scope = todo_scope_of(td)
    if pm_view == "所有人":
        return True
    return scope == pm_view


def todo_visible_for_sidebar(td, pm_view):
    scope = todo_scope_of(td)
    if pm_view == "所有人":
        return scope in ["", "未分配", "所有人"]
    return scope == pm_view


def build_todo_scope_options(current_pm):
    scope_vals = []
    for proj_name, proj_data in db.items():
        if proj_name == "系统配置" or not isinstance(proj_data, dict):
            continue
        owner = str(proj_data.get("负责人", "")).strip()
        if owner and owner != "所有人":
            scope_vals.append(owner)

    todo_all = db.get("系统配置", {}).get("PM_TODO_LIST", [])
    for td in todo_all:
        scope = str((td or {}).get("所属视角", "")).strip()
        creator = str((td or {}).get("创建者视角", "")).strip()
        if scope and scope != "所有人":
            scope_vals.append(scope)
        if creator and creator != "所有人":
            scope_vals.append(creator)

    if current_pm and current_pm != "所有人":
        scope_vals.insert(0, current_pm)
    scope_vals.append("未分配")
    return list(dict.fromkeys([x for x in scope_vals if x and x != "所有人"]))


def parse_target_year_month(target_str):
    s = str(target_str or "").strip()
    if not s or s.upper() == "TBD":
        return None

    m = re.match(r'^(\d{4})[/-](\d{1,2})$', s)
    if m:
        return (int(m.group(1)), int(m.group(2)))

    m_short = re.match(r'^(\d{2})\.(\d{1,2})$', s)
    if m_short:
        return (2000 + int(m_short.group(1)), int(m_short.group(2)))

    m_q = re.match(r'^(\d{4})\s*Q([1-4])$', s.upper())
    if m_q:
        y = int(m_q.group(1)); q = int(m_q.group(2))
        return (y, q * 3)

    m_q_short = re.match(r'^(\d{2})\s*Q([1-4])$', s.upper())
    if m_q_short:
        y = 2000 + int(m_q_short.group(1)); q = int(m_q_short.group(2))
        return (y, q * 3)

    m_cn = re.match(r'^(\d{2,4})年\s*(\d{1,2})月$', s)
    if m_cn:
        y_raw = int(m_cn.group(1)); mm = int(m_cn.group(2))
        year = y_raw if y_raw >= 1000 else (2000 + y_raw)
        return (year, mm)

    if len(s) >= 10:
        try:
            d = datetime.datetime.strptime(s[:10], "%Y-%m-%d").date()
            return (d.year, d.month)
        except:
            return None

    return None
def parse_period_marker_date(raw_text, end_of_period=False):
    s = str(raw_text or "").strip()
    if not s or s.upper() in ["TBD", "-", "—", "NONE", "无"]:
        return None
    if len(s) >= 10:
        try:
            return datetime.datetime.strptime(s[:10], "%Y-%m-%d").date()
        except Exception:
            pass

    q_match = re.match(r'^(20\d{2}|\d{2})\s*Q([1-4])$', s.upper())
    if q_match:
        y_raw = int(q_match.group(1))
        year = y_raw if y_raw >= 1000 else (2000 + y_raw)
        quarter = int(q_match.group(2))
        month = quarter * 3 if end_of_period else ((quarter - 1) * 3 + 1)
        day = month_last_day(year, month) if end_of_period else min(15, month_last_day(year, month))
        return datetime.date(year, month, day)

    ym_match = re.match(r'^(\d{4})[/-](\d{1,2})$', s)
    if ym_match:
        year = int(ym_match.group(1))
        month = int(ym_match.group(2))
        day = month_last_day(year, month) if end_of_period else min(15, month_last_day(year, month))
        return datetime.date(year, month, day)

    short_match = re.match(r'^(\d{2})\.(\d{1,2})$', s)
    if short_match:
        year = 2000 + int(short_match.group(1))
        month = int(short_match.group(2))
        day = month_last_day(year, month) if end_of_period else min(15, month_last_day(year, month))
        return datetime.date(year, month, day)

    cn_match = re.match(r'^(\d{2,4})年\s*(\d{1,2})月$', s)
    if cn_match:
        y_raw = int(cn_match.group(1))
        year = y_raw if y_raw >= 1000 else (2000 + y_raw)
        month = int(cn_match.group(2))
        day = month_last_day(year, month) if end_of_period else min(15, month_last_day(year, month))
        return datetime.date(year, month, day)
    return None


def extract_schedule_year_month(text, ref_year=None):
    s = str(text or "")
    if not s:
        return None

    m = re.search(r'\b(\d{4})[./-](\d{1,2})\b', s)
    if m:
        return (int(m.group(1)), int(m.group(2)))

    m_q = re.search(r'\b(20\d{2}|\d{2})\s*Q([1-4])\b', s.upper())
    if m_q:
        y_raw = int(m_q.group(1)); q = int(m_q.group(2))
        year = y_raw if y_raw >= 1000 else (2000 + y_raw)
        return (year, q * 3)

    m_short = re.search(r'\b(\d{2})\.(\d{1,2})\b', s)
    if m_short:
        yy = int(m_short.group(1)); mm = int(m_short.group(2))
        return (2000 + yy, mm)

    m_cn = re.search(r'(\d{2,4})年\s*(\d{1,2})月', s)
    if m_cn:
        y_raw = int(m_cn.group(1)); mm = int(m_cn.group(2))
        year = y_raw if y_raw >= 1000 else (2000 + y_raw)
        return (year, mm)

    m_month_only = re.search(r'(?<!\d)(\d{1,2})月', s)
    if m_month_only and ref_year:
        mm = int(m_month_only.group(1))
        if 1 <= mm <= 12:
            return (int(ref_year), mm)

    if ref_year:
        m_q_only = re.search(r'\bQ([1-4])\b', s.upper())
        if m_q_only:
            return (int(ref_year), int(m_q_only.group(1)) * 3)

    return None


def fmt_ym(ym):
    if not ym:
        return "-"
    return f"{int(ym[0])}/{int(ym[1]):02d}"


def infer_todo_target_hint(td, valid_projs):
    td_obj = td or {}
    title = str(td_obj.get("任务", "")).strip()
    cpddl = todo_cpddl_text(td_obj)
    txt = f"{title} {cpddl}".strip()
    txt_norm = norm_text(txt)
    ref_proj = str(td_obj.get("关联项目", "")).strip()
    alias_map = db.get("系统配置", {}).get("项目别名", {})

    proj = ref_proj if ref_proj else ""
    if not proj:
        for p in valid_projs:
            p_full = str(p)
            p_short = re.sub(r'^(1/6|1/4|1/12|1/3|1/1)\s*', "", p_full).strip()
            p_full_n = norm_text(p_full)
            p_short_n = norm_text(p_short)
            hit_direct = (p_full and p_full in txt) or (p_short and p_short in txt)
            hit_norm = (p_full_n and p_full_n in txt_norm) or (p_short_n and p_short_n in txt_norm)
            if hit_direct or hit_norm:
                proj = p
                break
            for a in alias_map.keys():
                if resolve_alias_project(a, alias_map) != p:
                    continue
                a_norm = norm_text(a)
                if a_norm and a_norm in txt_norm:
                    proj = p
                    break
            if proj:
                break

    if not proj:
        return "未识别项目"

    tgt = str(db.get(proj, {}).get("Target", "")).strip()
    tgt_ym = parse_target_year_month(tgt)
    sch_ym = extract_schedule_year_month(txt, ref_year=(tgt_ym[0] if tgt_ym else None))

    if not tgt_ym:
        return f"[{proj}] 开定:TBD"
    if not sch_ym:
        return f"[{proj}] 开定:{fmt_ym(tgt_ym)}"
    if sch_ym == tgt_ym:
        return f"✅ 档期{fmt_ym(sch_ym)}=开定{fmt_ym(tgt_ym)}"
    return f"⚠️ 档期{fmt_ym(sch_ym)} ≠ 开定{fmt_ym(tgt_ym)}"

def todo_link_status_text(td):
    td_obj = td or {}
    mod = str(td_obj.get("最近联动模块", "")).strip()
    dt = str(td_obj.get("最近联动日期", "")).strip()
    comp = str(td_obj.get("最近联动部件", "")).strip()
    stage = str(td_obj.get("最近联动阶段", "")).strip()
    if not mod and not dt:
        return "未落地"
    parts = []
    if dt:
        parts.append(dt)
    if mod:
        parts.append(mod)
    if comp:
        parts.append(comp)
    if stage:
        parts.append(stage)
    return " / ".join(parts)


def parse_role_person_label(raw_label):
    token = str(raw_label or "").strip()
    if not token:
        return "综合", ""
    if "-" in token:
        role, person = token.split("-", 1)
    elif ":" in token:
        role, person = token.split(":", 1)
    else:
        role, person = "综合", token
    role = str(role or "").strip() or "综合"
    person = str(person or "").strip()
    return role, person


def _append_role_person_to_maps(role, person, labels, name_map):
    role_txt = str(role or "").strip() or "综合"
    person_txt = str(person or "").strip()
    if not person_txt:
        return
    label = f"{role_txt}-{person_txt}" if role_txt != "综合" else person_txt
    if label not in labels:
        labels.append(label)
    key = norm_text(person_txt)
    info = name_map.setdefault(key, {"display": person_txt, "labels": []})
    if label not in info["labels"]:
        info["labels"].append(label)


def collect_role_person_options():
    labels = []
    name_map = {}
    for proj_name, proj_data in db.items():
        if proj_name == "系统配置" or not isinstance(proj_data, dict):
            continue
        for comp_data in proj_data.get("部件列表", {}).values():
            owner_str = str(comp_data.get("负责人", "")).strip()
            for pair in re.split(r"[,\uFF0C|/]+", owner_str):
                pair = str(pair or "").strip()
                if (not pair) or pair == "未分配":
                    continue
                role, person = parse_role_person_label(pair)
                if person == "未分配":
                    continue
                _append_role_person_to_maps(role, person, labels, name_map)

    extra_people = db.get("系统配置", {}).get("TODO_EXTRA_ROLE_PEOPLE", [])
    if isinstance(extra_people, str):
        extra_people = split_people_text(extra_people)
    if isinstance(extra_people, list):
        for pair in extra_people:
            role, person = parse_role_person_label(pair)
            _append_role_person_to_maps(role, person, labels, name_map)

    labels = sorted(labels, key=lambda x: (x.split("-", 1)[0] if "-" in x else "综合", x.split("-", 1)[-1]))
    return labels, name_map


def register_extra_role_people(raw_people_tokens):
    cfg = db.setdefault("系统配置", {})
    store = cfg.setdefault("TODO_EXTRA_ROLE_PEOPLE", [])
    if isinstance(store, str):
        store = split_people_text(store)
        cfg["TODO_EXTRA_ROLE_PEOPLE"] = store
    if not isinstance(store, list):
        store = []
        cfg["TODO_EXTRA_ROLE_PEOPLE"] = store

    existing = {norm_text(x) for x in store if str(x).strip()}
    added = []
    for token in raw_people_tokens or []:
        role, person = parse_role_person_label(token)
        if (not person) or person == "未分配":
            continue
        label = f"{role}-{person}" if role and role != "综合" else person
        label_key = norm_text(label)
        if label_key in existing:
            continue
        store.append(label)
        existing.add(label_key)
        added.append(label)
    return added


def create_project_shell_if_missing(project_name, owner_name=""):
    proj = str(project_name or "").strip()
    if not proj or proj == "系统配置":
        return False
    if proj in db and isinstance(db.get(proj), dict):
        return False
    owner = str(owner_name or "").strip() or "Mo"
    db[proj] = {
        "负责人": owner,
        "跟单": "",
        "Milestone": "待立项",
        "Target": "TBD",
        "发货区间": "",
        "部件列表": {},
        "发货数据": {},
        "成本数据": {},
    }
    return True


def split_people_text(raw_text):
    return [x.strip() for x in re.split(r'[,，;；、/|\n]+', str(raw_text or "")) if x.strip()]


def normalize_people_text(raw_text):
    uniq = []
    for token in split_people_text(raw_text):
        if token not in uniq:
            uniq.append(token)
    return ", ".join(uniq)


def infer_todo_people_bundle(td):
    td_obj = td or {}
    labels, name_map = collect_role_person_options()
    label_map = {norm_text(label): label for label in labels}
    matched, ambiguous, unknown = [], [], []

    def add_match(label):
        if label and label not in matched:
            matched.append(label)

    def add_amb(msg):
        if msg and msg not in ambiguous:
            ambiguous.append(msg)

    def add_unknown(token):
        if token and token not in unknown:
            unknown.append(token)

    for token in split_people_text(td_obj.get("关联人员", "")):
        token_norm = norm_text(token)
        if token_norm in label_map:
            add_match(label_map[token_norm])
            continue
        info = name_map.get(token_norm)
        if info:
            if len(info["labels"]) == 1:
                add_match(info["labels"][0])
            else:
                add_amb(f"{info['display']} -> {' / '.join(info['labels'][:3])}")
        else:
            add_unknown(token)

    free_text = f"{str(td_obj.get('任务', '')).strip()} {todo_cpddl_text(td_obj)}".strip()
    txt_norm = norm_text(free_text)
    for label in labels:
        label_norm = norm_text(label)
        if label_norm and label_norm in txt_norm:
            add_match(label)
    for name_key, info in name_map.items():
        display = info["display"]
        if not ((display and display in free_text) or (name_key and name_key in txt_norm)):
            continue
        if any(label in matched for label in info["labels"]):
            continue
        if len(info["labels"]) == 1:
            add_match(info["labels"][0])
        else:
            add_amb(f"{display} -> {' / '.join(info['labels'][:3])}")

    for pat in [
        r'(?:-|给|找|催|问|和|跟|让|owner[:：\-]?)\s*([A-Za-z][A-Za-z0-9_\-]{1,20}|[\u4e00-\u9fa5]{2,4})',
        r'([A-Za-z][A-Za-z0-9_\-]{1,20}|[\u4e00-\u9fa5]{2,4})\s*(?:确认|跟进|处理|回复|反馈)'
    ]:
        for m in re.finditer(pat, free_text):
            candidate = str(m.group(1)).strip()
            candidate_norm = norm_text(candidate)
            if candidate_norm in label_map or candidate_norm in name_map:
                continue
            if any(candidate in label for label in matched):
                continue
            add_unknown(candidate)

    return {"labels": matched, "ambiguous": ambiguous, "unknown": unknown}


def format_todo_people_hint(td):
    bundle = infer_todo_people_bundle(td)
    notes = []
    if bundle["labels"]:
        notes.append("已识别：" + " / ".join(bundle["labels"][:4]))
    if bundle["ambiguous"]:
        notes.append("同名待确认（请填写完整角色-姓名）：" + "；".join(bundle["ambiguous"][:2]))
    if bundle["unknown"]:
        notes.append("未在库中找到（请先在进度明细里补充该人员信息）：" + " / ".join(bundle["unknown"][:2]))
    return " | ".join(notes) if notes else "未识别到人员"


def collect_todo_loading_pairs(pm_view="所有人"):
    cfg = db.get("系统配置", {})
    todo_all = cfg.get("PM_TODO_LIST", [])
    pairs = set()
    for td in todo_all:
        if bool((td or {}).get("完成")):
            continue
        if not todo_visible_for_view(td, pm_view):
            continue
        proj = str((td or {}).get("关联项目", "")).strip()
        if not proj or proj == "系统配置" or proj not in db:
            continue

        people_tokens = split_people_text((td or {}).get("关联人员", ""))
        if not people_tokens:
            people_tokens = infer_todo_people_bundle(td).get("labels", [])

        for token in people_tokens:
            role, person = parse_role_person_label(token)
            if (not person) or person == "未分配":
                continue
            pairs.add((proj, person, role or "综合"))
    return pairs


def infer_todo_handoff_prefill(td, proj_name):
    td_obj = td or {}
    proj = str(proj_name or "").strip()
    txt = f"{str(td_obj.get('任务', '')).strip()} {todo_cpddl_text(td_obj)}".strip()
    txt_norm = norm_text(txt)
    comp_hits = []
    proj_comps = list(db.get(proj, {}).get("部件列表", {}).keys())
    for comp in proj_comps:
        variants = {str(comp).strip()}
        if " - " in str(comp):
            variants.add(str(comp).split(" - ", 1)[1].strip())
        if "全局" in str(comp):
            variants.add("全局")
            variants.add("整体")
        for std_comp in STD_COMPONENTS:
            if str(comp).startswith(std_comp):
                variants.add(std_comp)
                variants.add(re.sub(r"\(.*?\)", "", std_comp).strip())
        if any(v and ((v in txt) or (norm_text(v) in txt_norm)) for v in variants):
            if "全局" in str(comp):
                if "🌐 全局进度 (Overall)" not in comp_hits:
                    comp_hits.append("🌐 全局进度 (Overall)")
            elif comp not in comp_hits:
                comp_hits.append(comp)

    if not comp_hits:
        for std_comp in STD_COMPONENTS:
            base = re.sub(r"\(.*?\)", "", std_comp).strip()
            if (base and base in txt) or (norm_text(std_comp) in txt_norm):
                comp_hits.append(std_comp)
                break
    def _pick_component_from_hint(target_keyword):
        target_norm = norm_text(target_keyword)
        for comp_name in proj_comps:
            comp_txt = str(comp_name).strip()
            if not comp_txt:
                continue
            if target_keyword in comp_txt or (target_norm and target_norm in norm_text(comp_txt)):
                if "全局" in comp_txt:
                    return "🌐 全局进度 (Overall)"
                return comp_txt
        for std_comp in STD_COMPONENTS:
            std_txt = str(std_comp).strip()
            if target_keyword in std_txt or (target_norm and target_norm in norm_text(std_txt)):
                return std_txt
        return ""

    if not comp_hits:
        todo_comp_hint = [
            ("头发", "头雕"), ("发型", "头雕"), ("发丝", "头雕"), ("发际", "头雕"), ("刘海", "头雕"),
            ("头", "头雕"), ("脸", "头雕"), ("眼", "头雕"),
            ("手", "手型"), ("衣", "服装"), ("服", "服装"),
            ("包", "包装"), ("地台", "地台"),
        ]
        for kw, target in todo_comp_hint:
            if kw in txt:
                picked_comp = _pick_component_from_hint(target)
                if picked_comp and picked_comp not in comp_hits:
                    comp_hits.append(picked_comp)
                break

    if not comp_hits and any(k in txt for k in ["全局", "整体", "项目", "大盘"]):
        comp_hits = ["🌐 全局进度 (Overall)"]

    stage_guess = ""
    stage_kw = [
        ("开定", "立项"), ("立项", "立项"), ("资料", "建模(含打印/签样)"),
        ("建模", "建模(含打印/签样)"), ("打印", "建模(含打印/签样)"), ("涂装", "涂装"),
        ("设计", "设计"), ("官图", "官图"), ("拆件", "工程拆件"), ("工程", "工程拆件"),
        ("结构件", "工程拆件"), ("手板", "手板/结构板"), ("结构板", "手板/结构板"),
        ("开模", "开模"), ("模具", "开模"), ("试模", "开模"),
        ("复样", "工厂复样(含胶件/上色等)"), ("上色", "工厂复样(含胶件/上色等)"),
        ("大货", "大货"), ("暂停", "⏸️ 暂停/搁置"), ("完成", "✅ 已完成(结束)"), ("结束", "✅ 已完成(结束)")
    ]
    for kw, stage_name in stage_kw:
        if kw in txt:
            stage_guess = stage_name
            break
    if not stage_guess:
        for stg in STAGES_UNIFIED:
            stg_norm = norm_text(stg)
            if stg_norm and stg_norm in txt_norm:
                stage_guess = stg
                break

    people_bundle = infer_todo_people_bundle(td_obj)
    role_map = {}
    for label in people_bundle["labels"]:
        if '-' not in label:
            continue
        role, person = label.split('-', 1)
        role = role.strip()
        person = person.strip()
        if role in ["建模", "设计", "工程", "监修", "打印", "涂装"] and person:
            role_map[role] = person

    log_txt = str(td_obj.get("任务", "")).strip()
    cpddl = todo_cpddl_text(td_obj)
    if cpddl:
        log_txt = f"{log_txt} | {cpddl}" if log_txt else cpddl
    if not log_txt:
        log_txt = "To do 联动补充"

    return {
        "项目": proj,
        "todo_ids": [str(td_obj.get("_id", "")).strip()] if str(td_obj.get("_id", "")).strip() else [],
        "部件": comp_hits or ["🌐 全局进度 (Overall)"],
        "阶段": stage_guess,
        "内容": log_txt,
        "角色映射": role_map,
    }



def append_todo_completion_history(td, action_date=None):
    td_obj = td or {}
    proj_name = str(td_obj.get("关联项目", "")).strip()
    if not proj_name or proj_name not in db or proj_name == "系统配置":
        return False
    proj_data = db.get(proj_name, {})
    comps = proj_data.setdefault("部件列表", {})
    global_key = next((k for k in comps.keys() if "全局" in str(k)), "全局进度")
    if global_key not in comps or not isinstance(comps.get(global_key), dict):
        comps[global_key] = {"主流程": STAGES_UNIFIED[0], "日志流": []}
    stage_name = str(comps[global_key].get("主流程", "")).strip() or STAGES_UNIFIED[0]
    action_date = action_date or datetime.date.today()
    title = str(td_obj.get("任务", "")).strip()
    if not title:
        return False
    cpddl = todo_cpddl_text(td_obj)
    people = str(td_obj.get("关联人员", "")).strip()
    event_bits = [f"[To do完成] {title}"]
    if cpddl:
        event_bits.append(f"CP/DDL:{cpddl}")
    if people:
        event_bits.append(f"人员:{people}")
    event_text = " | ".join(event_bits)
    logs = comps[global_key].setdefault("日志流", [])
    if any(str(log.get("日期", "")).strip() == str(action_date) and str(log.get("事件", "")).strip() == event_text for log in logs):
        return False
    logs.append({
        "日期": str(action_date),
        "流转": "To do",
        "工序": stage_name,
        "事件": event_text,
    })
    return True

def add_months(base_date, delta_months):
    year = base_date.year + (base_date.month - 1 + delta_months) // 12
    month = (base_date.month - 1 + delta_months) % 12 + 1
    day = min(base_date.day, month_last_day(year, month))
    return datetime.date(year, month, day)


def _parse_log_date(log_obj):
    try:
        return datetime.datetime.strptime(str((log_obj or {}).get("日期", "")), "%Y-%m-%d").date()
    except Exception:
        return None


def infer_current_macro_stages(proj_data):
    proj_obj = proj_data or {}
    current = set()
    for comp_info in proj_obj.get("部件列表", {}).values():
        macro = get_macro_phase(comp_info.get("主流程", ""))
        if macro and macro not in ["立项", "暂停", "结束"]:
            current.add(macro)
    milestone = str(proj_obj.get("Milestone", "")).strip()
    if milestone == "暂停研发":
        current.add("暂停")
    elif milestone == "下模中":
        current.add("开模")
    elif milestone == "生产中":
        current.add("生产")
    elif milestone in ["研发中", "待开定", "已开定", "待立项"] and not current:
        current.add("建模")
    return current


def build_project_stage_segments(proj_label, proj_data):
    comps = (proj_data or {}).get("部件列表", {})
    stage_records = {k: [] for k in ["立项", "建模", "设计", "工程", "开模", "修模", "生产", "暂停", "结束"]}
    all_records = []
    today = datetime.date.today()

    for comp_name, comp_info in comps.items():
        for log in comp_info.get("日志流", []):
            if is_hidden_system_log(log):
                continue
            dt_obj = _parse_log_date(log)
            if not dt_obj:
                continue
            raw_stage = str(log.get("工序", comp_info.get("主流程", ""))).strip()
            macro = get_macro_phase(raw_stage)
            if not macro:
                continue
            evt = str(log.get("事件", "")).strip()
            entry = {
                "date": dt_obj,
                "stage": macro,
                "component": comp_name,
                "event": evt,
                "review_type": str(log.get("提审类型", "")).strip(),
                "review_result": str(log.get("提审结果", "")).strip(),
                "review_round": str(log.get("提审轮次", "")).strip(),
                "raw_stage": raw_stage,
            }
            stage_records.setdefault(macro, []).append(entry)
            all_records.append(entry)

    if stage_records.get("立项"):
        launch_dates = sorted({x["date"] for x in stage_records["立项"]})
        if launch_dates:
            true_launch_day = launch_dates[0]
            leftover_launch_records = [x for x in stage_records["立项"] if x["date"] > true_launch_day]
            if leftover_launch_records:
                stage_records["立项"] = [x for x in stage_records["立项"] if x["date"] == true_launch_day]
                for entry in leftover_launch_records:
                    reclassified = dict(
                        entry,
                        stage="建模",
                        event=f"[立项补充资料→建模口径] {entry['event']}",
                        raw_stage="建模(含打印/签样)",
                    )
                    stage_records.setdefault("建模", []).append(reclassified)
                    for record in all_records:
                        if (
                            record["date"] == entry["date"]
                            and record["component"] == entry["component"]
                            and record["event"] == entry["event"]
                            and record["stage"] == "立项"
                        ):
                            record["stage"] = "建模"
                            break

    if not all_records:
        return []

    all_records = sorted(all_records, key=lambda x: (x["date"], x["stage"], x["component"], x["event"]))
    first_date = all_records[0]["date"]
    latest_date = all_records[-1]["date"]
    unique_dates = sorted({x["date"] for x in all_records})
    second_date = next((d for d in unique_dates if d > first_date), None)
    current_macros = infer_current_macro_stages(proj_data)
    milestone = str((proj_data or {}).get("Milestone", "")).strip()

    if not stage_records["立项"]:
        stage_records["立项"].append({
            "date": first_date,
            "stage": "立项",
            "component": "全局进度",
            "event": "项目首次出现，立项按 1 天展示",
            "review_type": "",
            "review_result": "",
            "review_round": "",
            "raw_stage": "立项",
            "synthetic": True,
        })

    if not stage_records["建模"] and milestone not in ["暂停研发", "生产结束", "项目结束撒花🎉", "✅ 已完成(结束)"]:
        if len(unique_dates) > 1 or milestone in ["研发中", "待开定", "已开定", "下模中", "生产中"]:
            build_seed = second_date or (first_date + datetime.timedelta(days=1))
            stage_records["建模"].append({
                "date": build_seed,
                "stage": "建模",
                "component": "全局进度",
                "event": "立项后默认转入建模（甘特自动归类）",
                "review_type": "",
                "review_result": "",
                "review_round": "",
                "raw_stage": "建模(含打印/签样)",
                "synthetic": True,
            })

    if "开模" in current_macros and not stage_records["开模"]:
        mold_seed = max(
            [max([x["date"] for x in stage_records[s]]) for s in ["建模", "设计", "工程"] if stage_records[s]],
            default=second_date or (first_date + datetime.timedelta(days=1))
        )
        stage_records["开模"].append({
            "date": mold_seed,
            "stage": "开模",
            "component": "全局进度",
            "event": "里程碑已进入开模/下模，甘特自动补足开模阶段",
            "review_type": "",
            "review_result": "",
            "review_round": "",
            "raw_stage": "开模",
            "synthetic": True,
        })

    if "生产" in current_macros and not stage_records["生产"]:
        prod_seed = max(
            [max([x["date"] for x in stage_records[s]]) for s in ["开模", "修模", "工程", "设计", "建模"] if stage_records[s]],
            default=latest_date
        )
        stage_records["生产"].append({
            "date": prod_seed,
            "stage": "生产",
            "component": "全局进度",
            "event": "里程碑已进入生产，甘特自动补足生产阶段",
            "review_type": "",
            "review_result": "",
            "review_round": "",
            "raw_stage": "生产",
            "synthetic": True,
        })

    mold_start = min([x["date"] for x in stage_records["开模"]], default=None)
    segments = []

    def _detail_lines(records, prefix_note=""):
        lines = []
        if prefix_note:
            lines.append(prefix_note)
        for rec in sorted(records, key=lambda x: (x["date"], x["component"], x["event"])):
            rv_txt = ""
            if rec.get("review_type") and rec["review_type"] != "(无)":
                rv_txt = f" | 提审:{rec['review_type']}"
                if rec.get("review_result") and rec["review_result"] != "(无)":
                    rv_txt += f"/{rec['review_result']}"
                if rec.get("review_round"):
                    rv_txt += f"/第{rec['review_round']}轮"
            evt = rec.get("event") or rec.get("raw_stage") or "阶段记录"
            lines.append(f"• [{rec['date']}] [{rec['component']}] {evt}{rv_txt}")
        return "<br>".join(lines)

    launch_records = stage_records["立项"]
    if launch_records:
        launch_start = min(x["date"] for x in launch_records)
        segments.append({
            "项目": proj_label,
            "工序阶段": "立项",
            "Start": launch_start.strftime("%Y-%m-%d"),
            "Finish": (launch_start + datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
            "详情": _detail_lines(launch_records, "• 立项固定按 1 天展示；后续补充资料已归入【建模】口径"),
        })

    for stage in ["建模", "设计", "工程", "开模", "修模", "生产"]:
        records = stage_records.get(stage, [])
        if not records:
            continue
        start_dt = min(x["date"] for x in records)
        finish_dt = max(x["date"] for x in records) + datetime.timedelta(days=1)
        if stage in current_macros:
            finish_dt = max(finish_dt, today + datetime.timedelta(days=1))
        if stage in ["建模", "设计", "工程"] and mold_start and start_dt < mold_start:
            finish_dt = min(finish_dt, mold_start)
        if finish_dt <= start_dt:
            finish_dt = start_dt + datetime.timedelta(days=1)
        segments.append({
            "项目": proj_label,
            "工序阶段": stage,
            "Start": start_dt.strftime("%Y-%m-%d"),
            "Finish": finish_dt.strftime("%Y-%m-%d"),
            "详情": _detail_lines(records),
        })

    pause_dates = sorted({x["date"] for x in stage_records.get("暂停", [])})
    resume_dates = sorted({x["date"] for x in all_records if x["stage"] != "暂停"})
    for pause_dt in pause_dates:
        resume_dt = next((d for d in resume_dates if d > pause_dt), None)
        finish_dt = resume_dt or (today + datetime.timedelta(days=1))
        if finish_dt <= pause_dt:
            finish_dt = pause_dt + datetime.timedelta(days=1)
        records = [x for x in stage_records.get("暂停", []) if x["date"] == pause_dt]
        segments.append({
            "项目": proj_label,
            "工序阶段": "暂停",
            "Start": pause_dt.strftime("%Y-%m-%d"),
            "Finish": finish_dt.strftime("%Y-%m-%d"),
            "详情": _detail_lines(records),
        })

    end_records = stage_records.get("结束", [])
    if end_records:
        end_dt = min(x["date"] for x in end_records)
        segments.append({
            "项目": proj_label,
            "工序阶段": "结束",
            "Start": end_dt.strftime("%Y-%m-%d"),
            "Finish": (end_dt + datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
            "详情": _detail_lines(end_records),
        })

    return segments


def render_pm_todo_manager(valid_projs, current_pm):
    st.subheader("🗂️ To do List（CP/DDL 合并）")
    st.caption("To do 专注任务、DDL/CP、关联项目、关联人员。图片统一在进度明细里补；人员只写姓名时，系统会优先匹配库中的完整角色-姓名。")
    cfg = db.setdefault("系统配置", {})
    todo_all = cfg.setdefault("PM_TODO_LIST", [])
    todo_proj_options = ["(不关联项目)"] + valid_projs
    todo_new_proj_option = "➕ 新增项目..."
    todo_proj_options_create = todo_proj_options + [todo_new_proj_option]
    todo_new_person_option = "➕ 新增关联人员..."
    scope_options = build_todo_scope_options(current_pm)
    role_person_options, _ = collect_role_person_options()
    role_person_options_create = role_person_options + [todo_new_person_option]
    owner_pool = list(dict.fromkeys([
        "Mo", "越", "袁",
        *[
            str(db.get(p, {}).get("负责人", "")).strip()
            for p in valid_projs
            if str(db.get(p, {}).get("负责人", "")).strip()
        ],
    ]))

    touched = False
    today = datetime.date.today()
    for td in todo_all:
        if not str(td.get("_id", "")).strip():
            td["_id"] = uuid.uuid4().hex[:10]
            touched = True
        td.setdefault("任务", "")
        td.setdefault("关联项目", "")
        td.setdefault("关联人员", "")
        td.setdefault("完成", False)
        td.setdefault("完成时间", "")
        td.setdefault("创建", str(today))
        td.setdefault("最近联动模块", "")
        td.setdefault("最近联动日期", "")
        td.setdefault("最近联动项目", "")
        td.setdefault("最近联动部件", "")
        td.setdefault("最近联动阶段", "")
        td.setdefault("最近联动写入时间", "")
        td.setdefault("创建者视角", "")
        scope_val = str(td.get("所属视角", "")).strip()
        creator_scope = str(td.get("创建者视角", "")).strip()
        normalized_scope = scope_val
        if (not normalized_scope) or normalized_scope == "所有人":
            normalized_scope = creator_scope if creator_scope and creator_scope != "所有人" else "未分配"
        if normalized_scope not in scope_options and normalized_scope:
            scope_options.append(normalized_scope)
        if scope_val != normalized_scope:
            td["所属视角"] = normalized_scope
            touched = True
        if (not creator_scope) and normalized_scope not in ["", "未分配", "所有人"]:
            td["创建者视角"] = normalized_scope
            touched = True
        merged = todo_cpddl_text(td)
        if str(td.get("CPDDL", "")).strip() != merged:
            td["CPDDL"] = merged
            touched = True
        if str(td.get("CP", "")).strip() != merged:
            td["CP"] = merged
            touched = True
        due_dt = extract_deadline_from_text(merged)
        due_txt = str(due_dt) if due_dt else ""
        if str(td.get("DDL", "")).strip() != due_txt:
            td["DDL"] = due_txt
            touched = True
        normalized_people = normalize_people_text(td.get("关联人员", ""))
        if str(td.get("关联人员", "")).strip() != normalized_people:
            td["关联人员"] = normalized_people
            touched = True
    if touched:
        sync_save_db("系统配置")

    todo_list = [td for td in todo_all if todo_visible_for_view(td, current_pm)]
    pending = [x for x in todo_list if not x.get("完成")]
    overdue = [x for x in pending if todo_due_date(x) and (todo_due_date(x) - today).days < 0]
    near_due = [x for x in pending if todo_due_date(x) and 0 <= (todo_due_date(x) - today).days <= 3]
    linked_pending = [x for x in pending if str(x.get("关联项目", "")).strip()]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("未完成", len(pending))
    m2.metric("已逾期", len(overdue))
    m3.metric("3天内到期", len(near_due))
    m4.metric("已关联项目", len(linked_pending))

    with st.container(border=True):
        st.markdown("##### 新增待办")
        st.caption("支持先选关联人员；如果你只写“宇涵”，系统会优先匹配历史库里的“设计-宇涵”等唯一人选。")
        c1, c2, c3, c4 = st.columns([2.5, 1.6, 1.5, 1.0])
        with c1:
            todo_title = st.text_input("任务", key="todo_title_global", placeholder="例：3/7 金克丝 T2 结构件确认")
        with c2:
            todo_cpddl = st.text_input("CP/DDL(合并)", key="todo_cpddl_global", placeholder="例：3/7 结构件确认")
        with c3:
            todo_ref_proj = st.selectbox("关联项目", todo_proj_options_create, key="todo_ref_global")
        with c4:
            if current_pm == "所有人":
                todo_scope = st.selectbox("所属视角", scope_options, key="todo_scope_global")
            else:
                todo_scope = current_pm
                st.text_input("所属视角", value=current_pm, key="todo_scope_ro", disabled=True)

        resolved_ref_proj = "" if todo_ref_proj == "(不关联项目)" else todo_ref_proj
        new_proj_name = ""
        new_proj_owner = current_pm if current_pm != "所有人" else (owner_pool[0] if owner_pool else "Mo")
        if todo_ref_proj == todo_new_proj_option:
            np1, np2 = st.columns([2.2, 1.2])
            with np1:
                new_proj_name = st.text_input("新增项目名称", key="todo_new_proj_name", placeholder="例：1/6 新IP")
            with np2:
                if current_pm == "所有人":
                    new_proj_owner = st.selectbox("新增项目负责人", owner_pool or ["Mo"], key="todo_new_proj_owner")
                else:
                    new_proj_owner = current_pm
                    st.text_input("新增项目负责人", value=current_pm, key="todo_new_proj_owner_ro", disabled=True)
            resolved_ref_proj = new_proj_name.strip()

        p1, p2 = st.columns([2.2, 1.6])
        with p1:
            todo_people_sel = st.multiselect("关联人员（可多选/留空）", role_person_options_create, key="todo_people_global")
        with p2:
            todo_people_manual = st.text_input("补充人员(可手填)", key="todo_people_manual_global", placeholder="例：设计-宇涵 / 宇涵")

        todo_people_sel_clean = [x for x in todo_people_sel if x != todo_new_person_option]
        todo_new_person_token = ""
        if todo_new_person_option in todo_people_sel:
            pp1, pp2 = st.columns([1.2, 1.5])
            with pp1:
                todo_new_person_role = st.text_input("新增人员角色", key="todo_new_person_role", placeholder="例：设计")
            with pp2:
                todo_new_person_name = st.text_input("新增人员姓名", key="todo_new_person_name", placeholder="例：宇涵")
            if str(todo_new_person_name).strip():
                role_txt = str(todo_new_person_role).strip()
                name_txt = str(todo_new_person_name).strip()
                todo_new_person_token = f"{role_txt}-{name_txt}" if role_txt else name_txt

        people_tokens = todo_people_sel_clean + split_people_text(todo_people_manual)
        if todo_new_person_token:
            people_tokens.append(todo_new_person_token)
        people_input = normalize_people_text(", ".join(people_tokens))
        tmp_td = {
            "任务": todo_title,
            "CPDDL": todo_cpddl,
            "关联项目": resolved_ref_proj,
            "关联人员": people_input,
        }
        st.caption("开定识别：" + infer_todo_target_hint(tmp_td, valid_projs))
        st.caption("人员识别：" + format_todo_people_hint(tmp_td))
        people_bundle = infer_todo_people_bundle(tmp_td)
        if not people_input and (people_bundle["ambiguous"] or people_bundle["unknown"]):
            st.warning("人员没有唯一识别，建议在 To do 里直接补充【关联人员】；图片和细节再放到下方进度明细。")

        if st.button("➕ 添加", key="todo_add_global", type="primary"):
            if not todo_title.strip():
                st.warning("请先填写任务内容。")
            elif todo_ref_proj == todo_new_proj_option and not resolved_ref_proj:
                st.warning("你选择了【新增项目】，请先填写项目名称。")
            else:
                due_dt = extract_deadline_from_text(todo_cpddl)
                final_people = people_input or ", ".join(people_bundle["labels"])
                linked_proj = str(resolved_ref_proj or "").strip()
                created_project = False
                if linked_proj:
                    created_project = create_project_shell_if_missing(linked_proj, new_proj_owner)

                added_people = register_extra_role_people(split_people_text(final_people))
                todo_all.append({
                    "_id": uuid.uuid4().hex[:10],
                    "任务": todo_title.strip(),
                    "CPDDL": todo_cpddl.strip(),
                    "CP": todo_cpddl.strip(),
                    "DDL": str(due_dt) if due_dt else "",
                    "关联项目": linked_proj,
                    "关联人员": final_people,
                    "所属视角": (str(todo_scope).strip() or "未分配"),
                    "创建者视角": (current_pm if current_pm != "所有人" else (str(todo_scope).strip() or "未分配")),
                    "完成": False,
                    "完成时间": "",
                    "创建": str(today),
                })
                cfg["PM_TODO_LIST"] = todo_all
                if created_project and linked_proj:
                    sync_save_db(linked_proj)
                else:
                    sync_save_db("系统配置")

                success_bits = ["To do 已添加。"]
                if created_project and linked_proj:
                    success_bits.append(f"已新建项目：{linked_proj}。")
                if added_people:
                    preview = " / ".join(added_people[:3])
                    if len(added_people) > 3:
                        preview += " ..."
                    success_bits.append(f"已补充人员库：{preview}")
                st.success(" ".join(success_bits))
                st.rerun()

    st.markdown("##### 当前 To do")
    if not todo_list:
        st.info("当前视角下暂无 To do。")
        return todo_list

    def _todo_alert_text(td):
        return todo_alert_text(td, today)

    rows = []
    sorted_items = sorted(todo_list, key=lambda x: todo_sort_key(x, today))
    for td in sorted_items:
        done_at = str(td.get("完成时间", "")) if td.get("完成") else ""
        rows.append({
            "_id": str(td.get("_id", "")),
            "完成": bool(td.get("完成", False)),
            "任务": str(td.get("任务", "")),
            "CP/DDL": todo_cpddl_text(td),
            "关联项目": str(td.get("关联项目", "") or "(不关联项目)"),
            "完成时间": done_at or "-",
            "关联人员": str(td.get("关联人员", "")),
            "人员识别": format_todo_people_hint(td),
            "所属视角": todo_scope_of(td),
            "到期": str(todo_due_date(td) or "-"),
            "提醒": todo_alert_text(td, today),
            "开定识别": infer_todo_target_hint(td, valid_projs),
            "联动状态": todo_link_status_text(td),
            "删除": False,
        })
    editor_df = pd.DataFrame(rows)
    edited_df = st.data_editor(
        editor_df,
        width='stretch',
        hide_index=True,
        num_rows="fixed",
        column_config={
            "_id": st.column_config.TextColumn("_id", disabled=True, width="small"),
            "完成": st.column_config.CheckboxColumn("完成", width="small"),
            "完成时间": st.column_config.TextColumn("完成时间", disabled=True, width="small"),
            "任务": st.column_config.TextColumn("任务", required=True, width="large"),
            "CP/DDL": st.column_config.TextColumn("CP/DDL", width="medium"),
            "关联项目": st.column_config.SelectboxColumn("关联项目", options=todo_proj_options, width="medium"),
            "关联人员": st.column_config.TextColumn("关联人员", width="medium", help="支持逗号分隔多个角色-姓名；留空时系统会按文本尝试识别"),
            "人员识别": st.column_config.TextColumn("人员识别", disabled=True, width="large"),
            "所属视角": st.column_config.SelectboxColumn("所属视角", options=scope_options, width="small"),
            "到期": st.column_config.TextColumn("到期", disabled=True, width="small"),
            "提醒": st.column_config.TextColumn("提醒", disabled=True, width="small"),
            "开定识别": st.column_config.TextColumn("开定识别", disabled=True, width="large"),
            "联动状态": st.column_config.TextColumn("联动状态", disabled=True, width="medium"),
            "删除": st.column_config.CheckboxColumn("删除", width="small"),
        },
        disabled=["_id", "完成时间", "到期", "提醒", "开定识别", "联动状态", "人员识别"],
        key="todo_editor_df",
    )

    if st.button("💾 保存 To do 状态", key="todo_save_global"):
        id_map = {str(td.get("_id", "")): td for td in todo_all}
        delete_ids = set()
        skipped = 0
        project_history_updates = set()
        for row in edited_df.to_dict("records"):
            rid = str(row.get("_id", "")).strip()
            td = id_map.get(rid)
            if not td:
                continue
            if bool(row.get("删除", False)):
                delete_ids.add(rid)
                continue
            title = str(row.get("任务", "")).strip()
            if not title:
                skipped += 1
                continue
            cpddl = str(row.get("CP/DDL", "")).strip()
            ref_proj = str(row.get("关联项目", "")).strip()
            ref_proj = "" if ref_proj == "(不关联项目)" else ref_proj
            people_raw = normalize_people_text(row.get("关联人员", ""))
            people_td = {
                "任务": title,
                "CPDDL": cpddl,
                "关联项目": ref_proj,
                "关联人员": people_raw,
            }
            people_bundle = infer_todo_people_bundle(people_td)
            if not people_raw and people_bundle["labels"]:
                people_raw = ", ".join(people_bundle["labels"])
            register_extra_role_people(split_people_text(people_raw))

            prev_done = bool(td.get("完成", False))
            new_done = bool(row.get("完成", False))
            td["任务"] = title
            td["CPDDL"] = cpddl
            td["CP"] = cpddl
            due_dt = extract_deadline_from_text(cpddl)
            td["DDL"] = str(due_dt) if due_dt else ""
            td["关联项目"] = ref_proj
            td["关联人员"] = people_raw
            td["完成"] = new_done
            scope_val = str(row.get("所属视角", td.get("所属视角", "未分配"))).strip()
            if current_pm != "所有人":
                scope_val = current_pm
            if (not scope_val) or scope_val == "所有人":
                scope_val = "未分配"
            td["所属视角"] = scope_val if scope_val in scope_options else scope_val
            if (not str(td.get("创建者视角", "")).strip()) and scope_val not in ["", "未分配", "所有人"]:
                td["创建者视角"] = scope_val
            if new_done and not prev_done:
                done_date = due_dt or datetime.date.today()
                td["完成时间"] = str(done_date)
                if append_todo_completion_history(td, done_date):
                    project_history_updates.add(ref_proj)
            elif not new_done:
                td["完成时间"] = ""

        todo_all[:] = [x for x in todo_all if str(x.get("_id", "")).strip() not in delete_ids and str(x.get("任务", "")).strip()]
        cfg["PM_TODO_LIST"] = todo_all
        if project_history_updates:
            sync_save_db()
        else:
            sync_save_db("系统配置")
        st.success(f"To do 已保存：保留 {len(todo_all)} 条，删除 {len(delete_ids)} 条，跳过 {skipped} 条空任务。")
        st.rerun()

    st.caption("建议：To do 先做轻量提醒；图片、附件、流转详情统一在【细分配件交接工作台】里补充。")
    return [td for td in todo_all if todo_visible_for_view(td, current_pm)]

def render_sidebar_todo_panel(pm_view):
    cfg = db.setdefault("系统配置", {})
    todo_all = cfg.setdefault("PM_TODO_LIST", [])
    today = datetime.date.today()
    visible = [td for td in todo_all if todo_visible_for_sidebar(td, pm_view)]
    pending = sorted([td for td in visible if not td.get("完成")], key=lambda x: todo_sort_key(x, today))
    completed_count = len([td for td in visible if td.get("完成")])

    st.sidebar.divider()
    st.sidebar.markdown("### 🗂️ To do")
    st.sidebar.caption(f"未完成 {len(pending)} | 已完成 {completed_count}")
    if not pending:
        st.sidebar.caption("当前视角下没有未完成 To do。")
        return

    for idx, td in enumerate(pending[:6], 1):
        task = str(td.get("任务", "")).strip() or "(空任务)"
        due = todo_due_date(td)
        due_txt = due.strftime("%m-%d") if due else "无DDL"
        proj = str(td.get("关联项目", "")).strip() or "(未关联项目)"
        status_icon = todo_alert_text(td, today).split(" ")[0]
        st.sidebar.markdown(f"`{idx}` {status_icon} **{task}**")
        st.sidebar.caption(f"{due_txt} | {proj}")
    if len(pending) > 6:
        st.sidebar.caption(f"还有 {len(pending) - 6} 条未完成待办未展开。")

def _csv_cell_text(v):
    s = str(v if v is not None else "").strip()
    if s.lower() in ["nan", "none", "nat"]:
        return ""
    return s


def _read_csv_bytes_flex(raw_bytes, header='infer'):
    last_err = None
    for enc in [None, "utf-8-sig", "utf-8", "gbk"]:
        try:
            bio = io.BytesIO(raw_bytes)
            kwargs = {
                "header": header,
                "dtype": str,
                "keep_default_na": False,
                "on_bad_lines": "skip"
            }
            if enc:
                kwargs["encoding"] = enc
            return pd.read_csv(bio, **kwargs)
        except Exception as e:
            last_err = e
    raise last_err


def _pick_col_by_keywords(df_obj, keywords):
    return next((c for c in df_obj.columns if any(k in str(c) for k in keywords)), None)


def _detect_header_row_idx(df_raw, scan_rows=15):
    if df_raw is None or df_raw.empty:
        return None
    max_rows = min(scan_rows, len(df_raw))
    best_idx, best_score = None, -1
    for i in range(max_rows):
        vals = [_csv_cell_text(v) for v in df_raw.iloc[i].tolist()]
        non_empty = [v for v in vals if v]
        if len(non_empty) < 2:
            continue
        row_txt = " ".join(non_empty)
        score = 0
        if any(k in row_txt for k in ["项目", "名称", "产品"]):
            score += 3
        if any(k in row_txt for k in ["开定", "Target", "目标"]):
            score += 3
        if any(k in row_txt for k in ["负责", "PM", "阶段", "状态", "发货", "跟单"]):
            score += 1
        if score > best_score:
            best_idx, best_score = i, score
    if best_score >= 3:
        return best_idx
    return None


def _build_df_from_header_row(df_raw, header_idx):
    headers = []
    used = {}
    for c in df_raw.iloc[header_idx].tolist():
        name = _csv_cell_text(c) or "未命名列"
        if name in used:
            used[name] += 1
            name = f"{name}_{used[name]}"
        else:
            used[name] = 0
        headers.append(name)

    body = df_raw.iloc[header_idx + 1:].copy().reset_index(drop=True)
    body.columns = headers
    body = body.loc[~body.apply(lambda r: all(_csv_cell_text(x) == "" for x in r), axis=1)]
    return body


def _infer_year_hint_from_matrix(df_raw):
    years = []
    for v in df_raw.values.flatten().tolist():
        s = _csv_cell_text(v)
        if not s:
            continue
        for m in re.finditer(r'\b(20\d{2})[./-]\d{1,2}\b', s):
            years.append(int(m.group(1)))
        for m in re.finditer(r'\b(\d{2})\.\d{1,2}\b', s):
            years.append(2000 + int(m.group(1)))
        for m in re.finditer(r'(\d{2,4})年\s*\d{1,2}月', s):
            y_raw = int(m.group(1))
            years.append(y_raw if y_raw >= 1000 else (2000 + y_raw))
    if years:
        return Counter(years).most_common(1)[0][0]
    return datetime.date.today().year


def _split_schedule_cell_tokens(cell_text):
    s = _csv_cell_text(cell_text)
    if not s:
        return []
    s = s.replace("\r", "\n")
    parts = re.split(r'[\n,，;；、]+', s)
    out = []
    for p in parts:
        p = re.sub(r'[\[\]【】<>《》()（）]', ' ', str(p))
        p = re.sub(r'\s+', ' ', p).strip()
        if not p:
            continue
        if len(norm_text(p)) < 2:
            continue
        out.append(p)
    return out


def _match_projects_from_token(token, valid_projs, alias_map):
    q = norm_text(token)
    if not q:
        return []

    hits = []
    for p in valid_projs:
        p_full = norm_text(p)
        p_short = norm_text(re.sub(r'^(1/6|1/4|1/12|1/3|1/1)\s*', '', str(p)))
        if (p_full and p_full in q) or (p_short and p_short in q):
            hits.append(p)

    if not hits:
        for a in alias_map.keys():
            a_norm = norm_text(a)
            if not a_norm or a_norm not in q:
                continue
            canon = resolve_alias_project(a, alias_map)
            if canon in valid_projs:
                hits.append(canon)

    if not hits:
        for m in re.findall(r'(1/\d+\s*[A-Za-z0-9\u4e00-\u9fa5\-_]+)', token):
            m_norm = re.sub(r'\s+', '', m).strip()
            if len(m_norm) >= 4:
                hits.append(m_norm)

    return list(dict.fromkeys(hits))


def _extract_target_map_from_matrix(df_raw, valid_projs, alias_map):
    target_map = {}
    conflicts = {}
    year_hint = _infer_year_hint_from_matrix(df_raw)

    for _, row in df_raw.iterrows():
        cells = [_csv_cell_text(v) for v in row.tolist()]
        if not any(cells):
            continue

        row_ym = None
        ym_idx = -1
        for i, c in enumerate(cells[:3]):
            ym = extract_schedule_year_month(c, ref_year=year_hint)
            if ym:
                row_ym = ym
                ym_idx = i
                break
        if not row_ym:
            continue

        tgt = f"{int(row_ym[0]):04d}-{int(row_ym[1]):02d}"
        for c_idx, c in enumerate(cells):
            if not c or c_idx == ym_idx:
                continue
            for tk in _split_schedule_cell_tokens(c):
                for p in _match_projects_from_token(tk, valid_projs, alias_map):
                    old_tgt = target_map.get(p)
                    if old_tgt and old_tgt != tgt:
                        conflicts.setdefault(p, set()).update([old_tgt, tgt])
                        if tgt < old_tgt:
                            target_map[p] = tgt
                    else:
                        target_map[p] = tgt

    conflicts = {k: sorted(list(v)) for k, v in conflicts.items()}
    return target_map, conflicts

def get_status_label(milestone):
    bucket = get_project_status_bucket(milestone)
    if bucket == "pause":
        return "⏸️ 暂停研发"
    if bucket == "done":
        return "🏁 已结案"
    if bucket == "prod":
        return "🟢 生产期"
    if bucket == "dev":
        return "🟡 研发期"
    return "⚪ 未知阶段"



def get_risk_status(milestone, target_date_str="TBD"):
    return get_status_label(milestone), "normal"



def get_project_status_bucket(milestone):
    ms = str(milestone or "").strip()
    if ms == "暂停研发":
        return "pause"
    if ms in ["生产结束", "项目结束撒花🎉", "✅ 已完成(结束)"]:
        return "done"
    if ms in ["生产中", "下模中"]:
        return "prod"
    if "研发" in ms or ms in ["待开定", "已开定", "待立项"]:
        return "dev"
    return "unknown"



def month_last_day(year, month):
    if month in [1, 3, 5, 7, 8, 10, 12]:
        return 31
    if month == 2:
        return 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28
    return 30



def parse_schedule_marker_date(schedule_str, marker_mode="target"):
    s = str(schedule_str or "").strip()
    if not s or s.upper() in ["TBD", "NONE"] or s in ["-", "—", "无"]:
        return None

    for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"]:
        try:
            return datetime.datetime.strptime(s[:10], fmt).date()
        except Exception:
            pass

    m_q = re.match(r'^(\d{4}|\d{2})\s*Q([1-4])$', s.upper())
    if m_q:
        y_raw = int(m_q.group(1))
        year = y_raw if y_raw >= 1000 else (2000 + y_raw)
        quarter = int(m_q.group(2))
        if marker_mode == "ship":
            return quarter_to_deadline(f"{year} Q{quarter}")
        first_month = (quarter - 1) * 3 + 1
        return datetime.date(year, first_month, 15)

    ym = parse_target_year_month(s)
    if ym:
        year, month = int(ym[0]), int(ym[1])
        if marker_mode == "ship":
            return datetime.date(year, month, month_last_day(year, month))
        return datetime.date(year, month, 15)

    return None



def get_deadline_alert(milestone, target_text="TBD", ship_text="-", days=5):
    bucket = get_project_status_bucket(milestone)
    if bucket not in ["dev", "prod"]:
        return "✅ 正常", 2

    marker_mode = "target" if bucket == "dev" else "ship"
    raw_value = target_text if bucket == "dev" else ship_text
    alert_name = "开定" if bucket == "dev" else "发货"
    deadline = parse_schedule_marker_date(raw_value, marker_mode=marker_mode)
    if not deadline:
        return "✅ 正常", 2

    diff_days = (deadline - datetime.date.today()).days
    if diff_days < 0:
        return f"🔴 {alert_name}逾期", 0
    if diff_days <= int(days):
        return f"🟨 {alert_name}临期(<={int(days)}天)", 1
    return "✅ 正常", 2



def build_timeline_marker_info(proj_name, proj_label, milestone, target_text, ship_text):
    bucket = get_project_status_bucket(milestone)
    if bucket == "dev":
        mark_dt = parse_schedule_marker_date(target_text, marker_mode="target")
        if mark_dt:
            return {
                "项目": proj_label,
                "项目原名": proj_name,
                "日期": mark_dt.strftime("%Y-%m-%d"),
                "标记类型": "研发开定",
                "原始时间": str(target_text or "TBD"),
                "悬浮": f"{proj_name}<br>研发开定：{target_text}"
            }
    if bucket == "prod":
        mark_dt = parse_schedule_marker_date(ship_text, marker_mode="ship")
        if mark_dt:
            return {
                "项目": proj_label,
                "项目原名": proj_name,
                "日期": mark_dt.strftime("%Y-%m-%d"),
                "标记类型": "生产发货",
                "原始时间": str(ship_text or "-"),
                "悬浮": f"{proj_name}<br>生产发货：{ship_text}"
            }
    return None


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
        "拆件": "工程拆件", "官图": "官图", "开模": "开模", "模具": "开模", "试模": "开模", "大货": "大货",
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
    st.caption("提审识别规则：仅命中提审语义（提审/过审/review/打回等）时自动填写提审结果，普通 OK 不再默认=通过。")

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
        width='stretch',
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
                img_ref = save_uploaded_file_ref(f, prefix="pm_batch")
                if img_ref:
                    images_by_target.setdefault(target, []).append(img_ref)


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
        "拆件": "工程拆件", "官图": "官图", "开模": "开模", "模具": "开模", "试模": "开模", "大货": "大货",
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
    st.caption("提审识别规则：仅命中提审语义（提审/过审/review/打回等）时自动填写提审结果，普通 OK 不再默认=通过。")

    existing_comps = list(db[sel_proj].get("部件列表", {}).keys())
    comp_opts = [unresolved_comp, "全局进度"] + STD_COMPONENTS + existing_comps + ["其他配件(系统自动创建)"]
    comp_opts = list(dict.fromkeys(comp_opts))
    stage_opts = ["(维持原阶段)"] + STAGES_UNIFIED

    df_rows = pd.DataFrame(rows)
    edited_df = st.data_editor(
        df_rows,
        num_rows="dynamic",
        width='stretch',
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
                img_ref = save_uploaded_file_ref(f, prefix="pm_fast")
                if img_ref:
                    images_by_target.setdefault(target, []).append(img_ref)


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
                        render_image(ref, width='stretch')
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
        st.dataframe(pd.DataFrame(rows), width='stretch')


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
    qdf = st.data_editor(qdf, num_rows="dynamic", width='stretch', key=f"pmc_editor_{fk}")
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
        st.dataframe(comp_df, width='stretch')

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
        st.dataframe(share_df.sort_values(by='税后总成本', ascending=False), width='stretch')

    edited_df = st.data_editor(df_cost, num_rows="dynamic", width='stretch', key=f"pmc_detail_editor_{sel_proj}")
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
        st.dataframe(cmp_df.sort_values(by="差异", ascending=False), width='stretch')

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
        st.dataframe(df_eff.sort_values(by=["耗时(天)", "工序"], ascending=[False, True]), width='stretch')
    else:
        st.info("💡 暂无完整闭环的工时记录。勾选【彻底完成】后即可激活此工时排行榜！")
# ==========================================
# 4. 视图控制层
# ==========================================
st.sidebar.title("🚀 INART PM 系统")
pm_list    = ["所有人", "Mo", "越", "袁"]
current_pm = st.sidebar.selectbox("👤 视角切换", pm_list)

backend_name = get_storage_backend_name()
attachment_mode = get_storage_attachment_mode()
attachment_label = "GridFS 持久附件" if attachment_mode == "gridfs" else "本地文件引用"
backend_icon = "🟢" if backend_name == "MongoDB" else "🟡"
st.sidebar.caption(f"{backend_icon} 当前存储：{backend_name} | 附件：{attachment_label}")
if backend_name != "MongoDB":
    st.sidebar.warning("当前处于本地兜底模式。Cloud 重启或重新部署后，本地 JSON / 本地附件不保证保留。")

db          = st.session_state.db
valid_projs = get_visible_projects(db, current_pm)
render_sidebar_todo_panel(current_pm)


menu = st.sidebar.radio("📂 功能导航", [
    MENU_DASHBOARD, MENU_SPECIFIC, MENU_FASTLOG,
    MENU_HISTORY, MENU_SETTINGS, MENU_GUIDE
])
st.sidebar.caption("建议流程：先看全局，再进 PM 工作台；手机端可用 AI 速记做晚间复盘。")

# 备份与恢复
st.sidebar.divider()
st.sidebar.markdown("### ⚙️ 数据备份与恢复")
try:
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        json_bytes = json.dumps(st.session_state.db, ensure_ascii=False, indent=4).encode("utf-8")
        zf.writestr("database.json", json_bytes)
        for ref in iter_attachment_refs_in_db(st.session_state.db):
            raw = read_binary_ref(ref)
            if raw:
                zf.writestr(attachment_backup_path(ref), raw)
    zip_buffer.seek(0)
    st.sidebar.download_button(
        "💾 下载全量备份 (数据+图片)", data=zip_buffer,
        file_name=f"inart_pm_full_backup_{datetime.date.today()}.zip",
        mime="application/zip"
    )
except Exception as e:
    st.sidebar.warning(f"备份生成失败: {e}")

restore_file = st.sidebar.file_uploader("📂 上传备份以恢复", type=["zip", "json"])
if restore_file is not None and st.sidebar.button("⚠️ 确认覆盖恢复", type="primary"):
    try:
        restored_attachment_count = 0
        missing_attachment_count = 0
        if restore_file.name.endswith(".zip"):
            with zipfile.ZipFile(restore_file, "r") as zf:
                if "database.json" in zf.namelist():
                    with zf.open("database.json") as f:
                        restored_data = json.load(f)
                else:
                    st.sidebar.error("❌ 压缩包内未找到 database.json！")
                    st.stop()
                restored_data, restored_attachment_count, missing_attachment_count = restore_attachments_from_zip(restored_data, zf)
        else:
            restored_data = json.load(restore_file)
        db_manager.save(restored_data)
        st.session_state.db = restored_data
        msg = f"🎉 恢复成功！已恢复 {restored_attachment_count} 个附件。"
        if missing_attachment_count > 0:
            msg += f" 另有 {missing_attachment_count} 个附件未在备份包中找到。"
        st.sidebar.success(msg + " 请手动刷新网页！")
    except Exception as e:
        st.sidebar.error(f"解析失败: {e}")



if menu == MENU_DASHBOARD:
    st.title(f"📊 全局大盘与进度甘特图 ({current_pm} 的视角)")

    with st.expander("📥 批量导入/更新研发总表 (CSV)"):
        st.info("💡 支持自动识别含有【项目名称】、【负责人】、【当前阶段】、【开定时间】、【发货区间】、【跟单】等列的 CSV 文件。")
        st.caption("自动模式支持：首行非表头 + 手工月度矩阵（同月多个项目）。")
        import_mode = st.selectbox(
            "导入模式",
            ["自动识别(首行可非表头/手工月度矩阵)", "标准表头模式"],
            index=0,
            key="rd_csv_import_mode"
        )

        rd_csv = st.file_uploader("选择研发总表 CSV 文件", type=['csv'], key="rd_csv_uploader")
        if rd_csv and st.button("🚀 开始解析导入", type="primary"):
            try:
                raw_bytes = rd_csv.getvalue()
                alias_map = db.get("系统配置", {}).get("项目别名", {})
                all_proj_names = [k for k in db.keys() if k != "系统配置"]

                df_rd = _read_csv_bytes_flex(raw_bytes, header=0)
                header_from = "首行表头"
                col_proj = _pick_col_by_keywords(df_rd, ['项目', '名称', '产品'])

                if import_mode.startswith("自动识别") and not col_proj:
                    df_raw = _read_csv_bytes_flex(raw_bytes, header=None)
                    hdr_idx = _detect_header_row_idx(df_raw)
                    if hdr_idx is not None:
                        df_rd = _build_df_from_header_row(df_raw, hdr_idx)
                        header_from = f"第{hdr_idx + 1}行表头"
                        col_proj = _pick_col_by_keywords(df_rd, ['项目', '名称', '产品'])
                    else:
                        target_map, conflicts = _extract_target_map_from_matrix(df_raw, all_proj_names, alias_map)
                        if not target_map:
                            st.error("❌ 未识别到可导入的项目开定。请检查文本是否包含项目名 + 月份（如 26.5 / 2026-05 / 2026Q2 / 5月）。")
                        else:
                            count_new = 0
                            count_update = 0
                            for p_name, tgt_val in target_map.items():
                                if p_name not in db:
                                    db[p_name] = {
                                        "负责人": "", "跟单": "", "Milestone": "待立项",
                                        "Target": tgt_val, "发货区间": "",
                                        "部件列表": {}, "发货数据": {}, "成本数据": {}
                                    }
                                    count_new += 1
                                else:
                                    if str(db[p_name].get("Target", "")).strip() != tgt_val:
                                        db[p_name]["Target"] = tgt_val
                                        count_update += 1
                            sync_save_db()
                            st.success(f"🎉 导入完毕（矩阵抽取）！新建项目: {count_new} 个，更新开定: {count_update} 个。")
                            if conflicts:
                                sample = "；".join([f"{p}: {' / '.join(v)}" for p, v in list(conflicts.items())[:8]])
                                st.warning(f"以下项目识别到多个月份，已按最早月份写入：{sample}")
                            st.rerun()

                if col_proj:
                    col_pm = _pick_col_by_keywords(df_rd, ['负责', 'PM'])
                    col_ms = _pick_col_by_keywords(df_rd, ['阶段', '状态', 'Milestone'])
                    col_tgt = _pick_col_by_keywords(df_rd, ['开定', 'Target', '目标'])
                    col_ship = _pick_col_by_keywords(df_rd, ['发货', '出货'])
                    col_gd = _pick_col_by_keywords(df_rd, ['跟单'])

                    count_new = 0
                    count_update = 0
                    for _, row in df_rd.iterrows():
                        p_name_raw = _csv_cell_text(row[col_proj])
                        p_name = resolve_alias_project(p_name_raw, alias_map)
                        if not p_name:
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
                            db[p_name] = {
                                "负责人": pm_val, "跟单": gd_val, "Milestone": ms_val,
                                "Target": tgt_val, "发货区间": ship_val,
                                "部件列表": {}, "发货数据": {}, "成本数据": {}
                            }
                            count_new += 1
                        else:
                            if col_pm and pm_val:
                                db[p_name]["负责人"] = pm_val
                            if col_ms and ms_val:
                                db[p_name]["Milestone"] = ms_val
                            if col_tgt and tgt_val:
                                db[p_name]["Target"] = tgt_val
                            if col_ship:
                                db[p_name]["发货区间"] = ship_val
                            if col_gd:
                                db[p_name]["跟单"] = gd_val
                            count_update += 1

                    sync_save_db()
                    st.success(f"🎉 导入完毕！新增: {count_new} 个，更新: {count_update} 个。")
                    st.caption(f"识别来源：{header_from}")
                    st.rerun()
                elif import_mode == "标准表头模式":
                    st.error("❌ 未能找到【项目名称】列，请检查表头！")
            except Exception as e:
                st.error(f"解析失败: {e}")
    gantt_cat_orders = MACRO_STAGES.copy()
    combined_color_map = {
        "立项": "#F2C14E", "建模": "#34C6D3", "设计": "#8B5CF6",
        "工程": "#4F7CFF", "开模": "#FB7185", "修模": "#F97316",
        "生产": "#37B36B", "暂停": "#94A3B8", "结束": "#334155"
    }

    @st.cache_data(ttl=30, show_spinner=False)
    def _build_dash(proj_list_key: str, db_hash: str):
        _table = []; _gantt = []; _ppr = []; _marks = []; _meta = []
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
            target_dt = parse_period_marker_date(tgt, end_of_period=False)
            if target_dt:
                _marks.append({
                    "日期": target_dt.strftime("%Y-%m-%d"),
                    "项目": proj_y_label,
                    "类型": "开定",
                    "说明": f"[{proj}] 目标开定 {tgt}",
                })
            ship_dt = parse_period_marker_date(ship_itv, end_of_period=True)
            if ship_dt:
                _marks.append({
                    "日期": ship_dt.strftime("%Y-%m-%d"),
                    "项目": proj_y_label,
                    "类型": "发货",
                    "说明": f"[{proj}] 预计发货 {ship_itv}",
                })
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
        return _table, _gantt, _ppr, _marks, _meta

    # cache key：项目列表 + 数据指纹（只用非图片字段的哈希）
    import hashlib as _hl
    _db_sig = _hl.md5(json.dumps(
        {k: {fk: fv for fk, fv in v.items() if fk not in ("配件清单长图",)}
         for k, v in db.items() if k != "系统配置"},
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    table_data, gantt_data, _ppr_list, timeline_marks, _meta = _build_dash(",".join(valid_projs), _db_sig)
    project_person_roles = set(map(tuple, _ppr_list))
    project_person_roles.update(collect_todo_loading_pairs(current_pm))

    st.divider()
    st.subheader("📈 全局进展甘特图")
    st.markdown("💡 默认显示当前月份前后约半年区间；支持手动调整日期范围，并统计建模/设计/工程平均耗时（可选去极值）。")
    if gantt_data:
        df_g_all = pd.DataFrame(gantt_data).sort_values(by=["项目", "Start"])
        df_g_all["Start_dt"] = pd.to_datetime(df_g_all["Start"], errors="coerce")
        df_g_all["Finish_dt"] = pd.to_datetime(df_g_all["Finish"], errors="coerce")
        month_anchor = datetime.date.today().replace(day=1)
        default_gantt_start = add_months(month_anchor, -2)
        default_gantt_end_month = add_months(month_anchor, 3)
        default_gantt_end = default_gantt_end_month.replace(day=month_last_day(default_gantt_end_month.year, default_gantt_end_month.month))
        if not isinstance(st.session_state.get("gantt_start"), datetime.date):
            st.session_state["gantt_start"] = default_gantt_start
        if not isinstance(st.session_state.get("gantt_end"), datetime.date):
            st.session_state["gantt_end"] = default_gantt_end
        d1, d2 = st.columns(2)
        with d1:
            gantt_start = st.date_input("甘特开始日期", key="gantt_start")
        with d2:
            gantt_end = st.date_input("甘特结束日期", key="gantt_end")
        selected_start = pd.to_datetime(gantt_start)
        selected_end = pd.to_datetime(gantt_end)
        m = (df_g_all["Finish_dt"] >= selected_start) & (df_g_all["Start_dt"] <= selected_end)
        df_g = df_g_all[m].copy()
        showing_full_gantt = False
        if df_g.empty:
            st.info("当前时间窗口内无数据，已回退显示全部甘特数据。")
            df_g = df_g_all.copy()
            showing_full_gantt = True

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
        if timeline_marks:
            df_marks = pd.DataFrame(timeline_marks)
            df_marks["日期_dt"] = pd.to_datetime(df_marks["日期"], errors="coerce")
            if not showing_full_gantt:
                df_marks = df_marks[(df_marks["日期_dt"] >= selected_start) & (df_marks["日期_dt"] <= selected_end)].copy()
            if not df_marks.empty:
                for mark_type, label_text, color, symbol in [("开定", "开", "#E11D48", "diamond"), ("发货", "发", "#2563EB", "square")]:
                    part = df_marks[df_marks["类型"] == mark_type]
                    if part.empty:
                        continue
                    fig.add_trace(go.Scatter(
                        x=part["日期"], y=part["项目"], mode="markers+text",
                        marker=dict(symbol=symbol, size=18, color=color, line=dict(width=1.2, color="white")),
                        text=[label_text] * len(part),
                        textposition="middle center",
                        textfont=dict(size=10, color="white"),
                        name=f"{mark_type}标记",
                        customdata=part[["说明"]],
                        hovertemplate="%{customdata[0]}<extra></extra>"
                    ))
        today_dt = pd.to_datetime(datetime.date.today())
        if showing_full_gantt or (selected_start <= today_dt <= selected_end):
            today_str = datetime.date.today().strftime("%Y-%m-%d")
            fig.add_shape(
                type="line",
                x0=today_str,
                x1=today_str,
                y0=0,
                y1=1,
                xref="x",
                yref="paper",
                line=dict(color="#64748B", width=1, dash="dash"),
            )
            if y_order:
                fig.add_trace(go.Scatter(
                    x=[today_str],
                    y=[y_order[0]],
                    mode="markers+text",
                    text=["今日"],
                    textposition="top center",
                    textfont=dict(size=9, color="#64748B"),
                    marker=dict(size=0, opacity=0),
                    showlegend=False,
                    hoverinfo="skip",
                ))
        fig.update_yaxes(autorange="reversed")
        fig.update_layout(height=max(400, len(df_g['项目'].unique()) * 45))
        st.plotly_chart(fig, width='stretch')

        st.markdown("#### ⏱️ 建模/设计/工程平均耗时（天）")
        trim_outlier = st.checkbox("去掉最大值和最小值（样本>=3时）", value=False, key="trim_stage_avg")
        df_dur = df_g_all.copy()
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
        st.dataframe(pd.DataFrame(rows), width='stretch')
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
        df_table["开定排序"] = df_table["开定时间"].apply(
            lambda x: parse_period_marker_date(x, end_of_period=False) or datetime.date.max
        )
        df_table["发货排序"] = df_table["预计发货"].apply(
            lambda x: parse_period_marker_date(x, end_of_period=True) or datetime.date.max
        )
        df_table["断更天"] = df_table["断更"].str.extract(r'(\d+)').fillna('99999').astype(int)
        for i, r in df_table.iterrows():
            stt = str(r.get("状态", ""))
            if "研发" in stt and is_due_soon(r.get("开定时间", ""), 5):
                df_table.at[i, "开定延迟预警"] = "⚠️ +5天临期"
            if "生产" in stt and is_due_soon(r.get("预计发货", ""), 5):
                df_table.at[i, "发货延迟预警"] = "⚠️ +5天临期"

        df_table = df_table.sort_values(by=["状态组", "开定排序", "发货排序", "断更天", "项目"], ascending=[True, True, True, True, True])
        show_df = df_table.drop(columns=["状态组", "开定排序", "发货排序", "断更天"])
        dashboard_project_order = show_df["\u9879\u76ee"].tolist()
        project_rank_map = {str(p): idx for idx, p in enumerate(dashboard_project_order)}

        with st.expander("\u26a1 \u5927\u76d8\u8054\u52a8\u5feb\u6539\uff08\u57fa\u7840\u5b57\u6bb5 + \u4e8b\u4ef6\uff09", expanded=True):
            st.caption("\u540c\u4e00\u5165\u53e3\u5b8c\u6210\u5b57\u6bb5\u5feb\u6539\u548c\u4e8b\u4ef6\u5feb\u6539\uff0c\u6392\u5e8f\u4e0e\u4e0b\u65b9\u5927\u76d8\u660e\u7ec6\u8868\u4fdd\u6301\u4e00\u81f4\u3002")

            quick_edit_df = show_df[["\u9879\u76ee", "\u9879\u76ee\u5f53\u524d\u9636\u6bb5", "\u5f00\u5b9a\u65f6\u95f4", "\u9884\u8ba1\u53d1\u8d27", "\u8ddf\u5355"]].copy()
            if current_pm == "\u6240\u6709\u4eba":
                quick_edit_df.insert(
                    1,
                    "\u8d1f\u8d23\u4eba",
                    [str(db.get(str(p).strip(), {}).get("\u8d1f\u8d23\u4eba", "")).strip() for p in quick_edit_df["\u9879\u76ee"].tolist()],
                )

            owner_options = list(
                dict.fromkeys(
                    [
                        "Mo",
                        "\u8d8a",
                        "\u8881",
                        *[
                            str((db.get(p, {}) or {}).get("\u8d1f\u8d23\u4eba", "")).strip()
                            for p in valid_projs
                            if str((db.get(p, {}) or {}).get("\u8d1f\u8d23\u4eba", "")).strip()
                        ],
                    ]
                )
            )

            quick_column_config = {
                "\u9879\u76ee": st.column_config.TextColumn("\u9879\u76ee", disabled=True, width="large"),
                "\u9879\u76ee\u5f53\u524d\u9636\u6bb5": st.column_config.SelectboxColumn("\u9879\u76ee\u5f53\u524d\u9636\u6bb5", options=STD_MILESTONES, width="small"),
                "\u5f00\u5b9a\u65f6\u95f4": st.column_config.TextColumn("\u5f00\u5b9a\u65f6\u95f4", width="small", help="\u652f\u6301 2026-05 / 26.5 / 2026 Q2 / TBD"),
                "\u9884\u8ba1\u53d1\u8d27": st.column_config.TextColumn("\u9884\u8ba1\u53d1\u8d27", width="small", help="\u652f\u6301 2026 Q2 / 2026-06 / -"),
                "\u8ddf\u5355": st.column_config.TextColumn("\u8ddf\u5355", width="small"),
            }
            if current_pm == "\u6240\u6709\u4eba":
                quick_column_config["\u8d1f\u8d23\u4eba"] = st.column_config.SelectboxColumn("\u8d1f\u8d23\u4eba", options=owner_options, width="small")

            edited_quick_df = st.data_editor(
                quick_edit_df,
                width='stretch',
                hide_index=True,
                num_rows="fixed",
                column_config=quick_column_config,
                disabled=["\u9879\u76ee"],
                key="dashboard_quick_editor",
            )

            if st.button("\U0001f4be \u4fdd\u5b58\u5927\u76d8\u5feb\u901f\u7f16\u8f91", type="primary", key="btn_dash_quick_save"):
                changed_projects = []
                changed_count = 0
                today_str = str(datetime.date.today())

                def _normalize_target_text(v):
                    s = str(v or "").strip()
                    if s.upper() == "TBD" or s in ["-", "\u2014", "\u65e0", "\u6682\u65e0"]:
                        return ""
                    return s

                def _normalize_ship_text(v):
                    s = str(v or "").strip()
                    if s.upper() == "TBD" or s in ["-", "\u2014", "\u65e0", "\u6682\u65e0"]:
                        return ""
                    return s

                for row in edited_quick_df.to_dict("records"):
                    proj = str(row.get("\u9879\u76ee", "")).strip()
                    if not proj or proj not in db or proj == "\u7cfb\u7edf\u914d\u7f6e":
                        continue

                    proj_data = db[proj]
                    old_pm = str(proj_data.get("\u8d1f\u8d23\u4eba", "")).strip()
                    old_ms = str(proj_data.get("Milestone", "")).strip() or "\u5f85\u7acb\u9879"
                    old_target_raw = str(proj_data.get("Target", "")).strip()
                    old_ship_raw = str(proj_data.get("\u53d1\u8d27\u533a\u95f4", "")).strip()
                    old_gd = str(proj_data.get("\u8ddf\u5355", "")).strip()

                    new_pm = old_pm
                    if current_pm == "\u6240\u6709\u4eba":
                        new_pm = str(row.get("\u8d1f\u8d23\u4eba", old_pm)).strip() or old_pm
                    new_ms = str(row.get("\u9879\u76ee\u5f53\u524d\u9636\u6bb5", old_ms)).strip() or old_ms
                    if new_ms not in STD_MILESTONES:
                        new_ms = old_ms
                    old_target_norm = _normalize_target_text(old_target_raw)
                    old_ship_norm = _normalize_ship_text(old_ship_raw)
                    new_target_norm = _normalize_target_text(row.get("\u5f00\u5b9a\u65f6\u95f4", old_target_raw))
                    new_ship_norm = _normalize_ship_text(row.get("\u9884\u8ba1\u53d1\u8d27", old_ship_raw))
                    new_gd = str(row.get("\u8ddf\u5355", old_gd)).strip()

                    change_items = []
                    if current_pm == "\u6240\u6709\u4eba" and old_pm != new_pm:
                        change_items.append(("\u8d1f\u8d23\u4eba", old_pm or "\u672a\u5206\u914d", new_pm or "\u672a\u5206\u914d"))
                    if old_ms != new_ms:
                        change_items.append(("\u9636\u6bb5", old_ms or "-", new_ms or "-"))
                    if old_target_norm != new_target_norm:
                        change_items.append(("\u5f00\u5b9a", old_target_norm or "TBD", new_target_norm or "TBD"))
                    if old_ship_norm != new_ship_norm:
                        change_items.append(("\u53d1\u8d27", old_ship_norm or "-", new_ship_norm or "-"))
                    if old_gd != new_gd:
                        change_items.append(("\u8ddf\u5355", old_gd or "-", new_gd or "-"))
                    if not change_items:
                        continue

                    proj_data["\u8d1f\u8d23\u4eba"] = new_pm
                    proj_data["Milestone"] = new_ms
                    proj_data["Target"] = new_target_norm or "TBD"
                    proj_data["\u53d1\u8d27\u533a\u95f4"] = new_ship_norm
                    proj_data["\u8ddf\u5355"] = new_gd

                    comps = proj_data.setdefault("\u90e8\u4ef6\u5217\u8868", {})
                    global_key = next((k for k in comps.keys() if "\u5168\u5c40" in str(k)), "\u5168\u5c40\u8fdb\u5ea6")
                    if global_key not in comps or not isinstance(comps.get(global_key), dict):
                        comps[global_key] = {"\u4e3b\u6d41\u7a0b": STAGES_UNIFIED[0], "\u65e5\u5fd7\u6d41": []}
                    event_text = " | ".join([f"{k}:{ov}->{nv}" for k, ov, nv in change_items])
                    comps[global_key].setdefault("\u65e5\u5fd7\u6d41", []).append({
                        "\u65e5\u671f": today_str,
                        "\u6d41\u8f6c": "\u5927\u76d8\u5feb\u7f16",
                        "\u5de5\u5e8f": comps[global_key].get("\u4e3b\u6d41\u7a0b", STAGES_UNIFIED[0]),
                        "\u4e8b\u4ef6": f"[\u5927\u76d8\u5feb\u7f16] {event_text}",
                    })
                    changed_projects.append(proj)
                    changed_count += 1

                if changed_count <= 0:
                    st.info("\u672a\u68c0\u6d4b\u5230\u53ef\u4fdd\u5b58\u7684\u53d8\u5316\u3002")
                else:
                    for proj in list(dict.fromkeys(changed_projects)):
                        sync_save_db(proj)
                    st.success(f"\u5df2\u4fdd\u5b58 {changed_count} \u6761\u9879\u76ee\u66f4\u65b0\u3002")
                    st.rerun()


        with st.container(border=True):
            st.markdown("##### \U0001f5c2 \u4e8b\u4ef6\u660e\u7ec6\u5feb\u6539")
            st.caption("\u6309\u9879\u76ee\u7b5b\u9009\u65e5\u5fd7\u540e\uff0c\u53ef\u76f4\u63a5\u4fee\u6539\u65e5\u671f/\u5de5\u5e8f/\u4e8b\u4ef6/\u63d0\u5ba1\u5b57\u6bb5\uff0c\u4fdd\u5b58\u540e\u5404\u6a21\u5757\u540c\u6b65\u751f\u6548\u3002")
            ef1, ef2, ef3 = st.columns([2.0, 2.2, 1.0])
            with ef1:
                event_proj_scope = st.selectbox("\u9879\u76ee\u8303\u56f4", ["\U0001f310 \u5168\u90e8\u9879\u76ee"] + dashboard_project_order, key="dash_event_scope")
            with ef2:
                event_kw = st.text_input("\u5173\u952e\u5b57\u7b5b\u9009\uff08\u9879\u76ee/\u90e8\u4ef6/\u4e8b\u4ef6\uff09", key="dash_event_kw", placeholder="\u4f8b\uff1a\u91cc\u592b / \u6253\u56de / \u5305\u88c5")
            with ef3:
                event_limit = int(st.number_input("\u663e\u793a\u6761\u6570", min_value=20, max_value=500, value=120, step=20, key="dash_event_limit"))

            scope_projects = dashboard_project_order if event_proj_scope == "\U0001f310 \u5168\u90e8\u9879\u76ee" else [event_proj_scope]
            event_rows = []
            id_seeded = False
            for p_name in scope_projects:
                for c_name, c_info in db.get(p_name, {}).get("\u90e8\u4ef6\u5217\u8868", {}).items():
                    for lg in c_info.get("\u65e5\u5fd7\u6d41", []):
                        if is_hidden_system_log(lg):
                            continue
                        if not str(lg.get("_id", "")).strip():
                            lg["_id"] = str(uuid.uuid4())
                            id_seeded = True
                        event_rows.append({
                            "_id": str(lg.get("_id", "")).strip(),
                            "\u65e5\u671f": str(lg.get("\u65e5\u671f", "")),
                            "\u9879\u76ee": p_name,
                            "\u90e8\u4ef6": c_name,
                            "\u5de5\u5e8f": str(lg.get("\u5de5\u5e8f", "")),
                            "\u7c7b\u578b": str(lg.get("\u6d41\u8f6c", "")),
                            "\u4e8b\u4ef6": str(lg.get("\u4e8b\u4ef6", "")),
                            "\u63d0\u5ba1\u7c7b\u578b": str(lg.get("\u63d0\u5ba1\u7c7b\u578b", "(\u65e0)") or "(\u65e0)"),
                            "\u63d0\u5ba1\u7ed3\u679c": str(lg.get("\u63d0\u5ba1\u7ed3\u679c", "(\u65e0)") or "(\u65e0)"),
                            "\u63d0\u5ba1\u8f6e\u6b21": normalize_review_round(lg.get("\u63d0\u5ba1\u8f6e\u6b21", "")),
                            "\u5220\u9664": False,
                        })

            if id_seeded and scope_projects:
                for p_name in scope_projects:
                    if p_name in db:
                        sync_save_db(p_name)

            def _event_sort_key(r):
                d = str((r or {}).get("\u65e5\u671f", "")).strip()
                try:
                    d_obj = datetime.datetime.strptime(d, "%Y-%m-%d").date()
                except Exception:
                    d_obj = datetime.date.min
                proj_name = str((r or {}).get("\u9879\u76ee", "")).strip()
                proj_rank = project_rank_map.get(proj_name, 999999)
                return (
                    proj_rank,
                    -d_obj.toordinal(),
                    str((r or {}).get("\u90e8\u4ef6", "")),
                    str((r or {}).get("\u4e8b\u4ef6", "")),
                )

            event_rows = sorted(event_rows, key=_event_sort_key)
            kw_norm = norm_text(event_kw)
            if kw_norm:
                event_rows = [
                    r for r in event_rows
                    if kw_norm in norm_text(
                        f"{r.get('\\u9879\\u76ee','')} {r.get('\\u90e8\\u4ef6','')} {r.get('\\u5de5\\u5e8f','')} {r.get('\\u4e8b\\u4ef6','')}"
                    )
                ]

            edited_event_df = pd.DataFrame()
            if event_rows:
                event_df = pd.DataFrame(event_rows[:event_limit])
                edited_event_df = st.data_editor(
                    event_df,
                    width='stretch',
                    hide_index=True,
                    num_rows="fixed",
                    column_config={
                        "_id": st.column_config.TextColumn("_id", disabled=True, width="small"),
                        "\u65e5\u671f": st.column_config.TextColumn("\u65e5\u671f", width="small"),
                        "\u9879\u76ee": st.column_config.TextColumn("\u9879\u76ee", disabled=True, width="medium"),
                        "\u90e8\u4ef6": st.column_config.TextColumn("\u90e8\u4ef6", disabled=True, width="medium"),
                        "\u5de5\u5e8f": st.column_config.SelectboxColumn("\u5de5\u5e8f", options=STAGES_UNIFIED, width="medium"),
                        "\u7c7b\u578b": st.column_config.TextColumn("\u7c7b\u578b", width="small"),
                        "\u4e8b\u4ef6": st.column_config.TextColumn("\u4e8b\u4ef6", width="large"),
                        "\u63d0\u5ba1\u7c7b\u578b": st.column_config.SelectboxColumn("\u63d0\u5ba1\u7c7b\u578b", options=REVIEW_TYPE_OPTIONS, width="small"),
                        "\u63d0\u5ba1\u7ed3\u679c": st.column_config.SelectboxColumn("\u63d0\u5ba1\u7ed3\u679c", options=REVIEW_RESULT_OPTIONS, width="small"),
                        "\u63d0\u5ba1\u8f6e\u6b21": st.column_config.NumberColumn("\u63d0\u5ba1\u8f6e\u6b21", min_value=1, step=1, width="small"),
                        "\u5220\u9664": st.column_config.CheckboxColumn("\u5220\u9664", width="small"),
                    },
                    disabled=["_id", "\u9879\u76ee", "\u90e8\u4ef6"],
                    key="dashboard_event_editor",
                )
            else:
                st.info("\u5f53\u524d\u7b5b\u9009\u6761\u4ef6\u4e0b\u65e0\u4e8b\u4ef6\u660e\u7ec6\u3002")

            if st.button("\U0001f4be \u4fdd\u5b58\u4e8b\u4ef6\u660e\u7ec6\u4fee\u6539", type="primary", key="btn_dash_event_save"):
                if edited_event_df.empty:
                    st.info("\u5f53\u524d\u6ca1\u6709\u53ef\u4fdd\u5b58\u7684\u4e8b\u4ef6\u884c\u3002")
                else:
                    id_map = {}
                    for p_name in valid_projs:
                        for c_name, c_info in db.get(p_name, {}).get("\u90e8\u4ef6\u5217\u8868", {}).items():
                            for lg in c_info.get("\u65e5\u5fd7\u6d41", []):
                                rid = str(lg.get("_id", "")).strip()
                                if rid:
                                    id_map[rid] = (p_name, c_name, lg)

                    touched_projects = set()
                    update_count = 0
                    delete_count = 0

                    for row in edited_event_df.to_dict("records"):
                        rid = str(row.get("_id", "")).strip()
                        bind = id_map.get(rid)
                        if not bind:
                            continue
                        p_name, c_name, lg = bind

                        if bool(row.get("\u5220\u9664", False)):
                            lg["_delete_me"] = True
                            touched_projects.add(p_name)
                            delete_count += 1
                            continue

                        new_date = str(row.get("\u65e5\u671f", lg.get("\u65e5\u671f", ""))).strip() or str(lg.get("\u65e5\u671f", ""))
                        new_stage = str(row.get("\u5de5\u5e8f", lg.get("\u5de5\u5e8f", ""))).strip() or str(lg.get("\u5de5\u5e8f", ""))
                        if new_stage not in STAGES_UNIFIED:
                            new_stage = str(lg.get("\u5de5\u5e8f", ""))
                        new_type = str(row.get("\u7c7b\u578b", lg.get("\u6d41\u8f6c", ""))).strip() or str(lg.get("\u6d41\u8f6c", ""))
                        new_event = str(row.get("\u4e8b\u4ef6", lg.get("\u4e8b\u4ef6", ""))).strip() or str(lg.get("\u4e8b\u4ef6", ""))
                        new_rt = str(row.get("\u63d0\u5ba1\u7c7b\u578b", lg.get("\u63d0\u5ba1\u7c7b\u578b", "(\u65e0)"))).strip() or "(\u65e0)"
                        if new_rt not in REVIEW_TYPE_OPTIONS:
                            new_rt = "(\u65e0)"
                        new_rr = str(row.get("\u63d0\u5ba1\u7ed3\u679c", lg.get("\u63d0\u5ba1\u7ed3\u679c", "(\u65e0)"))).strip() or "(\u65e0)"
                        if new_rr not in REVIEW_RESULT_OPTIONS:
                            new_rr = "(\u65e0)"
                        new_round = normalize_review_round(row.get("\u63d0\u5ba1\u8f6e\u6b21", lg.get("\u63d0\u5ba1\u8f6e\u6b21", ""))) if new_rt != "(\u65e0)" else ""

                        changed = False
                        if str(lg.get("\u65e5\u671f", "")) != new_date:
                            lg["\u65e5\u671f"] = new_date
                            changed = True
                        if str(lg.get("\u5de5\u5e8f", "")) != new_stage:
                            lg["\u5de5\u5e8f"] = new_stage
                            changed = True
                        if str(lg.get("\u6d41\u8f6c", "")) != new_type:
                            lg["\u6d41\u8f6c"] = new_type
                            changed = True
                        if str(lg.get("\u4e8b\u4ef6", "")) != new_event:
                            lg["\u4e8b\u4ef6"] = new_event
                            changed = True
                        if str(lg.get("\u63d0\u5ba1\u7c7b\u578b", "(\u65e0)")) != new_rt:
                            lg["\u63d0\u5ba1\u7c7b\u578b"] = new_rt
                            changed = True
                        if str(lg.get("\u63d0\u5ba1\u7ed3\u679c", "(\u65e0)")) != new_rr:
                            lg["\u63d0\u5ba1\u7ed3\u679c"] = new_rr
                            changed = True
                        if str(lg.get("\u63d0\u5ba1\u8f6e\u6b21", "")) != str(new_round):
                            lg["\u63d0\u5ba1\u8f6e\u6b21"] = new_round
                            changed = True

                        if changed:
                            touched_projects.add(p_name)
                            update_count += 1

                    if touched_projects:
                        for p_name in list(touched_projects):
                            for c_name, c_info in db.get(p_name, {}).get("\u90e8\u4ef6\u5217\u8868", {}).items():
                                logs = []
                                for lg in c_info.get("\u65e5\u5fd7\u6d41", []):
                                    if lg.get("_delete_me"):
                                        continue
                                    lg.pop("_delete_me", None)
                                    logs.append(lg)
                                logs = sorted(logs, key=lambda x: str(x.get("\u65e5\u671f", "")))
                                c_info["\u65e5\u5fd7\u6d41"] = logs
                            sync_save_db(p_name)
                        st.success(f"\u4e8b\u4ef6\u5df2\u4fdd\u5b58\uff1a\u66f4\u65b0 {update_count} \u6761\uff0c\u5220\u9664 {delete_count} \u6761\u3002")
                        st.rerun()
                    else:
                        st.info("\u672a\u68c0\u6d4b\u5230\u9700\u8981\u4fdd\u5b58\u7684\u4e8b\u4ef6\u53d8\u66f4\u3002")

        def _hl_warn(v):
            return 'background-color: #fef08a; color: #111827; font-weight: 600' if str(v).strip() else ''

        st.caption("提示：开定/发货 +5 天临期会高亮黄色，仍可点击表头二次排序。")
        st.dataframe(
            show_df.style.map(_hl_warn, subset=["开定延迟预警", "发货延迟预警"]),
            width='stretch'
        )

        with st.expander("只读预览（含临期预警样式）", expanded=False):
            preview_warn_cols = [c for c in ["开定延迟预警", "发货延迟预警"] if c in show_df.columns]
            styled_preview = show_df.style
            if preview_warn_cols:
                styled_preview = styled_preview.map(_hl_warn, subset=preview_warn_cols)
            st.dataframe(
                styled_preview,
                width='stretch'
            )
    st.divider()
    if project_person_roles:
        df_ppr   = pd.DataFrame(list(project_person_roles), columns=["项目", "人员", "职务"])
        df_owner = df_ppr.groupby(["人员", "职务"]).size().reset_index(name='积压项目数')
        df_owner["积压项目数"] = df_owner["积压项目数"].astype(int)
        fig_owner = px.bar(df_owner, x='人员', y='积压项目数', color='职务',
                           title="👤 团队&责任人 Loading", text='积压项目数')
        fig_owner.update_yaxes(dtick=1)
        st.plotly_chart(fig_owner, width='stretch')

        st.markdown("#### 📌 Function 去重项目数（进行中）")
        table_df = pd.DataFrame(table_data)
        ongoing = table_df[table_df["状态"].str.contains("研发|生产", na=False)][["项目"]]
        role_df = df_ppr.merge(ongoing, on="项目", how="inner").drop_duplicates(subset=["项目", "职务"])
        fn_stats = role_df.groupby("职务")["项目"].nunique().reset_index(name="进行中项目数")
        st.dataframe(fn_stats.sort_values(by=["进行中项目数", "职务"], ascending=[False, True]), width='stretch')

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

    todo_list = render_pm_todo_manager(valid_projs, current_pm)
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
    st.divider()
    st.subheader("🔬 项目进度透视矩阵 (并行连消追踪)")
    st.caption("颜色说明：🟩 已完成 ｜ 🟦 进行中/生产中 ｜ ⬛ 暂停前已流转 ｜ 🟨 Delay ｜ ⬜ 未流转")
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
        guan_tu_idx = STAGES_UNIFIED.index("官图") if "官图" in STAGES_UNIFIED else len(STAGES_UNIFIED)
        factory_idx = STAGES_UNIFIED.index("工厂复样(含胶件/上色等)") if "工厂复样(含胶件/上色等)" in STAGES_UNIFIED else None
        project_in_production = str(db[sel_proj].get("Milestone", "")).strip() == "生产中"
        production_start_date = get_project_production_start_date(db.get(sel_proj, {})) if project_in_production else None
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
            late_added_component = (
                project_in_production and factory_idx is not None and
                is_late_added_component(comp_name, comps.get(comp_name, {}), production_start_date, factory_idx, STAGES_UNIFIED)
            )
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

                if project_in_production and factory_idx is not None and not late_added_component:
                    if i < factory_idx and "暂停" not in stg:
                        row_vals.append(1); row_hover.append(f"{hover_base}<br>状态: ✅ 生产期前置阶段默认视作完成")
                        continue
                    if i == factory_idx:
                        row_vals.append(2); row_hover.append(f"{hover_base}<br>状态: 🚀 <b>生产中（工厂复样）</b>")
                        continue
                if cur_stage == "✅ 已完成(结束)" and stg == "✅ 已完成(结束)":
                    row_vals.append(1); row_hover.append(f"{hover_base}<br>状态: ✅ 全部结束")
                elif (stg in completed_stages) and not is_pause_stage(stg):
                    row_vals.append(1); row_hover.append(f"{hover_base}<br>状态: ✅ 已彻底完成")
                elif (real_c_idx >= guan_tu_idx and i < real_c_idx and "暂停" not in stg) or \
                     (cur_stage == "✅ 已完成(结束)"):
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
            colorscale=colorscale, zmin=0, zmax=4, showscale=False, xgap=4, ygap=4,
            text=hover_text, hoverinfo='text'
        ))
        fig_grid.update_layout(
            xaxis=dict(side='top', tickangle=-45),
            yaxis=dict(autorange='reversed', automargin=True),
            plot_bgcolor='white',
            height=max(250, len(y_labels) * 45),
            margin=dict(t=120, b=20, r=20)
        )
        st.plotly_chart(fig_grid, width='stretch')

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
            old_pm = str(db[sel_proj].get("负责人", "")).strip()
            old_ms = str(db[sel_proj].get("Milestone", "")).strip()
            old_target_raw = str(db[sel_proj].get("Target", "")).strip()
            old_ship_raw = str(db[sel_proj].get("发货区间", "")).strip()

            def _normalize_target_text(v):
                s = str(v or "").strip()
                if s.upper() == "TBD" or s in ["-", "—", "无", "暂无"]:
                    return ""
                return s

            def _normalize_ship_text(v):
                s = str(v or "").strip()
                if s.upper() == "TBD" or s in ["-", "—", "无", "暂无"]:
                    return ""
                return s

            new_pm_norm = str(new_pm).strip()
            new_ms_norm = str(new_ms).strip()
            new_target_norm = _normalize_target_text(new_target)
            new_ship_norm = _normalize_ship_text(new_ship)
            old_target_norm = _normalize_target_text(old_target_raw)
            old_ship_norm = _normalize_ship_text(old_ship_raw)

            change_items = []
            if old_pm != new_pm_norm:
                change_items.append(("负责人", old_pm or "未分配", new_pm_norm or "未分配"))
            if old_ms != new_ms_norm:
                change_items.append(("阶段", old_ms or "-", new_ms_norm or "-"))
            if old_target_norm != new_target_norm:
                change_items.append(("开定", old_target_norm or "TBD", new_target_norm or "TBD"))
            if old_ship_norm != new_ship_norm:
                change_items.append(("发货", old_ship_norm or "-", new_ship_norm or "-"))

            if not change_items:
                st.info("未检测到基础信息变化，未写入更新日志。")
            else:
                db[sel_proj]["负责人"] = new_pm_norm
                db[sel_proj]["Milestone"] = new_ms_norm
                db[sel_proj]["Target"] = new_target_norm or "TBD"
                db[sel_proj]["发货区间"] = new_ship_norm

                td = str(datetime.date.today())
                comps_list = list(db[sel_proj].get("部件列表", {}).keys())
                t_c = "全局进度" if "全局进度" in comps_list else (comps_list[0] if comps_list else "全局进度")
                if t_c not in db[sel_proj].setdefault("部件列表", {}):
                    db[sel_proj]["部件列表"][t_c] = {"主流程": STAGES_UNIFIED[0], "日志流": []}
                event_text = " | ".join([f"{k}:{ov}→{nv}" for k, ov, nv in change_items])
                db[sel_proj]["部件列表"][t_c]["日志流"].append({
                    "日期": td,
                    "流转": "系统更新",
                    "工序": db[sel_proj]["部件列表"][t_c].get("主流程", STAGES_UNIFIED[0]),
                    "事件": f"[属性更新] {event_text}"
                })
                sync_save_db(sel_proj)
                st.success("大盘基础信息已更新。")
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
                st.dataframe(df_rv, width='stretch', hide_index=True)
            else:
                st.caption("当前项目暂无提审记录。")

        with st.expander("📄 产品配置清单 (图文长图底稿)"):
            curr_link = db[sel_proj].get("配件清单链接", "")
            new_link  = st.text_input("🔗 在线文档链接 (如飞书/腾讯文档，输入即自动保存)", value=curr_link)
            if new_link != curr_link:
                db[sel_proj]["配件清单链接"] = new_link
                sync_save_db(sel_proj)
                st.rerun()

        saved_drafts = db[sel_proj].get("配件清单长图", [])
        if saved_drafts:
            st.markdown("**🖼️ 当前图文底稿画廊**")
            draft_cols = st.columns(min(len(saved_drafts), 2) or 1)
            for idx, b64_str in enumerate(saved_drafts):
                with draft_cols[idx % 2]:
                    render_image(b64_str, width='stretch')
                    if st.button("🗑️ 移除此底稿", key=f"del_draft_{sel_proj}_{idx}"):
                        saved_drafts.pop(idx)
                        db[sel_proj]["配件清单长图"] = saved_drafts
                        sync_save_db(sel_proj)
                        st.rerun()

    project_pending_todos = [
        x for x in todo_list
        if (not x.get("完成")) and str(x.get("关联项目", "")).strip() == sel_proj
    ]
    project_pending_todos = sorted(project_pending_todos, key=lambda x: (todo_due_date(x) or datetime.date.max, str(x.get("创建", ""))))

    with st.container(border=True):
        st.markdown("**🔗 当前项目待办联动**")
        if project_pending_todos:
            todo_option_ids = [str(x.get("_id", "")).strip() for x in project_pending_todos]
            todo_option_map = {str(x.get("_id", "")).strip(): x for x in project_pending_todos}
            todo_option_labels = []
            todo_label_to_id = {}
            for todo_id in todo_option_ids:
                td = todo_option_map.get(todo_id, {})
                due = todo_due_date(td)
                due_txt = due.strftime("%m/%d") if due else "无DDL"
                label = f"{str(td.get('任务', '')).strip()} ｜ {due_txt}"
                if not label.strip(" ｜"):
                    label = f"待办 [{todo_id[:4]}]"
                if label in todo_label_to_id:
                    label = f"{label} [{todo_id[:4]}]"
                todo_option_labels.append(label)
                todo_label_to_id[label] = todo_id
            pick_todo_label = st.selectbox(
                "选择一条待办带入交接表单",
                todo_option_labels,
                key=f"todo_prefill_pick_{sel_proj}"
            )
            pick_todo_id = todo_label_to_id.get(pick_todo_label, "")
            pick_todo = todo_option_map.get(pick_todo_id, {})
            c_l1, c_l2 = st.columns([1.2, 3.8])
            with c_l1:
                if st.button("↘ 带入交接表单", key=f"todo_prefill_btn_{sel_proj}", type="secondary"):
                    st.session_state.todo_handoff_prefill = infer_todo_handoff_prefill(pick_todo, sel_proj)
                    st.rerun()
            with c_l2:
                st.caption("开定识别：" + infer_todo_target_hint(pick_todo, valid_projs))
                st.caption("最近落地：" + todo_link_status_text(pick_todo))
        else:
            st.caption("当前项目暂无关联 To do；你也可以先在左侧 To do 新建后再带入。")

    st.divider()

    st.markdown("**2. 细分配件交接工作台**")
    st.caption("说明：提审是独立维度，不会自动改变主阶段；仅做一致性校验提醒。")
    fk = st.session_state.form_key
    handoff_todos = [
        x for x in db.get("系统配置", {}).get("PM_TODO_LIST", [])
        if (not x.get("完成")) and todo_visible_for_view(x, current_pm) and str(x.get("关联项目", "")).strip() == sel_proj
    ]
    handoff_todo_map = {str(x.get("_id", "")).strip(): x for x in handoff_todos}
    prefill = st.session_state.get("todo_handoff_prefill")
    if prefill and str(prefill.get("项目", "")).strip() == sel_proj:
        if prefill.get("部件"):
            st.session_state[f"ms_{fk}"] = prefill.get("部件")
        if prefill.get("阶段") in STAGES_UNIFIED:
            st.session_state[f"stg_{fk}"] = prefill.get("阶段")
        if prefill.get("内容"):
            st.session_state[f"txt_{fk}"] = prefill.get("内容")
        if prefill.get("todo_ids"):
            st.session_state[f"todo_link_prefill_ids_{fk}"] = [
                str(tid).strip() for tid in prefill.get("todo_ids", []) if str(tid).strip()
            ]
            st.session_state[f"todo_auto_done_{fk}"] = True
        st.session_state.todo_handoff_prefill = None
    prefill_ids = [
        tid for tid in st.session_state.get(f"todo_link_prefill_ids_{fk}", [])
        if tid in handoff_todo_map
    ]
    st.session_state[f"todo_link_prefill_ids_{fk}"] = prefill_ids

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

        st.markdown("**(3.5) 关联 To do**")
        todo_link_labels = []
        todo_link_label_to_id = {}
        todo_link_id_to_label = {}
        for todo_id, td_obj in handoff_todo_map.items():
            todo_due = todo_due_date(td_obj)
            due_txt = todo_due.strftime("%m/%d") if todo_due else "无DDL"
            label = f"{str(td_obj.get('任务', '')).strip()} ｜ {due_txt}"
            if not label.strip(" ｜"):
                label = f"待办 [{todo_id[:4]}]"
            if label in todo_link_label_to_id:
                label = f"{label} [{todo_id[:4]}]"
            todo_link_labels.append(label)
            todo_link_label_to_id[label] = todo_id
            todo_link_id_to_label[todo_id] = label

        prefill_link_ids = [
            tid for tid in st.session_state.get(f"todo_link_prefill_ids_{fk}", [])
            if tid in todo_link_id_to_label
        ]
        if prefill_link_ids:
            st.session_state[f"todo_link_labels_{fk}"] = [todo_link_id_to_label[tid] for tid in prefill_link_ids]
            st.session_state[f"todo_link_prefill_ids_{fk}"] = []
        default_todo_labels = [
            label for label in st.session_state.get(f"todo_link_labels_{fk}", [])
            if label in todo_link_label_to_id
        ]
        linked_todo_labels = st.multiselect(
            "本次记录关联哪些待办",
            options=todo_link_labels,
            default=default_todo_labels,
            key=f"todo_link_labels_{fk}"
        )
        linked_todo_ids = [todo_link_label_to_id[label] for label in linked_todo_labels if label in todo_link_label_to_id]
        todo_auto_done = st.checkbox(
            "保存交接后自动完成所关联 To do",
            value=bool(st.session_state.get(f"todo_auto_done_{fk}", False) or linked_todo_ids),
            key=f"todo_auto_done_{fk}"
        )

        st.markdown("**(4) 参考图 (支持连按 Ctrl+V 缓存)**")
        try:
            from streamlit_paste_button import paste_image_button
            paste_result = paste_image_button(
                "📋 粘贴截图",
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
                            st.image(img_info["data"], width='stretch')
                        else:
                            img_info["data"].seek(0)
                            st.image(img_info["data"], width='stretch')
                        if st.button("🗑️ 移除", key=f"del_{img_info['id']}_{idx}",
                                     width='stretch', type="primary"):
                            st.session_state.exclude_imgs.add(img_info["id"])
                            st.rerun()
    
            st.markdown("---")
            is_completed = st.checkbox(
                f"✅ 标记所选部件的【{new_stage}】阶段已彻底完成 (矩阵变绿)",
                value=False, key=f"comp_{fk}"
            )
            force_submit_detail = st.checkbox("⚠️ 强制提交（忽略阶段/提审 warning）", value=False, key=f"force_detail_{fk}")
    
            if st.button("🚀 批量保存交接与进度", type="primary", width='stretch'):
                if "➕ 新增细分配件..." in comps_to_process and not new_comp_name:
                    st.error("❌ 新增名称为空！")
                else:
                    new_owner_final = ", ".join([f"{k}-{v}" for k, v in role_vals.items() if v])
                    img_ref_list    = []
                    for img_info in preview_imgs:
                        if img_info["type"] == "paste":
                            img_ref = save_image_ref_data(img_info["data"], filename=f"detail_{img_info['id']}.jpg", prefix="detail")
                        else:
                            img_ref = save_uploaded_file_ref(img_info["data"], prefix="detail")
                        if img_ref:
                            img_ref_list.append(img_ref)
    
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
                        todo_names_for_log = [
                            str(handoff_todo_map.get(str(tid).strip(), {}).get("任务", "")).strip()
                            for tid in linked_todo_ids
                            if str(handoff_todo_map.get(str(tid).strip(), {}).get("任务", "")).strip()
                        ]
                        if todo_names_for_log:
                            base_log += " [关联To do] " + "；".join(todo_names_for_log[:3])

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
                                "工序": "立项", "事件": base_log, "图片": img_ref_list,
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
                                "工序": new_stage, "事件": base_log, "图片": img_ref_list,
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
    
                    linked_todo_titles = []
                    if saved_records > 0 and linked_todo_ids:
                        todo_all_cfg = db.setdefault("系统配置", {}).setdefault("PM_TODO_LIST", [])
                        todo_cfg_map = {str(x.get("_id", "")).strip(): x for x in todo_all_cfg}
                        comp_label = "、".join([
                            (new_comp_name if c == "➕ 新增细分配件..." and new_comp_name else ("全局进度" if c == "🌐 全局进度 (Overall)" else c))
                            for c in comps_to_process
                        ])
                        write_ts = datetime.datetime.now().isoformat(timespec="seconds")
                        for todo_id in linked_todo_ids:
                            td_obj = todo_cfg_map.get(str(todo_id).strip())
                            if not td_obj:
                                continue
                            td_obj["最近联动模块"] = "交接工作台"
                            td_obj["最近联动日期"] = str(detail_record_date)
                            td_obj["最近联动项目"] = sel_proj
                            td_obj["最近联动部件"] = comp_label
                            td_obj["最近联动阶段"] = new_stage
                            td_obj["最近联动写入时间"] = write_ts
                            if todo_auto_done:
                                was_done = bool(td_obj.get("完成", False))
                                td_obj["完成"] = True
                                if not was_done:
                                    td_obj["完成时间"] = str(detail_record_date)
                                    append_todo_completion_history(td_obj, detail_record_date)
                            linked_todo_titles.append(str(td_obj.get("任务", "")).strip())

                    if saved_records <= 0:
                        st.warning("未写入任何记录：请检查提审/阶段 warning，或勾选强制提交。")
                    else:
                        st.session_state.form_key    += 1
                        st.session_state.pasted_cache = {}
                        st.session_state.exclude_imgs = set()
                        sync_save_db(sel_proj)
                        if linked_todo_ids:
                            sync_save_db("系统配置")
                        todo_msg = f"；联动待办 {len(linked_todo_titles)} 条" if linked_todo_titles else ""
                        st.success(f"🎉 记录成功！本次写入 {saved_records} 条{todo_msg}。")
                        st.rerun()
        with st.expander("📦 3. 包装&入库", expanded=False):
            render_pm_packing_inventory_integrated(sel_proj)

        with st.expander("💰 4. 成本面板", expanded=False):
            render_pm_cost_integrated(sel_proj)
# ==========================================
# 模块 3：AI 速记
# ==========================================
elif menu == MENU_FASTLOG:
    st.title("📝 手机 AI 速记")
    with st.expander("🌙 每晚复盘（多项目）", expanded=True):
        render_pm_batch_fastlog_integrated(valid_projs)
    st.caption("该页面已切换为新版多项目晚间复盘入口。")
    st.stop()

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
                "拆件": "工程拆件", "官图": "官图", "开模": "开模", "模具": "开模", "试模": "开模", "大货": "大货",
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
            c1, c2, c3, c4, c5, c6, c7 = st.columns([1.2, 1, 1, 1.6, 1, 1, 1])
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
            edited_logs.append({"项目": sel_proj_ai, "部件": sel_comp, "事件": sel_event,
                                 "推测阶段": sel_stage, "新词汇": ai_kw,
                                 "提审类型": rv_type, "提审结果": rv_res})

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
                    st.image(img, width='stretch')
                    if st.button("🗑️ 移除", key=f"del_ai_{k}", width='stretch'):
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
            ai_ref_list = []
            if ai_files:
                for f in ai_files:
                    img_ref = save_uploaded_file_ref(f, prefix="ai_note")
                    if img_ref:
                        ai_ref_list.append(img_ref)
            for k, img_obj in st.session_state.ai_pasted_cache.items():
                img_ref = save_image_ref_data(img_obj, filename=f"ai_note_{k}.jpg", prefix="ai_note")
                if img_ref:
                    ai_ref_list.append(img_ref)
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
                    "提审类型": log.get("提审类型", "(无)"), "提审结果": log.get("提审结果", "(无)")
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
                        render_image(ref, width='stretch')

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
        st.dataframe(pd.DataFrame(records), width='stretch')

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
    quote_df = st.data_editor(quote_df, num_rows="dynamic", width='stretch', key=f"q_editor_{form_key}")
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
        st.dataframe(comp_df, width='stretch')

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
            st.dataframe(share_df.sort_values(by='税后总成本', ascending=False), width='stretch')

        st.divider()
        st.markdown("### 📝 动态明细管理")
        edited_df = st.data_editor(df_cost_show, num_rows="dynamic", width='stretch')
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
                rv_type = str(g["log"].get("提审类型", "(无)") or "(无)")
        rv_res = str(g["log"].get("提审结果", "(无)") or "(无)")
        if rv_type == "(无)" and rv_res == "(无)":
            rv_state = "(无)"
        elif rv_type != "(无)" and rv_res == "(无)":
            rv_state = f"{rv_type} / 待补结果"
        elif rv_type == "(无)" and rv_res != "(无)":
            rv_state = f"仅结果:{rv_res}"
        else:
            rv_state = f"{rv_type} / {rv_res}"
        flat_data.append({
            "_ids": g["_ids"], "部件": ", ".join(g["部件"]),
            "日期": g["log"]["日期"], "工序": g["log"]["工序"],
            "类型": g["log"]["流转"], "事件": g["log"]["事件"],
            "提审类型": rv_type,
            "提审结果": rv_res,
            "提审状态": rv_state,
            "提审轮次": g["log"].get("提审轮次", ""),
            "图片": all_imgs
        })

    done_todos = [
        td for td in db.get("系统配置", {}).get("PM_TODO_LIST", [])
        if td.get("完成")
        and str(td.get("关联项目", "")).strip() == sel_proj
        and str(td.get("完成时间", "")).strip()
    ]

    st.divider()
    st.subheader("✅ 已完成 To do 记录（关联此项目）")
    if done_todos:
        done_rows = []
        for td in sorted(done_todos, key=lambda x: x.get("完成时间", ""), reverse=True):
            done_rows.append({
                "完成时间": td.get("完成时间", ""),
                "任务": td.get("任务", ""),
                "CP/DDL": todo_cpddl_text(td),
                "关联人员": td.get("关联人员", ""),
                "创建": td.get("创建", ""),
            })
        st.dataframe(pd.DataFrame(done_rows), width='stretch', hide_index=True)
    else:
        st.caption("暂无已完成并关联此项目的 To do 记录。")

    if flat_data:
        df_logs = pd.DataFrame(flat_data).sort_values(by="日期", ascending=False).reset_index(drop=True)
        df_logs.insert(0, '序号', range(len(df_logs), 0, -1))
        review_ctx = df_logs["事件"].astype(str).str.contains(r"提审|过审|review|打回|驳回|退回|待反馈|2d|3d|二维|三维|实物提审|包装提审", case=False, regex=True)
        mismatch_mask = (df_logs['提审类型'].astype(str) == '(无)') & (df_logs['提审结果'].astype(str).isin(['待反馈', '通过', '打回'])) & (~review_ctx)
        mismatch_cnt = int(mismatch_mask.sum())
        if mismatch_cnt > 0:
            st.warning(f"检测到 {mismatch_cnt} 条记录疑似误判提审（无提审语义但提审结果有值）。建议改为(无)或补齐提审信息。")

        st.info("💡 下方为历史日志。直接**双击修改文字**，或选中整行后按 **Delete** 删除。")
        edited_df = st.data_editor(
            df_logs.drop(columns=["_ids", "图片"]),
            column_config={
                "序号":  st.column_config.NumberColumn(disabled=True),
                "部件":  st.column_config.TextColumn(disabled=True),
                "工序":  st.column_config.SelectboxColumn("工序", options=STAGES_UNIFIED, required=True),
                "提审类型": st.column_config.SelectboxColumn("提审类型", options=REVIEW_TYPE_OPTIONS, required=True),
                "提审结果": st.column_config.SelectboxColumn("提审结果", options=REVIEW_RESULT_OPTIONS, required=True),
                "提审状态": st.column_config.TextColumn("提审状态", disabled=True),
                "提审轮次": st.column_config.NumberColumn("提审轮次", min_value=1, step=1)
            },
            num_rows="dynamic", width='stretch'
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
                            render_image(img_b64, width='stretch')
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
                st.dataframe(alias_df, width='stretch')
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
                st.dataframe(hist_df, width='stretch')
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
    with st.expander("🧹 数据库瘦身 & Base64 图片迁移工具", expanded=True):
        target_label = "GridFS 持久附件" if get_storage_attachment_mode() == "gridfs" else "本地文件引用"
        st.warning(f"⚠️ 如果 JSON 文件很大，说明旧版 Base64 图片还留在数据库里。点击下方按钮可一键迁移到【{target_label}】。")
        json_str     = json.dumps(st.session_state.db, ensure_ascii=False)
        json_size_mb = len(json_str.encode("utf-8")) / 1024 / 1024
        b64_count = 0; file_count = 0
        for p_name, p_data in st.session_state.db.items():
            if p_name == "系统配置" or not isinstance(p_data, dict): continue
            for c_data in p_data.get("部件列表", {}).values():
                for log in c_data.get("日志流", []):
                    imgs = log.get("图片", [])
                    if isinstance(imgs, str):
                        imgs = [imgs] if imgs else []
                    for img in imgs:
                        if isinstance(img, str):
                            if is_attachment_ref(img): file_count += 1
                            elif len(img) > 100:      b64_count  += 1
            drafts = p_data.get("配件清单长图", [])
            if isinstance(drafts, str):
                drafts = [drafts] if drafts else []
            for img in drafts:
                if isinstance(img, str):
                    if is_attachment_ref(img): file_count += 1
                    elif len(img) > 100:      b64_count  += 1
        col_s1, col_s2, col_s3 = st.columns(3)
        col_s1.metric("📦 JSON 当前体积", f"{json_size_mb:.1f} MB")
        col_s2.metric("🖼️ 待迁移 Base64 图片", f"{b64_count} 张")
        col_s3.metric("✅ 已迁移附件引用", f"{file_count} 张")

        if b64_count > 0:
            if st.button(f"🚀 一键迁移：将所有 Base64 图片转存为{target_label}", type="primary"):
                migrated = 0; errors = 0
                progress = st.progress(0, text="迁移中...")
                all_refs = []
                for p_name, p_data in st.session_state.db.items():
                    if p_name == "系统配置" or not isinstance(p_data, dict): continue
                    for c_data in p_data.get("部件列表", {}).values():
                        for log in c_data.get("日志流", []):
                            imgs = log.get("图片", [])
                            if isinstance(imgs, str):
                                continue
                            for idx, img in enumerate(imgs):
                                if isinstance(img, str) and not is_attachment_ref(img) and len(img) > 100:
                                    all_refs.append((imgs, idx))
                    drafts = p_data.get("配件清单长图", [])
                    if isinstance(drafts, list):
                        for idx, img in enumerate(drafts):
                            if isinstance(img, str) and not is_attachment_ref(img) and len(img) > 100:
                                all_refs.append((drafts, idx))
                total = len(all_refs)
                for i, (container, idx) in enumerate(all_refs):
                    try:
                        b64_str = container[idx]
                        img_bytes = base64.b64decode(b64_str)
                        new_ref = save_image_ref_data(img_bytes, filename=f"migrated_{uuid.uuid4().hex}.jpg", prefix="migrated")
                        if new_ref:
                            container[idx] = new_ref
                            migrated += 1
                        else:
                            errors += 1
                    except Exception:
                        errors += 1
                    progress.progress((i + 1) / max(total, 1), text=f"迁移中... {i+1}/{max(total, 1)}")
                sync_save_db()
                new_json_str  = json.dumps(st.session_state.db, ensure_ascii=False)
                new_size_mb   = len(new_json_str.encode("utf-8")) / 1024 / 1024
                saved_mb      = json_size_mb - new_size_mb
                st.success(f"🎉 迁移完成！成功 {migrated} 张，失败 {errors} 张。JSON 从 {json_size_mb:.1f}MB → {new_size_mb:.1f}MB，节省 {saved_mb:.1f}MB！")
                st.rerun()
        else:
            st.success("✅ 数据库已是最优状态，无需迁移！")
            st.success("✅ 当前数据已是最优状态。")
            st.success("✅ 可继续使用下方图片二次压缩工具。")

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
            "1. **进入专属操作台**：点击左侧 **【🎯 PM 工作台】**，先维护 To do 再选择项目更新。\n"
            "2. **填写【基础信息】与【细分角色】**：根据进度选择更新阶段并填入成员名称。\n"
            "3. **填入进展详情**与图片。\n"
            "4. 点击最下方的批量保存按钮，**系统会在保存后全自动为你清空表单！**"
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
