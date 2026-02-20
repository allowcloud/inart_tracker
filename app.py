import streamlit as st
import json
import os
import datetime
import pandas as pd
import plotly.express as px
import plotly.io as pio
import webbrowser
import re

# ==========================================
# 页面基础配置 (必须第一行)
# ==========================================
st.set_page_config(page_title="INART 项目管控中心", page_icon="🚀", layout="wide")

DATA_FILE = "tracker_data_web.json"
IMG_DIR = "tracker_images"
if not os.path.exists(IMG_DIR): os.makedirs(IMG_DIR)

STD_MILESTONES = ["立项预研", "2D设计", "3D建模", "打样与修改", "版权监修", "签板确认", "工厂试产", "大货量产", "生产结束", "已发货"]
STD_COMPONENTS = ["头雕", "素体(主体)", "手型", "服装", "鞋子", "武器", "配件", "地台", "包装"]
STD_STAGES = ["立项", "建模设计", "打样", "提审", "签板", "工厂复样", "大货"]
STD_COSTS = ["模具-钢模/铜模", "生产-啤件", "生产-涂装", "植发-打样", "植发-大货", "服装-穿衣", "包装及纸品", "辅料与运费", "外包", "版权"]

# ==========================================
# 预载入微信记录数据库
# ==========================================
DEFAULT_DB = {
    "1/12 三角洲行动": {"Milestone": "版权监修", "Target": "TBD", "跟单": "袁", "部件列表": {"全局进度": {"主流程": "提审", "打印状态": "未安排", "日志流": [{"日期": "2025-11-26", "事件": "送审一体", "图片": "无"}, {"日期": "2025-12-08", "事件": "12/15那周要送审一体 需跟版权方约时间。Risk KO模种1套 版权方确认350体 总体按400推", "图片": "无"}]}}, "备忘录": "包装：哈夫币尺寸 涂装安排。外包装在按新的。", "包装专项": {"彩盒": {"done": False, "weight": False}}, "成本数据": {}, "发货数据": {"总单量":0, "批次明细":[]}},
    "1/6 玛奇玛": {"Milestone": "打样与修改", "Target": "TBD", "跟单": "袁", "部件列表": {"地台": {"主流程": "打样", "日志流": [{"日期": "2025-12-08", "事件": "地台修改（涂装已安排给平姐）", "图片": "无"}]}, "头雕": {"主流程": "建模设计", "日志流": [{"日期": "2025-12-08", "事件": "脸修改（微笑脸推进，平静脸需文铭修改）", "图片": "无"}]}, "素体(主体)": {"主流程": "打样", "日志流": [{"日期": "2025-12-08", "事件": "身体修改（身形已确认 安排打印掏空1.5的件用于硅胶打样）*需要提审", "图片": "无"}]}}, "备忘录": "", "包装专项": {}, "成本数据": {}, "发货数据": {}},
    "1/6 链锯人-淀治": {"Milestone": "已发货", "Target": "TBD", "跟单": "袁", "发货数据": {"总单量": 400, "批次明细": [{"日期": "2025-12-06", "类型": "大货入库", "数量": 373, "备注": "12/8一起送到云仓"}]}, "部件列表": {"全局进度": {"主流程":"大货", "日志流": [{"日期": "2025-12-08", "事件": "需要送出6体给版权方。需改关节（3块18/个已安排 要走采购流程）", "图片": "无"}]}}, "备忘录": "", "包装专项": {}, "成本数据": {}},
    "1/6 斯内普": {"Milestone": "已发货", "Target": "TBD", "跟单": "浪", "发货数据": {"总单量": 3000, "批次明细": [{"日期": "2025-11-19", "类型": "大货入库", "数量": 1062, "备注": "交货1062 有1体送去质检"}, {"日期": "2025-12-05", "类型": "大货入库", "数量": 991, "备注": "包完第二批，其余掉扣子未包"}]}, "部件列表": {"全局进度": {"主流程":"大货", "日志流": [{"日期": "2025-12-08", "事件": "计划12/13包第三批（1009）", "图片": "无"}]}}, "备忘录": "", "包装专项": {}, "成本数据": {}},
    "1/6 里夫超人": {"Milestone": "打样与修改", "Target": "TBD", "跟单": "袁", "部件列表": {"头雕": {"主流程": "打样", "日志流": [{"日期": "2025-11-28", "事件": "修改头身比已签字确认 已安排打印翻模 需涂装", "图片": "无"}, {"日期": "2025-12-02", "事件": "组装头发后发现落地需要修改", "图片": "无"}, {"日期": "2025-12-08", "事件": "预计12/8给文件。大货翻肉色", "图片": "无"}]}, "地台": {"主流程": "打样", "日志流": [{"日期": "2025-12-08", "事件": "战衣地台已涂色 西装地台收3D待涂装植绒", "图片": "无"}]}}, "备忘录": "月中等韩国回来拍官图。开定和测评不要超过半个月。", "包装专项": {}, "成本数据": {}, "发货数据": {}},
    "1/6 马尔福": {"Milestone": "打样与修改", "Target": "TBD", "跟单": "袁", "部件列表": {"头雕": {"主流程": "打样", "日志流": [{"日期": "2025-12-08", "事件": "根据落地修改眼皮（胶发ok植发待确认效果）", "图片": "无"}]}, "配件": {"主流程": "打样", "日志流": [{"日期": "2025-12-08", "事件": "鱼漂灯的规格 4*4", "图片": "无"}]}}, "包装专项": {"说明书": {"done": True, "weight": False}}, "备忘录": "说明书已转翻译。出色样给倍特萌（待12/9报价）", "成本数据": {}, "发货数据": {}}
}

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f: return json.load(f)
        except: pass
    return DEFAULT_DB

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=4)

