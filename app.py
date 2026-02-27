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

# ==========================================
# 1. 页面基础配置与核心变量
# ==========================================
st.set_page_config(page_title="INART PM 系统", page_icon="🚀", layout="wide")

MENU_DASHBOARD = "📊 全局大盘与甘特图"
MENU_SPECIFIC  = "🎯 特定项目管控台"
MENU_FASTLOG   = "📝 手机 AI 速记"
MENU_PACKING   = "📦 包装与入库特殊领用"
MENU_COST      = "💰 专属成本台账"
MENU_HISTORY   = "🔍 历史溯源 (全局可编)"
MENU_SETTINGS  = "⚙️ 系统维护 (全局配置)"
MENU_GUIDE     = "📖 新手使用指南"

STD_MILESTONES  = ["待立项", "研发中", "暂停研发", "下模中", "生产中", "生产结束", "项目结束撒花🎉"]
HANDOFF_METHODS = ["内部正常推进", "微信", "飞书", "实物/打印件交接", "网盘链接", "当面沟通"]
STD_COSTS_LIST  = ["研发费", "模具费", "大货生产", "包装印刷", "物流运输", "外包设计", "杂项其他"]

DEFAULT_SYS_CFG = {
    "标准部件": ["头雕(表情)", "素体", "手型", "服装", "配件", "地台", "包装"],
    "标准阶段": ["立项", "建模(含打印/签样)", "涂装", "设计", "工程拆件", "手板/结构板", "官图", "工厂复样(含胶件/上色等)", "大货", "⏸️ 暂停/搁置", "✅ 已完成(结束)"],
    "宏观阶段": ["立项", "建模", "设计", "工程", "模具", "修模", "生产", "暂停", "结束"],
    "排期基线": {"立项": 7, "建模": 42, "设计": 35, "工程": 49, "模具": 28, "修模": 14, "生产": 30},
    "AI_COMP_KW":  {},
    "AI_STAGE_KW": {}
}
DEFAULT_DB = {"系统配置": DEFAULT_SYS_CFG}

# ==========================================
# 2. 核心数据层 (DAL) & 状态初始化
# ==========================================
class DatabaseManager:
    def __init__(self):
        from pymongo import MongoClient
        from pymongo.errors import PyMongoError
        self.PyMongoError = PyMongoError
        # 兼容 Streamlit Cloud 的 secrets 读取方式
        try:
            uri = st.secrets["MONGO_URI"]
        except Exception:
            uri = os.environ.get("MONGO_URI", "")
        if not uri:
            st.error("❌ 未配置 MONGO_URI，请在 Streamlit Secrets 中添加。")
            st.stop()
        # 连接 MongoDB，connectTimeoutMS 给足时间
        self.client = MongoClient(
            uri,
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
            socketTimeoutMS=10000
        )
        self.col = self.client["inart_pm"]["projects"]

    def load(self):
        try:
            docs = list(self.col.find({}, {"_id": 0}))
            if not docs:
                # 首次启动：尝试从旧 JSON 迁移
                return self._migrate_from_json()
            data = {}
            for doc in docs:
                key = doc.get("_doc_key")
                if key:
                    data[key] = doc.get("payload", {})
            return data if data else DEFAULT_DB.copy()
        except self.PyMongoError as e:
            st.error(f"数据库读取失败: {e}")
            return DEFAULT_DB.copy()

    def save(self, data):
        """全量保存（备份恢复、系统配置变更时使用）"""
        try:
            for key, value in data.items():
                self.col.replace_one(
                    {"_doc_key": key},
                    {"_doc_key": key, "payload": value},
                    upsert=True
                )
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

