import React, { useEffect, useState, useMemo, useRef } from 'react';
import { getProjects } from '../api';

const MILESTONE_ORDER = ['待立项','研发中','暂停研发','下模中','生产中','生产结束','项目结束撒花🎉'];
const STAGES_UNIFIED = ["立项","建模(含打印/签样)","涂装","设计","工程拆件","手板/结构板","官图","工厂复样(含胶件/上色等)","大货","⏸️ 暂停/搁置","✅ 已完成(结束)"];
const STAGE_COLORS = {
  "立项":"#FFB84C","建模(含打印/签样)":"#2CD3E1","涂装":"#F47C7C",
  "设计":"#A555EC","工程拆件":"#4D96FF","手板/结构板":"#4D96FF",
  "官图":"#6BCB77","工厂复样(含胶件/上色等)":"#6BCB77","大货":"#6BCB77",
  "⏸️ 暂停/搁置":"#B2B2B2","✅ 已完成(结束)":"#52c41a"
};

function getRiskStatus(milestone, target) {
  const ms = String(milestone || '').trim();
  const tgt = String(target || 'TBD').trim();
  const isFinished = ['生产结束','项目结束撒花🎉'].includes(ms);
  if (ms === '暂停研发') return { text: '⏸️ 暂停', color: '#888' };
  if (isFinished) return { text: '🏁 已结案', color: '#52c41a' };
  if (tgt !== 'TBD' && tgt !== '') {
    try { const t = new Date(tgt); if (!isNaN(t) && new Date() > t) return { text: '🔴 逾期', color: '#ff4d4f' }; } catch {}
  }
  if (['生产中','下模中'].includes(ms)) return { text: '🟢 生产期', color: '#52c41a' };
  if (ms.includes('研发') || ms === '待立项') return { text: '🟡 研发期', color: '#faad14' };
  return { text: '⚪ 未知', color: '#aaa' };
}

function getLatestLog(proj) {
  const comps = proj?.部件列表 || {};
  let latestDate = null, latestEvent = '无数据', latestComp = '-';
  for (const [cName, cData] of Object.entries(comps)) {
    const logs = cData?.日志流 || [];
    if (logs.length > 0) {
      const last = logs[logs.length - 1];
      const d = new Date(last.日期);
      if (!latestDate || d > latestDate) { latestDate = d; latestEvent = last.事件 || '无数据'; latestComp = cName; }
    }
  }
  return { latestDate, latestEvent, latestComp };
}