def parse_excel_date(val):
    val = str(val).strip()
    if val.lower() in ['nan', 'tbd', '', 'nat']: return 'TBD'
    try:
        if val.isdigit() and len(val) >= 4: return pd.to_datetime(int(val), unit='D', origin='1899-12-30').strftime('%Y-%m-%d')
        return pd.to_datetime(val).strftime('%Y-%m-%d')
    except: return val

def get_risk_status(milestone, target_date_str):
    ms = str(milestone).lower()
    if any(k in ms for k in ["生产结束", "已发货", "大货量产"]): return "🟢 生产完毕", "safe"
    if target_date_str == "TBD" or "年" in target_date_str or "月" in target_date_str: return "🟡 暂未定档", "warning"
    try:
        days_left = (datetime.datetime.strptime(target_date_str, "%Y-%m-%d").date() - datetime.date.today()).days
        if days_left < 0: return f"🔴 延期({abs(days_left)}天)", "danger"
        elif days_left <= 30: return f"🟡 冲刺(剩{days_left}天)", "warning"
        else: return f"🟢 正常(剩{days_left}天)", "safe"
    except: return "⚪ 未知", "normal"

# 初始化 State
if 'db' not in st.session_state: st.session_state.db = load_data()
if 'parsed_logs' not in st.session_state: st.session_state.parsed_logs = []

# ==========================================
# 侧边栏导航
# ==========================================
st.sidebar.title("🚀 óò的Tracker")
menu = st.sidebar.radio("模块导航", ["📊 大盘与 KPI 洞察", "📝 手机 AI 速记", "🔧 部件明细更新", "📦 入库与包装", "💰 成本核算", "🔍 历史溯源"])

