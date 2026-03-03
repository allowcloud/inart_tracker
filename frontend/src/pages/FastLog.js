import React, { useState, useEffect } from 'react';
import { getProjects, getProject, saveProject } from '../api';

const today = () => new Date().toISOString().slice(0, 10);
const nowTime = () => new Date().toTimeString().slice(0, 5);

const STAGES_UNIFIED = [
  "立项","建模(含打印/签样)","涂装","设计","工程拆件","手板/结构板",
  "官图","工厂复样(含胶件/上色等)","大货","⏸️ 暂停/搁置","✅ 已完成(结束)"
];

const QUICK_EVENTS = [
  '已完成', '进行中', '待确认', '有问题', '已交接', '需修改', '客户确认中', '暂停'
];

export default function FastLog() {
  const [projects, setProjects] = useState({});
  const [selectedProj, setSelectedProj] = useState('');
  const [projData, setProjData] = useState(null);
  const [selectedComp, setSelectedComp] = useState('');
  const [selectedStage, setSelectedStage] = useState('');
  const [event, setEvent] = useState('');
  const [remark, setRemark] = useState('');
  const [logDate, setLogDate] = useState(today());
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [recentLogs, setRecentLogs] = useState([]);
  const [aiInput, setAiInput] = useState('');
  const [aiParsing, setAiParsing] = useState(false);
  const [aiResult, setAiResult] = useState(null);
  const [activeTab, setActiveTab] = useState('quick'); // 'quick' | 'ai'

  useEffect(() => {
    getProjects().then(p => setProjects(p || {}));
  }, []);

  useEffect(() => {
    if (selectedProj) {
      getProject(selectedProj).then(d => {
        setProjData(d);
        setSelectedComp('');
        setSelectedStage('');
        // Build recent logs across all components
        const logs = [];
        Object.entries(d?.部件列表 || {}).forEach(([cName, cData]) => {
          (cData?.日志流 || []).forEach(log => {
            logs.push({ ...log, _comp: cName });
          });
        });
        logs.sort((a, b) => (b.日期 || '').localeCompare(a.日期 || ''));
        setRecentLogs(logs.slice(0, 10));
      });
    }
  }, [selectedProj]);

  const comps = projData?.部件列表 || {};
  const compList = Object.keys(comps);

  const handleQuickSave = async () => {
    if (!selectedProj || !selectedComp || !event) return;
    setSaving(true);
    const fresh = await getProject(selectedProj);
    const stage = selectedStage || comps[selectedComp]?.主流程 || STAGES_UNIFIED[0];
    const newLog = {
      日期: logDate,
      时间: nowTime(),
      工序: stage,
      事件: event,
      备注: remark,
      来源: '速记'
    };
    if (!fresh.部件列表[selectedComp].日志流) fresh.部件列表[selectedComp].日志流 = [];
    fresh.部件列表[selectedComp].日志流.push(newLog);
    if (selectedStage) fresh.部件列表[selectedComp].主流程 = selectedStage;
    await saveProject(selectedProj, fresh);
    setSaving(false);
    setSaved(true);
    setEvent('');
    setRemark('');
    // Refresh
    const updated = await getProject(selectedProj);
    setProjData(updated);
    const logs = [];
    Object.entries(updated?.部件列表 || {}).forEach(([cName, cData]) => {
      (cData?.日志流 || []).forEach(log => logs.push({ ...log, _comp: cName }));
    });
    logs.sort((a, b) => (b.日期 || '').localeCompare(a.日期 || ''));
    setRecentLogs(logs.slice(0, 10));
    setTimeout(() => setSaved(false), 2000);
  };

  const handleAiParse = async () => {
    if (!aiInput.trim()) return;
    setAiParsing(true);
    setAiResult(null);
    try {
      const resp = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: 'claude-sonnet-4-20250514',
          max_tokens: 1000,
          system: `你是一个项目管理助手，帮助解析用户输入的口语化项目进展记录。
从用户输入中提取以下信息，以JSON格式返回（只返回JSON，无需其他内容）：
{
  "部件": "部件名称（如头雕、素体、手型、服装、配件等，若无法判断返回null）",
  "工序": "工序名称（从以下选项中选：立项、建模、涂装、设计、工程拆件、手板/结构板、官图、工厂复样、大货，若无法判断返回null）",
  "事件": "简洁的事件描述（20字以内）",
  "备注": "详细备注（若有）",
  "日期": "日期（格式YYYY-MM-DD，若用户提到今天/昨天/前天请换算，若无提及返回today）"
}
今天日期是：${today()}`,
          messages: [{ role: 'user', content: aiInput }]
        })
      });
      const data = await resp.json();
      const text = data.content?.map(c => c.text || '').join('');
      const clean = text.replace(/```json|```/g, '').trim();
      const parsed = JSON.parse(clean);
      if (parsed.日期 === 'today') parsed.日期 = today();
      setAiResult(parsed);
      if (parsed.部件 && compList.includes(parsed.部件)) setSelectedComp(parsed.部件);
      if (parsed.工序) {
        const matched = STAGES_UNIFIED.find(s => s.includes(parsed.工序));
        if (matched) setSelectedStage(matched);
      }
      if (parsed.事件) setEvent(parsed.事件);
      if (parsed.备注) setRemark(parsed.备注);
      if (parsed.日期) setLogDate(parsed.日期);
    } catch (e) {
      setAiResult({ error: '解析失败，请手动填写' });
    }
    setAiParsing(false);
  };

  return (
    <div style={s.page}>
      {/* Header */}
      <div style={s.header}>
        <div>
          <div style={s.title}>📝 手机AI速记</div>
          <div style={s.subtitle}>快速记录项目进展，支持AI智能解析口语输入</div>
        </div>
      </div>

      <div style={s.body}>
        {/* Left: Input Panel */}
        <div style={s.inputPanel}>
          {/* Project Selector */}
          <div style={s.card}>
            <div style={s.cardTitle}>选择项目</div>
            <select
              value={selectedProj}
              onChange={e => setSelectedProj(e.target.value)}
              style={s.select}
            >
              <option value="">-- 请选择项目 --</option>
              {Object.keys(projects).map(p => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </div>

          {selectedProj && (
            <>
              {/* Tab Switch */}
              <div style={s.tabRow}>
                <button
                  style={{ ...s.tab, ...(activeTab === 'quick' ? s.tabActive : {}) }}
                  onClick={() => setActiveTab('quick')}
                >✍️ 快速填写</button>
                <button
                  style={{ ...s.tab, ...(activeTab === 'ai' ? s.tabActive : {}) }}
                  onClick={() => setActiveTab('ai')}
                >🤖 AI解析</button>
              </div>

              {activeTab === 'ai' && (
                <div style={s.card}>
                  <div style={s.cardTitle}>AI智能解析 <span style={s.badge}>Beta</span></div>
                  <div style={s.hint}>用自然语言描述进展，AI自动提取结构化信息</div>
                  <textarea
                    style={s.textarea}
                    placeholder={"例如：头雕今天建模完成了，已经交给涂装那边了，感觉效果还不错\n或：素体工程拆件有问题，需要修改后重新确认"}
                    value={aiInput}
                    onChange={e => setAiInput(e.target.value)}
                    rows={4}
                  />
                  <button
                    style={{ ...s.btn, ...s.btnAI }}
                    onClick={handleAiParse}
                    disabled={aiParsing || !aiInput.trim()}
                  >
                    {aiParsing ? '🤖 解析中...' : '🤖 AI解析并填充'}
                  </button>
                  {aiResult && !aiResult.error && (
                    <div style={s.aiResultBox}>
                      <div style={s.aiResultTitle}>✅ 解析结果（已自动填充）</div>
                      {Object.entries(aiResult).map(([k, v]) => v && (
                        <div key={k} style={s.aiResultRow}>
                          <span style={s.aiResultKey}>{k}</span>
                          <span>{v}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {aiResult?.error && (
                    <div style={{ color: '#ff4d4f', fontSize: 13, marginTop: 8 }}>{aiResult.error}</div>
                  )}
                </div>
              )}

              {/* Form */}
              <div style={s.card}>
                <div style={s.cardTitle}>记录详情</div>

                <div style={s.formRow}>
                  <div style={s.formGroup}>
                    <label style={s.label}>部件 *</label>
                    <select value={selectedComp} onChange={e => setSelectedComp(e.target.value)} style={s.select}>
                      <option value="">-- 选择部件 --</option>
                      {compList.map(c => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </div>
                  <div style={s.formGroup}>
                    <label style={s.label}>日期</label>
                    <input type="date" value={logDate} onChange={e => setLogDate(e.target.value)} style={s.input} />
                  </div>
                </div>

                <div style={s.formGroup}>
                  <label style={s.label}>更新工序（可选，留空则保持当前）</label>
                  <select value={selectedStage} onChange={e => setSelectedStage(e.target.value)} style={s.select}>
                    <option value="">-- 不变更工序 --</option>
                    {STAGES_UNIFIED.map(st => (
                      <option key={st} value={st}>{st}</option>
                    ))}
                  </select>
                </div>

                <div style={s.formGroup}>
                  <label style={s.label}>事件 *</label>
                  <div style={s.quickBtns}>
                    {QUICK_EVENTS.map(q => (
                      <button
                        key={q}
                        style={{ ...s.quickBtn, ...(event === q ? s.quickBtnActive : {}) }}
                        onClick={() => setEvent(q)}
                      >{q}</button>
                    ))}
                  </div>
                  <input
                    style={s.input}
                    placeholder="或自定义输入事件描述..."
                    value={event}
                    onChange={e => setEvent(e.target.value)}
                  />
                </div>

                <div style={s.formGroup}>
                  <label style={s.label}>备注</label>
                  <textarea
                    style={{ ...s.textarea, rows: 2 }}
                    placeholder="详细说明（可选）"
                    value={remark}
                    onChange={e => setRemark(e.target.value)}
                    rows={2}
                  />
                </div>

                <button
                  style={{
                    ...s.btn,
                    ...s.btnPrimary,
                    ...(!selectedComp || !event ? s.btnDisabled : {})
                  }}
                  onClick={handleQuickSave}
                  disabled={saving || !selectedComp || !event}
                >
                  {saving ? '⏳ 保存中...' : saved ? '✅ 已保存！' : '💾 保存记录'}
                </button>
              </div>
            </>
          )}
        </div>

        {/* Right: Recent Logs */}
        <div style={s.logPanel}>
          <div style={s.card}>
            <div style={s.cardTitle}>
              📋 最近记录
              {selectedProj && <span style={s.projTag}>{selectedProj}</span>}
            </div>
            {recentLogs.length === 0 ? (
              <div style={s.empty}>
                {selectedProj ? '暂无日志记录' : '请先选择项目'}
              </div>
            ) : (
              <div style={s.logList}>
                {recentLogs.map((log, i) => (
                  <div key={i} style={s.logItem}>
                    <div style={s.logHeader}>
                      <span style={s.logComp}>{log._comp}</span>
                      <span style={s.logDate}>{log.日期} {log.时间 || ''}</span>
                    </div>
                    <div style={s.logStage}>{log.工序}</div>
                    <div style={s.logEvent}>{log.事件}</div>
                    {log.备注 && <div style={s.logRemark}>{log.备注}</div>}
                    {log.来源 === '速记' && <span style={s.sourceTag}>速记</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

const s = {
  page: { padding: 24, fontFamily: "'PingFang SC', 'Microsoft YaHei', sans-serif" },
  header: { marginBottom: 20 },
  title: { fontSize: 22, fontWeight: 700, color: '#1a1a2e' },
  subtitle: { fontSize: 13, color: '#888', marginTop: 4 },
  body: { display: 'flex', gap: 20, alignItems: 'flex-start' },
  inputPanel: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 16 },
  logPanel: { width: 340, flexShrink: 0 },
  card: { background: '#fff', borderRadius: 10, padding: 20, boxShadow: '0 2px 8px rgba(0,0,0,0.07)' },
  cardTitle: { fontSize: 15, fontWeight: 700, color: '#1a1a2e', marginBottom: 14, display: 'flex', alignItems: 'center', gap: 8 },
  badge: { background: '#4D96FF', color: '#fff', fontSize: 10, padding: '2px 6px', borderRadius: 4, fontWeight: 600 },
  hint: { fontSize: 12, color: '#aaa', marginBottom: 10 },
  tabRow: { display: 'flex', gap: 8 },
  tab: { flex: 1, padding: '9px 0', border: '1px solid #e8e8e8', borderRadius: 8, background: '#fafafa', cursor: 'pointer', fontSize: 13, fontWeight: 600, color: '#888', transition: 'all 0.2s' },
  tabActive: { background: '#4D96FF', color: '#fff', border: '1px solid #4D96FF' },
  select: { width: '100%', padding: '8px 10px', border: '1px solid #e8e8e8', borderRadius: 6, fontSize: 13, background: '#fafafa', boxSizing: 'border-box' },
  input: { width: '100%', padding: '8px 10px', border: '1px solid #e8e8e8', borderRadius: 6, fontSize: 13, boxSizing: 'border-box' },
  textarea: { width: '100%', padding: '8px 10px', border: '1px solid #e8e8e8', borderRadius: 6, fontSize: 13, resize: 'vertical', boxSizing: 'border-box', fontFamily: 'inherit' },
  formRow: { display: 'flex', gap: 12 },
  formGroup: { flex: 1, marginBottom: 12 },
  label: { display: 'block', fontSize: 12, fontWeight: 600, color: '#555', marginBottom: 5 },
  quickBtns: { display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 },
  quickBtn: { padding: '4px 10px', border: '1px solid #e8e8e8', borderRadius: 4, fontSize: 12, cursor: 'pointer', background: '#fafafa', color: '#555', transition: 'all 0.15s' },
  quickBtnActive: { background: '#4D96FF', color: '#fff', border: '1px solid #4D96FF' },
  btn: { width: '100%', padding: '10px 0', borderRadius: 7, border: 'none', cursor: 'pointer', fontSize: 14, fontWeight: 700, transition: 'all 0.2s', marginTop: 8 },
  btnPrimary: { background: '#4D96FF', color: '#fff' },
  btnAI: { background: '#722ed1', color: '#fff', marginTop: 10 },
  btnDisabled: { background: '#d9d9d9', cursor: 'not-allowed' },
  aiResultBox: { marginTop: 12, background: '#f6ffed', border: '1px solid #b7eb8f', borderRadius: 8, padding: 12 },
  aiResultTitle: { fontSize: 13, fontWeight: 700, color: '#52c41a', marginBottom: 8 },
  aiResultRow: { display: 'flex', gap: 8, fontSize: 12, marginBottom: 4 },
  aiResultKey: { fontWeight: 600, color: '#555', minWidth: 40 },
  empty: { color: '#bbb', fontSize: 13, textAlign: 'center', padding: '30px 0' },
  logList: { display: 'flex', flexDirection: 'column', gap: 10 },
  logItem: { padding: 12, background: '#f9f9f9', borderRadius: 8, position: 'relative', borderLeft: '3px solid #4D96FF' },
  logHeader: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  logComp: { fontWeight: 700, fontSize: 13, color: '#1a1a2e' },
  logDate: { fontSize: 11, color: '#aaa' },
  logStage: { fontSize: 11, color: '#4D96FF', marginBottom: 3 },
  logEvent: { fontSize: 13, color: '#333', fontWeight: 600 },
  logRemark: { fontSize: 11, color: '#888', marginTop: 3 },
  sourceTag: { position: 'absolute', top: 8, right: 8, background: '#fff7e6', color: '#fa8c16', fontSize: 10, padding: '1px 5px', borderRadius: 3, fontWeight: 600 },
  projTag: { fontSize: 11, background: '#e8f4ff', color: '#4D96FF', padding: '2px 8px', borderRadius: 4, fontWeight: 400 },
};