db_manager = DatabaseManager()  # MongoDB 版本，无需文件路径参数

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
            for fill_idx in range(max(0, curr_idx + 1), max_idx + 1):
                fill_stage = STAGES_UNIFIED[fill_idx]
                if "暂停" in fill_stage:
                    continue
                evt_txt = (f"[系统自动追踪] 因子部件到达【{max_stage}】，全局被倒逼流转"
                           if fill_idx != max_idx
                           else f"[系统自动追踪] 因子部件到达【{max_stage}】，全局对齐！")
                comps[global_key].setdefault("日志流", []).append({
                    "日期": str(datetime.date.today()), "流转": "系统自动",
                    "工序": fill_stage, "事件": evt_txt
                })
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
@st.cache_data
def get_macro_phase(detail_stage):
    s = str(detail_stage).strip()
    if "完成" in s or "结束" in s or "撒花" in s: return "结束"
    if "暂停" in s or "搁置" in s: return "暂停"
    if any(x in s for x in ["大货", "复样", "量产", "开定"]): return "生产"
    if any(x in s for x in ["拆件", "手板", "结构"]): return "工程"
    if "涂装" in s: return "生产"
    if "模具" in s: return "模具"
    if "设计" in s or "官图" in s: return "设计"
    if "建模" in s or "打印" in s: return "建模"
    if "立项" in s: return "立项"
    return "工程"

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

# ==========================================
# 4. 视图控制层
# ==========================================
st.sidebar.title("🚀 INART PM 系统")
pm_list    = ["所有人", "Mo", "越", "袁"]
current_pm = st.sidebar.selectbox("👤 视角切换", pm_list)

db          = st.session_state.db
valid_projs = [p for p, d in db.items()
               if p != "系统配置" and
               (current_pm == "所有人" or str(d.get('负责人', '')).strip() == current_pm)]

