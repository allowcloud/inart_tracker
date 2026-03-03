import React, { useState, useEffect } from 'react';
import { getProjects, getProject } from '../api';

const STAGES_UNIFIED = [
  "立项","建模(含打印/签样)","涂装","设计","工程拆件","手板/结构板",
  "官图","工厂复样(含胶件/上色等)","大货","⏸️ 暂停/搁置","✅ 已完成(结束)"
];

const STAGE_COLORS = {
  "立项":"#FFB84C","建模(含打印/签样)":"#2CD3E1","涂装":"#F47C7C",
  "设计":"#A555EC","工程拆件":"#4D96FF","手板/结构板":"#4D96FF",
  "官图":"#6BCB77","工厂复样(含胶件/上色等)":"#6BCB77","大货":"#6BCB77",
  "⏸️ 暂停/搁置":"#B2B2B2","✅ 已完成(结束)":"#52c41a"
};

function formatDate(d) {
  if (!d) return '';
  try { return new Date(d).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }); } catch { return d; }
}

export default function HistoryTrace() {
  const [projects, setProjects] = useState({});
  const [selectedProj, setSelectedProj] = useState('');
  const [projData, setProjData] = useState(null);
  const [selectedComp, setSelectedComp] = useState('全部');
  const [selectedStage, setSelectedStage] = useState('全部');
  const [searchText, setSearchText] = useState('');
  const [loading, setLoading] = useState(false);
  const [viewMode, setViewMode] = useState('timeline'); // 'timeline' | 'table' | 'matrix'

  useEffect(() => { getProjects().then(p => setProjects(p || {})); }, []);

  useEffect(() => {
    if (selectedProj) {
      setLoading(true);
      getProject(selectedProj).then(d => {
        setProjData(d);
        setSelectedComp('全部');
        setSelectedStage('全部');
        setLoading(false);
      });
    } else {
      setProjData(null);
    }
  }, [selectedProj]);

  const comps = projData?.部件列表 || {};
  const compList = ['全部', ...Object.keys(comps)];

  // Collect all logs
  const allLogs = [];
  Object.entries(comps).forEach(([cName, cData]) => {
    (cData?.日志流 || []).forEach((log, i) => {
      allLogs.push({ ...log, _comp: cName, _idx: i });
    });
  });
  allLogs.sort((a, b) => {
    const da = a.日期 || '', db = b.日期 || '';
    if (da !== db) return db.localeCompare(da);
    return (b.时间 || '').localeCompare(a.时间 || '');
  });

  const filteredLogs = allLogs.filter(log => {
    if (selectedComp !== '全部' && log._comp !== selectedComp) return false;
    if (selectedStage !== '全部' && log.工序 !== selectedStage) return false;
    if (searchText) {
      const q = searchText.toLowerCase();
      if (!(log.事件 || '').toLowerCase().includes(q) &&
          !(log.备注 || '').toLowerCase().includes(q) &&
          !(log._comp || '').toLowerCase().includes(q)) return false;
    }
    return true;
  });

  // Group by date for timeline
  const byDate = {};
  filteredLogs.forEach(log => {
    const d = log.日期 || '未知日期';
    if (!byDate[d]) byDate[d] = [];
    byDate[d].push(log);
  });
  const dateGroups = Object.entries(byDate).sort(([a], [b]) => b.localeCompare(a));

  // Stage stats
  const stageStats = {};
  allLogs.forEach(log => {
    if (log.工序) {
      if (!stageStats[log.工序]) stageStats[log.工序] = 0;
      stageStats[log.工序]++;
    }
  });

  // Component progress summary
  const compSummary = Object.entries(comps).map(([name, data]) => {
    const logs = data?.日志流 || [];
    const firstLog = logs[0];
    const lastLog = logs[logs.length - 1];
    return {
      name,
      stage: data?.主流程 || '-',
      logCount: logs.length,
      firstDate: firstLog?.日期 || '-',
      lastDate: lastLog?.日期 || '-',
      lastEvent: lastLog?.事件 || '-',
    };
  });

  return (
    <div style={s.page}>
      {/* Header */}
      <div style={s.header}>
        <div>
          <div style={s.title}>🔍 历史溯源</div>
          <div style={s.subtitle}>完整追溯项目每个部件的历史流转记录</div>
        </div>
      </div>

      {/* Controls */}
      <div style={s.controlBar}>
        <select
          value={selectedProj}
          onChange={e => setSelectedProj(e.target.value)}
          style={{ ...s.select, width: 220 }}
        >
          <option value="">-- 选择项目 --</option>
          {Object.keys(projects).map(p => <option key={p} value={p}>{p}</option>)}
        </select>

        {selectedProj && (
          <>
            <select value={selectedComp} onChange={e => setSelectedComp(e.target.value)} style={s.select}>
              {compList.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
            <select value={selectedStage} onChange={e => setSelectedStage(e.target.value)} style={s.select}>
              <option value="全部">全部工序</option>
              {STAGES_UNIFIED.map(st => <option key={st} value={st}>{st}</option>)}
            </select>
            <input
              style={s.searchInput}
              placeholder="🔍 搜索事件/备注..."
              value={searchText}
              onChange={e => setSearchText(e.target.value)}
            />
          </>
        )}

        {selectedProj && (
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
            {[
              { key: 'timeline', label: '📅 时间轴' },
              { key: 'table', label: '📋 列表' },
              { key: 'matrix', label: '📊 部件概览' },
            ].map(v => (
              <button
                key={v.key}
                style={{ ...s.viewBtn, ...(viewMode === v.key ? s.viewBtnActive : {}) }}
                onClick={() => setViewMode(v.key)}
              >{v.label}</button>
            ))}
          </div>
        )}
      </div>

      {!selectedProj && (
        <div style={s.emptyState}>
          <div style={{ fontSize: 48, marginBottom: 16 }}>🔍</div>
          <div style={{ fontSize: 16, color: '#888' }}>请选择一个项目开始溯源</div>
        </div>
      )}

      {selectedProj && loading && (
        <div style={s.emptyState}>
          <div style={{ fontSize: 14, color: '#aaa' }}>加载中...</div>
        </div>
      )}

      {selectedProj && !loading && projData && (
        <>
          {/* Summary bar */}
          <div style={s.summaryBar}>
            <div style={s.summaryItem}>
              <span style={s.summaryNum}>{allLogs.length}</span>
              <span style={s.summaryLabel}>总日志</span>
            </div>
            <div style={s.summaryDivider} />
            <div style={s.summaryItem}>
              <span style={s.summaryNum}>{Object.keys(comps).length}</span>
              <span style={s.summaryLabel}>部件数</span>
            </div>
            <div style={s.summaryDivider} />
            <div style={s.summaryItem}>
              <span style={s.summaryNum}>{filteredLogs.length}</span>
              <span style={s.summaryLabel}>当前筛选</span>
            </div>
            <div style={s.summaryDivider} />
            {Object.entries(stageStats).slice(0, 4).map(([stage, count]) => (
              <div key={stage} style={s.stageStatItem}>
                <span style={{ ...s.stageDot, background: STAGE_COLORS[stage] || '#aaa' }} />
                <span style={s.stageStatName}>{stage.length > 4 ? stage.slice(0, 4) + '..' : stage}</span>
                <span style={s.stageStatCount}>{count}</span>
              </div>
            ))}
          </div>

          {/* Timeline View */}
          {viewMode === 'timeline' && (
            <div style={s.timelineWrap}>
              {dateGroups.length === 0 ? (
                <div style={s.empty}>没有找到匹配的记录</div>
              ) : (
                dateGroups.map(([date, logs]) => (
                  <div key={date} style={s.dateGroup}>
                    <div style={s.dateLine}>
                      <span style={s.dateChip}>{date}</span>
                    </div>
                    <div style={s.logItems}>
                      {logs.map((log, i) => {
                        const stageColor = STAGE_COLORS[log.工序] || '#aaa';
                        return (
                          <div key={i} style={s.timelineItem}>
                            <div style={{ ...s.timelineDot, background: stageColor }} />
                            <div style={s.timelineContent}>
                              <div style={s.timelineHeader}>
                                <span style={{ ...s.compBadge, background: stageColor + '20', color: stageColor }}>
                                  {log._comp}
                                </span>
                                <span style={{ ...s.stageBadge, borderColor: stageColor + '60', color: stageColor }}>
                                  {log.工序}
                                </span>
                                {log.时间 && <span style={s.timeText}>{log.时间}</span>}
                                {log.来源 && <span style={s.sourceTag}>{log.来源}</span>}
                              </div>
                              <div style={s.eventText}>{log.事件}</div>
                              {log.备注 && <div style={s.remarkText}>{log.备注}</div>}
                              {log.交接方式 && (
                                <div style={s.handoffText}>📤 交接: {log.交接方式}</div>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Table View */}
          {viewMode === 'table' && (
            <div style={s.tableCard}>
              <table style={s.table}>
                <thead>
                  <tr style={s.thead}>
                    {['日期', '时间', '部件', '工序', '事件', '备注', '交接方式', '来源'].map(h => (
                      <th key={h} style={s.th}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filteredLogs.length === 0 ? (
                    <tr><td colSpan={8} style={s.empty}>没有匹配的记录</td></tr>
                  ) : (
                    filteredLogs.map((log, i) => {
                      const stageColor = STAGE_COLORS[log.工序] || '#aaa';
                      return (
                        <tr key={i} style={s.tr}>
                          <td style={s.td}>{log.日期}</td>
                          <td style={{ ...s.td, color: '#aaa' }}>{log.时间 || '-'}</td>
                          <td style={s.td}>
                            <span style={{ ...s.compBadge, background: stageColor + '20', color: stageColor, fontSize: 11 }}>
                              {log._comp}
                            </span>
                          </td>
                          <td style={s.td}>
                            <span style={{ fontSize: 11, color: stageColor, fontWeight: 600 }}>{log.工序}</span>
                          </td>
                          <td style={s.td}><strong>{log.事件}</strong></td>
                          <td style={{ ...s.td, color: '#888', fontSize: 12 }}>{log.备注 || '-'}</td>
                          <td style={{ ...s.td, fontSize: 12 }}>{log.交接方式 || '-'}</td>
                          <td style={s.td}>
                            {log.来源 && <span style={s.sourceTag}>{log.来源}</span>}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          )}

          {/* Matrix View */}
          {viewMode === 'matrix' && (
            <div style={s.matrixWrap}>
              {compSummary.map(comp => {
                const stageColor = STAGE_COLORS[comp.stage] || '#aaa';
                return (
                  <div key={comp.name} style={s.compCard}>
                    <div style={s.compCardHeader}>
                      <span style={s.compCardName}>{comp.name}</span>
                      <span style={{ ...s.stagePill, background: stageColor + '20', color: stageColor }}>
                        {comp.stage}
                      </span>
                    </div>
                    <div style={s.compCardStats}>
                      <div style={s.compStat}>
                        <span style={s.compStatNum}>{comp.logCount}</span>
                        <span style={s.compStatLabel}>日志条数</span>
                      </div>
                      <div style={s.compStat}>
                        <span style={s.compStatNum}>{comp.firstDate}</span>
                        <span style={s.compStatLabel}>首次记录</span>
                      </div>
                      <div style={s.compStat}>
                        <span style={s.compStatNum}>{comp.lastDate}</span>
                        <span style={s.compStatLabel}>最近更新</span>
                      </div>
                    </div>
                    <div style={s.compLastEvent}>
                      <span style={s.compLastLabel}>最新动态：</span>
                      {comp.lastEvent}
                    </div>
                    {/* Mini progress bar */}
                    <div style={s.progressBarWrap}>
                      {STAGES_UNIFIED.filter(st => !st.includes('⏸️')).map((st, i) => {
                        const compData = comps[comp.name];
                        const curStage = compData?.主流程 || '';
                        const curIdx = STAGES_UNIFIED.indexOf(curStage);
                        const stIdx = STAGES_UNIFIED.indexOf(st);
                        let bg = '#f0f0f0';
                        if (curStage === '✅ 已完成(结束)') bg = '#52c41a';
                        else if (stIdx === curIdx && curIdx >= 0) bg = STAGE_COLORS[st] || '#4D96FF';
                        else if (stIdx < curIdx && curIdx >= 0) bg = '#bae7ff';
                        return <div key={st} title={st} style={{ ...s.progCell, background: bg }} />;
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}

const s = {
  page: { padding: 24, fontFamily: "'PingFang SC', 'Microsoft YaHei', sans-serif" },
  header: { marginBottom: 16 },
  title: { fontSize: 22, fontWeight: 700, color: '#1a1a2e' },
  subtitle: { fontSize: 13, color: '#888', marginTop: 4 },
  controlBar: { display: 'flex', gap: 10, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' },
  select: { padding: '7px 10px', border: '1px solid #e8e8e8', borderRadius: 6, fontSize: 13, background: '#fff' },
  searchInput: { padding: '7px 12px', border: '1px solid #e8e8e8', borderRadius: 6, fontSize: 13, width: 200 },
  viewBtn: { padding: '6px 12px', border: '1px solid #e8e8e8', borderRadius: 6, cursor: 'pointer', fontSize: 12, background: '#fafafa', color: '#555' },
  viewBtnActive: { background: '#1a1a2e', color: '#fff', border: '1px solid #1a1a2e' },
  emptyState: { display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '50vh' },
  summaryBar: { display: 'flex', alignItems: 'center', gap: 16, background: '#fff', borderRadius: 10, padding: '12px 20px', marginBottom: 16, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', flexWrap: 'wrap' },
  summaryItem: { display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 },
  summaryNum: { fontSize: 22, fontWeight: 800, color: '#1a1a2e', lineHeight: 1 },
  summaryLabel: { fontSize: 11, color: '#aaa' },
  summaryDivider: { width: 1, height: 32, background: '#f0f0f0', margin: '0 4px' },
  stageStatItem: { display: 'flex', alignItems: 'center', gap: 4 },
  stageDot: { width: 8, height: 8, borderRadius: '50%', flexShrink: 0 },
  stageStatName: { fontSize: 11, color: '#666' },
  stageStatCount: { fontSize: 11, fontWeight: 700, color: '#1a1a2e', background: '#f5f5f5', padding: '1px 5px', borderRadius: 3 },
  timelineWrap: { display: 'flex', flexDirection: 'column', gap: 0 },
  dateGroup: { marginBottom: 0 },
  dateLine: { display: 'flex', alignItems: 'center', margin: '16px 0 10px' },
  dateChip: { background: '#1a1a2e', color: '#fff', padding: '4px 12px', borderRadius: 20, fontSize: 12, fontWeight: 700 },
  logItems: { display: 'flex', flexDirection: 'column', gap: 8, marginLeft: 20, paddingLeft: 16, borderLeft: '2px solid #f0f0f0' },
  timelineItem: { display: 'flex', gap: 12, position: 'relative', alignItems: 'flex-start' },
  timelineDot: { width: 10, height: 10, borderRadius: '50%', flexShrink: 0, marginTop: 5, marginLeft: -21, position: 'relative', zIndex: 1, boxShadow: '0 0 0 3px #fff' },
  timelineContent: { flex: 1, background: '#fff', borderRadius: 8, padding: '10px 14px', boxShadow: '0 1px 4px rgba(0,0,0,0.06)' },
  timelineHeader: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 6 },
  compBadge: { padding: '2px 8px', borderRadius: 4, fontSize: 12, fontWeight: 700 },
  stageBadge: { padding: '2px 7px', borderRadius: 4, fontSize: 11, border: '1px solid', fontWeight: 600 },
  timeText: { fontSize: 11, color: '#aaa' },
  sourceTag: { background: '#fff7e6', color: '#fa8c16', fontSize: 10, padding: '1px 5px', borderRadius: 3, fontWeight: 600 },
  eventText: { fontSize: 13, fontWeight: 600, color: '#1a1a2e' },
  remarkText: { fontSize: 12, color: '#888', marginTop: 3 },
  handoffText: { fontSize: 11, color: '#4D96FF', marginTop: 3 },
  tableCard: { background: '#fff', borderRadius: 10, boxShadow: '0 2px 8px rgba(0,0,0,0.07)', overflow: 'auto' },
  table: { width: '100%', borderCollapse: 'collapse' },
  thead: { background: '#f5f7fa' },
  th: { padding: '11px 12px', textAlign: 'left', fontSize: 12, fontWeight: 700, color: '#555', borderBottom: '1px solid #f0f0f0', whiteSpace: 'nowrap' },
  tr: { borderBottom: '1px solid #f9f9f9' },
  td: { padding: '10px 12px', fontSize: 13, color: '#333', verticalAlign: 'middle' },
  empty: { textAlign: 'center', color: '#bbb', padding: '40px 0', fontSize: 13 },
  matrixWrap: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 16 },
  compCard: { background: '#fff', borderRadius: 10, padding: 16, boxShadow: '0 2px 8px rgba(0,0,0,0.07)' },
  compCardHeader: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 },
  compCardName: { fontSize: 15, fontWeight: 700, color: '#1a1a2e' },
  stagePill: { padding: '3px 9px', borderRadius: 20, fontSize: 11, fontWeight: 600 },
  compCardStats: { display: 'flex', gap: 12, marginBottom: 10 },
  compStat: { flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center' },
  compStatNum: { fontSize: 14, fontWeight: 700, color: '#1a1a2e' },
  compStatLabel: { fontSize: 10, color: '#aaa' },
  compLastEvent: { fontSize: 12, color: '#555', background: '#f9f9f9', borderRadius: 6, padding: '6px 10px', marginBottom: 10 },
  compLastLabel: { color: '#aaa', marginRight: 4 },
  progressBarWrap: { display: 'flex', gap: 2 },
  progCell: { flex: 1, height: 6, borderRadius: 2 },
};