# ==========================================
# 页面 1：大盘与 KPI 洞察
# ==========================================
if menu == "📊 大盘与 KPI 洞察":
    st.title("📊 大盘总览与商业智能 (BI)")

    db = st.session_state.db
    total_proj = len(db); cnt_safe = cnt_warn = cnt_danger = 0
    table_data = []; owner_stats = []; stage_stats = []

    for proj, data in db.items():
        gd = data.get('跟单', ''); ms = data.get('Milestone', ''); tgt = data.get('Target', 'TBD')
        risk_txt, risk_tag = get_risk_status(ms, tgt)

        if risk_tag == 'safe': cnt_safe += 1
        elif risk_tag == 'warning': cnt_warn += 1
        elif risk_tag == 'danger': cnt_danger += 1

        comps = data.get('部件列表', {})
        if not comps:
            table_data.append({"状态": risk_txt, "项目": proj, "跟单": gd, "Milestone": ms, "Target": tgt, "部件": "-", "流程": "-", "断更": "-", "最新动态": "无数据"})
        else:
            for c_name, info in comps.items():
                owner = info.get('负责人', '未分配'); stage = info.get('主流程', '未知')
                owner_stats.append(owner); stage_stats.append(stage)

                logs = info.get('日志流', [])
                if logs:
                    try:
                        last_dt = datetime.datetime.strptime(logs[-1]['日期'], "%Y-%m-%d").date()
                        off = (datetime.date.today() - last_dt).days
                        dt_txt = f"{off} 天"
                    except: dt_txt = "未知"
                    l_log = logs[-1]['事件']
                else: dt_txt = "-"; l_log = "无数据"

                table_data.append({"状态": risk_txt, "项目": proj, "跟单": gd, "Milestone": ms, "Target": tgt, "部件": c_name, "流程": stage, "断更": dt_txt, "最新动态": l_log})

    # KPI 数据卡片
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("总项目数 (Ongoing)", f"{total_proj} 个")
    col2.metric("🟢 安全 / 已发货", f"{cnt_safe} 个")
    col3.metric("🟡 冲刺 / 未定档", f"{cnt_warn} 个")
    col4.metric("🔴 延期高风险", f"{cnt_danger} 个")
    st.divider()

    # 🌟 一键导入 CSV（适配 Streamlit 网页版）
    st.subheader("📥 从本地导入研发总表 (CSV 格式)")
    st.info("提示：网页版无法直接读取 Excel，请将您负责的 Excel Sheet 另存为 CSV 后上传。")
    uploaded_csv = st.file_uploader("选择 CSV 文件", type=['csv'])
    if uploaded_csv is not None:
        if st.button("开始分析并合并数据"):
            try:
                df = pd.read_csv(uploaded_csv, dtype=str, on_bad_lines='skip')
                header_idx = -1
                for i, row in df.iterrows():
                    if '项目名称' in [str(x).strip() for x in row.values]: header_idx = i; break
                if header_idx != -1:
                    cols = pd.Series(df.iloc[header_idx].values)
                    cols[cols.duplicated(keep='first')] = cols[cols.duplicated(keep='first')] + '_dup'
                    df.columns = cols; df = df.iloc[header_idx+1:]

                if '负责人' in df.columns:
                    cnt = 0
                    for _, r in df[df['负责人'] == '袁'].iterrows():
                        p = str(r.get('项目名称', '')).strip()
                        if not p or p == 'nan': continue
                        gd = str(r.get('跟单', '')).replace('nan', '')
                        jd = str(r.get('进度', r.get('项目流程', '立项预研'))).replace('nan', '立项预研')
                        cd = parse_excel_date(r.get('预计出货时间', r.get('开定时间', 'TBD')))
                        if p not in db: db[p] = {"跟单": gd, "Milestone": jd, "Target": cd, "部件列表": {}, "备忘录": "", "包装专项": {}, "成本数据": {}, "发货数据": {"总单量":0, "批次明细":[]}}
                        else: db[p]["跟单"] = gd; db[p]["Milestone"] = jd; db[p]["Target"] = cd
                        cnt += 1
                    save_data(db); st.success(f"同步成功！共更新了 {cnt} 个项目！"); st.rerun()
            except Exception as e: st.error(f"解析出错: {e}")

    st.subheader("📋 项目明细总表")
    if table_data:
        df_table = pd.DataFrame(table_data)
        st.dataframe(df_table, use_container_width=True)

    st.divider()
    st.subheader("📈 人员负荷 (Loading) 与研发漏斗")
    c1, c2 = st.columns(2)
    with c1:
        if owner_stats:
            df_owner = pd.DataFrame({'人员': owner_stats}).value_counts().reset_index(name='任务数')
            fig1 = px.bar(df_owner, x='人员', y='任务数', title="👤 团队成员研发负荷分布", color='任务数', color_continuous_scale='Reds')
            st.plotly_chart(fig1, use_container_width=True)
    with c2:
        if stage_stats:
            df_stage = pd.DataFrame({'阶段': stage_stats}).value_counts().reset_index(name='部件数')
            fig2 = px.pie(df_stage, names='阶段', values='部件数', title="🌪️ 当前研发环节大盘漏斗", hole=0.4)
            st.plotly_chart(fig2, use_container_width=True)

