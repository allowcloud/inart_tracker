import React, { useEffect, useState, useMemo } from 'react';
import { getProjects } from '../api';

const MILESTONE_ORDER = ['待立项','研发中','暂停研发','下模中','生产中','生产结束','项目结束撒花🎉'];

function getRiskStatus(milestone, target) {
  const ms = String(milestone || '').trim();
  const tgt = String(target || 'TBD').trim();
  const isFinished = ['生产结束','项目结束撒花🎉'].includes(ms);
  if (ms === '暂停研发') return { text: '⏸️ 暂停', color: '#888' };
  if (isFinished) return { text: '🏁 已结案', color: '#52c41a' };

  if (tgt !== 'TBD' && tgt !== '') {
    try {
      const today = new Date();
      const t = new Date(tgt);
      if (!isNaN(t) && today > t) return { text: '🔴 逾期', color: '#ff4d4f' };
    } catch {}
  }
  if (['生产中','下模中'].includes(ms)) return { text: '🟢 生产期', color: '#52c41a' };
  if (ms.includes('研发') || ms === '待立项') return { text: '🟡 研发期', color: '#faad14' };
  return { text: '⚪ 未知', color: '#aaa' };
}

function daysSince(dateStr) {
  if (!dateStr) return '-';
  try {
    const d = new Date(dateStr);
    const diff = Math.floor((new Date() - d) / 86400000);
    return diff + ' 天';
  } catch { return '-'; }
}

function getLatestLog(proj) {
  const comps = proj?.部件列表 || {};
  let latestDate = null, latestEvent = '无数据', latestComp = '-';
  for (const [cName, cData] of Object.entries(comps)) {
    const logs = cData?.日志流 || [];
    if (logs.length > 0) {
      const last = logs[logs.length - 1];
      const d = new Date(last.日期);
      if (!latestDate || d > latestDate) {
        latestDate = d;
        latestEvent = last.事件 || '无数据';
        latestComp = cName;
      }
    }
  }
  return { latestDate, latestEvent, latestComp };
}