menu = st.sidebar.radio("模块导航", [
    MENU_DASHBOARD, MENU_SPECIFIC, MENU_FASTLOG,
    MENU_PACKING, MENU_COST, MENU_HISTORY,
    MENU_SETTINGS, MENU_GUIDE
])

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
                        pm_val   = str(row[col_pm]).strip()   if col_pm   and not pd.isna(row[col_pm])   else "Mo"
                        ms_val   = str(row[col_ms]).strip()   if col_ms   and not pd.isna(row[col_ms])   else "待立项"
                        tgt_val  = str(row[col_tgt]).strip()  if col_tgt  and not pd.isna(row[col_tgt])  else "TBD"
                        ship_val = str(row[col_ship]).strip() if col_ship and not pd.isna(row[col_ship]) else ""
                        gd_val   = str(row[col_gd]).strip()   if col_gd   and not pd.isna(row[col_gd])   else ""
                        if pm_val.lower()   == 'nan': pm_val   = "Mo"
                        if ms_val.lower()   == 'nan': ms_val   = "待立项"
                        if tgt_val.lower()  == 'nan': tgt_val  = "TBD"
                        if ship_val.lower() == 'nan': ship_val = ""
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

    table_data = []; gantt_data = []; project_person_roles = set()
    star_x = []; star_y = []

    gantt_cat_orders = MACRO_STAGES.copy()
    combined_color_map = {
        "立项": "#FFB84C", "建模": "#2CD3E1", "设计": "#A555EC",
        "工程": "#4D96FF", "模具": "#F47C7C", "修模": "#FF7B54",
        "生产": "#6BCB77", "暂停": "#B2B2B2", "结束": "#1A1A2E"
    }

    for proj in valid_projs:
        data = db[proj]
        # 跳过完全空白的储备项目，仅记录到表格
        if not data.get('部件列表') and not data.get('Milestone') and not data.get('Target'):
            table_data.append({
                "状态": "⚪ 未知阶段", "项目": proj, "跟单": "",
                "项目当前阶段": "待立项", "开定时间": "TBD",
                "预计发货": "-", "断更": "-", "最新全盘动态": "无数据"
            })
            continue

        gd       = data.get('跟单', '')
        ms       = data.get('Milestone', '')
        tgt      = data.get('Target', 'TBD')
        ship_itv = data.get('发货区间', '-')
        r_txt, _ = get_risk_status(ms, tgt)
        comps    = data.get('部件列表', {})

        proj_y_label = f"{proj} 📦[{ship_itv}]" if ship_itv and ship_itv != '-' else proj

        if not comps:
            table_data.append({
                "状态": r_txt, "项目": proj, "跟单": gd,
                "项目当前阶段": ms, "开定时间": tgt,
                "预计发货": ship_itv, "断更": "-", "最新全盘动态": "无数据"
            })
        else:
            latest_date_obj = None; latest_event_str = "无数据"; latest_comp_name = "-"
            grouped_logs_dict = {}

            for c_name, info in comps.items():
                owner_str = str(info.get('负责人', '')).strip()
                for pair in re.split(r'[,，|]', owner_str):
                    pair = pair.strip()
                    if not pair or pair == '未分配': continue
                    if '-' in pair:
                        r_part, p_part = pair.split('-', 1)
                        project_person_roles.add((proj, p_part.strip(), r_part.strip()))
                    elif ':' in pair:
                        r_part, p_part = pair.split(':', 1)
                        project_person_roles.add((proj, p_part.strip(), r_part.strip()))
                    else:
                        project_person_roles.add((proj, pair.strip(), "综合"))

                logs = info.get('日志流', [])
                if logs:
                    try:
                        l_dt = datetime.datetime.strptime(logs[-1]['日期'], "%Y-%m-%d").date()
                        if latest_date_obj is None or l_dt > latest_date_obj:
                            latest_date_obj = l_dt
                            latest_event_str = logs[-1]['事件']
                            latest_comp_name = c_name
                    except:
                        pass

                for log in logs:
                    macro_stage = get_macro_phase(log.get('工序', info.get('主流程', '未知')))
                    try:
                        dt_obj  = datetime.datetime.strptime(log['日期'], "%Y-%m-%d")
                        evt_txt = log['事件']
                        key = (dt_obj, macro_stage, evt_txt)
                        if key not in grouped_logs_dict:
                            grouped_logs_dict[key] = {
                                "日期_obj": dt_obj, "日期_str": log['日期'],
                                "工序": macro_stage, "事件": evt_txt, "部件": [c_name]
                            }
                        else:
                            if c_name not in grouped_logs_dict[key]["部件"]:
                                grouped_logs_dict[key]["部件"].append(c_name)
                    except:
                        pass

            dt_txt = f"{(datetime.date.today() - latest_date_obj).days} 天" if latest_date_obj else "-"
            clean_event = latest_event_str
            if "补充:" in clean_event:
                clean_event = clean_event.split("补充:")[-1].split("[系统]")[0].strip()
            elif "】" in clean_event:
                clean_event = clean_event.split("】")[-1].split("[系统]")[0].strip()

            table_data.append({
                "状态": r_txt, "项目": proj, "跟单": gd,
                "项目当前阶段": ms, "开定时间": tgt, "预计发货": ship_itv,
                "断更": dt_txt, "最新全盘动态": f"[{latest_comp_name}] {clean_event}"
            })

            try:
                if tgt and tgt.upper() != 'TBD':
                    parsed_tgt = datetime.datetime.strptime(
                        f"{tgt}-01" if len(tgt) == 7 else tgt[:10], "%Y-%m-%d"
                    )
                    star_x.append(parsed_tgt.strftime("%Y-%m-%d"))
                    star_y.append(proj_y_label)
            except:
                pass

            all_logs = list(grouped_logs_dict.values())
            if all_logs:
                all_logs.sort(key=lambda x: x["日期_obj"])
                curr_stage = all_logs[0]["工序"]
                s_dt = all_logs[0]["日期_obj"]
                cache = []
                for i, log in enumerate(all_logs):
                    c_str = ", ".join(log["部件"])
                    cache.append(f"[{log['日期_str']}] [{c_str}] {log['事件']}")
                    is_last  = (i == len(all_logs) - 1)
                    nxt_stage = all_logs[i+1]["工序"] if not is_last else None
                    if is_last or nxt_stage != curr_stage:
                        e_dt = log["日期_obj"]
                        if s_dt == e_dt:
                            e_dt += datetime.timedelta(days=1)
                        gantt_data.append({
                            "项目": proj_y_label, "工序阶段": curr_stage,
                            "Start": s_dt.strftime("%Y-%m-%d"),
                            "Finish": e_dt.strftime("%Y-%m-%d"),
                            "详情": "<br>".join([f"• {e}" for e in cache])
                        })
                        if not is_last:
                            curr_stage = nxt_stage
                            s_dt = log["日期_obj"]
                            cache = []

    st.divider()
    st.subheader("📈 全局进展甘特图")
    if gantt_data:
        df_g = pd.DataFrame(gantt_data).sort_values(by=["项目", "Start"])
        fig = px.timeline(
            df_g, x_start="Start", x_end="Finish", y="项目",
            color="工序阶段", hover_name="详情",
            category_orders={"工序阶段": gantt_cat_orders},
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
    else:
        st.warning("无足够日志数据生成甘特图。")

    st.subheader("📋 大盘状态明细表")
    if table_data:
        df_table = pd.DataFrame(table_data)
        df_table['is_finished'] = df_table['状态'].apply(lambda x: 1 if "已结案" in str(x) else 0)
        df_table['sort_date']   = df_table['开定时间'].replace({'TBD': '9999-12-31', '': '9999-12-31'})
        df_table = df_table.sort_values(by=['is_finished', 'sort_date', '项目']).drop(columns=['is_finished', 'sort_date'])
        st.dataframe(df_table, use_container_width=True)

    st.divider()
    if project_person_roles:
        df_ppr   = pd.DataFrame(list(project_person_roles), columns=["项目", "人员", "职务"])
        df_owner = df_ppr.groupby(["人员", "职务"]).size().reset_index(name='积压项目数')
        fig_owner = px.bar(df_owner, x='人员', y='积压项目数', color='职务',
                           title="👤 团队&责任人 Loading", text='积压项目数')
        st.plotly_chart(fig_owner, use_container_width=True)

# ==========================================
# 模块 2：特定项目管控台
# ==========================================
elif menu == MENU_SPECIFIC:
    st.title("🎯 特定项目专属管控台")

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
                        sync_save_db(sel_proj)
                        st.success(f"建档成功！已分配给 {new_pm}")
                        st.session_state.new_proj_mode = False
                        st.rerun()

    if not valid_projs:
        st.warning("当前视角下暂无项目。")
        st.stop()

    if 'current_proj_context' not in st.session_state:
        st.session_state.current_proj_context = valid_projs[0] if valid_projs else None
    sel_proj = st.selectbox("📌 1. 选择要透视与操作的项目 (💡支持键盘打字模糊搜索)", valid_projs)
    if sel_proj != st.session_state.current_proj_context:
        st.session_state.pasted_cache        = {}
        st.session_state.config_pasted_cache = {}
        st.session_state.exclude_imgs        = set()
        st.session_state.config_consumed_hashes = set()
        st.session_state.current_proj_context   = sel_proj

    st.divider()
    st.subheader("🔬 项目进度透视矩阵 (并行连消追踪)")
    comps = db[sel_proj].get('部件列表', {})
    if not comps:
        st.warning("暂无录入部件明细。请在下方录入。")
    else:
        z_data = []; y_labels = list(comps.keys()); y_labels_display = []; hover_text = []
        guan_tu_idx = STAGES_UNIFIED.index("官图") if "官图" in STAGES_UNIFIED else len(STAGES_UNIFIED)
        for comp_name in y_labels:
            owner_str    = comps[comp_name].get('负责人', '').strip()
            display_name = f"{comp_name} 👤 {owner_str}" if owner_str and owner_str != '未分配' else comp_name
            y_labels_display.append(display_name)
            cur_stage = comps[comp_name].get('主流程', STAGES_UNIFIED[0])
            c_idx     = STAGES_UNIFIED.index(cur_stage) if cur_stage in STAGES_UNIFIED else 0
            active_stages = set(); completed_stages = set()
            for log in comps[comp_name].get('日志流', []):
                stg = log.get('工序', ''); evt = log.get('事件', '')
                if stg in STAGES_UNIFIED:
                    active_stages.add(stg)
                    if any(k in evt for k in ["彻底完成", "OK", "通过", "完结", "结束", "撒花"]):
                        active_stages.discard(stg); completed_stages.add(stg)
            if active_stages or completed_stages:
                active_stages.discard("立项"); completed_stages.add("立项")
            if "暂停" in cur_stage:
                active_idxs = [STAGES_UNIFIED.index(s) for s in active_stages if s in STAGES_UNIFIED]
                real_c_idx  = max(active_idxs) if active_idxs else 0
            else:
                real_c_idx = c_idx
            row_vals = []; row_hover = []
            for i in range(len(STAGES_UNIFIED)):
                stg        = STAGES_UNIFIED[i]
                hover_base = f"部件: {comp_name}<br>负责人: {owner_str or '未分配'}<br>工序: {stg}"
                if cur_stage == "✅ 已完成(结束)" and stg == "✅ 已完成(结束)":
                    row_vals.append(1); row_hover.append(f"{hover_base}<br>状态: ✅ 全部结束")
                elif (real_c_idx >= guan_tu_idx and i < real_c_idx and "暂停" not in stg) or \
                     (stg in completed_stages) or (cur_stage == "✅ 已完成(结束)"):
                    row_vals.append(1); row_hover.append(f"{hover_base}<br>状态: ✅ 已彻底完成")
                elif i <= real_c_idx and "暂停" not in stg:
                    row_vals.append(2); row_hover.append(f"{hover_base}<br>状态: 🚀 <b>已流转至此/并行中</b>")
                elif stg in active_stages:
                    row_vals.append(2); row_hover.append(f"{hover_base}<br>状态: 🚀 <b>进行中/暂停停留</b>")
                else:
                    row_vals.append(0); row_hover.append(f"{hover_base}<br>状态: ⏳ 未流转")
            z_data.append(row_vals); hover_text.append(row_hover)
        colorscale = [[0.0, '#f1f5f9'], [0.33, '#f1f5f9'], [0.33, '#2ecc71'],
                      [0.66, '#2ecc71'], [0.66, '#3b82f6'], [1.0, '#3b82f6']]
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

    st.divider()
    st.subheader("🔧 进度明细与流转交接工作台")
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
    fk = st.session_state.form_key
    existing_comps = list(db[sel_proj].get('部件列表', {}).keys())
    custom_comps   = sorted([c for c in existing_comps if c not in STD_COMPONENTS and "全局" not in c])
    all_comps      = ["➕ 新增细分配件...", "🌐 全局进度 (Overall)"] + STD_COMPONENTS + custom_comps

    with st.container(border=True):
        st.markdown("**(1) 基础流转信息**")
        c1, c2, c3, c4 = st.columns(4)
        with c1: selected_comps_raw = st.multiselect("操作部件", all_comps, default=[], key=f"ms_{fk}")
        with c2: evt_type  = st.selectbox("记录类型", ["🔄 内部进展/正常流转", "⬅️ 收到反馈/被打回"], key=f"evt_{fk}")
        with c3: new_stage = st.selectbox("🎯 目标工序阶段", STAGES_UNIFIED, key=f"stg_{fk}")
        with c4: handoff   = st.selectbox("关联媒介", HANDOFF_METHODS, key=f"hd_{fk}")

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

                    if new_stage == "立项":
                        db[sel_proj]["部件列表"][actual_c]['日志流'].append({
                            "日期": str(detail_record_date), "流转": evt_type,
                            "工序": "立项", "事件": base_log, "图片": img_b64_list
                        })
                        db[sel_proj]["部件列表"][actual_c]['日志流'].append({
                            "日期": str(detail_record_date + datetime.timedelta(days=1)),
                            "流转": "系统自动", "工序": "建模(含打印/签样)",
                            "事件": "[系统] 立项完成自动推演"
                        })
                        db[sel_proj]["部件列表"][actual_c]['主流程'] = "建模(含打印/签样)"
                    else:
                        db[sel_proj]["部件列表"][actual_c]['日志流'].append({
                            "日期": str(detail_record_date), "流转": evt_type,
                            "工序": new_stage, "事件": base_log, "图片": img_b64_list
                        })
                        db[sel_proj]["部件列表"][actual_c]['主流程'] = new_stage

                st.session_state.form_key    += 1
                st.session_state.pasted_cache = {}
                st.session_state.exclude_imgs = set()
                sync_save_db(sel_proj)
                st.success("🎉 记录成功！")
                st.rerun()

    st.divider()
    st.subheader("⏱️ 团队效能与工时分析板")
    efficiency_data = []
    for c_name, info in db[sel_proj].get('部件列表', {}).items():
        if c_name == "全局进度": continue
        logs      = info.get('日志流', [])
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
                efficiency_data.append({"部件": c_name, "工序": stg,
                                        "耗时(天)": days_spent, "参与人员": owner_str})
    if efficiency_data:
        st.dataframe(pd.DataFrame(efficiency_data), use_container_width=True)
    else:
        st.info("💡 暂无完整闭环的工时记录。勾选【彻底完成】后即可激活此工时排行榜！")

# ==========================================
# 模块 3：AI 速记
# ==========================================
elif menu == MENU_FASTLOG:
    st.title("🚀 移动端 智能速记引擎")

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

    DYNAMIC_COMP_KW  = {**COMP_KW,  **SYS_CFG.get("AI_COMP_KW",  {})}
    DYNAMIC_STAGE_KW = {**STAGE_KW, **SYS_CFG.get("AI_STAGE_KW", {})}

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

                if not candidates: return None
                candidates.sort(key=lambda x: (x[0], -x[1]))
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
                        resolved.append(proj or "未知/请手动修改")
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
                if not proj_parts: proj_parts = ["未知/请手动修改"]
                raw_content = "&".join(content_parts)
                contents = [c.strip() for c in re.split(r'[;；]', raw_content) if c.strip()] or [raw_content or "(无内容)"]
                return [(p, c) for p in proj_parts for c in contents]

            for line in raw_text.splitlines():
                line = line.strip()
                if not line: continue
                for proj, content in parse_line(line):
                    detected_comp  = next((comp for kw, comp in DYNAMIC_COMP_KW.items()  if kw in content), "全局进度")
                    detected_stage = next((stg  for kw, stg  in DYNAMIC_STAGE_KW.items() if kw in content), "(维持原阶段)")
                    parsed.append({"识别项目": proj, "推测部件": detected_comp,
                                   "推测阶段": detected_stage, "待写入事件": content})

            st.session_state.parsed_logs = parsed
            st.success(f"🎉 拆解完成！共识别 {len(parsed)} 条记录。")

    if st.session_state.parsed_logs:
        st.divider()
        st.subheader("👀 核对与入库")
        edited_logs     = []
        project_options = ["未知/请手动修改", "⚠️冲突: 请手动选择"] + valid_projs
        comp_options    = ["全局进度"] + STD_COMPONENTS + ["其他配件(系统自动创建)"]

        for i, item in enumerate(st.session_state.parsed_logs):
            is_unknown = item['识别项目'] in ["未知/请手动修改", "⚠️冲突: 请手动选择"]
            c1, c2, c3, c4, c5 = st.columns([1.2, 1, 1, 1.8, 1])
            with c1:
                sel_proj_ai = st.selectbox(
                    "归属项目", project_options,
                    index=project_options.index(item['识别项目']) if item['识别项目'] in project_options else 0,
                    key=f"sel_p_{i}"
                )
                # 识别失败时，显示快速新建项目入口
                if is_unknown or sel_proj_ai in ["未知/请手动修改", "⚠️冲突: 请手动选择"]:
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
                    "AI预测", options_stages,
                    index=options_stages.index(item['推测阶段']) if item.get('推测阶段') in options_stages else 0,
                    key=f"stg_{i}"
                )
            with c4:
                sel_event = st.text_input("📝 写入事件", value=item['待写入事件'], key=f"evt_{i}")
            with c5:
                ai_kw = st.text_input("🧠 提取触发新词", placeholder="如: 法杖", key=f"kw_{i}")
            edited_logs.append({"项目": sel_proj_ai, "部件": sel_comp, "事件": sel_event,
                                 "推测阶段": sel_stage, "新词汇": ai_kw})

        st.markdown("**🖼️ 附件图片**")
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
                if p not in db or "未知" in p or "冲突" in p:
                    st.error(f"跳过无效项目: {p}")
                    continue
                target_comp = log["部件"] if log["部件"] != "其他配件(系统自动创建)" else "自定义配件"
                snippet     = log.get("新词汇", "").strip()
                if snippet:
                    if target_comp != "全局进度":
                        SYS_CFG.setdefault("AI_COMP_KW", {})[snippet]  = target_comp
                        learned_count += 1
                    if log["推测阶段"] != "(维持原阶段)":
                        SYS_CFG.setdefault("AI_STAGE_KW", {})[snippet] = log["推测阶段"]
                        learned_count += 1
                if target_comp not in db[p].setdefault("部件列表", {}):
                    db[p]["部件列表"][target_comp] = {"主流程": STAGES_UNIFIED[0], "日志流": []}
                final_stage = (db[p]["部件列表"][target_comp].get("主流程", STAGES_UNIFIED[0])
                               if log["推测阶段"] == "(维持原阶段)" else log["推测阶段"])
                db[p]["部件列表"][target_comp]['日志流'].append({
                    "日期": td, "流转": "AI速记",
                    "工序": final_stage, "事件": log['事件'], "图片": ai_b64_list
                })
                db[p]["部件列表"][target_comp]["主流程"] = final_stage

            # AI速记可能涉及多个项目，逐项目保存
            changed_projs = list(set(log["项目"] for log in edited_logs
                                     if log["项目"] in db and "未知" not in log["项目"] and "冲突" not in log["项目"]))
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
    st.markdown("### 🎁 包装 Checklist")
    pack_data = db[sel_proj].get("包装专项", {})
    p11, p12, p13 = st.columns(3)
    s1 = p11.checkbox("1. 实物寄包装厂",  value=pack_data.get("实物寄厂",   False))
    s2 = p12.checkbox("2. 提供刀线",      value=pack_data.get("提供刀线",   False))
    s5 = p13.checkbox("⚖️ 3. 内部已称重", value=pack_data.get("已称重",     False))
    p21, p22, p23 = st.columns(3)
    s3 = p21.checkbox("彩盒设计完毕",     value=pack_data.get("彩盒设计",   False))
    s4 = p22.checkbox("灰箱设计完毕",     value=pack_data.get("灰箱设计",   False))
    s6 = p23.checkbox("物流箱已设计",     value=pack_data.get("物流箱设计", False))
    p31, p32, p33 = st.columns(3)
    s7 = p31.checkbox("说明书定版",       value=pack_data.get("说明书",     False))
    s8 = p32.checkbox("感谢信定版",       value=pack_data.get("感谢信",     False))
    s9 = p33.checkbox("杂项纸品",         value=pack_data.get("杂项纸品",   False))
    if st.button("💾 保存包装进度"):
        db[sel_proj]["包装专项"] = {
            "实物寄厂": s1, "提供刀线": s2, "彩盒设计": s3, "灰箱设计": s4,
            "已称重": s5, "物流箱设计": s6, "说明书": s7, "感谢信": s8, "杂项纸品": s9
        }
        sync_save_db(sel_proj)
        st.success("已存档！")
        st.rerun()

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
            log_ref_map[log["_id"]] = log
            key = (log.get("日期",""), log.get("工序",""), log.get("流转",""), log.get("事件",""))
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
                "工序":  st.column_config.SelectboxColumn("工序", options=STAGES_UNIFIED, required=True)
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
        for g_data in flat_data:
            images = g_data["图片"]
            if not isinstance(images, list):
                images = [images] if images else []
            if images:
                has_images  = True
                raw_evt     = g_data['事件']
                clean_detail = raw_evt
                if "补充:" in raw_evt:
                    clean_detail = raw_evt.split("补充:")[-1].split("[系统]")[0].strip()
                elif "】" in raw_evt:
                    clean_detail = raw_evt.split("】")[-1].split("[系统]")[0].strip()
                st.markdown(f"**📅 {g_data['日期']} | 📍 {g_data['工序']} | 🧩 {g_data['部件']}**")
                if clean_detail:
                    st.caption(f"📝 {clean_detail}")
                cols = st.columns(6)
                for i, img_b64 in enumerate(images):
                    with cols[i % 6]:
                        render_image(img_b64, use_container_width=True)
                st.markdown("---")
        if not has_images:
            st.caption("该过滤条件下暂无历史参考图片。")
    else:
        st.info("该过滤条件下暂无记录。")

