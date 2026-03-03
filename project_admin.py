import json
import re
import pandas as pd
import streamlit as st


def render_project_management_section(db, norm_text, sync_save_db):
    with st.expander("🧭 项目管理（重命名 / 合并同类 / 别名学习）", expanded=True):
        all_proj_names = [p for p in db.keys() if p != "系统配置"]
        if not all_proj_names:
            st.info("暂无项目可管理。")
            return

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
                st.session_state.db["系统配置"]["最近合并回滚"] = {
                    "merge_src": merge_src,
                    "merge_dst": merge_dst,
                    "src_data": json.loads(json.dumps(src_data, ensure_ascii=False)),
                    "dst_data_before": json.loads(json.dumps(dst_data, ensure_ascii=False)),
                    "alias_map_before": json.loads(json.dumps(st.session_state.db["系统配置"].get("项目别名", {}), ensure_ascii=False))
                }
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
                st.session_state.db["系统配置"]["最近合并回滚"] = {}
                sync_save_db()
                st.success("✅ 已撤销最近一次合并。")
                st.rerun()