# ==========================================
# 页面 2：手机 AI 速记
# ==========================================
elif menu == "📝 手机 AI 速记":
    st.title("🚀 移动端沉浸式 AI 记录")
    st.info("💡 特别适合出差、开会时在手机上使用。将会议纪要复制进来，AI 会自动拆分并派发到各自项目。")

    raw_text = st.text_area("✍️ 在此一锅端输入今日进展：", height=150, placeholder="例如：里夫西装在打样。玛奇玛头雕重新修了发际线...")

    if st.button("✨ 智能拆解并生成预览", type="primary"):
        if not raw_text.strip(): st.warning("内容不能为空！")
        else:
            lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
            parsed = []; curr_p = "未知/请手动修改"
            for line in lines:
                f_p = None; cl = line.replace('/', '').replace('*', '').strip()
                for p in st.session_state.db.keys():
                    if p.lower() in cl.lower(): f_p = p; break
                if not f_p:
                    for p in st.session_state.db.keys():
                        kw = p.replace('1/6', '').replace('1/12', '').replace('-', ' ').strip()
                        if kw and kw in cl:
                            f_p = "⚠️比例冲突" if len([x for x in st.session_state.db.keys() if kw in x]) > 1 else p
                            break
                if not f_p: f_p = curr_p
                else: curr_p = f_p
                parsed.append({"识别项目": f_p, "待写入事件": line})
            st.session_state.parsed_logs = parsed
            st.success("拆解完成！请在下方核对修改。")

    if st.session_state.parsed_logs:
        st.divider()
        st.subheader("👀 拆解结果核对台")
        edited_logs = []
        project_options = ["⚠️比例冲突", "未知/请手动修改"] + list(st.session_state.db.keys())

        for i, item in enumerate(st.session_state.parsed_logs):
            c1, c2 = st.columns([1, 2])
            with c1:
                def_idx = project_options.index(item['识别项目']) if item['识别项目'] in project_options else 0
                sel_proj = st.selectbox(f"日志 {i+1} 归属项目", project_options, index=def_idx, key=f"sel_{i}")
            with c2:
                sel_event = st.text_input(f"内容", value=item['待写入事件'], key=f"evt_{i}")
            edited_logs.append({"项目": sel_proj, "事件": sel_event})

        if st.button("💾 确认无误，全部入库！", type="primary"):
            td = str(datetime.date.today())
            for log in edited_logs:
                p = log['项目']
                if p not in st.session_state.db:
                    st.error(f"项目【{p}】无效，入库中止！请选择有效的项目。"); st.stop()
                comps = list(st.session_state.db[p].get('部件列表', {}).keys())
                t_c = comps[0] if comps else "全局进度"
                st.session_state.db[p].setdefault("部件列表", {}).setdefault(t_c, {"主流程": "打样", "日志流": []})['日志流'].append({"日期": td, "事件": log['事件'], "图片": "无"})
            save_data(st.session_state.db); st.session_state.parsed_logs = []; st.success("🎉 日记已精准分发入库！"); st.rerun()