# ==========================================
# 模块 7：系统维护
# ==========================================
elif menu == MENU_SETTINGS:
    st.title("⚙️ 系统维护 (全局参数与词库管理)")

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
    st.title("📖 INART PM 系统 (v48) 核心操作指南")
    st.info("👋 欢迎使用 INART 产品研发追踪管控系统！本系统专为解决制造业并行研发、跨部门协作痛点而生。")
    st.markdown("---")

    with st.expander("🎯 核心场景 1：日常进度交接与流转", expanded=True):
        st.markdown(
            "1. **进入专属操作台**：点击左侧 **【🎯 特定项目管控台】**，上方选择项目。\n"
            "2. **填写【基础信息】与【细分角色】**：根据进度选择更新阶段并填入成员名称。\n"
            "3. **填入进展详情**与图片。\n"
            "4. 点击最下方的批量保存按钮，**系统会在保存后全自动为你清空表单！**"
        )

    with st.expander("🖼️ 核心场景 2：无感压缩与免按钮粘贴", expanded=True):
        st.markdown(
            "系统内置 **无感画质压缩引擎**，原图会被瞬间压缩保证网页流畅！\n\n"
            "**原生闪电粘贴**：点击网页里的【任意空白背景处】让网页获得焦点，"
            "直接按 `Ctrl+V`，图片就会丝滑飞入虚线框！"
        )

    with st.expander("📊 核心场景 3：单轨智能进度大盘", expanded=False):
        st.markdown(
            "1. **【单轨进度条】**：按真实发生时间追踪进度。\n"
            "2. **发货区间标签**：项目名旁边会带上 `📦[2026 Q2]` 样式的标签。\n"
            "3. **🌟 亮金红星**：如果项目录入了【预计开定时间】，甘特图上会亮起金色星星！"
        )

    with st.expander("💾 核心场景 4：数据备份与恢复", expanded=False):
        st.markdown(
            "每次收工前点击左侧侧边栏的 **【💾 下载全量备份】** 按钮，将 ZIP 保存到本地。\n\n"
            "下次开工或换电脑时，通过 **【📂 上传备份以恢复】** 一键还原所有数据与图片。"
        )
