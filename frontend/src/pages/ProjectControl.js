import React, { useEffect, useState, useCallback } from 'react';
import { getProjects, getProject, saveProject } from '../api';

const STAGES_UNIFIED = [
  "立项","建模(含打印/签样)","涂装","设计","工程拆件","手板/结构板",
  "官图","工厂复样(含胶件/上色等)","大货","⏸️ 暂停/搁置","✅ 已完成(结束)"
];
const STD_COMPONENTS = ["头雕(表情)","素体","手型","服装","配件","地台","包装"];
const HANDOFF_METHODS = ["内部正常推进","微信","飞书","实物/打印件交接","网盘链接","当面沟通"];
const ROLE_LIST = ["建模","设计","工程","监修","打印","涂装"];
const STAGE_COLORS = {
  "立项":"#FFB84C","建模(含打印/签样)":"#2CD3E1","涂装":"#F47C7C",
  "设计":"#A555EC","工程拆件":"#4D96FF","手板/结构板":"#4D96FF",
  "官图":"#6BCB77","工厂复样(含胶件/上色等)":"#6BCB77","大货":"#6BCB77",
  "⏸️ 暂停/搁置":"#B2B2B2","✅ 已完成(结束)":"#52c41a"
};

function today() { return new Date().toISOString().slice(0, 10); }

function parseOwner(ownerStr) {
  const result = {};
  if (!ownerStr) return result;
  ownerStr.split(/[,，]/).forEach(pair => {
    pair = pair.trim();
    if (pair.includes('-')) { const [r, n] = pair.split('-', 2); result[r.trim()] = n.trim(); }
  });
  return result;
}

function buildOwnerStr(roleVals) {
  return Object.entries(roleVals).filter(([,v]) => v).map(([k,v]) => `${k}-${v}`).join(', ');
}