export default function Dashboard() {
  const [projects, setProjects] = useState({});
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [filterPM, setFilterPM] = useState('所有人');
  const [filterMS, setFilterMS] = useState('全部');
  const [sortBy, setSortBy] = useState('断更');

  useEffect(() => {
    getProjects().then(data => {
      setProjects(data);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const pmList = useMemo(() => {
    const pms = new Set(['所有人']);
    Object.values(projects).forEach(p => {
      if (p?.负责人) pms.add(p.负责人);
    });
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
          name, data, risk,
          latestDate,
          latestDateStr: latestDate ? latestDate.toISOString().slice(0, 10) : null,
          daysSince: latestDate ? Math.floor((new Date() - latestDate) / 86400000) : 9999,
          latestEvent: cleanEvent,
          latestComp,
          milestone: data?.Milestone || '待立项',
          pm: data?.负责人 || '-',
          target: data?.Target || 'TBD',
          ship: data?.发货区间 || '-',
        };
      })
      .sort((a, b) => {
        if (sortBy === '断更') return b.daysSince - a.daysSince;
        if (sortBy === '里程碑') return MILESTONE_ORDER.indexOf(a.milestone) - MILESTONE_ORDER.indexOf(b.milestone);
        if (sortBy === '开定时间') {
          const av = a.target === 'TBD' || !a.target ? '9999' : a.target;
          const bv = b.target === 'TBD' || !b.target ? '9999' : b.target;
          return av.localeCompare(bv);
        }
        if (sortBy === '发货区间') {
          const av = !a.ship || a.ship === '-' ? '9999' : a.ship;
          const bv = !b.ship || b.ship === '-' ? '9999' : b.ship;
          return av.localeCompare(bv);
        }
        return a.name.localeCompare(b.name);
      });
  }, [projects, filterPM, filterMS, search, sortBy]);

  // 统计卡片数据
  const stats = useMemo(() => {
    const all = Object.values(projects);
    return {
      total: all.length,
      active: all.filter(p => p?.Milestone === '研发中').length,
      production: all.filter(p => ['生产中','下模中'].includes(p?.Milestone)).length,
      overdue: all.filter(p => getRiskStatus(p?.Milestone, p?.Target).text === '🔴 逾期').length,
    };
  }, [projects]);

  if (loading) return (
    <div style={styles.loadingWrap}>
      <div style={styles.spinner} />
      <p style={{ color: '#888', marginTop: 16 }}>加载中...</p>
    </div>
  );

  return (
    <div style={styles.page}>
      {/* 统计卡片 */}
      <div style={styles.statsRow}>
        {[
          { label: '项目总数', value: stats.total, color: '#4D96FF' },
          { label: '研发中', value: stats.active, color: '#A555EC' },
          { label: '生产期', value: stats.production, color: '#6BCB77' },
          { label: '逾期预警', value: stats.overdue, color: '#ff4d4f' },
        ].map(s => (
          <div key={s.label} style={{ ...styles.statCard, borderTop: `3px solid ${s.color}` }}>
            <div style={{ ...styles.statValue, color: s.color }}>{s.value}</div>
            <div style={styles.statLabel}>{s.label}</div>
          </div>
        ))}
      </div>

      {/* 筛选栏 */}
      <div style={styles.filterBar}>
        <input
          placeholder="🔍 搜索项目名..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={styles.searchInput}
        />
        <select value={filterPM} onChange={e => setFilterPM(e.target.value)} style={styles.select}>
          {pmList.map(pm => <option key={pm}>{pm}</option>)}
        </select>
        <select value={filterMS} onChange={e => setFilterMS(e.target.value)} style={styles.select}>
          <option value="全部">全部阶段</option>
          {MILESTONE_ORDER.map(ms => <option key={ms}>{ms}</option>)}
        </select>
        <select value={sortBy} onChange={e => setSortBy(e.target.value)} style={styles.select}>
          <option value="断更">按断更排序</option>
          <option value="里程碑">按里程碑排序</option>
          <option value="开定时间">按开定时间↑</option>
          <option value="发货区间">按发货区间↑</option>
          <option value="名称">按名称排序</option>
        </select>
        <span style={styles.countBadge}>{rows.length} 个项目</span>
      </div>

      {/* 项目表格 */}
      <div style={styles.tableWrap}>
        <table style={styles.table}>
          <thead>
            <tr style={styles.thead}>
              {['状态','项目名称','负责PM','里程碑','开定时间','发货区间','断更','最新动态'].map(h => (
                <th key={h} style={styles.th}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={row.name} style={{ ...styles.tr, background: i % 2 === 0 ? '#fff' : '#f9f9fb' }}>
                <td style={styles.td}>
                  <span style={{ ...styles.badge, background: row.risk.color + '20', color: row.risk.color }}>
                    {row.risk.text}
                  </span>
                </td>
                <td style={{ ...styles.td, fontWeight: 600, maxWidth: 200 }}>{row.name}</td>
                <td style={styles.td}>{row.pm}</td>
                <td style={styles.td}>
                  <span style={styles.msBadge}>{row.milestone}</span>
                </td>
                <td style={styles.td}>{row.target}</td>
                <td style={styles.td}>{row.ship}</td>
                <td style={{ ...styles.td, color: row.daysSince > 14 ? '#ff4d4f' : '#52c41a', fontWeight: 600 }}>
                  {row.daysSince === 9999 ? '-' : row.daysSince + ' 天'}
                </td>
                <td style={{ ...styles.td, color: '#555', maxWidth: 300, fontSize: 12 }}>
                  {row.latestComp !== '-' ? <span style={styles.compTag}>[{row.latestComp}]</span> : null}
                  {' '}{row.latestEvent}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && (
          <div style={styles.empty}>没有符合条件的项目</div>
        )}
      </div>
    </div>
  );
}

const styles = {
  page: { padding: '24px', fontFamily: "'PingFang SC', 'Microsoft YaHei', sans-serif" },
  loadingWrap: { display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '60vh' },
  spinner: { width: 40, height: 40, border: '4px solid #f0f0f0', borderTop: '4px solid #4D96FF', borderRadius: '50%', animation: 'spin 1s linear infinite' },
  statsRow: { display: 'flex', gap: 16, marginBottom: 24 },
  statCard: { flex: 1, background: '#fff', borderRadius: 8, padding: '16px 20px', boxShadow: '0 2px 8px rgba(0,0,0,0.06)' },
  statValue: { fontSize: 32, fontWeight: 700 },
  statLabel: { fontSize: 13, color: '#888', marginTop: 4 },
  filterBar: { display: 'flex', gap: 12, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' },
  searchInput: { padding: '8px 12px', borderRadius: 6, border: '1px solid #e0e0e0', fontSize: 14, width: 200, outline: 'none' },
  select: { padding: '8px 12px', borderRadius: 6, border: '1px solid #e0e0e0', fontSize: 14, outline: 'none', cursor: 'pointer', background: '#fff' },
  countBadge: { marginLeft: 'auto', color: '#888', fontSize: 13 },
  tableWrap: { background: '#fff', borderRadius: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', overflow: 'auto' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  thead: { background: '#f5f7fa' },
  th: { padding: '12px 14px', textAlign: 'left', fontWeight: 600, color: '#555', borderBottom: '1px solid #eee', whiteSpace: 'nowrap' },
  tr: { borderBottom: '1px solid #f0f0f0', transition: 'background 0.15s' },
  td: { padding: '10px 14px', verticalAlign: 'middle' },
  badge: { display: 'inline-block', padding: '2px 8px', borderRadius: 12, fontSize: 12, fontWeight: 600 },
  msBadge: { display: 'inline-block', padding: '2px 8px', borderRadius: 4, fontSize: 12, background: '#f0f0f0', color: '#444' },
  compTag: { display: 'inline-block', padding: '1px 6px', borderRadius: 3, background: '#e8f4ff', color: '#4D96FF', fontSize: 11, marginRight: 4 },
  empty: { textAlign: 'center', padding: 40, color: '#aaa' },
};
