import streamlit as st
import json
import os
import datetime
import base64
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# ==========================================
# 1. 页面基础配置与核心变量
# ==========================================
st.set_page_config(page_title="INART PM 系统", page_icon="🚀", layout="wide")

MENU_DASHBOARD = "📊 全局大盘与甘特图"
MENU_SPECIFIC = "🎯 特定项目管控台"  
MENU_FASTLOG = "📝 手机 AI 速记"
MENU_PACKING = "📦 包装与入库特殊领用" 
MENU_COST = "💰 专属成本台账"
MENU_HISTORY = "🔍 历史溯源"

STD_MILESTONES = ["待立项", "研发中", "即将进入生产", "生产中", "生产结束", "项目结束撒花🎉"]
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
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        st.cache_data.clear()

db_manager = DatabaseManager()

if 'db' not in st.session_state: st.session_state.db = db_manager.load()
if 'parsed_logs' not in st.session_state: st.session_state.parsed_logs = []

def auto_sync_milestone(proj_name):
    """🚀 底层状态智能冒泡引擎 (防御了反向降级Bug)"""
    proj_data = st.session_state.db.get(proj_name)
    if not proj_data: return
    comps = proj_data.get('部件列表', {})
    if not comps: return

    stages = [info.get('主流程', '') for c_name, info in comps.items() if c_name != "全局进度"]
    if not stages: stages = [comps.get("全局进度", {}).get("主流程", "")]

    cur_ms = proj_data.get('Milestone', '')

    if all(s == "✅ 已完成(结束)" for s in stages) and stages:
        proj_data['Milestone'] = "项目结束撒花🎉"
    elif any(s in ["工厂复样(含胶件/上色等)", "大货"] for s in stages):
        if cur_ms not in ["生产结束", "项目结束撒花🎉"]: 
            proj_data['Milestone'] = "生产中"
    elif any(s in ["建模(含打印/签样)", "涂装", "设计", "工程拆件", "手板/结构板", "官图"] for s in stages):
        if cur_ms == "待立项":
            proj_data['Milestone'] = "研发中"