# ==========================================
# 页面 3：部件明细与附图
# ==========================================
elif menu == "🔧 部件明细更新":
    st.title("📝 核心部件进度流转台")

    db = st.session_state.db
    projects = list(db.keys())
    if not projects: st.warning("大盘无项目！请先前往【大盘】导入，或在此手动新增。"); projects = ["无项目"]

    c1, c2 = st.columns([1, 1])
    with c1:
        sel_proj = st.selectbox("📌 1. 选择大盘项目", projects)
        if st.button("➕ 手动新增项目"): st.session_state.new_proj_mode = True
        if st.session_state.get('new_proj_mode', False):
            new_p = st.text_input("请输入新项目名称")
            if st.button("💾 创建档案"):
                if new_p and new_p not in db:
                    db[new_p] = {"跟单": "", "Milestone": "立项预研", "Target": "TBD", "部件列表": {}, "包装专项": {}, "发货数据": {"总单量": 0, "批次明细": []}, "成本数据": {}}
                    save_data(db); st.success(f"建档成功！"); st.session_state.new_proj_mode = False; st.rerun()
                else: st.error("项目已存在或为空！")

    with c2:
        if sel_proj in db:
            cur_ms = db[sel_proj].get('Milestone', ''); cur_tgt = db[sel_proj].get('Target', 'TBD')
            new_ms = st.selectbox("当前 Milestone 阶段", STD_MILESTONES, index=STD_MILESTONES.index(cur_ms) if cur_ms in STD_MILESTONES else 0)
            new_tgt = st.text_input("出货/开定 Target (YYYY-MM-DD)", value=cur_tgt)
            if st.button("更新项目大盘排期"):
                db[sel_proj]['Milestone'] = new_ms; db[sel_proj]['Target'] = parse_excel_date(new_tgt); save_data(db); st.success("大盘排期已更新！")

    st.divider()
    if sel_proj in db:
        st.subheader(f"⚙️ 2. 【{sel_proj}】部件流转与图文日志")
        comps = list(db[sel_proj].get('部件列表', {}).keys())
        all_comps = list(set(comps + STD_COMPONENTS))

        col_c1, col_c2, col_c3 = st.columns(3)
        with col_c1:
            sel_comp = st.selectbox("操作部件", all_comps)
            if sel_comp == "主体": sel_comp = "素体(主体)"
        with col_c2:
            cur_stage = db[sel_proj].get('部件列表', {}).get(sel_comp, {}).get('主流程', '立项')
            new_stage = st.selectbox("流转主阶段", STD_STAGES, index=STD_STAGES.index(cur_stage) if cur_stage in STD_STAGES else 0)
        with col_c3:
            cur_print = db[sel_proj].get('部件列表', {}).get(sel_comp, {}).get('打印状态', '未安排')
            new_print = st.selectbox("3D打印专项", ["未安排", "已安排打印", "已拿到实物件"], index=["未安排", "已安排打印", "已拿到实物件"].index(cur_print) if cur_print in ["未安排", "已安排打印", "已拿到实物件"] else 0)

        log_txt = st.text_area("进展记录/打回说明", placeholder="例如：版权方反馈面雕需要修改...", height=100)
        uploaded_file = st.file_uploader("📎 上传截图 (选填)", type=["jpg", "jpeg", "png"])

        if st.button("💾 保存部件进度与日志", type="primary"):
            if sel_comp not in db[sel_proj]["部件列表"]: db[sel_proj]["部件列表"][sel_comp] = {"主流程": "立项", "打印状态": "未安排", "日志流": []}
            db[sel_proj]["部件列表"][sel_comp]['主流程'] = new_stage; db[sel_proj]["部件列表"][sel_comp]['打印状态'] = new_print
            img_dest = "无"
            if uploaded_file is not None:
                ext = os.path.splitext(uploaded_file.name)[1]; safe_p = sel_proj.replace('/', '_').replace('\\', '_')
                img_name = f"{safe_p}_{sel_comp}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
                img_dest = os.path.join(IMG_DIR, img_name)
                with open(img_dest, "wb") as f: f.write(uploaded_file.getbuffer())
            if log_txt or img_dest != "无":
                db[sel_proj]["部件列表"][sel_comp]['日志流'].append({"日期": str(datetime.date.today()), "事件": log_txt or "图文更新", "图片": img_dest})
            save_data(db); st.success("🎉 日志追加成功！"); st.rerun()