// ==========================================
// 悬浮卡片
// ==========================================
function HoverCard({ proj, projName, mousePos, visible }) {
  if (!visible || !proj) return null;
  const comps = proj?.部件列表 || {};

  // 最新5条日志（所有部件合并排序）
  const allLogs = [];
  Object.entries(comps).forEach(([cName, cData]) => {
    (cData?.日志流 || []).forEach(log => allLogs.push({ ...log, _comp: cName }));
  });
  allLogs.sort((a, b) => (b.日期 || '').localeCompare(a.日期 || ''));
  const recentLogs = allLogs.slice(0, 5);

  // 卡片位置：跟随鼠标，避免超出屏幕
  const cardW = 420;
  const cardH = 360;
  let left = mousePos.x + 16;
  let top = mousePos.y - 20;
  if (left + cardW > window.innerWidth - 20) left = mousePos.x - cardW - 16;
  if (top + cardH > window.innerHeight - 20) top = window.innerHeight - cardH - 20;

  return (
    <div style={{ ...s.hoverCard, left, top, width: cardW }}>
      {/* 卡片标题 */}
      <div style={s.hcTitle}>{projName}</div>

      {/* 部件矩阵 mini 版 */}
      {Object.keys(comps).length > 0 && (
        <div style={s.hcSection}>
          <div style={s.hcSectionTitle}>📊 部件进度</div>
          <div style={{ overflowX: 'auto' }}>
            <table style={s.miniMatrix}>
              <thead>
                <tr>
                  <th style={s.mmTh}>部件</th>
                  {STAGES_UNIFIED.map(stg => (
                    <th key={stg} title={stg} style={s.mmStgTh}>
                      {stg.slice(0, 2)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Object.entries(comps).map(([cName, cData]) => {
                  const curStage = cData?.主流程 || '';
                  const curIdx = STAGES_UNIFIED.indexOf(curStage);
                  const completedStages = new Set();
                  (cData?.日志流 || []).forEach(log => {
                    if (['彻底完成','OK','通过','完结','结束','撒花'].some(k => (log.事件||'').includes(k)))
                      completedStages.add(log.工序);
                  });
                  return (
                    <tr key={cName}>
                      <td style={s.mmTdLabel} title={cName}>{cName.length > 6 ? cName.slice(0,6)+'…' : cName}</td>
                      {STAGES_UNIFIED.map((stg, i) => {
                        let bg = '#f0f0f0';
                        if (curStage === '✅ 已完成(结束)') bg = '#52c41a';
                        else if (completedStages.has(stg)) bg = '#95de64';
                        else if (i === curIdx && curIdx >= 0) bg = STAGE_COLORS[stg] || '#4D96FF';
                        else if (i < curIdx && curIdx >= 0) bg = '#bae7ff';
                        return <td key={stg} title={stg} style={{ ...s.mmCell, background: bg }} />;
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 最新日志 */}
      <div style={s.hcSection}>
        <div style={s.hcSectionTitle}>📋 最新动态</div>
        {recentLogs.length === 0
          ? <div style={{ color: '#aaa', fontSize: 12 }}>暂无记录</div>
          : recentLogs.map((log, i) => {
            let clean = log.事件 || '';
            if (clean.includes('补充:')) clean = clean.split('补充:').pop().split('[系统]')[0].trim();
            else if (clean.includes('】')) clean = clean.split('】').pop().split('[系统]')[0].trim();
            return (
              <div key={i} style={s.hcLogItem}>
                <span style={s.hcLogDate}>{log.日期}</span>
                <span style={{ ...s.hcLogComp, background: (STAGE_COLORS[log.工序]||'#4D96FF')+'20', color: STAGE_COLORS[log.工序]||'#4D96FF' }}>
                  {log._comp}
                </span>
                <span style={s.hcLogText}>{clean.slice(0, 40)}{clean.length > 40 ? '…' : ''}</span>
              </div>
            );
          })
        }
      </div>
    </div>
  );
}

// ==========================================
// 主组件
// ==========================================
export default function Dashboard() {
  const [projects, setProjects] = useState({});
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [filterPM, setFilterPM] = useState('所有人');
  const [filterMS, setFilterMS] = useState('全部');
  const [sortBy, setSortBy] = useState('断更');

  // 悬浮卡片状态
  const [hoverProj, setHoverProj] = useState(null);
  const [hoverName, setHoverName] = useState('');
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const [cardVisible, setCardVisible] = useState(false);
  const hoverTimer = useRef(null);

  useEffect(() => {
    getProjects().then(data => { setProjects(data); setLoading(false); }).catch(() => setLoading(false));
  }, []);

  const pmList = useMemo(() => {
    const pms = new Set(['所有人']);
    Object.values(projects).forEach(p => { if (p?.负责人) pms.add(p.负责人); });
    return [...pms];
  }, [projects]);

  const rows = useMemo(() => {
    return Object.entries(projects)
      .filter(([name, data]) => {
        if (filterPM !== '所有人' && data?.负责人 !== filterPM) return false;
        if (filterMS !== '全部' && data?.Milestone !== filterMS) return false;
        if (search && !name.includes(search)) return false;
        return true;
      })
      .map(([name, data]) => {
        const { latestDate, latestEvent, latestComp } = getLatestLog(data);
        const risk = getRiskStatus(data?.Milestone, data?.Target);
        let cleanEvent = latestEvent;
        if (cleanEvent.includes('补充:')) cleanEvent = cleanEvent.split('补充:').pop().split('[系统]')[0].trim();
        else if (cleanEvent.includes('】')) cleanEvent = cleanEvent.split('】').pop().split('[系统]')[0].trim();
        return {
          name, data, risk, latestDate,
          daysSince: latestDate ? Math.floor((new Date() - latestDate) / 86400000) : 9999,
          latestEvent: cleanEvent, latestComp,
          milestone: data?.Milestone || '待立项',
          pm: data?.负责人 || '-',
          target: data?.Target || 'TBD',
          ship: data?.发货区间 || '-',
        };
      })
      .sort((a, b) => {
        if (sortBy === '断更') return b.daysSince - a.daysSince;
        if (sortBy === '里程碑') return MILESTONE_ORDER.indexOf(a.milestone) - MILESTONE_ORDER.indexOf(b.milestone);
        if (sortBy === '开定时间') { const av = a.target==='TBD'||!a.target?'9999':a.target; const bv = b.target==='TBD'||!b.target?'9999':b.target; return av.localeCompare(bv); }
        if (sortBy === '发货区间') { const av = !a.ship||a.ship==='-'?'9999':a.ship; const bv = !b.ship||b.ship==='-'?'9999':b.ship; return av.localeCompare(bv); }
        return a.name.localeCompare(b.name);
      });
  }, [projects, filterPM, filterMS, search, sortBy]);

  const stats = useMemo(() => {
    const all = Object.values(projects);
    return {
      total: all.length,
      active: all.filter(p => p?.Milestone === '研发中').length,
      production: all.filter(p => ['生产中','下模中'].includes(p?.Milestone)).length,
      overdue: all.filter(p => getRiskStatus(p?.Milestone, p?.Target).text === '🔴 逾期').length,
    };
  }, [projects]);

  // 鼠标移动追踪
  const handleMouseMove = (e) => setMousePos({ x: e.clientX, y: e.clientY });

  const handleRowEnter = (row) => {
    clearTimeout(hoverTimer.current);
    hoverTimer.current = setTimeout(() => {
      setHoverName(row.name);
      setHoverProj(row.data);
      setCardVisible(true);
    }, 300); // 300ms 延迟，避免闪烁
  };

  const handleRowLeave = () => {
    clearTimeout(hoverTimer.current);
    hoverTimer.current = setTimeout(() => setCardVisible(false), 200);
  };

  if (loading) return (
    <div style={s.loadingWrap}>
      <div style={s.spinner} />
      <p style={{ color: '#888', marginTop: 16 }}>加载中...</p>
    </div>
  );

  return (
    <div style={s.page} onMouseMove={handleMouseMove}>
      {/* 统计卡片 */}
      <div style={s.statsRow}>
        {[
          { label: '项目总数', value: stats.total, color: '#4D96FF' },
          { label: '研发中', value: stats.active, color: '#A555EC' },
          { label: '生产期', value: stats.production, color: '#52c41a' },
          { label: '逾期预警', value: stats.overdue, color: '#ff4d4f' },
        ].map(stat => (
          <div key={stat.label} style={{ ...s.statCard, borderTop: `3px solid ${stat.color}` }}>
            <div style={{ ...s.statValue, color: stat.color }}>{stat.value}</div>
            <div style={s.statLabel}>{stat.label}</div>
          </div>
        ))}
      </div>

      {/* 筛选栏 */}
      <div style={s.filterBar}>
        <input placeholder="🔍 搜索项目名..." value={search} onChange={e => setSearch(e.target.value)} style={s.searchInput} />
        <select value={filterPM} onChange={e => setFilterPM(e.target.value)} style={s.select}>
          {pmList.map(pm => <option key={pm}>{pm}</option>)}
        </select>
        <select value={filterMS} onChange={e => setFilterMS(e.target.value)} style={s.select}>
          <option value="全部">全部阶段</option>
          {MILESTONE_ORDER.map(ms => <option key={ms}>{ms}</option>)}
        </select>
        <select value={sortBy} onChange={e => setSortBy(e.target.value)} style={s.select}>
          <option value="断更">按断更排序</option>
          <option value="里程碑">按里程碑排序</option>
          <option value="开定时间">按开定时间↑</option>
          <option value="发货区间">按发货区间↑</option>
          <option value="名称">按名称排序</option>
        </select>
        <span style={s.countBadge}>{rows.length} 个项目</span>
      </div>

      {/* 项目表格 */}
      <div style={s.tableWrap}>
        <table style={s.table}>
          <thead>
            <tr style={s.thead}>
              {['状态','项目名称','负责PM','里程碑','开定时间','发货区间','断更','最新动态'].map(h => (
                <th key={h} style={s.th}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={row.name}
                style={{ ...s.tr, background: i % 2 === 0 ? '#fff' : '#f9f9fb', cursor: 'pointer' }}
                onMouseEnter={() => handleRowEnter(row)}
                onMouseLeave={handleRowLeave}
              >
                <td style={s.td}>
                  <span style={{ ...s.badge, background: row.risk.color + '20', color: row.risk.color }}>{row.risk.text}</span>
                </td>
                <td style={{ ...s.td, fontWeight: 600, maxWidth: 200 }}>{row.name}</td>
                <td style={s.td}>{row.pm}</td>
                <td style={s.td}><span style={s.msBadge}>{row.milestone}</span></td>
                <td style={s.td}>{row.target}</td>
                <td style={s.td}>{row.ship}</td>
                <td style={{ ...s.td, color: row.daysSince > 14 ? '#ff4d4f' : '#52c41a', fontWeight: 600 }}>
                  {row.daysSince === 9999 ? '-' : row.daysSince + ' 天'}
                </td>
                <td style={{ ...s.td, color: '#555', maxWidth: 300, fontSize: 12 }}>
                  {row.latestComp !== '-' && <span style={s.compTag}>[{row.latestComp}]</span>}
                  {' '}{row.latestEvent}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <div style={s.empty}>没有符合条件的项目</div>}
      </div>

      {/* 悬浮卡片 */}
      <HoverCard proj={hoverProj} projName={hoverName} mousePos={mousePos} visible={cardVisible} />
    </div>
  );
}

const s = {
  page: { padding: 24, fontFamily: "'PingFang SC','Microsoft YaHei',sans-serif" },
  loadingWrap: { display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'60vh' },
  spinner: { width:40, height:40, border:'4px solid #f0f0f0', borderTop:'4px solid #4D96FF', borderRadius:'50%', animation:'spin 1s linear infinite' },
  statsRow: { display:'flex', gap:16, marginBottom:24 },
  statCard: { flex:1, background:'#fff', borderRadius:8, padding:'16px 20px', boxShadow:'0 2px 8px rgba(0,0,0,0.06)' },
  statValue: { fontSize:32, fontWeight:700 },
  statLabel: { fontSize:13, color:'#888', marginTop:4 },
  filterBar: { display:'flex', gap:12, marginBottom:16, alignItems:'center', flexWrap:'wrap' },
  searchInput: { padding:'8px 12px', borderRadius:6, border:'1px solid #e0e0e0', fontSize:14, width:200, outline:'none' },
  select: { padding:'8px 12px', borderRadius:6, border:'1px solid #e0e0e0', fontSize:14, outline:'none', cursor:'pointer', background:'#fff' },
  countBadge: { marginLeft:'auto', color:'#888', fontSize:13 },
  tableWrap: { background:'#fff', borderRadius:8, boxShadow:'0 2px 8px rgba(0,0,0,0.06)', overflow:'auto' },
  table: { width:'100%', borderCollapse:'collapse', fontSize:13 },
  thead: { background:'#f5f7fa' },
  th: { padding:'12px 14px', textAlign:'left', fontWeight:600, color:'#555', borderBottom:'1px solid #eee', whiteSpace:'nowrap' },
  tr: { borderBottom:'1px solid #f0f0f0', transition:'background 0.15s' },
  td: { padding:'10px 14px', verticalAlign:'middle' },
  badge: { display:'inline-block', padding:'2px 8px', borderRadius:12, fontSize:12, fontWeight:600 },
  msBadge: { display:'inline-block', padding:'2px 8px', borderRadius:4, fontSize:12, background:'#f0f0f0', color:'#444' },
  compTag: { display:'inline-block', padding:'1px 6px', borderRadius:3, background:'#e8f4ff', color:'#4D96FF', fontSize:11, marginRight:4 },
  empty: { textAlign:'center', padding:40, color:'#aaa' },
  // 悬浮卡片
  hoverCard: {
    position: 'fixed', zIndex: 9999,
    background: '#fff', borderRadius: 10,
    boxShadow: '0 8px 32px rgba(0,0,0,0.18)',
    padding: 16, pointerEvents: 'none',
    border: '1px solid #e8e8e8',
    maxHeight: 420, overflow: 'hidden',
  },
  hcTitle: { fontWeight: 700, fontSize: 14, color: '#222', marginBottom: 10, borderBottom: '1px solid #f0f0f0', paddingBottom: 8 },
  hcSection: { marginBottom: 10 },
  hcSectionTitle: { fontSize: 11, fontWeight: 700, color: '#888', marginBottom: 6, textTransform: 'uppercase', letterSpacing: 0.5 },
  hcLogItem: { display:'flex', alignItems:'center', gap:6, marginBottom:4, fontSize:12 },
  hcLogDate: { color:'#aaa', fontSize:11, minWidth:70 },
  hcLogComp: { fontSize:10, padding:'1px 5px', borderRadius:8, fontWeight:600, whiteSpace:'nowrap' },
  hcLogText: { color:'#444', flex:1 },
  miniMatrix: { borderCollapse:'collapse', fontSize:11 },
  mmTh: { padding:'3px 6px', background:'#f5f7fa', border:'1px solid #eee', textAlign:'left', color:'#666', whiteSpace:'nowrap' },
  mmStgTh: { padding:'3px 2px', background:'#f5f7fa', border:'1px solid #eee', textAlign:'center', color:'#888', fontSize:10, width:20 },
  mmTdLabel: { padding:'3px 6px', border:'1px solid #eee', fontSize:11, background:'#fafafa', whiteSpace:'nowrap' },
  mmCell: { width:18, height:16, border:'1px solid #eee' },
};