def sync_save_db():
    for p in st.session_state.db:
        auto_sync_milestone(p)
    db_manager.save(st.session_state.db)

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
# 🚀 彻底修复大盘过滤：去掉 strip 空格引发的潜在干扰
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
                owner = info.get('负责人', '未分配'); stage = info.get('主流程', '未知')
                if owner and owner != '未分配': owner_stats.append(owner)
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
        df = pd.read_csv(uploaded_csv, dtype=str, on_bad_lines='skip')
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
    else: st.warning("无足够日志数据生成甘特图。暂无数据展示，请先录入项目进展。")

    st.subheader("📋 大盘状态明细表")
    if table_data: 
        df_table = pd.DataFrame(table_data)
        df_table['is_finished'] = df_table['状态'].apply(lambda x: 1 if "已结案" in str(x) else 0)
        df_table['sort_date'] = df_table['开定时间'].replace({'TBD': '9999-12-31', '': '9999-12-31'})
        df_table = df_table.sort_values(by=['is_finished', 'sort_date', '项目']).drop(columns=['is_finished', 'sort_date'])
        st.dataframe(df_table, use_container_width=True)

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
    else: st.info("暂无足够历史日志以生成时长漏斗。请在下方录入进度即可激活。")

    # ------------------------------------------
    # 图表区 2: 部件透视网格 (🚀 官图引力场)
    # ------------------------------------------
    st.divider(); st.subheader("🔬 单项目部件级进度透视矩阵")
    comps = db[sel_proj].get('部件列表', {})
    if not comps: st.warning("暂无录入部件明细。请在下方录入。")
    else:
        z_data = []; y_labels = list(comps.keys()); hover_text = []
        guan_tu_idx = STAGES_UNIFIED.index("官图") 
        for comp_name in y_labels:
            cur_stage = comps[comp_name].get('主流程', STAGES_UNIFIED[0])
            c_idx = STAGES_UNIFIED.index(cur_stage) if cur_stage in STAGES_UNIFIED else 0
            history_stages = set([log.get('工序', '') for log in comps[comp_name].get('日志流', [])])

            row_vals = []; row_hover = []
            for i in range(len(STAGES_UNIFIED)):
                stg = STAGES_UNIFIED[i]
                if cur_stage == "✅ 已完成(结束)":
                    row_vals.append(1); row_hover.append(f"部件: {comp_name}<br>状态: ✅ 全部结束")
                elif stg == cur_stage:
                    row_vals.append(2); row_hover.append(f"部件: {comp_name}<br>工序: {stg}<br>状态: 🚀 <b>当前进行中</b>")
                else:
                    is_forced_completed = (c_idx >= guan_tu_idx and i < c_idx)
                    if is_forced_completed or (stg in history_stages):
                        row_vals.append(1); row_hover.append(f"部件: {comp_name}<br>工序: {stg}<br>状态: ✅ 已完成")
                    else:
                        row_vals.append(0); row_hover.append(f"部件: {comp_name}<br>工序: {stg}<br>状态: ⏳ 未流转")
            z_data.append(row_vals); hover_text.append(row_hover)

        colorscale = [[0.0, '#f1f5f9'], [0.33, '#f1f5f9'], [0.33, '#2ecc71'], [0.66, '#2ecc71'], [0.66, '#3b82f6'], [1.0, '#3b82f6']]
        fig_grid = go.Figure(data=go.Heatmap(z=z_data, x=STAGES_UNIFIED, y=y_labels, colorscale=colorscale, showscale=False, xgap=4, ygap=4, text=hover_text, hoverinfo='text'))
        fig_grid.update_layout(xaxis=dict(side='top', tickangle=-45), yaxis=dict(autorange='reversed'), plot_bgcolor='white', height=max(250, len(y_labels) * 45), margin=dict(t=120, b=20, l=20, r=20))
        st.plotly_chart(fig_grid, use_container_width=True)

    # ------------------------------------------
    # 操作区: 进度明细与交接 
    # ------------------------------------------
    st.divider(); st.subheader("🔧 进度明细与流转交接工作台")

    cur_pm = db[sel_proj].get('负责人', 'Mo')
    cur_ms = db[sel_proj].get('Milestone', '')
    cur_target = db[sel_proj].get('Target', 'TBD') 

    # 🚀 优化 1：清爽的大盘控制区，去除多余的宏观切换和日期！
    st.markdown("**1. 全局大盘基础信息**")
    col_m1, col_m2, col_m3 = st.columns(3)
    with col_m1: new_pm = st.selectbox("👤 负责人分配", ["Mo", "越", "袁"], index=["Mo", "越", "袁"].index(cur_pm) if cur_pm in ["Mo", "越", "袁"] else 0)
    with col_m2: new_ms = st.selectbox("项目当前阶段", STD_MILESTONES, index=STD_MILESTONES.index(cur_ms) if cur_ms in STD_MILESTONES else 0)
    with col_m3: new_target = st.text_input("📅 预计开定时间", value=cur_target)

    if st.button("💾 更新全局基础信息", type="primary", key="btn_global"): 
        db[sel_proj]['负责人'] = new_pm 
        db[sel_proj]['Milestone'] = new_ms
        db[sel_proj]['Target'] = new_target 

        # 🚀 优化 2：真正的全绿魔法！只要项目阶段改成“结束撒花”，触发全盘完结！
        if new_ms == "项目结束撒花🎉" and cur_ms != "项目结束撒花🎉":
            td = str(datetime.date.today())
            for c_name in db[sel_proj].setdefault("部件列表", {}).keys():
                db[sel_proj]["部件列表"][c_name]["主流程"] = "✅ 已完成(结束)"
                db[sel_proj]["部件列表"][c_name]['日志流'].append({"日期": td, "流转": "系统完结", "工序": "✅ 已完成(结束)", "事件": f"[系统同步] 随大盘一键完结"})

        sync_save_db(); st.success(f"已更新大盘配置！"); st.rerun()

    # ------------------------------------------
    # 🚀 优化 1：下沉的阶段切入与历史补录
    st.markdown("**2. 阶段切入与历史补录 (细分配件状态)**")

    with st.expander("📥 批量导入产品配件清单 (CSV)"):
        comp_csv = st.file_uploader("上传 CSV (包含配件/名称/部件等表头)", type=['csv'])
        if comp_csv and st.button("开始识别并导入配件"):
            try:
                try: df_c = pd.read_csv(comp_csv)
                except: comp_csv.seek(0); df_c = pd.read_csv(comp_csv, encoding='gbk')
                # 🚀 优化 4：完美适配 Neo 的物料表表头
                name_col = next((c for c in df_c.columns if any(k in str(c) for k in ['品名', '物料', '名称', '部件', '零件', '配件', '明细'])), None)
                if name_col:
                    count = 0
                    for val in df_c[name_col].dropna().unique():
                        c_name_clean = str(val).strip()
                        if c_name_clean and c_name_clean not in db[sel_proj].setdefault("部件列表", {}):
                            db[sel_proj]["部件列表"][c_name_clean] = {"主流程": STAGES_UNIFIED[0], "日志流": []}
                            count += 1
                    sync_save_db(); st.success(f"成功导入 {count} 个配件！"); st.rerun()
                else: st.warning("未能在CSV中找到合适的列名(名称/部件/配件等)。")
            except Exception as e: st.error(f"解析失败：{e}")

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

        new_stage = st.selectbox(f"🎯 部件工序阶段 (将批量更新)", STAGES_UNIFIED, index=STAGES_UNIFIED.index(default_stage) if default_stage in STAGES_UNIFIED else 0)
        p_owner = st.text_input("部件负责人 (将批量应用)", value="")
        detail_record_date = st.date_input("🕒 发生日期(支持历史补录)", datetime.date.today(), key="date_detail")
        img_file = st.file_uploader("🖼️ 上传参考图 (将自动关联所选部件)", type=['png', 'jpg', 'jpeg'])

    with m2:
        col_h1, col_h2 = st.columns(2)
        with col_h1: 
            evt_type = st.selectbox("记录类型", ["🔄 内部进展/正常流转", "⬅️ 收到反馈/被打回"])
            handoff = st.selectbox("关联媒介", HANDOFF_METHODS)
        with col_h2: 
            if evt_type == "⬅️ 收到反馈/被打回": reject = st.selectbox("选择打回原因", REJECT_REASONS)
            else: reject = "正常"

        log_txt = st.text_area("补充详细进展 (如：版权释放资料等)", height=80)

        if st.button("💾 批量保存交接与进度", type="primary", key="btn_detail"):
            if "➕ 新增细分配件..." in comps_to_process and not new_comp_name: 
                st.error("❌ 新增配件名称不能为空！")
            else:
                for c_raw in comps_to_process:
                    actual_c = "全局进度" if c_raw == "🌐 全局进度 (Overall)" else (new_comp_name if c_raw == "➕ 新增细分配件..." else c_raw)
                    if actual_c not in db[sel_proj].setdefault("部件列表", {}): db[sel_proj]["部件列表"][actual_c] = {"主流程": STAGES_UNIFIED[0], "日志流": []}
                    if p_owner: db[sel_proj]["部件列表"][actual_c]['负责人'] = p_owner

                    full_log = f"【{evt_type} | {handoff}】状态:{reject}。补充: {log_txt}" if log_txt else f"【{evt_type} | {handoff}】状态:{reject}。"

                    # 🚀 优化 3：立项一天的智能防呆
                    if new_stage == "立项":
                        log_entry_1 = {"日期": str(detail_record_date), "流转": evt_type, "工序": "立项", "事件": full_log}
                        if img_file is not None:
                            img_file.seek(0); log_entry_1["图片"] = base64.b64encode(img_file.read()).decode()
                        db[sel_proj]["部件列表"][actual_c]['日志流'].append(log_entry_1)

                        next_day = str(detail_record_date + datetime.timedelta(days=1))
                        db[sel_proj]["部件列表"][actual_c]['日志流'].append({"日期": next_day, "流转": "系统自动", "工序": "建模(含打印/签样)", "事件": "[系统自动] 立项完成，自动进入建模阶段"})
                        db[sel_proj]["部件列表"][actual_c]['主流程'] = "建模(含打印/签样)"
                    else:
                        log_entry = {"日期": str(detail_record_date), "流转": evt_type, "工序": new_stage, "事件": full_log}
                        if img_file is not None:
                            img_file.seek(0); log_entry["图片"] = base64.b64encode(img_file.read()).decode()
                        db[sel_proj]["部件列表"][actual_c]['日志流'].append(log_entry)
                        db[sel_proj]["部件列表"][actual_c]['主流程'] = new_stage

                sync_save_db(); st.success(f"🎉 成功为 {len(comps_to_process)} 个模块记录了进展！"); st.rerun()

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
# 模块 4：包装与台账
# ==========================================
elif menu == MENU_PACKING:
    st.title("📦 包装与入库特殊领用记录")
    if not valid_projs: st.stop()
    sel_proj = st.selectbox("📌 追踪项目 (💡支持打字模糊搜索)", valid_projs)

    st.divider(); st.markdown("### 📝 项目全局专属备忘录")
    memo_txt = st.text_area("记录跨部门叮嘱等杂项", value=db[sel_proj].get("备忘录", ""), height=100)
    if st.button("💾 保存备忘录"): db[sel_proj]["备忘录"] = memo_txt; sync_save_db(); st.success("已保存！")

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
        sync_save_db(); st.success("已存档！")

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
        tot_after = float(c_unit * c_qty)
        db[sel_proj].setdefault("成本数据", {}).setdefault("动态明细", []).append({
            "分类": c_name, "供应商": vendor, "税后单价": float(c_unit), "数量": float(c_qty),
            "税后总成本": tot_after, "税点": f"{tax_rate}%", "税前总成本": round(tot_after / (1 + tax_rate/100), 2)
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

        st.divider(); st.markdown("### 📝 动态明细管理 (💡 支持直接修改，按 Delete 删除行)")
        edited_df = st.data_editor(df_cost_show, num_rows="dynamic", use_container_width=True)

        if st.button("💾 确认并保存表格修改", type="primary"):
            for idx, row in edited_df.iterrows():
                try:
                    qty = float(row.get('数量', 1.0)); unit = float(row.get('税后单价', 0.0))
                    tax_str = str(row.get('税点', '0%')).replace('%', ''); rate = float(tax_str) if tax_str else 0.0
                    edited_df.at[idx, '税后总成本'] = qty * unit
                    edited_df.at[idx, '税前总成本'] = round((qty * unit) / (1 + rate / 100), 2)
                except: pass
            db[sel_proj]["成本数据"]["动态明细"] = edited_df.to_dict('records')
            sync_save_db(); st.success("✅ 成本明细已更新！"); st.rerun()

        st.divider()
        total_c = sum(edited_df['税后总成本']) if not edited_df.empty else 0
        unit_c = total_c / orders if orders > 0 else 0
        st.info(f"**💰 累计税后总成本:** ¥{total_c:,.2f} | **单体核算成本:** ¥{unit_c:,.2f} | **单体毛利:** ¥{price - unit_c:,.2f} | **预测毛利率:** {(price - unit_c) / price * 100 if price > 0 else 0:.2f}%")

elif menu == MENU_HISTORY:
    st.title("🔍 图文交接溯源档案")
    if not valid_projs: st.stop()
    c1, c2 = st.columns(2)
    with c1: sel_proj = st.selectbox("📌 选择溯源项目", valid_projs)
    with c2:
        comps = list(db[sel_proj].get("部件列表", {}).keys())
        sel_comp = st.selectbox("选择部件", comps) if comps else None
    st.divider()
    if sel_comp:
        logs = db[sel_proj]["部件列表"][sel_comp].get("日志流", [])
        if not logs: st.info("该部件暂无记录。")
        else:
            for idx, log in reversed(list(enumerate(logs))):
                with st.container():
                    st.markdown(f"**📅 {log['日期']}** | 工序: {log.get('工序', '未知')}")
                    st.write(log['事件'])
                    if "图片" in log:
                        try: st.image(base64.b64decode(log["图片"]), width=200)
                        except: pass
                    if st.button("🗑️ 删除此条记录", key=f"del_{idx}_{log['日期']}"):
                        del db[sel_proj]["部件列表"][sel_comp]["日志流"][idx]; sync_save_db(); st.rerun()
                    st.markdown("---")
