import React, { useState } from 'react';
import Sidebar from './components/Sidebar';
import Dashboard from './pages/Dashboard';
import ProjectControl from './pages/ProjectControl';
import FastLog from './pages/FastLog';
import Packing from './pages/Packing';
import CostLedger from './pages/CostLedger';
import HistoryTrace from './pages/HistoryTrace';
import './App.css';

function ComingSoon({ name }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '60vh', color: '#aaa' }}>
      <div style={{ fontSize: 48, marginBottom: 16 }}>🚧</div>
      <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>{name}</div>
      <div style={{ fontSize: 14 }}>开发中，敬请期待...</div>
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState('dashboard');

  const renderPage = () => {
    switch (page) {
      case 'dashboard': return <Dashboard />;
      case 'project':   return <ProjectControl />;
      case 'fastlog':   return <FastLog />;
      case 'packing':   return <Packing />;
      case 'cost':      return <CostLedger />;
      case 'history':   return <HistoryTrace />;
      case 'settings':  return <ComingSoon name="⚙️ 系统维护" />;
      case 'guide':     return <ComingSoon name="📖 使用指南" />;
      default:          return <Dashboard />;
    }
  };

  return (
    <div style={{ display: 'flex', minHeight: '100vh', background: '#f5f7fa' }}>
      <Sidebar current={page} onChange={setPage} />
      <main style={{ marginLeft: 200, flex: 1, minHeight: '100vh' }}>
        {renderPage()}
      </main>
    </div>
  );
}
