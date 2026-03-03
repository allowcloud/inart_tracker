import React, { useState, useEffect } from 'react';
import { getProjects } from '../api';

const today = () => new Date().toISOString().slice(0, 10);

const COST_CATEGORIES = [
  { key: '建模费', icon: '🖥️', color: '#2CD3E1' },
  { key: '涂装费', icon: '🎨', color: '#F47C7C' },
  { key: '设计费', icon: '✏️', color: '#A555EC' },
  { key: '手板费', icon: '🔧', color: '#4D96FF' },
  { key: '开模费', icon: '⚙️', color: '#FFB84C' },
  { key: '大货生产', icon: '🏭', color: '#6BCB77' },
  { key: '包装费', icon: '📦', color: '#fa8c16' },
  { key: '运输费', icon: '🚚', color: '#36cfc9' },
  { key: '版权/授权', icon: '©️', color: '#eb2f96' },
  { key: '其他', icon: '💡', color: '#B2B2B2' },
];

const MOCK_COSTS = [
  { id: 1, project: '1/12英雄联盟-金克丝', category: '建模费', amount: 8000, vendor: '外包建模工作室', date: '2025-03-10', status: '已付', remark: '头雕+素体建模' },
  { id: 2, project: '1/12英雄联盟-金克丝', category: '涂装费', amount: 3000, vendor: '涂装师张三', date: '2025-04-05', status: '已付', remark: '监修色彩涂装方案' },
  { id: 3, project: '1/12英雄联盟-金克丝', category: '开模费', amount: 25000, vendor: '深圳某模具厂', date: '2025-05-01', status: '部分付款', remark: '首期50%' },
  { id: 4, project: '1/12英雄联盟-金克丝', category: '大货生产', amount: 40000, vendor: '工厂', date: '2025-06-01', status: '未付', remark: '500件大货' },
];

const STATUS_COLORS = { '已付': '#52c41a', '部分付款': '#faad14', '未付': '#ff4d4f', '取消': '#aaa' };

function formatMoney(n) {
  return '¥' + Number(n || 0).toLocaleString();
}

