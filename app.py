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
from decimal import Decimal

# ==========================================
# 1. 页面基础配置与核心变量
# ==========================================
st.set_page_config(page_title="INART PM 系统", page_icon="🚀", layout="wide")

MENU_DASHBOARD = "📊 全局大盘与甘特图"
MENU_SPECIFIC = "🎯 特定项目管控台"  
MENU_FASTLOG = "📝 手机 AI 速记"
MENU_PACKING = "📦 包装与入库特殊领用" 
MENU_COST = "💰 专属成本台账"
MENU_HISTORY = "🔍 历史溯源 (全局可编)"

STD_MILESTONES = ["待立项", "研发中", "暂停研发", "即将进入生产", "生产中", "生产结束", "项目结束撒花🎉"]
STD_COMPONENTS = ["头雕(表情)", "素体", "手型", "服装", "配件", "地台", "包装"]
STAGES_UNIFIED = ["立项", "建模(含打印/签样)", "涂装", "设计", "工程拆件", "手板/结构板", "官图", "工厂复样(含胶件/上色等)", "大货", "✅ 已完成(结束)"]
STD_COSTS = ["模具", "生产(啤件/涂装/翻模等)", "植发", "服装", "包装纸品(说明书/盒)", "辅料&运费"]
HANDOFF_METHODS = ["内部正常推进", "微信", "飞书", "实物/打印件交接", "网盘链接", "当面沟通"]
REJECT_REASONS = ["[工程打回]壁厚不够", "[工程打回]可动破型/干涉", "[工程打回]无法拆件/开模", "[设计打回]需补纹/加深细节", "[设计打回]头身比不对", "[版权打回]不还原需重做", "其他打回"]

MACRO_STAGES = ["立项", "建模", "设计", "工程", "生产", "结束"]
MACRO_COLORS = {"立项": "#f1c40f", "建模": "#bdc3c7", "设计": "#9b59b6", "工程": "#3498db", "生产": "#e67e22", "结束": "#2ecc71"}

DEFAULT_DB = {}

# ==========================================
# 2. 核心数据访问层 (DAL) 
# ==========================================
class DatabaseManager:
    def __init__(self, file_path="tracker_data_web_v20.json"):
        self.file_path = file_path

    def load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except: pass
        return DEFAULT_DB 

    def save(self, data):
        # 🚀 架构级修复：采用原子写入防断电导致的数据清零
        dir_name = os.path.dirname(os.path.abspath(self.file_path)) or "."
        with tempfile.NamedTemporaryFile("w", delete=False, dir=dir_name, encoding="utf-8") as tmp_file:
            json.dump(data, tmp_file, ensure_ascii=False, indent=4)
            temp_name = tmp_file.name
        os.replace(temp_name, self.file_path)
        st.cache_data.clear()

db_manager = DatabaseManager()

