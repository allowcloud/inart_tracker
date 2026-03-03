import React, { useState, useEffect } from 'react';
import { getProjects } from '../api';

const today = () => new Date().toISOString().slice(0, 10);

const BOX_TYPES = ['单人盒', '双人盒', '礼盒', '盲盒', '展示盒', '普通彩盒', '其他'];
const WAREHOUSE_LIST = ['深圳仓', '义乌仓', '上海仓', '广州仓', '客户直发'];
const STATUS_OPTIONS = ['待入库', '已入库', '部分入库', '已发货', '异常'];

const STATUS_COLORS = {
  '待入库': '#faad14',
  '已入库': '#52c41a',
  '部分入库': '#1890ff',
  '已发货': '#722ed1',
  '异常': '#ff4d4f',
};

const MOCK_RECORDS = [
  { id: 1, project: '1/12英雄联盟-金克丝', batch: 'B001', boxType: '单人盒', qty: 200, warehouse: '深圳仓', status: '已入库', date: '2025-06-10', remark: '首批正式货' },
  { id: 2, project: '1/12英雄联盟-金克丝', batch: 'B002', boxType: '单人盒', qty: 150, warehouse: '深圳仓', status: '待入库', date: '2025-06-18', remark: '补货批次' },
];

export default function Packing() {
  const [projects, setProjects] = useState({});
  const [records, setRecords] = useState(MOCK_RECORDS);
  const [showForm, setShowForm] = useState(false);
  const [filterStatus, setFilterStatus] = useState('全部');
  const [filterProj, setFilterProj] = useState('全部');
  const [form, setForm] = useState({
    project: '', batch: '', boxType: '单人盒', qty: '', warehouse: '深圳仓',
    status: '待入库', date: today(), remark: '', inboundQty: ''
  });
  const [editId, setEditId] = useState(null);

  useEffect(() => { getProjects().then(p => setProjects(p || {})); }, []);

  const projList = ['全部', ...Object.keys(projects)];

  const filtered = records.filter(r => {
    if (filterStatus !== '全部' && r.status !== filterStatus) return false;
    if (filterProj !== '全部' && r.project !== filterProj) return false;
    return true;
  });

  const stats = {
    total: records.length,
    pending: records.filter(r => r.status === '待入库').length,
    inStock: records.filter(r => r.status === '已入库').length,
    totalQty: records.reduce((sum, r) => sum + (Number(r.qty) || 0), 0),
  };

  const handleSave = () => {
    if (!form.project || !form.batch || !form.qty) return;
    if (editId != null) {
      setRecords(prev => prev.map(r => r.id === editId ? { ...r, ...form, id: editId } : r));
      setEditId(null);
    } else {
      setRecords(prev => [...prev, { ...form, id: Date.now() }]);
    }
    setForm({ project: '', batch: '', boxType: '单人盒', qty: '', warehouse: '深圳仓', status: '待入库', date: today(), remark: '', inboundQty: '' });
    setShowForm(false);
  };

  const handleEdit = (rec) => {
    setForm({ ...rec });
    setEditId(rec.id);
    setShowForm(true);
  };

  const handleStatusChange = (id, newStatus) => {
    setRecords(prev => prev.map(r => r.id === id ? { ...r, status: newStatus } : r));
  };

  return (
    <div style={s.page}>
      {/* Header */}
      <div style={s.header}>
        <div>
          <div style={s.title}>📦 包装与入库</div>
          <div style={s.subtitle}>管理产品包装批次、入库数量与仓储状态</div>
        </div>
        <button style={s.btnAdd} onClick={() => { setShowForm(true); setEditId(null); }}>
          + 新增入库记录
        </button>
      </div>

      {/* Stats */}
      <div style={s.statsRow}>
        {[
          { label: '总批次', value: stats.total, color: '#4D96FF', bg: '#e8f4ff' },
          { label: '待入库', value: stats.pending, color: '#faad14', bg: '#fff7e6' },
          { label: '已入库', value: stats.inStock, color: '#52c41a', bg: '#f6ffed' },
          { label: '总数量', value: stats.totalQty.toLocaleString(), color: '#722ed1', bg: '#f9f0ff' },
        ].map(stat => (
          <div key={stat.label} style={{ ...s.statCard, background: stat.bg }}>
            <div style={{ ...s.statValue, color: stat.color }}>{stat.value}</div>
            <div style={s.statLabel}>{stat.label}</div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div style={s.filterRow}>
        <div style={s.filterGroup}>
          <label style={s.filterLabel}>项目筛选：</label>
          <select value={filterProj} onChange={e => setFilterProj(e.target.value)} style={s.filterSelect}>
            {projList.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        <div style={s.filterGroup}>
          <label style={s.filterLabel}>状态筛选：</label>
          {['全部', ...STATUS_OPTIONS].map(st => (
            <button
              key={st}
              style={{ ...s.filterBtn, ...(filterStatus === st ? { background: STATUS_COLORS[st] || '#4D96FF', color: '#fff', border: 'none' } : {}) }}
              onClick={() => setFilterStatus(st)}
            >{st}</button>
          ))}
        </div>
      </div>

      {/* Table */}
      <div style={s.tableCard}>
        <table style={s.table}>
          <thead>
            <tr style={s.thead}>
              {['项目', '批次号', '包装类型', '计划数量', '仓库', '状态', '入库日期', '备注', '操作'].map(h => (
                <th key={h} style={s.th}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr><td colSpan={9} style={s.empty}>暂无记录</td></tr>
            ) : (
              filtered.map(rec => (
                <tr key={rec.id} style={s.tr}>
                  <td style={s.td}><div style={s.projName}>{rec.project}</div></td>
                  <td style={s.td}><span style={s.batchTag}>{rec.batch}</span></td>
                  <td style={s.td}>{rec.boxType}</td>
                  <td style={s.td}><strong>{rec.qty}</strong></td>
                  <td style={s.td}>{rec.warehouse}</td>
                  <td style={s.td}>
                    <select
                      value={rec.status}
                      onChange={e => handleStatusChange(rec.id, e.target.value)}
                      style={{ ...s.statusBadge, background: STATUS_COLORS[rec.status] + '20', color: STATUS_COLORS[rec.status], border: `1px solid ${STATUS_COLORS[rec.status]}40` }}
                    >
                      {STATUS_OPTIONS.map(st => <option key={st} value={st}>{st}</option>)}
                    </select>
                  </td>
                  <td style={s.td}>{rec.date}</td>
                  <td style={s.td}><span style={s.remarkText}>{rec.remark || '-'}</span></td>
                  <td style={s.td}>
                    <button style={s.editBtn} onClick={() => handleEdit(rec)}>编辑</button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Form Modal */}
      {showForm && (
        <div style={s.overlay}>
          <div style={s.modal}>
            <div style={s.modalTitle}>{editId != null ? '✏️ 编辑记录' : '📦 新增入库记录'}</div>
            <div style={s.formGrid}>
              <div style={s.formGroup}>
                <label style={s.label}>项目 *</label>
                <select value={form.project} onChange={e => setForm(f => ({ ...f, project: e.target.value }))} style={s.select}>
                  <option value="">-- 选择项目 --</option>
                  {Object.keys(projects).map(p => <option key={p} value={p}>{p}</option>)}
                </select>
              </div>
              <div style={s.formGroup}>
                <label style={s.label}>批次号 *</label>
                <input style={s.input} value={form.batch} onChange={e => setForm(f => ({ ...f, batch: e.target.value }))} placeholder="如 B001" />
              </div>
              <div style={s.formGroup}>
                <label style={s.label}>包装类型</label>
                <select value={form.boxType} onChange={e => setForm(f => ({ ...f, boxType: e.target.value }))} style={s.select}>
                  {BOX_TYPES.map(b => <option key={b} value={b}>{b}</option>)}
                </select>
              </div>
              <div style={s.formGroup}>
                <label style={s.label}>计划数量 *</label>
                <input type="number" style={s.input} value={form.qty} onChange={e => setForm(f => ({ ...f, qty: e.target.value }))} placeholder="件数" />
              </div>
              <div style={s.formGroup}>
                <label style={s.label}>仓库</label>
                <select value={form.warehouse} onChange={e => setForm(f => ({ ...f, warehouse: e.target.value }))} style={s.select}>
                  {WAREHOUSE_LIST.map(w => <option key={w} value={w}>{w}</option>)}
                </select>
              </div>
              <div style={s.formGroup}>
                <label style={s.label}>状态</label>
                <select value={form.status} onChange={e => setForm(f => ({ ...f, status: e.target.value }))} style={s.select}>
                  {STATUS_OPTIONS.map(st => <option key={st} value={st}>{st}</option>)}
                </select>
              </div>
              <div style={s.formGroup}>
                <label style={s.label}>入库日期</label>
                <input type="date" style={s.input} value={form.date} onChange={e => setForm(f => ({ ...f, date: e.target.value }))} />
              </div>
              <div style={{ ...s.formGroup, gridColumn: 'span 2' }}>
                <label style={s.label}>备注</label>
                <input style={s.input} value={form.remark} onChange={e => setForm(f => ({ ...f, remark: e.target.value }))} placeholder="可选" />
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
  statValue: { fontSize: 28, fontWeight: 800, lineHeight: 1 },
  statLabel: { fontSize: 12, color: '#888', marginTop: 4 },
  filterRow: { display: 'flex', gap: 20, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' },
  filterGroup: { display: 'flex', alignItems: 'center', gap: 8 },
  filterLabel: { fontSize: 13, color: '#555', fontWeight: 600 },
  filterSelect: { padding: '5px 10px', border: '1px solid #e8e8e8', borderRadius: 6, fontSize: 13 },
  filterBtn: { padding: '4px 12px', border: '1px solid #e8e8e8', borderRadius: 5, cursor: 'pointer', fontSize: 12, background: '#fafafa', color: '#555', transition: 'all 0.15s' },
  tableCard: { background: '#fff', borderRadius: 10, boxShadow: '0 2px 8px rgba(0,0,0,0.07)', overflow: 'hidden' },
  table: { width: '100%', borderCollapse: 'collapse' },
  thead: { background: '#f5f7fa' },
  th: { padding: '12px 14px', textAlign: 'left', fontSize: 12, fontWeight: 700, color: '#555', borderBottom: '1px solid #f0f0f0' },
  tr: { borderBottom: '1px solid #f9f9f9', transition: 'background 0.15s' },
  td: { padding: '11px 14px', fontSize: 13, color: '#333', verticalAlign: 'middle' },
  projName: { fontWeight: 600, color: '#1a1a2e', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  batchTag: { background: '#e8f4ff', color: '#4D96FF', padding: '2px 8px', borderRadius: 4, fontSize: 12, fontWeight: 600 },
  statusBadge: { padding: '3px 8px', borderRadius: 4, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: 'none' },
  remarkText: { color: '#888', fontSize: 12 },
  editBtn: { padding: '4px 10px', border: '1px solid #4D96FF', color: '#4D96FF', background: 'transparent', borderRadius: 4, cursor: 'pointer', fontSize: 12 },
  empty: { textAlign: 'center', color: '#bbb', padding: '40px 0', fontSize: 13 },
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
