import React from 'react';

const navItems = [
  { key: 'dashboard', icon: '📊', label: '全局大盘' },
  { key: 'project', icon: '🎯', label: '项目管控台' },
  { key: 'fastlog', icon: '📝', label: '手机AI速记' },
  { key: 'packing', icon: '📦', label: '包装与入库' },
  { key: 'cost', icon: '💰', label: '成本台账' },
  { key: 'history', icon: '🔍', label: '历史溯源' },
  { key: 'settings', icon: '⚙️', label: '系统维护' },
  { key: 'guide', icon: '📖', label: '使用指南' },
];

export default function Sidebar({ current, onChange }) {
  return (
    <div style={styles.sidebar}>
      <div style={styles.logo}>
        <span style={styles.logoIcon}>🚀</span>
        <span style={styles.logoText}>INART PM</span>
      </div>
      <nav style={styles.nav}>
        {navItems.map(item => (
          <div
            key={item.key}
            onClick={() => onChange(item.key)}
            style={{
              ...styles.navItem,
              ...(current === item.key ? styles.navItemActive : {}),
            }}
          >
            <span style={styles.navIcon}>{item.icon}</span>
            <span style={styles.navLabel}>{item.label}</span>
          </div>
        ))}
      </nav>
      <div style={styles.footer}>
        <div style={styles.footerText}>INART PM System</div>
        <div style={styles.footerSub}>v2.0 React版</div>
      </div>
    </div>
  );
}

const styles = {
  sidebar: {
    width: 200, minHeight: '100vh', background: '#1a1a2e',
    display: 'flex', flexDirection: 'column',
    position: 'fixed', left: 0, top: 0, bottom: 0,
    boxShadow: '2px 0 8px rgba(0,0,0,0.15)',
  },
  logo: {
    padding: '24px 20px 20px', display: 'flex', alignItems: 'center', gap: 10,
    borderBottom: '1px solid rgba(255,255,255,0.08)',
  },
  logoIcon: { fontSize: 24 },
  logoText: { color: '#fff', fontWeight: 700, fontSize: 16, fontFamily: "'PingFang SC', sans-serif" },
  nav: { flex: 1, padding: '12px 0' },
  navItem: {
    display: 'flex', alignItems: 'center', gap: 10,
    padding: '10px 20px', cursor: 'pointer', borderRadius: 0,
    color: 'rgba(255,255,255,0.6)', fontSize: 14,
    transition: 'all 0.2s', margin: '2px 0',
  },
  navItemActive: {
    background: 'rgba(77,150,255,0.2)', color: '#4D96FF',
    borderLeft: '3px solid #4D96FF',
  },
  navIcon: { fontSize: 16, width: 20 },
  navLabel: { fontFamily: "'PingFang SC', 'Microsoft YaHei', sans-serif" },
  footer: {
    padding: '16px 20px', borderTop: '1px solid rgba(255,255,255,0.08)',
    textAlign: 'center',
  },
  footerText: { color: 'rgba(255,255,255,0.3)', fontSize: 11 },
  footerSub: { color: 'rgba(255,255,255,0.2)', fontSize: 10, marginTop: 2 },
};