# ==========================================
# 页面 4：量产入库与领用
# ==========================================
elif menu == "📦 入库与包装":
    st.title("📦 大货入库与包装工作台")

    db = st.session_state.db
    projects = list(db.keys())
    if not projects: st.warning("请先建立项目！"); st.stop()
    sel_proj = st.selectbox("📌 追踪项目", projects)
    inv_data = db[sel_proj].get("发货数据", {"总单量": 0, "批次明细": []})

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1: total_qty = st.number_input("工厂生产总单量 (PCS)", value=int(inv_data.get("总单量", 0)), step=100)
    with c2:
        st.write(""); st.write("")
        if st.button("💾 保存总单量"): db[sel_proj].setdefault("发货数据", {})["总单量"] = total_qty; save_data(db); st.success("已保存")

    in_accum = out_accum = 0; records = []
    for item in inv_data.get("批次明细", []):
        q = int(item.get('数量', 0))
        if item.get('类型') == '内部领用(拍图/质检等)': out_accum += q
        else: in_accum += q
        records.append({"日期": item['日期'], "类型": item.get('类型', '大货入库'), "数量": q, "去向/用途": item.get('备注', '无')})

    fac_left = total_qty - in_accum; real_stock = in_accum - out_accum
    st.markdown("### 🧮 实时库存核算")
    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("累计已入库", f"{in_accum} PCS"); mc2.metric("内部已领用", f"{out_accum} PCS")
    mc3.metric("📦 仓内可用", f"{real_stock} PCS", "净资产"); mc4.metric("🏭 工厂未交", f"{fac_left} PCS", "🔴 催货" if fac_left > 0 else "🟢 交齐", delta_color="inverse")

    st.divider(); st.subheader("➕ 登记流水明细")
    rc1, rc2, rc3, rc4, rc5 = st.columns(5)
    with rc1: d = st.date_input("操作日期")
    with rc2: typ = st.selectbox("操作类型", ["大货入库", "内部领用(拍图/质检等)"])
    with rc3: q = st.number_input("操作数量", min_value=1, value=100)
    with rc4: note = st.text_input("去向 / 用途", placeholder="如：入国内仓 / 寄给小宅")
    with rc5:
        st.write("")
        if st.button("📝 确认登记", type="primary"):
            db[sel_proj].setdefault("发货数据", {}).setdefault("批次明细", []).append({"日期": str(d), "类型": typ, "数量": int(q), "备注": note})
            save_data(db); st.rerun()

    if records: st.dataframe(pd.DataFrame(records), use_container_width=True)

    st.divider(); st.title("🎁 包装专项与项目备忘")
    mc1, mc2 = st.columns(2)
    with mc1: st.markdown("##### 📝 专属备忘录"); memo_txt = st.text_area("记录包装意见、细节备忘", value=db[sel_proj].get("备忘录", ""), height=200)
    with mc2:
        st.markdown("##### ✅ 包装 Checklist")
        pack_data = db[sel_proj].get("包装专项", {})
        pack_items = ["彩盒", "灰箱", "国内物流箱", "国外物流箱", "说明书", "感谢信", "合格证", "电影票"]
        new_pack_data = {}
        for item in pack_items:
            pc1, pc2, pc3 = st.columns([2, 1, 1])
            pc1.markdown(f"**{item}**")
            d_val = pc2.checkbox("已定板", value=pack_data.get(item, {}).get("done", False), key=f"d_{item}")
            w_val = pc3.checkbox("⚖️ 已称重", value=pack_data.get(item, {}).get("weight", False), key=f"w_{item}")
            new_pack_data[item] = {"done": d_val, "weight": w_val}

    if st.button("💾 保存备忘录与包装 Checklist"):
        db[sel_proj]["备忘录"] = memo_txt; db[sel_proj]["包装专项"] = new_pack_data; save_data(db); st.success("保存成功！")