function HeatMatrix({ comps }) {
  if (!comps || Object.keys(comps).length === 0)
    return <div style={s.empty}>暂无部件数据，请在【录入交接】标签页添加</div>;
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={s.matrix}>
        <thead>
          <tr>
            <th style={s.mTh}>部件 / 工序</th>
            {STAGES_UNIFIED.map(stg => (
              <th key={stg} style={{ ...s.mTh, fontSize: 10, writingMode: 'vertical-rl', padding: '8px 4px', minWidth: 32 }}>{stg}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Object.entries(comps).map(([cName, cData]) => {
            const curStage = cData?.主流程 || STAGES_UNIFIED[0];
            const curIdx = STAGES_UNIFIED.indexOf(curStage);
            const owner = cData?.负责人 || '';
            const completedStages = new Set();
            (cData?.日志流 || []).forEach(log => {
              if (['彻底完成','OK','通过','完结','结束','撒花'].some(k => (log.事件||'').includes(k)))
                completedStages.add(log.工序);
            });
            return (
              <tr key={cName}>
                <td style={s.mTdLabel}>
                  <div style={{ fontWeight: 600, fontSize: 12 }}>{cName}</div>
                  {owner && <div style={{ fontSize: 10, color: '#888' }}>{owner}</div>}
                </td>
                {STAGES_UNIFIED.map((stg, i) => {
                  let bg = '#f5f5f5';
                  if (curStage === '✅ 已完成(结束)') bg = '#52c41a';
                  else if (completedStages.has(stg)) bg = '#95de64';
                  else if (i === curIdx && curIdx >= 0) bg = STAGE_COLORS[stg] || '#4D96FF';
                  else if (i < curIdx && curIdx >= 0) bg = '#bae7ff';
                  return <td key={stg} title={stg} style={{ ...s.mTdCell, background: bg }} />;
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
      <div style={s.legend}>
        {[['当前阶段','#4D96FF'],['已流转','#bae7ff'],['已完成','#95de64'],['未开始','#f5f5f5']].map(([label,color]) => (
          <span key={label} style={s.legendItem}><span style={{ ...s.legendDot, background: color }} />{label}</span>
        ))}
      </div>
    </div>
  );
}

function LogStream({ comps, selComp }) {
  const logs = [];
  Object.entries(comps || {}).forEach(([cName, cData]) => {
    if (selComp !== '全部' && cName !== selComp) return;
    (cData?.日志流 || []).forEach(log => logs.push({ ...log, _comp: cName }));
  });
  logs.sort((a, b) => b.日期?.localeCompare(a.日期));
  if (logs.length === 0) return <div style={s.empty}>暂无日志记录</div>;
  return (
    <div style={s.logList}>
      {logs.map((log, i) => (
        <div key={i} style={s.logItem}>
          <div style={s.logHeader}>
            <span style={s.logDate}>{log.日期}</span>
            <span style={{ ...s.logComp, background: (STAGE_COLORS[log.工序]||'#4D96FF')+'20', color: STAGE_COLORS[log.工序]||'#4D96FF' }}>{log._comp}</span>
            <span style={s.logStage}>{log.工序}</span>
          </div>
          <div style={s.logEvent}>{log.事件}</div>
          {log.图片?.length > 0 && (
            <div style={s.logImgs}>
              {log.图片.map((img, j) => img ? <img key={j} src={`data:image/jpeg;base64,${img}`} alt="" style={s.logImg} /> : null)}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export default function ProjectControl() {
  const [allProjectsData, setAllProjectsData] = useState({});
  const [selPM, setSelPM] = useState('所有人');
  const [pmList, setPmList] = useState(['所有人']);
  const [filteredProjects, setFilteredProjects] = useState([]);
  const [selProj, setSelProj] = useState('');
  const [projData, setProjData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [activeTab, setActiveTab] = useState('matrix');
  const [logFilterComp, setLogFilterComp] = useState('全部');
  const [allNames, setAllNames] = useState([]);
  const [selComps, setSelComps] = useState([]);
  const [newCompName, setNewCompName] = useState('');
  const [newCompSubCat, setNewCompSubCat] = useState(STD_COMPONENTS[0]);
  const [evtType, setEvtType] = useState('🔄 内部进展/正常流转');
  const [newStage, setNewStage] = useState(STAGES_UNIFIED[1]);
  const [handoff, setHandoff] = useState(HANDOFF_METHODS[0]);
  const [recordDate, setRecordDate] = useState(today());
  const [logTxt, setLogTxt] = useState('');
  const [roleVals, setRoleVals] = useState({});
  const [isCompleted, setIsCompleted] = useState(false);
  const [images, setImages] = useState([]);
  const [showNewProj, setShowNewProj] = useState(false);
  const [newProjName, setNewProjName] = useState('');
  const [newProjPM, setNewProjPM] = useState('Mo');

  // 加载所有项目
  useEffect(() => {
    getProjects().then(data => {
      setAllProjectsData(data);
      const pms = new Set(['所有人']);
      const nameSet = new Set();
      Object.values(data).forEach(p => {
        if (p?.负责人) pms.add(p.负责人);
        Object.values(p?.部件列表 || {}).forEach(c => {
          (c?.负责人 || '').split(/[,，]/).forEach(pair => {
            if (pair.includes('-')) { const n = pair.split('-')[1]?.trim(); if (n) nameSet.add(n); }
          });
        });
      });
      setPmList([...pms]);
      setAllNames([...nameSet].sort());
    });
  }, []);

  // PM 筛选
  useEffect(() => {
    const names = Object.keys(allProjectsData).filter(k => {
      if (selPM === '所有人') return true;
      return allProjectsData[k]?.负责人 === selPM;
    });
    setFilteredProjects(names);
    if (names.length > 0) setSelProj(names[0]);
    else { setSelProj(''); setProjData(null); }
  }, [selPM, allProjectsData]);

  // 加载单个项目完整数据（含图片）
  useEffect(() => {
    if (!selProj) return;
    setLoading(true);
    setProjData(null);
    getProject(selProj).then(data => {
      setProjData(data);
      setLoading(false);
      const firstComp = Object.values(data?.部件列表 || {})[0];
      if (firstComp?.负责人) setRoleVals(parseOwner(firstComp.负责人));
    }).catch(() => setLoading(false));
  }, [selProj]);

  const comps = projData?.部件列表 || {};
  const compNames = Object.keys(comps);
  const customComps = compNames.filter(c => !STD_COMPONENTS.includes(c) && !c.includes('全局'));
  const allCompOpts = ['➕ 新增细分配件...', '🌐 全局进度 (Overall)', ...STD_COMPONENTS, ...customComps];

  const handleImageUpload = (e) => {
    Array.from(e.target.files).forEach(file => {
      const reader = new FileReader();
      reader.onload = ev => setImages(prev => [...prev, { id: Date.now() + Math.random(), base64: ev.target.result.split(',')[1], name: file.name }]);
      reader.readAsDataURL(file);
    });
  };

  useEffect(() => {
    const handlePaste = (e) => {
      for (const item of (e.clipboardData?.items || [])) {
        if (item.type.startsWith('image/')) {
          const blob = item.getAsFile();
          const reader = new FileReader();
          reader.onload = ev => setImages(prev => [...prev, { id: Date.now() + Math.random(), base64: ev.target.result.split(',')[1], name: 'paste.jpg' }]);
          reader.readAsDataURL(blob);
        }
      }
    };
    window.addEventListener('paste', handlePaste);
    return () => window.removeEventListener('paste', handlePaste);
  }, []);

  const resetForm = () => { setSelComps([]); setLogTxt(''); setImages([]); setIsCompleted(false); setNewCompName(''); setRecordDate(today()); };

  const handleSave = useCallback(async () => {
    const compsToProcess = selComps.length > 0 ? selComps : ['🌐 全局进度 (Overall)'];
    if (compsToProcess.includes('➕ 新增细分配件...') && !newCompName.trim()) { alert('请填写新增部件名称！'); return; }
    setSaving(true);
    const newData = JSON.parse(JSON.stringify(projData));
    if (!newData.部件列表) newData.部件列表 = {};
    const newOwnerStr = buildOwnerStr(roleVals);

    for (const cRaw of compsToProcess) {
      const actualC = cRaw === '🌐 全局进度 (Overall)' ? '全局进度'
        : cRaw === '➕ 新增细分配件...' ? `${newCompSubCat} - ${newCompName}` : cRaw;
      if (!newData.部件列表[actualC]) newData.部件列表[actualC] = { 主流程: STAGES_UNIFIED[0], 日志流: [] };
      if (newOwnerStr) newData.部件列表[actualC].负责人 = newOwnerStr;
      let baseLog = `【${evtType} | ${handoff}】${logTxt ? '补充: ' + logTxt : ''}`;
      if (isCompleted) baseLog += ' [系统]彻底完成';
      newData.部件列表[actualC].日志流.push({ 日期: recordDate, 流转: evtType, 工序: newStage, 事件: baseLog, 图片: images.map(img => img.base64) });
      newData.部件列表[actualC].主流程 = newStage;
      if (newStage === '立项') {
        newData.部件列表[actualC].日志流.push({ 日期: recordDate, 流转: '系统自动', 工序: '建模(含打印/签样)', 事件: '[系统] 立项完成自动推演', 图片: [] });
        newData.部件列表[actualC].主流程 = '建模(含打印/签样)';
      }
    }
    await saveProject(selProj, newData);
    setProjData(newData);
    resetForm();
    setSaving(false);
    alert('🎉 保存成功！');
  }, [selComps, newCompName, newCompSubCat, projData, roleVals, evtType, handoff, logTxt, isCompleted, recordDate, images, newStage, selProj]);

  const handleCreateProject = async () => {
    if (!newProjName.trim()) { alert('请填写项目名称'); return; }
    const newData = { 负责人: newProjPM, 跟单: '', Milestone: '待立项', Target: 'TBD', 发货区间: '', 部件列表: {}, 发货数据: {}, 成本数据: {} };
    await saveProject(newProjName, newData);
    setAllProjectsData(prev => ({ ...prev, [newProjName]: newData }));
    setSelProj(newProjName);
    setProjData(newData);
    setShowNewProj(false);
    setNewProjName('');
  };

  return (
    <div style={s.page}>
      {/* 顶部控制栏 */}
      <div style={s.topBar}>
        <div style={s.topItem}>
          <label style={s.label}>👤 PM 视角</label>
          <select value={selPM} onChange={e => setSelPM(e.target.value)} style={s.select}>
            {pmList.map(p => <option key={p}>{p}</option>)}
          </select>
        </div>
        <div style={{ ...s.topItem, flex: 2 }}>
          <label style={s.label}>📌 选择项目（支持键盘搜索）</label>
          <select value={selProj} onChange={e => setSelProj(e.target.value)} style={{ ...s.select, width: '100%' }}>
            {filteredProjects.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        <div style={{ alignSelf: 'flex-end' }}>
          <button style={s.btnSecondary} onClick={() => setShowNewProj(!showNewProj)}>➕ 新建项目</button>
        </div>
      </div>

      {showNewProj && (
        <div style={s.card}>
          <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div><label style={s.label}>项目名称</label>
              <input value={newProjName} onChange={e => setNewProjName(e.target.value)} placeholder="如：1/12 黑暗骑士-蝙蝠侠" style={s.input} /></div>
            <div><label style={s.label}>负责PM</label>
              <select value={newProjPM} onChange={e => setNewProjPM(e.target.value)} style={s.select}>
                {['Mo','越','袁'].map(p => <option key={p}>{p}</option>)}
              </select></div>
            <button style={s.btnPrimary} onClick={handleCreateProject}>✅ 确认创建</button>
            <button style={s.btnSecondary} onClick={() => setShowNewProj(false)}>取消</button>
          </div>
        </div>
      )}

      {loading && <div style={s.loadingBox}>⏳ 加载项目数据...</div>}
      {!loading && !projData && selProj && <div style={s.loadingBox}>无法加载项目数据</div>}
      {!selProj && <div style={s.loadingBox}>该 PM 下暂无项目</div>}

      {projData && !loading && (
        <>
          <div style={s.card}>
            <div style={s.infoRow}>
              <span style={s.infoItem}><b>负责PM：</b>{projData.负责人 || '-'}</span>
              <span style={s.infoItem}><b>里程碑：</b><span style={{ ...s.badge, background:'#4D96FF20', color:'#4D96FF' }}>{projData.Milestone || '待立项'}</span></span>
              <span style={s.infoItem}><b>开定时间：</b>{projData.Target || 'TBD'}</span>
              <span style={s.infoItem}><b>发货区间：</b>{projData.发货区间 || '-'}</span>
              <span style={s.infoItem}><b>部件数：</b>{Object.keys(comps).length}</span>
            </div>
          </div>

          <div style={s.tabs}>
            {[['matrix','🔬 透视矩阵'],['form','📝 录入交接'],['logs','📋 日志流']].map(([key, label]) => (
              <button key={key} onClick={() => setActiveTab(key)} style={{ ...s.tab, ...(activeTab===key ? s.tabActive : {}) }}>{label}</button>
            ))}
          </div>

          {activeTab === 'matrix' && (
            <div style={s.card}><h3 style={s.cardTitle}>🔬 项目进度透视矩阵</h3><HeatMatrix comps={comps} /></div>
          )}

          {activeTab === 'form' && (
            <div style={s.card}>
              <h3 style={s.cardTitle}>📝 极速交接表单</h3>

              <div style={s.formSection}>
                <div style={s.sectionTitle}>(1) 基础流转信息</div>
                <div style={s.formRow}>
                  <div style={{ ...s.formItem, flex: 2 }}>
                    <label style={s.label}>操作部件（可多选）</label>
                    <div style={s.compGrid}>
                      {allCompOpts.map(opt => (
                        <label key={opt} style={{ ...s.compCheckLabel, ...(selComps.includes(opt) ? { background: '#e8f4ff', borderColor: '#4D96FF', color: '#4D96FF' } : {}) }}>
                          <input type="checkbox" checked={selComps.includes(opt)}
                            onChange={e => setSelComps(prev => e.target.checked ? [...prev, opt] : prev.filter(x => x !== opt))} style={{ marginRight: 4 }} />
                          {opt}
                        </label>
                      ))}
                    </div>
                    {selComps.includes('➕ 新增细分配件...') && (
                      <div style={{ display:'flex', gap:8, marginTop:8 }}>
                        <select value={newCompSubCat} onChange={e => setNewCompSubCat(e.target.value)} style={s.select}>{STD_COMPONENTS.map(c => <option key={c}>{c}</option>)}</select>
                        <input value={newCompName} onChange={e => setNewCompName(e.target.value)} placeholder="细分名称" style={s.input} />
                      </div>
                    )}
                  </div>
                  <div style={s.formItem}>
                    <label style={s.label}>记录类型</label>
                    <select value={evtType} onChange={e => setEvtType(e.target.value)} style={s.select}>
                      {['🔄 内部进展/正常流转','⬅️ 收到反馈/被打回'].map(t => <option key={t}>{t}</option>)}
                    </select>
                  </div>
                  <div style={s.formItem}>
                    <label style={s.label}>目标工序阶段</label>
                    <select value={newStage} onChange={e => setNewStage(e.target.value)} style={s.select}>
                      {STAGES_UNIFIED.map(stg => <option key={stg}>{stg}</option>)}
                    </select>
                  </div>
                  <div style={s.formItem}>
                    <label style={s.label}>关联媒介</label>
                    <select value={handoff} onChange={e => setHandoff(e.target.value)} style={s.select}>
                      {HANDOFF_METHODS.map(m => <option key={m}>{m}</option>)}
                    </select>
                  </div>
                </div>
              </div>

              <div style={s.formSection}>
                <div style={s.sectionTitle}>(2) 细分角色分配</div>
                <div style={s.roleGrid}>
                  {ROLE_LIST.map(role => (
                    <div key={role} style={s.roleItem}>
                      <label style={s.label}>{role}</label>
                      <select value={roleVals[role] || ''} onChange={e => setRoleVals(prev => ({ ...prev, [role]: e.target.value }))} style={s.select}>
                        <option value="">留空</option>
                        {allNames.map(n => <option key={n}>{n}</option>)}
                      </select>
                    </div>
                  ))}
                </div>
              </div>

              <div style={s.formSection}>
                <div style={s.sectionTitle}>(3) 日期与进展</div>
                <div style={s.formRow}>
                  <div style={s.formItem}>
                    <label style={s.label}>发生日期</label>
                    <input type="date" value={recordDate} onChange={e => setRecordDate(e.target.value)} style={s.input} />
                  </div>
                  <div style={{ ...s.formItem, flex: 3 }}>
                    <label style={s.label}>详细进展（打回原因等）</label>
                    <textarea value={logTxt} onChange={e => setLogTxt(e.target.value)} rows={3} style={{ ...s.input, resize:'vertical' }} placeholder="可留空，或填写详细说明..." />
                  </div>
                </div>
              </div>

              <div style={s.formSection}>
                <div style={s.sectionTitle}>(4) 参考图（支持 Ctrl+V 直接粘贴截图）</div>
                <div style={s.imgUploadArea}>
                  <input type="file" accept="image/*" multiple onChange={handleImageUpload} style={{ display:'none' }} id="imgInput" />
                  <label htmlFor="imgInput" style={s.imgUploadBtn}>📁 选择图片</label>
                  <span style={{ color:'#aaa', fontSize:13 }}>或直接 Ctrl+V 粘贴截图</span>
                </div>
                {images.length > 0 && (
                  <div style={s.imgPreviewGrid}>
                    {images.map(img => (
                      <div key={img.id} style={s.imgPreviewItem}>
                        <img src={`data:image/jpeg;base64,${img.base64}`} alt="" style={s.previewImg} />
                        <button style={s.imgDelBtn} onClick={() => setImages(prev => prev.filter(x => x.id !== img.id))}>🗑️</button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <label style={s.checkLabel}>
                <input type="checkbox" checked={isCompleted} onChange={e => setIsCompleted(e.target.checked)} />
                {' '}✅ 标记所选部件的【{newStage}】阶段已彻底完成（矩阵变绿）
              </label>

              <button style={{ ...s.btnPrimary, width:'100%', marginTop:16, padding:'12px 0', fontSize:16 }}
                onClick={handleSave} disabled={saving}>
                {saving ? '保存中...' : '🚀 批量保存交接与进度'}
              </button>
            </div>
          )}

          {activeTab === 'logs' && (
            <div style={s.card}>
              <div style={{ display:'flex', alignItems:'center', gap:12, marginBottom:16 }}>
                <h3 style={{ ...s.cardTitle, margin:0 }}>📋 日志流水</h3>
                <select value={logFilterComp} onChange={e => setLogFilterComp(e.target.value)} style={s.select}>
                  <option value="全部">全部部件</option>
                  {compNames.map(c => <option key={c}>{c}</option>)}
                </select>
              </div>
              <LogStream comps={comps} selComp={logFilterComp} />
            </div>
          )}
        </>
      )}
    </div>
  );
}

const s = {
  page: { padding:24, fontFamily:"'PingFang SC','Microsoft YaHei',sans-serif" },
  topBar: { display:'flex', alignItems:'flex-end', gap:12, marginBottom:16, flexWrap:'wrap' },
  topItem: { display:'flex', flexDirection:'column', gap:4 },
  card: { background:'#fff', borderRadius:8, padding:20, marginBottom:16, boxShadow:'0 2px 8px rgba(0,0,0,0.06)' },
  cardTitle: { margin:'0 0 16px', fontSize:16, fontWeight:700, color:'#222' },
  infoRow: { display:'flex', gap:24, flexWrap:'wrap' },
  infoItem: { fontSize:14, color:'#444' },
  badge: { display:'inline-block', padding:'2px 8px', borderRadius:12, fontSize:12, fontWeight:600 },
  tabs: { display:'flex', gap:8, marginBottom:16 },
  tab: { padding:'8px 20px', borderRadius:6, border:'1px solid #e0e0e0', background:'#fff', cursor:'pointer', fontSize:14, color:'#666' },
  tabActive: { background:'#4D96FF', color:'#fff', border:'1px solid #4D96FF', fontWeight:600 },
  formSection: { marginBottom:20, paddingBottom:20, borderBottom:'1px solid #f0f0f0' },
  sectionTitle: { fontWeight:600, color:'#444', marginBottom:10, fontSize:14 },
  formRow: { display:'flex', gap:16, flexWrap:'wrap' },
  formItem: { flex:1, minWidth:160, display:'flex', flexDirection:'column', gap:4 },
  label: { fontSize:12, color:'#666', fontWeight:600 },
  select: { padding:'8px 10px', borderRadius:6, border:'1px solid #e0e0e0', fontSize:13, outline:'none', background:'#fff', cursor:'pointer' },
  input: { padding:'8px 10px', borderRadius:6, border:'1px solid #e0e0e0', fontSize:13, outline:'none', width:'100%' },
  compGrid: { display:'flex', flexWrap:'wrap', gap:8 },
  compCheckLabel: { display:'flex', alignItems:'center', fontSize:13, cursor:'pointer', padding:'4px 8px', borderRadius:4, border:'1px solid #e0e0e0', background:'#fafafa', transition:'all 0.15s' },
  roleGrid: { display:'flex', gap:12, flexWrap:'wrap' },
  roleItem: { display:'flex', flexDirection:'column', gap:4, minWidth:120 },
  imgUploadArea: { display:'flex', alignItems:'center', gap:12, marginBottom:12 },
  imgUploadBtn: { padding:'8px 16px', background:'#f5f5f5', border:'1px dashed #d9d9d9', borderRadius:6, cursor:'pointer', fontSize:13 },
  imgPreviewGrid: { display:'flex', flexWrap:'wrap', gap:8 },
  imgPreviewItem: { position:'relative' },
  previewImg: { width:120, height:90, objectFit:'cover', borderRadius:6, border:'1px solid #e0e0e0' },
  imgDelBtn: { position:'absolute', top:2, right:2, background:'rgba(0,0,0,0.5)', border:'none', borderRadius:4, color:'#fff', cursor:'pointer', fontSize:12, padding:'2px 4px' },
  checkLabel: { display:'flex', alignItems:'center', gap:6, fontSize:14, cursor:'pointer' },
  btnPrimary: { padding:'8px 20px', background:'#4D96FF', color:'#fff', border:'none', borderRadius:6, cursor:'pointer', fontSize:14, fontWeight:600 },
  btnSecondary: { padding:'8px 16px', background:'#f5f5f5', color:'#444', border:'1px solid #e0e0e0', borderRadius:6, cursor:'pointer', fontSize:14 },
  loadingBox: { textAlign:'center', padding:60, color:'#aaa', fontSize:16, background:'#fff', borderRadius:8 },
  empty: { textAlign:'center', padding:24, color:'#aaa', fontSize:14 },
  matrix: { borderCollapse:'collapse', width:'100%', fontSize:12 },
  mTh: { padding:'6px 8px', background:'#f5f7fa', border:'1px solid #eee', textAlign:'center', fontSize:11, color:'#666' },
  mTdLabel: { padding:'6px 10px', border:'1px solid #eee', minWidth:120, background:'#fafafa' },
  mTdCell: { width:28, height:28, border:'1px solid #eee' },
  legend: { display:'flex', gap:16, marginTop:10, fontSize:12, color:'#666' },
  legendItem: { display:'flex', alignItems:'center', gap:4 },
  legendDot: { width:12, height:12, borderRadius:2, display:'inline-block', border:'1px solid #e0e0e0' },
  logList: { display:'flex', flexDirection:'column', gap:12 },
  logItem: { padding:12, background:'#fafafa', borderRadius:8, border:'1px solid #f0f0f0' },
  logHeader: { display:'flex', alignItems:'center', gap:8, marginBottom:6 },
  logDate: { fontSize:12, color:'#888', fontWeight:600 },
  logComp: { fontSize:11, padding:'1px 6px', borderRadius:10, fontWeight:600 },
  logStage: { fontSize:11, color:'#aaa' },
  logEvent: { fontSize:13, color:'#333', lineHeight:1.5 },
  logImgs: { display:'flex', gap:8, flexWrap:'wrap', marginTop:8 },
  logImg: { height:80, borderRadius:4, border:'1px solid #e0e0e0' },
};