export default function CostLedger() {
  const [projects, setProjects] = useState({});
  const [costs, setCosts] = useState(MOCK_COSTS);
  const [filterProj, setFilterProj] = useState('全部');
  const [filterCat, setFilterCat] = useState('全部');
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState(null);
  const [form, setForm] = useState({ project: '', category: '建模费', amount: '', vendor: '', date: today(), status: '未付', remark: '' });
  const [activeView, setActiveView] = useState('table'); // 'table' | 'chart'

  useEffect(() => { getProjects().then(p => setProjects(p || {})); }, []);

  const projList = ['全部', ...Object.keys(projects)];

  const filtered = costs.filter(c => {
    if (filterProj !== '全部' && c.project !== filterProj) return false;
    if (filterCat !== '全部' && c.category !== filterCat) return false;
    return true;
  });

  const totalPaid = filtered.filter(c => c.status === '已付').reduce((s, c) => s + Number(c.amount), 0);
  const totalUnpaid = filtered.filter(c => c.status === '未付').reduce((s, c) => s + Number(c.amount), 0);
  const totalPartial = filtered.filter(c => c.status === '部分付款').reduce((s, c) => s + Number(c.amount), 0);
  const totalAll = filtered.reduce((s, c) => s + Number(c.amount), 0);

  // Category breakdown
  const catBreakdown = COST_CATEGORIES.map(cat => {
    const catCosts = filtered.filter(c => c.category === cat.key);
    const total = catCosts.reduce((s, c) => s + Number(c.amount), 0);
    return { ...cat, total, count: catCosts.length };
  }).filter(c => c.total > 0).sort((a, b) => b.total - a.total);

  const maxCatTotal = Math.max(...catBreakdown.map(c => c.total), 1);

  const handleSave = () => {
    if (!form.project || !form.category || !form.amount) return;
    if (editId != null) {
      setCosts(prev => prev.map(c => c.id === editId ? { ...form, id: editId } : c));
      setEditId(null);
    } else {
      setCosts(prev => [...prev, { ...form, id: Date.now() }]);
    }
    setForm({ project: '', category: '建模费', amount: '', vendor: '', date: today(), status: '未付', remark: '' });
    setShowForm(false);
  };

  const handleEdit = (rec) => {
    setForm({ ...rec });
    setEditId(rec.id);
    setShowForm(true);
  };

  const handleDelete = (id) => {
    if (window.confirm('确认删除此条成本记录？')) {
      setCosts(prev => prev.filter(c => c.id !== id));
    }
  };

  return (
    <div style={s.page}>
      {/* Header */}
      <div style={s.header}>
        <div>
          <div style={s.title}>💰 成本台账</div>
          <div style={s.subtitle}>追踪各项目费用支出，掌握成本全貌</div>
        </div>
        <button style={s.btnAdd} onClick={() => { setShowForm(true); setEditId(null); }}>
          + 新增费用
        </button>
      </div>

      {/* Stats */}
      <div style={s.statsRow}>
        {[
          { label: '总预算/支出', value: formatMoney(totalAll), color: '#1a1a2e', bg: '#f5f7fa' },
          { label: '已付款', value: formatMoney(totalPaid), color: '#52c41a', bg: '#f6ffed' },
          { label: '部分付款', value: formatMoney(totalPartial), color: '#faad14', bg: '#fff7e6' },
          { label: '待付款', value: formatMoney(totalUnpaid), color: '#ff4d4f', bg: '#fff1f0' },
        ].map(stat => (
          <div key={stat.label} style={{ ...s.statCard, background: stat.bg }}>
            <div style={{ ...s.statValue, color: stat.color }}>{stat.value}</div>
            <div style={s.statLabel}>{stat.label}</div>
          </div>
        ))}
      </div>

      {/* View Toggle */}
      <div style={s.viewRow}>
        <div style={s.filterGroup}>
          <label style={s.filterLabel}>项目：</label>
          <select value={filterProj} onChange={e => setFilterProj(e.target.value)} style={s.filterSelect}>
            {projList.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        <div style={s.filterGroup}>
          <label style={s.filterLabel}>类型：</label>
          <select value={filterCat} onChange={e => setFilterCat(e.target.value)} style={s.filterSelect}>
            <option value="全部">全部类型</option>
            {COST_CATEGORIES.map(c => <option key={c.key} value={c.key}>{c.icon} {c.key}</option>)}
          </select>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          {['table', 'chart'].map(v => (
            <button key={v} style={{ ...s.viewBtn, ...(activeView === v ? s.viewBtnActive : {}) }} onClick={() => setActiveView(v)}>
              {v === 'table' ? '📋 明细' : '📊 图表'}
            </button>
          ))}
        </div>
      </div>

      {activeView === 'chart' ? (
        /* Chart View */
        <div style={s.chartCard}>
          <div style={s.chartTitle}>按类型费用分布</div>
          <div style={s.chartBody}>
            {catBreakdown.length === 0 ? (
              <div style={s.empty}>暂无数据</div>
            ) : catBreakdown.map(cat => (
              <div key={cat.key} style={s.chartRow}>
                <div style={s.chartLabel}>{cat.icon} {cat.key}</div>
                <div style={s.chartBarWrap}>
                  <div
                    style={{ ...s.chartBar, width: `${(cat.total / maxCatTotal) * 100}%`, background: cat.color }}
                  />
                </div>
                <div style={{ ...s.chartAmount, color: cat.color }}>{formatMoney(cat.total)}</div>
                <div style={s.chartCount}>{cat.count}笔</div>
              </div>
            ))}
          </div>

          {/* Pie-style summary */}
          <div style={s.summaryRow}>
            {catBreakdown.map(cat => (
              <div key={cat.key} style={s.summaryItem}>
                <div style={{ ...s.summaryDot, background: cat.color }} />
                <div>
                  <div style={s.summaryName}>{cat.key}</div>
                  <div style={{ ...s.summaryAmt, color: cat.color }}>{formatMoney(cat.total)}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        /* Table View */
        <div style={s.tableCard}>
          <table style={s.table}>
            <thead>
              <tr style={s.thead}>
                {['项目', '费用类型', '金额', '供应商/对象', '日期', '状态', '备注', '操作'].map(h => (
                  <th key={h} style={s.th}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr><td colSpan={8} style={s.empty}>暂无费用记录</td></tr>
              ) : (
                filtered.map(rec => {
                  const cat = COST_CATEGORIES.find(c => c.key === rec.category);
                  return (
                    <tr key={rec.id} style={s.tr}>
                      <td style={s.td}><div style={s.projName}>{rec.project}</div></td>
                      <td style={s.td}>
                        <span style={{ ...s.catTag, background: (cat?.color || '#aaa') + '20', color: cat?.color || '#aaa' }}>
                          {cat?.icon} {rec.category}
                        </span>
                      </td>
                      <td style={s.td}><strong style={{ color: '#1a1a2e', fontSize: 14 }}>{formatMoney(rec.amount)}</strong></td>
                      <td style={s.td}>{rec.vendor || '-'}</td>
                      <td style={s.td}>{rec.date}</td>
                      <td style={s.td}>
                        <span style={{ ...s.statusBadge, background: (STATUS_COLORS[rec.status] || '#aaa') + '20', color: STATUS_COLORS[rec.status] || '#aaa' }}>
                          {rec.status}
                        </span>
                      </td>
                      <td style={s.td}><span style={s.remarkText}>{rec.remark || '-'}</span></td>
                      <td style={s.td}>
                        <button style={s.editBtn} onClick={() => handleEdit(rec)}>编辑</button>
                        <button style={s.delBtn} onClick={() => handleDelete(rec.id)}>删除</button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Form Modal */}
      {showForm && (
        <div style={s.overlay}>
          <div style={s.modal}>
            <div style={s.modalTitle}>{editId != null ? '✏️ 编辑费用' : '💰 新增费用'}</div>
            <div style={s.formGrid}>
              <div style={s.formGroup}>
                <label style={s.label}>项目 *</label>
                <select value={form.project} onChange={e => setForm(f => ({ ...f, project: e.target.value }))} style={s.select}>
                  <option value="">-- 选择项目 --</option>
                  {Object.keys(projects).map(p => <option key={p} value={p}>{p}</option>)}
                </select>
              </div>
              <div style={s.formGroup}>
                <label style={s.label}>费用类型 *</label>
                <select value={form.category} onChange={e => setForm(f => ({ ...f, category: e.target.value }))} style={s.select}>
                  {COST_CATEGORIES.map(c => <option key={c.key} value={c.key}>{c.icon} {c.key}</option>)}
                </select>
              </div>
              <div style={s.formGroup}>
                <label style={s.label}>金额 (¥) *</label>
                <input type="number" style={s.input} value={form.amount} onChange={e => setForm(f => ({ ...f, amount: e.target.value }))} placeholder="0" />
              </div>
              <div style={s.formGroup}>
                <label style={s.label}>供应商/付款对象</label>
                <input style={s.input} value={form.vendor} onChange={e => setForm(f => ({ ...f, vendor: e.target.value }))} placeholder="可选" />
              </div>
              <div style={s.formGroup}>
                <label style={s.label}>日期</label>
                <input type="date" style={s.input} value={form.date} onChange={e => setForm(f => ({ ...f, date: e.target.value }))} />
              </div>
              <div style={s.formGroup}>
                <label style={s.label}>付款状态</label>
                <select value={form.status} onChange={e => setForm(f => ({ ...f, status: e.target.value }))} style={s.select}>
                  {['已付', '部分付款', '未付', '取消'].map(st => <option key={st} value={st}>{st}</option>)}
                </select>
              </div>
              <div style={{ ...s.formGroup, gridColumn: 'span 2' }}>
                <label style={s.label}>备注</label>
                <input style={s.input} value={form.remark} onChange={e => setForm(f => ({ ...f, remark: e.target.value }))} placeholder="费用说明（可选）" />
              </div>
            </div>
            <div style={s.modalBtns}>
              <button style={s.btnCancel} onClick={() => { setShowForm(false); setEditId(null); }}>取消</button>
              <button style={s.btnSave} onClick={handleSave}>💾 保存</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const s = {
  page: { padding: 24, fontFamily: "'PingFang SC', 'Microsoft YaHei', sans-serif" },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 },
  title: { fontSize: 22, fontWeight: 700, color: '#1a1a2e' },
  subtitle: { fontSize: 13, color: '#888', marginTop: 4 },
  btnAdd: { background: '#4D96FF', color: '#fff', border: 'none', borderRadius: 7, padding: '9px 18px', cursor: 'pointer', fontSize: 13, fontWeight: 700 },
  statsRow: { display: 'flex', gap: 16, marginBottom: 20 },
  statCard: { flex: 1, borderRadius: 10, padding: '16px 20px', boxShadow: '0 2px 8px rgba(0,0,0,0.05)' },
  statValue: { fontSize: 22, fontWeight: 800, lineHeight: 1 },
  statLabel: { fontSize: 12, color: '#888', marginTop: 4 },
  viewRow: { display: 'flex', gap: 16, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' },
  filterGroup: { display: 'flex', alignItems: 'center', gap: 8 },
  filterLabel: { fontSize: 13, color: '#555', fontWeight: 600 },
  filterSelect: { padding: '5px 10px', border: '1px solid #e8e8e8', borderRadius: 6, fontSize: 13 },
  viewBtn: { padding: '6px 14px', border: '1px solid #e8e8e8', borderRadius: 6, cursor: 'pointer', fontSize: 13, background: '#fafafa', color: '#555' },
  viewBtnActive: { background: '#4D96FF', color: '#fff', border: '1px solid #4D96FF' },
  tableCard: { background: '#fff', borderRadius: 10, boxShadow: '0 2px 8px rgba(0,0,0,0.07)', overflow: 'hidden' },
  table: { width: '100%', borderCollapse: 'collapse' },
  thead: { background: '#f5f7fa' },
  th: { padding: '12px 14px', textAlign: 'left', fontSize: 12, fontWeight: 700, color: '#555', borderBottom: '1px solid #f0f0f0' },
  tr: { borderBottom: '1px solid #f9f9f9' },
  td: { padding: '11px 14px', fontSize: 13, color: '#333', verticalAlign: 'middle' },
  projName: { fontWeight: 600, color: '#1a1a2e', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  catTag: { padding: '3px 8px', borderRadius: 4, fontSize: 12, fontWeight: 600 },
  statusBadge: { padding: '3px 8px', borderRadius: 4, fontSize: 12, fontWeight: 600 },
  remarkText: { color: '#888', fontSize: 12 },
  editBtn: { padding: '3px 8px', border: '1px solid #4D96FF', color: '#4D96FF', background: 'transparent', borderRadius: 4, cursor: 'pointer', fontSize: 12, marginRight: 4 },
  delBtn: { padding: '3px 8px', border: '1px solid #ff4d4f', color: '#ff4d4f', background: 'transparent', borderRadius: 4, cursor: 'pointer', fontSize: 12 },
  empty: { textAlign: 'center', color: '#bbb', padding: '40px 0', fontSize: 13 },
  chartCard: { background: '#fff', borderRadius: 10, padding: 24, boxShadow: '0 2px 8px rgba(0,0,0,0.07)' },
  chartTitle: { fontSize: 15, fontWeight: 700, color: '#1a1a2e', marginBottom: 20 },
  chartBody: { display: 'flex', flexDirection: 'column', gap: 12 },
  chartRow: { display: 'flex', alignItems: 'center', gap: 12 },
  chartLabel: { width: 120, fontSize: 13, color: '#555', flexShrink: 0 },
  chartBarWrap: { flex: 1, height: 24, background: '#f5f7fa', borderRadius: 4, overflow: 'hidden' },
  chartBar: { height: '100%', borderRadius: 4, transition: 'width 0.5s ease' },
  chartAmount: { width: 90, textAlign: 'right', fontWeight: 700, fontSize: 13 },
  chartCount: { width: 40, textAlign: 'right', fontSize: 12, color: '#aaa' },
  summaryRow: { display: 'flex', flexWrap: 'wrap', gap: 16, marginTop: 24, paddingTop: 20, borderTop: '1px solid #f0f0f0' },
  summaryItem: { display: 'flex', alignItems: 'center', gap: 8 },
  summaryDot: { width: 10, height: 10, borderRadius: '50%', flexShrink: 0 },
  summaryName: { fontSize: 12, color: '#555' },
  summaryAmt: { fontSize: 13, fontWeight: 700 },
  overlay: { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' },
  modal: { background: '#fff', borderRadius: 12, padding: 28, width: 560, boxShadow: '0 8px 32px rgba(0,0,0,0.15)' },
  modalTitle: { fontSize: 18, fontWeight: 700, color: '#1a1a2e', marginBottom: 20 },
  formGrid: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 },
  formGroup: { display: 'flex', flexDirection: 'column' },
  label: { fontSize: 12, fontWeight: 600, color: '#555', marginBottom: 5 },
  select: { padding: '8px 10px', border: '1px solid #e8e8e8', borderRadius: 6, fontSize: 13, background: '#fafafa' },
  input: { padding: '8px 10px', border: '1px solid #e8e8e8', borderRadius: 6, fontSize: 13 },
  modalBtns: { display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 20 },
  btnCancel: { padding: '8px 18px', border: '1px solid #e8e8e8', borderRadius: 6, background: '#fafafa', cursor: 'pointer', fontSize: 13 },
  btnSave: { padding: '8px 18px', background: '#4D96FF', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 13, fontWeight: 700 },
};