# ==========================================
# 页面 5：成本核算
# ==========================================
elif menu == "💰 成本核算":
    st.title("💰 动态成本与利润控制台")

    db = st.session_state.db; projects = list(db.keys())
    if not projects: st.stop()
    sel_proj = st.selectbox("核算项目", projects); c_data = db[sel_proj].get("成本数据", {})

    c1, c2, c3 = st.columns(3)
    with c1: orders = st.number_input("总订单数", value=int(c_data.get("总订单数", 0)), step=100)
    with c2: price = st.number_input("目标销售单价 (¥)", value=float(c_data.get("销售单价", 0.0)), step=100.0)
    with c3:
        st.write(""); st.write("")
        if st.button("💾 保存基础设置"):
            db[sel_proj].setdefault("成本数据", {})["总订单数"] = orders; db[sel_proj]["成本数据"]["销售单价"] = price; save_data(db); st.success("保存成功")

    st.divider(); st.subheader("➕ 添加成本明细")
    ac1, ac2, ac3 = st.columns([1, 1, 1])
    with ac1: c_name = st.selectbox("成本分类", STD_COSTS)
    with ac2: c_val = st.number_input("总金额 (¥)", min_value=0.0, step=1000.0)
    with ac3:
        st.write("")
        if st.button("入账"):
            db[sel_proj].setdefault("成本数据", {}).setdefault("动态明细", []).append({"名目": c_name, "金额": float(c_val)})
            save_data(db); st.rerun()

    st.subheader("🧾 成本结构清单")
    details = c_data.get("动态明细", [])
    if details:
        df_cost = pd.DataFrame(details)
        st.dataframe(df_cost, use_container_width=True)

        total_c = sum(df_cost['金额']); unit_c = total_c / orders if orders > 0 else 0
        profit = price - unit_c; margin = (profit / price * 100) if price > 0 else 0

        st.markdown(f"### 💸 核算结果")
        rc1, rc2, rc3, rc4 = st.columns(4)
        rc1.metric("总投入", f"¥ {total_c:,.2f}"); rc2.metric("单体成本", f"¥ {unit_c:,.2f}")
        rc3.metric("单体毛利", f"¥ {profit:,.2f}"); rc4.metric("利润率", f"{margin:.2f} %")
        st.plotly_chart(px.pie(df_cost, names="名目", values="金额", title=f"[{sel_proj}] 成本结构分布", hole=0.3), use_container_width=True)
    else: st.info("暂未添加成本明细。")

# ==========================================
# 页面 6：历史溯源
# ==========================================
elif menu == "🔍 历史溯源":
    st.title("🔍 图文履历溯源档案")
    db = st.session_state.db; projects = list(db.keys())
    if not projects: st.stop()

    c1, c2, c3 = st.columns([2, 2, 1])
    with c1: sel_proj = st.selectbox("选择项目", projects)
    with c2:
        comps = list(db[sel_proj].get("部件列表", {}).keys())
        sel_comp = st.selectbox("选择部件", comps) if comps else None

    st.divider()
    if sel_comp:
        logs = db[sel_proj]["部件列表"][sel_comp].get("日志流", [])
        if not logs: st.info("该部件暂无日志记录。")
        else:
            for idx, log in reversed(list(enumerate(logs))):
                with st.container():
                    st.markdown(f"**📅 {log['日期']}**"); st.write(log['事件'])
                    img_path = log.get('图片', '无')
                    if img_path != "无" and os.path.exists(img_path): st.image(img_path, width=300)
                    if st.button("🗑️ 删除此条记录", key=f"del_{idx}"):
                        del db[sel_proj]["部件列表"][sel_comp]["日志流"][idx]
                        save_data(db); st.rerun()
                    st.markdown("---")