# 🚀 架构级修复：全局状态统一初始化基座，预防 KeyError
def init_session():
    defaults = {
        'db': db_manager.load(),
        'parsed_logs': [],
        'pasted_cache': {},
        'exclude_imgs': set(),
        'new_proj_mode': False
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_session()

def auto_sync_milestone(proj_name):
    proj_data = st.session_state.db.get(proj_name)
    if not proj_data: return
    comps = proj_data.get('部件列表', {})
    if not comps: return

    # 1. 提取所有子部件的主流程，找到最高阶段的索引
    max_idx = -1
    max_stage = None

    for c_name, info in comps.items():
        if "全局" in c_name: continue  
        s = str(info.get('主流程', '')).strip()

        s_idx = -1
        for i, std_s in enumerate(STAGES_UNIFIED):
            if s == std_s or s in std_s or std_s in s:
                s_idx = i; break

        if s_idx > max_idx:
            max_idx = s_idx
            max_stage = STAGES_UNIFIED[s_idx]

    # 2. 如果找到了子部件的最高阶段，强制倒逼全局进度同步升级
    if max_idx >= 0 and max_stage:
        global_key = next((k for k in comps.keys() if "全局" in k), "全局进度")
        if global_key not in comps:
            comps[global_key] = {"主流程": STAGES_UNIFIED[0], "日志流": []}

        curr_global_stage = str(comps[global_key].get("主流程", "")).strip()

        curr_idx = -1
        for i, std_s in enumerate(STAGES_UNIFIED):
            if curr_global_stage == std_s or curr_global_stage in std_s or std_s in curr_global_stage:
                curr_idx = i; break

        # 🚀 核心逻辑：如果全局当前阶段落后（或未识别），立刻启动升级引擎！
        if curr_idx < max_idx:
            for fill_idx in range(max(0, curr_idx + 1), max_idx + 1):
                fill_stage = STAGES_UNIFIED[fill_idx]
                evt_txt = f"[系统自动追踪] 因子部件到达【{max_stage}】，全局被倒逼流转"
                if fill_idx == max_idx: evt_txt = f"[系统自动追踪] 因子部件到达【{max_stage}】，全局进度已对齐！"

                comps[global_key].setdefault("日志流", []).append({
                    "日期": str(datetime.date.today()),
                    "流转": "系统自动",
                    "工序": fill_stage,
                    "事件": evt_txt
                })
            comps[global_key]["主流程"] = max_stage

    # 3. 继续执行宏观大盘 Milestone 计算
    sub_stages = [info.get('主流程', '') for c_name, info in comps.items() if "全局" not in c_name]
    stages = sub_stages if sub_stages else [comps.get("全局进度", {}).get("主流程", "")]
    cur_ms = proj_data.get('Milestone', '')

    if all(s == "✅ 已完成(结束)" for s in stages) and stages:
        proj_data['Milestone'] = "项目结束撒花🎉"
    elif any(s in ["工厂复样(含胶件/上色等)", "大货"] for s in stages):
        if cur_ms not in ["生产结束", "项目结束撒花🎉", "暂停研发"]: proj_data['Milestone'] = "生产中"
    elif any(s in ["建模(含打印/签样)", "涂装", "设计", "工程拆件", "手板/结构板", "官图"] for s in stages):
        if cur_ms == "待立项": proj_data['Milestone'] = "研发中"

def sync_save_db():
    for p in st.session_state.db:
        auto_sync_milestone(p)

    latest_db = db_manager.load()
    for proj_name, proj_data in st.session_state.db.items():
        latest_db[proj_name] = proj_data

    db_manager.save(latest_db)
    st.session_state.db = latest_db  

# ==========================================
# 3. 业务逻辑层 (Services & Utils)
# ==========================================
@st.cache_data
def get_macro_phase(detail_stage):
    s = str(detail_stage).strip()
    if "完成" in s or "结束" in s or "撒花" in s: return "结束"
    if any(x in s for x in ["生产", "大货", "复样", "量产", "开定"]): return "生产"
    if "设计" in s: return "设计"
    if "建模" in s: return "建模"
    if "立项" in s: return "立项"
    return "工程"

def get_risk_status(milestone, target_date_str="TBD"):
    ms = str(milestone).strip()
    target_date_str = str(target_date_str).strip()
    if ms == "暂停研发": return "⏸️ 暂停研发", "normal"

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
        except: pass
    if is_finished: return "🏁 已结案", "safe"
    if ms in ["生产中", "即将进入生产"]: return "🟢 生产期", "safe"
    if "研发" in ms or ms in ["待开定", "已开定", "待立项"]: return "🟡 研发期", "warning"
    return "⚪ 未知阶段", "normal"

def parse_excel_date(val):
    val = str(val).strip()
    if val.lower() in ['nan', 'tbd', '', 'nat']: return 'TBD'
    try:
        if val.isdigit() and len(val) >= 4: return pd.to_datetime(int(val), unit='D', origin='1899-12-30').strftime('%Y-%m-%d')
        return pd.to_datetime(val).strftime('%Y-%m-%d')
    except: return val

# ==========================================
# 4. 视图控制层 (多 PM 隔离)
# ==========================================
st.sidebar.title("🚀 INART PM 系统")

pm_list = ["所有人", "Mo", "越", "袁"]
current_pm = st.sidebar.selectbox("👤 视角切换", pm_list)

if current_pm == "袁": st.sidebar.markdown("✨ **圆圆的tracker**")
elif current_pm != "所有人": st.sidebar.markdown(f"**当前项目负责人: {current_pm}**")

db = st.session_state.db
valid_projs = [p for p, d in db.items() if current_pm == "所有人" or str(d.get('负责人', '')).strip() == current_pm]

menu = st.sidebar.radio("模块导航", [MENU_DASHBOARD, MENU_SPECIFIC, MENU_FASTLOG, MENU_PACKING, MENU_COST, MENU_HISTORY])

st.sidebar.divider()
st.sidebar.markdown("### ⚙️ 数据备份与恢复")
try:
    with open("tracker_data_web_v20.json", "r", encoding="utf-8") as f:
        json_bytes = f.read()
    st.sidebar.download_button("💾 下载最新系统数据 (备份)", data=json_bytes, file_name=f"inart_pm_backup_{datetime.date.today()}.json", mime="application/json")
except:
    st.sidebar.warning("暂无数据可备份。")

restore_file = st.sidebar.file_uploader("📂 上传备份文件以恢复", type=['json'])
if restore_file is not None and st.sidebar.button("⚠️ 确认覆盖恢复数据", type="primary"):
    try:
        restored_data = json.load(restore_file)
        db_manager.save(restored_data); st.session_state.db = restored_data
        st.sidebar.success("🎉 恢复成功！请手动刷新网页！")
    except: st.sidebar.error("文件格式错误！")

# ==========================================
# 模块 1：大盘与甘特图 
# ==========================================
if menu == MENU_DASHBOARD:
    st.title(f"📊 全局大盘与甘特图 ({current_pm} 的视角)")

    table_data = []; gantt_data = []; owner_stats = []; stage_stats = []

    for proj in valid_projs:
        data = db[proj]
        gd = data.get('跟单', ''); ms = data.get('Milestone', ''); tgt = data.get('Target', 'TBD')
        r_txt, _ = get_risk_status(ms, tgt)
        comps = data.get('部件列表', {})

        if not comps:
            table_data.append({"状态": r_txt, "项目": proj, "跟单": gd, "项目当前阶段": ms, "开定时间": tgt, "断更": "-", "最新全盘动态": "无数据"})
        else:
            latest_date_obj = None; latest_event_str = "无数据"; latest_comp_name = "-"
            all_logs = []

            for c_name, info in comps.items():
                owner_str = str(info.get('负责人', '')).strip()
                stage = info.get('主流程', '未知')

                if owner_str and owner_str != '未分配':
                    individuals = [x.split(':')[-1].strip() for x in re.split(r'[,，|]', owner_str) if x.strip()]
                    owner_stats.extend([x for x in individuals if x])

                if stage and stage != '未知': stage_stats.append(stage)

                logs = info.get('日志流', [])
                if logs:
                    last_log = logs[-1]
                    try:
                        l_dt = datetime.datetime.strptime(last_log['日期'], "%Y-%m-%d").date()
                        if latest_date_obj is None or l_dt > latest_date_obj:
                            latest_date_obj = l_dt
                            latest_event_str = last_log['事件']
                            latest_comp_name = c_name
                    except: pass

                for log in logs:
                    macro_stage = get_macro_phase(log.get('工序', info.get('主流程', '未知')))
                    try: 
                        dt_obj = datetime.datetime.strptime(log['日期'], "%Y-%m-%d")
                        all_logs.append({"日期_obj": dt_obj, "日期_str": log['日期'], "工序": macro_stage, "事件": f"[{log['日期']}] [{c_name}] {log['事件']}"})
                    except: pass

            dt_txt = f"{(datetime.date.today() - latest_date_obj).days} 天" if latest_date_obj else "-"
            final_event = f"[{latest_comp_name}] {latest_event_str}" if latest_date_obj else "无数据"
            table_data.append({"状态": r_txt, "项目": proj, "跟单": gd, "项目当前阶段": ms, "开定时间": tgt, "断更": dt_txt, "最新全盘动态": final_event})

            if all_logs:
                all_logs.sort(key=lambda x: x["日期_obj"])
                curr_stage = all_logs[0]["工序"]; s_dt = all_logs[0]["日期_obj"]; cache = []
                for i, log in enumerate(all_logs):
                    cache.append(log["事件"])
                    is_last = (i == len(all_logs) - 1)
                    nxt_stage = all_logs[i+1]["工序"] if not is_last else None
                    if is_last or nxt_stage != curr_stage:
                        e_dt = log["日期_obj"]
                        if s_dt == e_dt: e_dt += datetime.timedelta(days=1)
                        gantt_data.append({"项目": proj, "工序阶段": curr_stage, "Start": s_dt.strftime("%Y-%m-%d"), "Finish": e_dt.strftime("%Y-%m-%d"), "详情": "<br>".join([f"• {e}" for e in cache])})
                        if not is_last: curr_stage = nxt_stage; s_dt = log["日期_obj"]; cache = []

    st.subheader("📥 导入研发总表 (CSV)")
    uploaded_csv = st.file_uploader("选择 CSV 文件", type=['csv'])
    if uploaded_csv is not None and st.button("开始智能合并"):
        try:
            df = pd.read_csv(uploaded_csv, dtype=str, on_bad_lines='skip')
        except UnicodeDecodeError:
            uploaded_csv.seek(0)
            df = pd.read_csv(uploaded_csv, dtype=str, on_bad_lines='skip', encoding='gbk')

        h_idx = next((i for i, row in df.iterrows() if '项目名称' in [str(x).strip() for x in row.values]), -1)
        if h_idx != -1:
            cols = pd.Series(df.iloc[h_idx].values); cols[cols.duplicated()] = cols[cols.duplicated()] + '_dup'; df.columns = cols; df = df.iloc[h_idx+1:]
            if '负责人' in df.columns:
                cnt = 0
                for _, r in df[df['负责人'].notna()].iterrows(): 
                    p_raw = str(r.get('项目名称', '')).strip()
                    if not p_raw or p_raw == 'nan': continue
                    fzr = str(r.get('负责人', '')).replace('nan', '')
                    gd = str(r.get('跟单', '')).replace('nan', '')
                    jd = str(r.get('进度', r.get('项目流程', '研发中'))).replace('nan', '研发中')
                    cd = parse_excel_date(r.get('预计出货时间', r.get('开定时间', 'TBD')))

                    if p_raw not in db: db[p_raw] = {"负责人": fzr, "跟单": gd, "Milestone": jd, "Target": cd, "部件列表": {}, "备忘录": "", "包装专项": {}, "发货数据": {"总单量":0, "批次明细":[]}, "成本数据": {}}
                    else: db[p_raw]["负责人"] = fzr; db[p_raw]["跟单"] = gd; db[p_raw]["Milestone"] = jd; db[p_raw]["Target"] = cd
                    cnt += 1
                sync_save_db(); st.success(f"同步了 {cnt} 条数据！"); st.rerun()

    st.divider(); st.subheader("📈 核心工序全景流转甘特图")
    if gantt_data:
        df_g = pd.DataFrame(gantt_data).sort_values(by="Start")
        fig = px.timeline(df_g, x_start="Start", x_end="Finish", y="项目", color="工序阶段", hover_name="详情", category_orders={"工序阶段": MACRO_STAGES}, color_discrete_map=MACRO_COLORS)
        fig.update_yaxes(autorange="reversed"); st.plotly_chart(fig, use_container_width=True)
    else: st.warning("无足够日志数据生成甘特图。")

    st.subheader("📋 大盘状态明细表")
    if table_data: 
        df_table = pd.DataFrame(table_data)
        df_table['is_finished'] = df_table['状态'].apply(lambda x: 1 if "已结案" in str(x) else 0)
        df_table['sort_date'] = df_table['开定时间'].replace({'TBD': '9999-12-31', '': '9999-12-31'})
        df_table = df_table.sort_values(by=['is_finished', 'sort_date', '项目']).drop(columns=['is_finished', 'sort_date'])
        st.dataframe(df_table, use_container_width=True)

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        if owner_stats:
            df_owner = pd.DataFrame({'人员': owner_stats}).value_counts().reset_index(name='积压任务数')
            st.plotly_chart(px.bar(df_owner, x='人员', y='积压任务数', title="👤 团队&责任人 当前负荷", color='积压任务数', color_continuous_scale='Reds'), use_container_width=True)
    with c2:
        if stage_stats:
            df_stage = pd.DataFrame({'阶段': stage_stats}).value_counts().reset_index(name='部件数')
            st.plotly_chart(px.pie(df_stage, names='阶段', values='部件数', title="🌪️ 研发环节漏斗", hole=0.4), use_container_width=True)

# ==========================================
# 🌟 模块 1.5：特定项目管控台 
# ==========================================
elif menu == MENU_SPECIFIC:
    st.title("🎯 特定项目专属管控台")

    if st.button("➕ 手动建档新项目"): st.session_state.new_proj_mode = not st.session_state.get('new_proj_mode', False)
    if st.session_state.get('new_proj_mode', False):
        with st.container(border=True):
            c_n1, c_n2, c_n3 = st.columns(3)
            with c_n1: new_p = st.text_input("新项目名称 (如: 1/6 新蝙蝠侠)")
            with c_n2: new_pm = st.selectbox("分配负责人", ["Mo", "越", "袁"], index=0)
            with c_n3: 
                st.write("")
                if st.button("✅ 确认创建", type="primary"):
                    if new_p and new_p not in db:
                        db[new_p] = {"负责人": new_pm, "跟单": "", "Milestone": "待立项", "Target": "TBD", "部件列表": {}, "包装专项": {}, "发货数据": {"总单量": 0, "批次明细": []}, "成本数据": {}}
                        sync_save_db(); st.success(f"建档成功！已分配给 {new_pm}"); st.session_state.new_proj_mode = False; st.rerun()

    if not valid_projs:
        st.warning(f"当前视角 ({current_pm}) 下暂无项目。请在左侧切换至'所有人'，或点击上方建档。")
        st.stop()

    sel_proj = st.selectbox("📌 1. 选择要透视与操作的项目 (💡支持键盘打字模糊搜索)", valid_projs)

    # ------------------------------------------
    # 图表区 1: 生命周期大盘
    # ------------------------------------------
    st.divider(); st.subheader("⏱️ 宏观生命周期耗时漏斗")
    timeline_data = []; today = datetime.date.today(); all_logs = []
    for c_name, info in db[sel_proj].get('部件列表', {}).items():
        for log in info.get('日志流', []):
            try: 
                dt_obj = datetime.datetime.strptime(log['日期'], "%Y-%m-%d").date()
                all_logs.append({"date": dt_obj, "stage": get_macro_phase(log.get('工序', '')), "event": f"[{log['日期']}] [{c_name}] {log.get('事件', '')}"})
            except: pass

    if all_logs:
        all_logs.sort(key=lambda x: x['date'])
        curr_stage = all_logs[0]['stage']; curr_start = all_logs[0]['date']; stage_events = [all_logs[0]['event']]
        project_stages = []
        for log in all_logs[1:]:
            if log['stage'] != curr_stage:
                days = max(1, (log['date'] - curr_start).days)
                project_stages.append({'项目': sel_proj, '阶段': curr_stage, '耗时': days, '详情_list': ["<br>".join([f"• {e}" for e in stage_events[-3:]])]})
                curr_stage = log['stage']; curr_start = log['date']; stage_events = [log['event']]
            else: stage_events.append(log['event'])

        project_stages.append({'项目': sel_proj, '阶段': curr_stage, '耗时': max(1, (today - curr_start).days), '详情_list': ["<br>".join([f"• {e}" for e in stage_events[-3:]])]})

        merged_stages = {}
        for item in project_stages:
            stg = item['阶段']
            if stg not in merged_stages: merged_stages[stg] = {'项目': item['项目'], '阶段': stg, '耗时': 0, '详情文本': ""}
            merged_stages[stg]['耗时'] += item['耗时']
            if item['详情_list']: merged_stages[stg]['详情文本'] += "<br>".join(item['详情_list']) + "<br>"

        total_days = sum(m['耗时'] for m in merged_stages.values())
        for stg, m_data in merged_stages.items():
            pct = (m_data['耗时'] / total_days) * 100
            m_data['标签'] = f"{stg} {m_data['耗时']}天 ({pct:.1f}%)"
            logs_split = [l for l in m_data['详情文本'].split("<br>") if l.strip()]
            m_data['Hover详情'] = "<br>".join(logs_split[-5:]) if logs_split else "无特别交互记录"
            timeline_data.append(m_data)

        fig_time = px.bar(pd.DataFrame(timeline_data), x='耗时', y='项目', color='阶段', orientation='h', barmode='stack', text='标签', custom_data=['Hover详情'], category_orders={"阶段": MACRO_STAGES}, color_discrete_map=MACRO_COLORS)
        fig_time.update_traces(textposition='inside', insidetextanchor='middle', hovertemplate="<b>%{y} | %{color}</b><br>耗时: %{x}天<br><br><b>📝 最新日志:</b><br>%{customdata[0]}<extra></extra>")
        fig_time.update_layout(xaxis_title="生命周期天数", yaxis_title=None, height=200, showlegend=True, margin=dict(t=20, b=20))
        st.plotly_chart(fig_time, use_container_width=True)
    else: st.info("暂无足够历史日志以生成时长漏斗。")

    # ------------------------------------------
    # 图表区 2: 部件透视网格 (🚀 完美并行引擎)
    # ------------------------------------------
    st.divider(); st.subheader("🔬 单项目部件级进度透视矩阵 (并行追踪)")
    comps = db[sel_proj].get('部件列表', {})
    if not comps: st.warning("暂无录入部件明细。请在下方录入。")
    else:
        z_data = []; y_labels = list(comps.keys()); hover_text = []
        guan_tu_idx = STAGES_UNIFIED.index("官图") 

        for comp_name in y_labels:
            cur_stage = comps[comp_name].get('主流程', STAGES_UNIFIED[0])
            c_idx = STAGES_UNIFIED.index(cur_stage) if cur_stage in STAGES_UNIFIED else 0

            active_stages = set()
            completed_stages = set()

            for log in comps[comp_name].get('日志流', []):
                stg = log.get('工序', '')
                evt = log.get('事件', '')
                if stg in STAGES_UNIFIED:
                    active_stages.add(stg)
                    if any(k in evt for k in ["彻底完成", "OK", "通过", "完结", "结束", "撒花"]):
                        active_stages.discard(stg)
                        completed_stages.add(stg)

            if active_stages or completed_stages:
                active_stages.discard("立项"); completed_stages.add("立项")

            row_vals = []; row_hover = []
            for i in range(len(STAGES_UNIFIED)):
                stg = STAGES_UNIFIED[i]
                is_forced_completed = (c_idx >= guan_tu_idx and i < c_idx)

                if cur_stage == "✅ 已完成(结束)" and stg == "✅ 已完成(结束)":
                    row_vals.append(1); row_hover.append(f"部件: {comp_name}<br>状态: ✅ 全部结束")
                elif is_forced_completed or (stg in completed_stages) or (cur_stage == "✅ 已完成(结束)"):
                    row_vals.append(1); row_hover.append(f"部件: {comp_name}<br>工序: {stg}<br>状态: ✅ 已完成")
                elif stg in active_stages or stg == cur_stage:
                    row_vals.append(2); row_hover.append(f"部件: {comp_name}<br>工序: {stg}<br>状态: 🚀 <b>当前进行中</b>")
                else:
                    row_vals.append(0); row_hover.append(f"部件: {comp_name}<br>工序: {stg}<br>状态: ⏳ 未流转")
            z_data.append(row_vals); hover_text.append(row_hover)

        colorscale = [[0.0, '#f1f5f9'], [0.33, '#f1f5f9'], [0.33, '#2ecc71'], [0.66, '#2ecc71'], [0.66, '#3b82f6'], [1.0, '#3b82f6']]
        fig_grid = go.Figure(data=go.Heatmap(z=z_data, x=STAGES_UNIFIED, y=y_labels, colorscale=colorscale, showscale=False, xgap=4, ygap=4, text=hover_text, hoverinfo='text'))
        fig_grid.update_layout(xaxis=dict(side='top', tickangle=-45), yaxis=dict(autorange='reversed'), plot_bgcolor='white', height=max(250, len(y_labels) * 45), margin=dict(t=120, b=20, l=20, r=20))
        st.plotly_chart(fig_grid, use_container_width=True)
        st.markdown("💡 **进度图例**: &nbsp;&nbsp; 🟩 **彻底完成** &nbsp;|&nbsp; 🟦 **并行进行中** (未标记完成的流转均视为进行中) &nbsp;|&nbsp; ⬜ **未开始**")

    # ------------------------------------------
    # 操作区: 进度明细与交接 
    # ------------------------------------------
    st.divider(); st.subheader("🔧 进度明细与流转交接工作台")

    cur_pm = db[sel_proj].get('负责人', 'Mo')
    cur_ms = db[sel_proj].get('Milestone', '')
    cur_target = db[sel_proj].get('Target', 'TBD') 

    st.markdown("**1. 全局大盘基础信息**")
    col_m1, col_m2, col_m3 = st.columns(3)
    with col_m1: new_pm = st.selectbox("👤 负责人分配", ["Mo", "越", "袁"], index=["Mo", "越", "袁"].index(cur_pm) if cur_pm in ["Mo", "越", "袁"] else 0)
    with col_m2: new_ms = st.selectbox("项目当前阶段", STD_MILESTONES, index=STD_MILESTONES.index(cur_ms) if cur_ms in STD_MILESTONES else 0)
    with col_m3: new_target = st.text_input("📅 预计开定时间", value=cur_target)

    if st.button("💾 更新大盘基础信息", type="primary", key="btn_global"): 
        db[sel_proj]['负责人'] = new_pm 
        db[sel_proj]['Milestone'] = new_ms
        db[sel_proj]['Target'] = new_target 

        td = str(datetime.date.today())
        comps_list = list(db[sel_proj].get('部件列表', {}).keys())
        t_c = "全局进度" if "全局进度" in comps_list else (comps_list[0] if comps_list else "全局进度")
        cur_macro_state = db[sel_proj].get('宏观大阶段', '立项')

        if t_c not in db[sel_proj].setdefault("部件列表", {}): db[sel_proj]["部件列表"][t_c] = {"主流程": cur_macro_state, "日志流": []}

        if new_ms == "项目结束撒花🎉" and cur_ms != "项目结束撒花🎉":
            db[sel_proj]['宏观大阶段'] = "结束"
            for c_name in db[sel_proj]["部件列表"].keys():
                db[sel_proj]["部件列表"][c_name]["主流程"] = "✅ 已完成(结束)"
                db[sel_proj]["部件列表"][c_name]['日志流'].append({"日期": td, "流转": "系统完结", "工序": "✅ 已完成(结束)", "事件": f"[系统同步] 随大盘一键完结"})
            st.success(f"已更新配置，触发一键全绿魔法！")
        else:
            db[sel_proj]["部件列表"][t_c]['日志流'].append({"日期": td, "流转": "系统更新", "工序": db[sel_proj]["部件列表"][t_c]["主流程"], "事件": f"[属性更新] 当前阶段:{new_ms} | 开定:{new_target}"})
            st.success(f"已更新大盘基础信息！")

        sync_save_db(); st.rerun()

    st.markdown("**2. 细分配件状态推进 (💡支持批量多选，历史补录，无损多图)**")

    with st.expander("📄 产品配置清单 (外部链接 / 表格纯预览)"):
        st.info("💡 **轻量化挂载**：告别格式烦恼，直接贴入在线文档链接，或上传表格留作底稿预览。")

        curr_link = db[sel_proj].get("配件清单链接", "")
        new_link = st.text_input("🔗 在线文档链接 (如飞书/腾讯文档，输入即自动保存)", value=curr_link)
        if new_link != curr_link:
            db[sel_proj]["配件清单链接"] = new_link
            sync_save_db(); st.rerun()

        if curr_link:
            st.markdown(f"👉 **[点击此处，在新标签页打开产品配置清单]({curr_link})**")

        st.divider()

        uploaded_preview = st.file_uploader("或上传 Excel/CSV 作为内部备份与纯预览", type=['csv', 'xlsx', 'xls'])
        if uploaded_preview and st.button("💾 保存表格为只读预览", type="secondary"):
            try:
                if uploaded_preview.name.endswith('.csv'):
                    try: df_p = pd.read_csv(uploaded_preview)
                    except: uploaded_preview.seek(0); df_p = pd.read_csv(uploaded_preview, encoding='gbk')
                else:
                    df_p = pd.read_excel(uploaded_preview)

                db[sel_proj]["配件清单表格"] = df_p.to_dict('records')
                sync_save_db(); st.success("✅ 表格已存为只读预览！"); st.rerun()
            except Exception as e:
                st.error(f"解析失败: {e}")

        saved_table = db[sel_proj].get("配件清单表格", [])
        if saved_table:
            st.markdown("**📊 当前挂载的清单底稿**")
            st.dataframe(pd.DataFrame(saved_table), use_container_width=True)
            if st.button("🗑️ 移除此预览表格"):
                db[sel_proj]["配件清单表格"] = []
                sync_save_db(); st.rerun()

    existing_comps = list(db[sel_proj].get('部件列表', {}).keys())
    custom_comps = sorted([c for c in existing_comps if c not in STD_COMPONENTS and "全局" not in c])
    all_comps = ["➕ 新增细分配件...", "🌐 全局进度 (Overall)"] + STD_COMPONENTS + custom_comps

    m1, m2 = st.columns([1, 2])
    with m1:
        selected_comps_raw = st.multiselect("操作部件 (💡留空则默认更新全局 Overall)", all_comps, default=[])
        comps_to_process = selected_comps_raw if selected_comps_raw else ["🌐 全局进度 (Overall)"]

        new_comp_name = ""
        if "➕ 新增细分配件..." in comps_to_process:
            with st.container(border=True):
                sub_cat = st.selectbox("所属主分类", STD_COMPONENTS)
                sub_name = st.text_input("细分名称 (如: 持枪手, 电锯)", placeholder="输入具体名称")
                new_comp_name = f"{sub_cat} - {sub_name}" if sub_name else ""

        default_stage = STAGES_UNIFIED[0]
        ref_c = comps_to_process[0]
        if ref_c == "🌐 全局进度 (Overall)": ref_c = "全局进度"
        elif ref_c == "➕ 新增细分配件...": ref_c = new_comp_name

        if ref_c in db[sel_proj].get('部件列表', {}):
            default_stage = db[sel_proj]['部件列表'][ref_c].get('主流程', STAGES_UNIFIED[0])

        new_stage = st.selectbox(f"🎯 目标工序阶段 (将批量应用)", STAGES_UNIFIED, index=STAGES_UNIFIED.index(default_stage) if default_stage in STAGES_UNIFIED else 0)

        st.markdown("**👥 细分角色分配 (💡 支持打字模糊搜索提示)**")

        all_historical_names = set()
        for p_data in db.values():
            for c_data in p_data.get('部件列表', {}).values():
                o_str = c_data.get('负责人', '')
                for pair in re.split(r'[,，|]', o_str):
                    v = pair.split(':')[-1].strip() if ':' in pair else pair.strip()
                    if v and v != '未分配': all_historical_names.add(v)

        with st.expander("🛠️ 人员词库管理 (修正错别字 / 移除人员)"):
            c_old, c_new, c_btn = st.columns([1.5, 1.5, 1])
            with c_old:
                old_name_to_fix = st.selectbox("1. 选择要修改/删除的旧名", [""] + sorted(list(all_historical_names)))
            with c_new:
                new_name_to_fix = st.text_input("2. 替换为新名 (留空则全库删除)")
            with c_btn:
                st.write("")
                if st.button("🚨 确认全局替换", type="primary") and old_name_to_fix:
                    count_fixed = 0
                    for p_data in db.values():
                        for c_data in p_data.get('部件列表', {}).values():
                            owner_str = c_data.get('负责人', '')
                            if not owner_str: continue

                            pairs = [x.strip() for x in re.split(r'[,，]', owner_str) if x.strip()]
                            new_pairs = []
                            changed = False
                            for p in pairs:
                                if ':' in p:
                                    r_part, n_part = p.split(':', 1)
                                    if n_part.strip() == old_name_to_fix:
                                        if new_name_to_fix.strip(): new_pairs.append(f"{r_part.strip()}:{new_name_to_fix.strip()}")
                                        changed = True
                                    else: new_pairs.append(p)
                                else:
                                    if p == old_name_to_fix:
                                        if new_name_to_fix.strip(): new_pairs.append(new_name_to_fix.strip())
                                        changed = True
                                    else: new_pairs.append(p)
                            if changed:
                                c_data['负责人'] = ", ".join(new_pairs)
                                count_fixed += 1
                    sync_save_db()
                    st.success(f"✅ 清洗完成！全库共修正 {count_fixed} 处记录，词库已同步更新。"); st.rerun()

        base_options = ["(留空/暂不分配)"] + sorted(list(all_historical_names)) + ["➕ 手动输入新成员..."]

        role_list = ["建模", "设计", "工程", "监修", "打印", "涂装"]
        role_vals = {}
        old_owner_str = db[sel_proj].get('部件列表', {}).get(ref_c, {}).get('负责人', '')
        old_dict = {}
        for pair in old_owner_str.split(','):
            if ':' in pair:
                k, v = pair.split(':', 1)
                old_dict[k.strip()] = v.strip()
            elif pair.strip(): old_dict["综合"] = pair.strip()

        r_cols = st.columns(3)
        for idx, r in enumerate(role_list):
            with r_cols[idx%3]:
                old_v = old_dict.get(r, "")

                temp_opts = base_options.copy()
                if old_v and old_v not in temp_opts and old_v != "(留空/暂不分配)":
                    temp_opts.insert(1, old_v)

                def_idx = temp_opts.index(old_v) if old_v in temp_opts else 0

                sel_val = st.selectbox(f"{r} (当前:{old_v or '无'})", temp_opts, index=def_idx, key=f"role_{sel_proj}_{ref_c}_{r}")

                if sel_val == "➕ 手动输入新成员...":
                    final_val = st.text_input(f"👉 输入 {r} 负责人姓名", key=f"role_new_{sel_proj}_{ref_c}_{r}")
                elif sel_val == "(留空/暂不分配)":
                    final_val = ""
                else:
                    final_val = sel_val

                role_vals[r] = final_val

        detail_record_date = st.date_input("🕒 发生日期 (支持历史补录)", datetime.date.today(), key="date_detail")

        st.markdown("**🖼️ 上传参考图 & 快捷粘贴**")

        try:
            from streamlit_paste_button import paste_image_button
            st.caption("👇 点击下方按钮，按 `Ctrl+V` (支持连续多次截图并粘贴！)")
            paste_result = paste_image_button("📋 专属剪贴板捕获区", background_color="#f1f5f9", hover_background_color="#e2e8f0")

            if paste_result is not None and hasattr(paste_result, 'image_data') and paste_result.image_data is not None:
                buffered = io.BytesIO()
                paste_result.image_data.save(buffered, format="PNG")
                img_hash = hashlib.md5(buffered.getvalue()).hexdigest()
                if img_hash not in st.session_state.pasted_cache:
                    st.session_state.pasted_cache[img_hash] = paste_result.image_data
        except ImportError:
            st.warning("💡 请确保 requirements.txt 中包含了 streamlit-paste-button")
            paste_result = None

        img_files = st.file_uploader("或传统选择文件/拖拽多图", type=['png', 'jpg', 'jpeg'], accept_multiple_files=True)

        preview_imgs = []
        if img_files:
            for f in img_files: preview_imgs.append({"type": "file", "id": f.name, "data": f})
        for h_key, img_obj in st.session_state.pasted_cache.items():
            preview_imgs.append({"type": "paste", "id": h_key, "data": img_obj})

        preview_imgs = [img for img in preview_imgs if img["id"] not in st.session_state.exclude_imgs]

        if preview_imgs:
            st.markdown("---")
            st.markdown("**👀 待上传池 (如有多余，点击下方垃圾桶剔除)**")
            p_cols = st.columns(min(len(preview_imgs), 4) or 1)
            for idx, img_info in enumerate(preview_imgs):
                with p_cols[idx % 4]:
                    if img_info["type"] == "paste": st.image(img_info["data"], use_container_width=True)
                    else:
                        img_info["data"].seek(0)
                        st.image(img_info["data"], use_container_width=True)

                    if st.button("🗑️ 移除", key=f"del_{img_info['id']}_{idx}", use_container_width=True, type="primary"):
                        st.session_state.exclude_imgs.add(img_info["id"])
                        st.rerun()

    with m2:
        col_h1, col_h2 = st.columns(2)
        with col_h1: 
            evt_type = st.selectbox("记录类型", ["🔄 内部进展/正常流转", "⬅️ 收到反馈/被打回"])
            handoff = st.selectbox("关联媒介", HANDOFF_METHODS)
        with col_h2: 
            if evt_type == "⬅️ 收到反馈/被打回": reject = st.selectbox("选择打回原因", REJECT_REASONS)
            else: reject = "正常"

        log_txt = st.text_area("补充详细进展 (如：版权释放资料等)", height=80)

        st.markdown("---")
        is_completed = st.checkbox(f"✅ 标记所选部件的【{new_stage}】阶段已彻底完成 (将在矩阵中由蓝变绿)", value=False)

        if st.button("💾 批量保存交接与进度", type="primary", key="btn_detail"):
            if "➕ 新增细分配件..." in comps_to_process and not new_comp_name: 
                st.error("❌ 新增配件名称不能为空！")
            else:
                new_owner_final = ", ".join([f"{k}:{v}" for k, v in role_vals.items() if v])

                img_b64_list = []
                for img_info in preview_imgs:
                    if img_info["type"] == "paste":
                        buffered = io.BytesIO()
                        img_info["data"].save(buffered, format="PNG")
                        img_b64_list.append(base64.b64encode(buffered.getvalue()).decode())
                    else:
                        img_info["data"].seek(0)
                        img_b64_list.append(base64.b64encode(img_info["data"].read()).decode())

                for c_raw in comps_to_process:
                    actual_c = "全局进度" if c_raw == "🌐 全局进度 (Overall)" else (new_comp_name if c_raw == "➕ 新增细分配件..." else c_raw)
                    if actual_c not in db[sel_proj].setdefault("部件列表", {}): db[sel_proj]["部件列表"][actual_c] = {"主流程": STAGES_UNIFIED[0], "日志流": []}

                    if new_owner_final: db[sel_proj]["部件列表"][actual_c]['负责人'] = new_owner_final

                    base_log = f"【{evt_type} | {handoff}】状态:{reject}。补充: {log_txt}" if log_txt else f"【{evt_type} | {handoff}】状态:{reject}。"
                    if is_completed: base_log += " [系统]该工序已标记彻底完成"

                    if new_stage == "立项":
                        log_entry_1 = {"日期": str(detail_record_date), "流转": evt_type, "工序": "立项", "事件": base_log, "图片": img_b64_list}
                        db[sel_proj]["部件列表"][actual_c]['日志流'].append(log_entry_1)
                        next_day = str(detail_record_date + datetime.timedelta(days=1))
                        db[sel_proj]["部件列表"][actual_c]['日志流'].append({"日期": next_day, "流转": "系统自动", "工序": "建模(含打印/签样)", "事件": "[系统自动] 立项完成，自动进入建模阶段"})
                        db[sel_proj]["部件列表"][actual_c]['主流程'] = "建模(含打印/签样)"
                    else:
                        log_entry = {"日期": str(detail_record_date), "流转": evt_type, "工序": new_stage, "事件": base_log, "图片": img_b64_list}
                        db[sel_proj]["部件列表"][actual_c]['日志流'].append(log_entry)
                        db[sel_proj]["部件列表"][actual_c]['主流程'] = new_stage

                st.session_state.pasted_cache = {}
                st.session_state.exclude_imgs = set()

                sync_save_db()
                st.success(f"🎉 成功为 {len(comps_to_process)} 个模块记录了进展！")
                st.rerun()

    # ------------------------------------------
    # 图表区 3: ⏱️ 团队效能与工时排行榜
    # ------------------------------------------
    st.divider(); st.subheader("⏱️ 团队效能与工时分析板")
    efficiency_data = []
    for c_name, info in db[sel_proj].get('部件列表', {}).items():
        if c_name == "全局进度": continue
        logs = info.get('日志流', [])
        owner_str = info.get('负责人', '未分配')

        stage_times = {}
        for log in logs:
            stg = log.get('工序', '')
            date_obj = datetime.datetime.strptime(log['日期'], "%Y-%m-%d").date()
            if stg not in stage_times: stage_times[stg] = {'start': date_obj, 'end': None}
            if "彻底完成" in log.get('事件', '') or "OK" in log.get('事件', ''):
                stage_times[stg]['end'] = date_obj

        for stg, times in stage_times.items():
            if times['end']:
                days_spent = max(1, (times['end'] - times['start']).days)
                efficiency_data.append({"部件": c_name, "工序": stg, "耗时(天)": days_spent, "参与人员": owner_str})

    if efficiency_data:
        df_eff = pd.DataFrame(efficiency_data)
        st.dataframe(df_eff, use_container_width=True)
    else:
        st.info("💡 暂无完整闭环的工时记录。在上方交接推进时，勾选【标记该工序已彻底完成】，即可激活此工时排行榜！")

# ==========================================
# 模块 2：AI 速记 
# ==========================================
elif menu == MENU_FASTLOG:
    st.title("🚀 移动端 AI 记录")
    global_ai_date = st.date_input("🕒 本次批量记录发生日期 (支持补录历史)", datetime.date.today())
    raw_text = st.text_area("✍️ 输入进展 (如: 里夫转交设计OK)：", height=150)

    if st.button("✨ 智能拆解并生成预览", type="primary"):
        if not raw_text.strip(): st.warning("内容不能为空！")
        else:
            lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
            parsed = []; curr_p = "未知/请手动修改"
            for line in lines:
                f_p = None; cl = line.replace('/', '').replace('*', '').strip()
                matched_projs = []
                for p in valid_projs:
                    if any(part in cl for part in p.replace('1/6', '').replace('1/12', '').replace('-', ' ').strip().split()): 
                        matched_projs.append(p)

                matched_projs = list(set(matched_projs))
                if len(matched_projs) > 1: f_p = "⚠️冲突: 请手动选择"
                elif len(matched_projs) == 1: f_p = matched_projs[0]
                else: f_p = curr_p
                if f_p != "⚠️冲突: 请手动选择": curr_p = f_p

                detected_stage = None
                if any(kw in cl for kw in ["转", "交", "进入", "开始", "发给", "给到"]):
                    for stg in STAGES_UNIFIED:
                        clean_stg = stg.split('(')[0].replace('✅ ', '')
                        if clean_stg in cl: detected_stage = stg; break

                parsed.append({"识别项目": f_p, "待写入事件": line, "AI推测阶段": detected_stage})
            st.session_state.parsed_logs = parsed; st.success("拆解完成！请在下方核对。")

    if st.session_state.parsed_logs:
        st.divider(); st.subheader("👀 核对与入库")
        edited_logs = []; project_options = ["未知/请手动修改", "⚠️冲突: 请手动选择"] + valid_projs
        for i, item in enumerate(st.session_state.parsed_logs):
            c1, c2, c3 = st.columns([1, 1.5, 1])
            with c1: sel_proj = st.selectbox(f"归属项目", project_options, index=project_options.index(item['识别项目']) if item['识别项目'] in project_options else 0, key=f"sel_{i}_{item['识别项目']}")
            with c2: sel_event = st.text_input(f"写入内容", value=item['待写入事件'], key=f"evt_{i}")
            with c3:
                options_stages = ["(维持原阶段)"] + STAGES_UNIFIED
                def_s_idx = options_stages.index(item['AI推测阶段']) if item.get('AI推测阶段') in options_stages else 0
                sel_stage = st.selectbox(f"AI阶段转入预测", options_stages, index=def_s_idx, key=f"stg_{i}")
            edited_logs.append({"项目": sel_proj, "事件": sel_event, "推测阶段": sel_stage})

        if st.button("💾 确认无误，全部入库！", type="primary"):
            td = str(global_ai_date) 
            for log in edited_logs:
                p = log['项目']
                if p not in db or "未知" in p or "冲突" in p: st.error(f"项目无效！"); st.stop()

                comps = list(db[p].get('部件列表', {}).keys())
                t_c = "全局进度" if "全局进度" in comps else (comps[0] if comps else "全局进度")
                curr_actual_stage = db[p].get("部件列表", {}).get(t_c, {}).get("主流程", STAGES_UNIFIED[0])

                final_stage = curr_actual_stage if log["推测阶段"] == "(维持原阶段)" else log["推测阶段"]
                db[p].setdefault("部件列表", {}).setdefault(t_c, {"主流程": final_stage, "日志流": []})['日志流'].append({"日期": td, "流转": "AI速记", "工序": final_stage, "事件": log['事件']})
                db[p]["部件列表"][t_c]["主流程"] = final_stage 

            sync_save_db(); st.session_state.parsed_logs = []; st.success("🎉 入库成功！矩阵图已同步点亮！"); st.rerun()

# ==========================================
# 模块 4 & 5 (保持极速稳定)
# ==========================================
elif menu == MENU_PACKING:
    st.title("📦 包装与入库特殊领用记录")
    if not valid_projs: st.stop()
    sel_proj = st.selectbox("📌 追踪项目 (💡支持打字模糊搜索)", valid_projs)

    st.divider(); st.markdown("### 📝 项目全局专属备忘录")
    memo_txt = st.text_area("记录跨部门叮嘱等杂项", value=db[sel_proj].get("备忘录", ""), height=100)
    if st.button("💾 保存备忘录"): 
        db[sel_proj]["备忘录"] = memo_txt; sync_save_db(); st.success("已保存！"); st.rerun()

    st.divider(); st.markdown("### 🎁 包装 Checklist")
    pack_data = db[sel_proj].get("包装专项", {})
    p11, p12, p13 = st.columns(3)
    s1 = p11.checkbox("1. 实物寄包装厂", value=pack_data.get("实物寄厂", False))
    s2 = p12.checkbox("2. 提供刀线", value=pack_data.get("提供刀线", False))
    s5 = p13.checkbox("⚖️ 3. 内部已称重", value=pack_data.get("已称重", False))
    p21, p22, p23 = st.columns(3)
    s3 = p21.checkbox("彩盒设计完毕", value=pack_data.get("彩盒设计", False))
    s4 = p22.checkbox("灰箱设计完毕", value=pack_data.get("灰箱设计", False))
    s6 = p23.checkbox("物流箱已设计", value=pack_data.get("物流箱设计", False))
    p31, p32, p33 = st.columns(3)
    s7 = p31.checkbox("说明书定版", value=pack_data.get("说明书", False))
    s8 = p32.checkbox("感谢信定版", value=pack_data.get("感谢信", False))
    s9 = p33.checkbox("杂项纸品", value=pack_data.get("杂项纸品", False))
    if st.button("💾 保存包装进度"):
        db[sel_proj]["包装专项"] = {"实物寄厂": s1, "提供刀线": s2, "彩盒设计": s3, "灰箱设计": s4, "已称重": s5, "物流箱设计": s6, "说明书": s7, "感谢信": s8, "杂项纸品": s9}
        sync_save_db(); st.success("已存档！"); st.rerun()

    st.divider(); st.markdown("### 🧮 工厂大货入库与内部特殊领用台账")
    inv_data = db[sel_proj].get("发货数据", {"总单量": 0, "批次明细": []})
    c1, c2 = st.columns([1, 2])
    with c1:
        total_qty = st.number_input("工厂生产总单量 (PCS)", value=int(inv_data.get("总单量", 0)), step=100)
        if st.button("保存单量"): db[sel_proj].setdefault("发货数据", {})["总单量"] = total_qty; sync_save_db(); st.rerun()
    in_a = out_a = 0; records = []
    for item in inv_data.get("批次明细", []):
        q = int(item.get('数量', 0))
        if item.get('类型') == '内部领用': out_a += q
        else: in_a += q
        records.append({"日期": item['日期'], "类型": item['类型'], "数量": q, "用途": item.get('备注', '无')})

    fac_left = total_qty - in_a; real_stock = in_a - out_a
    st.write(f"**累计已入库:** {in_a} | **内部已领用:** {out_a} | **📦 仓内可用:** {real_stock} | **🏭 工厂未交:** {fac_left}")

    with st.expander("➕ 登记新流水"):
        rc1, rc2, rc3, rc4 = st.columns(4)
        with rc1: typ = st.selectbox("类型", ["大货入库", "内部领用"])
        with rc2: q = st.number_input("数量", min_value=1, value=10)
        with rc3: note = st.text_input("用途")
        with rc4:
            st.write(""); 
            if st.button("登记"):
                db[sel_proj].setdefault("发货数据", {}).setdefault("批次明细", []).append({"日期": str(datetime.date.today()), "类型": typ, "数量": int(q), "备注": note})
                sync_save_db(); st.rerun()
    if records: st.dataframe(pd.DataFrame(records), use_container_width=True)

elif menu == MENU_COST:
    st.title("💰 纯净动态成本控制台")
    if not valid_projs: st.stop()
    sel_proj = st.selectbox("📌 核算项目 (💡支持打字模糊搜索)", valid_projs)
    c_data = db[sel_proj].get("成本数据", {})

    c1, c2, c3 = st.columns(3)
    with c1: orders = st.number_input("总订单数", value=int(c_data.get("总订单数", 0)), step=100)
    with c2: price = st.number_input("目标销售单价 (¥)", value=float(c_data.get("销售单价", 0.0)), step=100.0)
    with c3: 
        st.write(""); 
        if st.button("💾 保存基础单量"): 
            db[sel_proj].setdefault("成本数据", {})["总订单数"] = orders; db[sel_proj]["成本数据"]["销售单价"] = price
            sync_save_db(); st.success("已保存"); st.rerun()

    st.divider(); st.subheader("📥 批量导入成本明细 (CSV)")
    cost_csv = st.file_uploader("选择成本 CSV 文件", type=['csv'], key="cost_csv")
    if cost_csv and st.button("🚀 开始解析并导入", type="primary"):
        try:
            try: df_cost = pd.read_csv(cost_csv)
            except UnicodeDecodeError: cost_csv.seek(0); df_cost = pd.read_csv(cost_csv, encoding='gbk')

            col_cat = next((c for c in df_cost.columns if any(k in str(c) for k in ['分类', '项目', '名称', '类别'])), None)
            col_vendor = next((c for c in df_cost.columns if any(k in str(c) for k in ['供应商', '收款', '公司', '厂家'])), None)
            col_price = next((c for c in df_cost.columns if any(k in str(c) for k in ['单价'])), None)
            col_qty = next((c for c in df_cost.columns if any(k in str(c) for k in ['数量', '件数'])), None)
            col_amt = next((c for c in df_cost.columns if any(k in str(c) for k in ['金额', '价', '款', '费用', '总计'])), None)
            col_tax = next((c for c in df_cost.columns if '税' in str(c)), None)

            count = 0
            for _, row in df_cost.iterrows():
                if not col_amt and not col_price: continue
                if col_amt and pd.isna(row[col_amt]): continue

                cat = str(row[col_cat]) if col_cat else "未分类"
                vendor = str(row[col_vendor]) if col_vendor else "未知"
                raw_qty = float(row[col_qty]) if col_qty and not pd.isna(row[col_qty]) else 1.0

                if col_price and not pd.isna(row[col_price]):
                    raw_price = float(str(row[col_price]).replace(',', '').replace('¥', '').replace('￥', '').strip())
                    tot_after = raw_price * raw_qty
                elif col_amt:
                    tot_after = float(str(row[col_amt]).replace(',', '').replace('¥', '').replace('￥', '').strip())
                    raw_price = tot_after; raw_qty = 1.0
                else: continue

                tax_str = str(row[col_tax]).replace('%', '') if col_tax else "0"
                try: tax_rate = float(tax_str)
                except: tax_rate = 0.0

                db[sel_proj].setdefault("成本数据", {}).setdefault("动态明细", []).append({
                    "分类": cat, "供应商": vendor, "税后单价": raw_price, "数量": raw_qty,
                    "税后总成本": tot_after, "税点": f"{tax_rate}%", "税前总成本": round(tot_after / (1 + tax_rate/100), 2)
                })
                count += 1
            sync_save_db()
            if count > 0: st.success(f"🎉 导入 {count} 条明细！"); st.balloons() 
            else: st.warning("⚠️ 未能识别金额数据。")
        except Exception as e: st.error(f"解析失败: {e}")

    st.divider(); st.subheader("➕ 手动录入单笔成本明细")
    ac1, ac2, ac3, ac4, ac5 = st.columns([2, 2, 2, 1.5, 1.5])
    with ac1: c_name = st.selectbox("成本分类", STD_COSTS)
    with ac2: vendor = st.text_input("供应商", placeholder="例：志昇")
    with ac3: c_unit = st.number_input("税后单价(¥)", min_value=0.0, step=100.0)
    with ac4: c_qty = st.number_input("数量", min_value=1.0, value=1.0, step=1.0)
    with ac5: tax_rate = st.selectbox("税点(%)", [0, 1, 3, 6, 9, 13])

    if st.button("入账"):
        d_unit, d_qty = Decimal(str(c_unit)), Decimal(str(c_qty))
        tot_after = d_unit * d_qty
        tax_div = Decimal("1") + (Decimal(str(tax_rate)) / Decimal("100"))
        pre_tax = tot_after / tax_div

        db[sel_proj].setdefault("成本数据", {}).setdefault("动态明细", []).append({
            "分类": c_name, "供应商": vendor, "税后单价": float(d_unit), "数量": float(d_qty),
            "税后总成本": float(tot_after), "税点": f"{tax_rate}%", "税前总成本": float(round(pre_tax, 2))
        })
        sync_save_db(); st.rerun()

    details = c_data.get("动态明细", [])
    if details:
        for d in details:
            if '含税金额' in d and '税后总成本' not in d:
                d['税后总成本'] = d['含税金额']; d['数量'] = 1.0; d['税后单价'] = d['含税金额']
                if '税前金额' in d: d['税前总成本'] = d['税前金额']

        df_cost_show = pd.DataFrame(details)
        display_cols = ['分类', '供应商', '税后单价', '数量', '税后总成本', '税点', '税前总成本']
        df_cost_show = df_cost_show[[c for c in display_cols if c in df_cost_show.columns]]

        st.divider(); st.markdown("### 📊 各分类成本总计看板")
        subtotals = df_cost_show.groupby('分类')['税后总成本'].sum().reset_index()
        num_cols = min(len(subtotals), 6)
        if num_cols > 0:
            metric_cols = st.columns(num_cols)
            for i, row in subtotals.iterrows(): metric_cols[i % num_cols].metric(label=row['分类'], value=f"¥ {row['税后总成本']:,.2f}")

        st.divider(); st.markdown("### 📝 动态明细管理")
        edited_df = st.data_editor(df_cost_show, num_rows="dynamic", use_container_width=True)

        if st.button("💾 确认并保存表格修改", type="primary"):
            for idx, row in edited_df.iterrows():
                try:
                    qty = float(row.get('数量', 1.0)); unit = float(row.get('税后单价', 0.0))
                    tax_str = str(row.get('税点', '0%')).replace('%', ''); rate = float(tax_str) if tax_str else 0.0
                    edited_df.at[idx, '税后总成本'] = qty * unit; edited_df.at[idx, '税前总成本'] = round((qty * unit) / (1 + rate / 100), 2)
                except: pass
            db[sel_proj]["成本数据"]["动态明细"] = edited_df.to_dict('records')
            sync_save_db(); st.success("✅ 成本明细已更新！"); st.rerun()

        st.divider()
        total_c = sum(edited_df['税后总成本']) if not edited_df.empty else 0
        unit_c = total_c / orders if orders > 0 else 0
        st.info(f"**💰 累计税后总成本:** ¥{total_c:,.2f} | **单体核算成本:** ¥{unit_c:,.2f} | **单体毛利:** ¥{price - unit_c:,.2f} | **预测毛利率:** {(price - unit_c) / price * 100 if price > 0 else 0:.2f}%")

# ==========================================
# 模块 6：历史溯源 (🚀 全局交互式表格 + 智能防呆)
# ==========================================
elif menu == MENU_HISTORY:
    st.title("🔍 图文交接溯源档案 (全局/可编辑)")
    if not valid_projs: st.stop()

    sel_proj = st.selectbox("📌 选择溯源项目", valid_projs)

    for c_name, comp in db[sel_proj].get("部件列表", {}).items():
        for log in comp.get("日志流", []):
            if "_id" not in log: log["_id"] = str(uuid.uuid4()) 

    flat_data = []; log_ref_map = {}
    comps_in_proj = ["🌐 全部展示"] + list(db[sel_proj].get("部件列表", {}).keys())
    sel_comp = st.selectbox("📌 筛选特定部件 (默认全览)", comps_in_proj)

    for c_name, comp in db[sel_proj].get("部件列表", {}).items():
        if sel_comp != "🌐 全部展示" and c_name != sel_comp: continue
        for log in comp.get("日志流", []):
            log_ref_map[log["_id"]] = log
            flat_data.append({
                "_id": log["_id"], "部件": c_name, "日期": log.get("日期", ""),
                "工序": log.get("工序", ""), "类型": log.get("流转", ""), "事件": log.get("事件", "")
            })

    if flat_data:
        df_logs = pd.DataFrame(flat_data).sort_values(by="日期", ascending=False)
        st.info("💡 下方为历史日志。直接**双击修改文字**，或选中行后按 **Delete** 删除。")

        edited_df = st.data_editor(
            df_logs,
            column_config={
                "_id": None, 
                "部件": st.column_config.TextColumn(disabled=True),
                "工序": st.column_config.SelectboxColumn("工序", options=STAGES_UNIFIED, required=True)
            },
            num_rows="dynamic", use_container_width=True
        )

        if st.button("💾 确认并覆盖保存历史记录", type="primary"):
            new_logs_by_comp = {}
            for _, row in edited_df.iterrows():
                c = row["部件"]
                if pd.isna(c) or not c: c = "全局进度"
                if c not in new_logs_by_comp: new_logs_by_comp[c] = []

                old_id = row.get("_id")
                old_images = []
                if pd.notna(old_id) and old_id in log_ref_map:
                    imgs = log_ref_map[old_id].get("图片", [])
                    old_images = imgs if isinstance(imgs, list) else ([imgs] if imgs else [])

                preserved_id = old_id if pd.notna(old_id) and str(old_id).strip() else str(uuid.uuid4())
                new_logs_by_comp[c].append({
                    "_id": str(preserved_id), "日期": str(row["日期"]), "工序": str(row["工序"]),
                    "流转": str(row["类型"]), "事件": str(row["事件"]), "图片": old_images
                })

            comps_in_scope = [sel_comp] if sel_comp != "🌐 全部展示" else list(db[sel_proj].get("部件列表", {}).keys())
            for c in comps_in_scope:
                if c in db[sel_proj].get("部件列表", {}):
                    db[sel_proj]["部件列表"][c]["日志流"] = new_logs_by_comp.get(c, [])

            sync_save_db(); st.success("✅ 历史记录已更新！"); st.rerun()

        st.divider(); st.subheader("🖼️ 历史参考图画廊")
        has_images = False
        for log_id, log in log_ref_map.items():
            images = log.get("图片", [])
            if not isinstance(images, list): images = [images] if images else []
            if images:
                has_images = True
                st.markdown(f"**📅 {log.get('日期','')} | 📍 {log.get('工序','')} | 📝 {log.get('事件', '')[:30]}...**")
                cols = st.columns(min(len(images), 4))
                for i, img_b64 in enumerate(images):
                    with cols[i % 4]: st.image(base64.b64decode(img_b64), use_container_width=True)
        if not has_images: st.caption("该过滤条件下暂无历史参考图片。")
    else: st.info("该过滤条件下暂无记录。")
