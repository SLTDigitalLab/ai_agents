import React, { useState } from 'react';
import { AGENTS } from '../../config/agents';
import { motion, AnimatePresence } from 'framer-motion';

const API_BASE = 'http://localhost:8000/api/v1/admin';

const AGENT_LIST = Object.values(AGENTS).map(cfg => ({
    id: cfg.id,
    title: cfg.title,
}));

// ── Status Toast ──────────────────────────────────────────────────────
const StatusToast = ({ status, onClose }) => {
    if (!status) return null;

    const isSuccess = status.type === 'success';
    const isError = status.type === 'error';

    return (
        <motion.div
            initial={{ opacity: 0, y: 20, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 20, scale: 0.95 }}
            className={`fixed bottom-6 right-6 z-50 max-w-lg rounded-2xl border p-5 shadow-2xl backdrop-blur-sm ${isSuccess
                ? 'bg-emerald-500/10 border-emerald-500/20'
                : isError
                    ? 'bg-red-500/10 border-red-500/20'
                    : 'bg-amber-500/10 border-amber-500/20'
                }`}
        >
            <div className="flex items-start gap-3">
                <div className={`mt-0.5 text-lg ${isSuccess ? 'text-emerald-400' : isError ? 'text-red-400' : 'text-amber-400'}`}>
                    {isSuccess ? '✓' : isError ? '✕' : '⚠'}
                </div>
                <div className="flex-1">
                    <p className={`text-sm font-semibold ${isSuccess ? 'text-emerald-300' : isError ? 'text-red-300' : 'text-amber-300'}`}>
                        {status.title}
                    </p>
                    <p className="text-white/50 text-xs mt-1 leading-relaxed">{status.message}</p>
                    {status.files && status.files.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1">
                            {status.files.map((f, i) => (
                                <span key={i} className="text-[10px] bg-white/[0.06] text-white/40 px-2 py-0.5 rounded-full">{f}</span>
                            ))}
                        </div>
                    )}
                </div>
                <button onClick={onClose} className="text-white/30 hover:text-white/60 transition-colors p-1">
                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                </button>
            </div>
        </motion.div>
    );
};


// ── Main Ingestion Panel ──────────────────────────────────────────────
const IngestionPanel = () => {
    const [activeTab, setActiveTab] = useState('url');
    const [status, setStatus] = useState(null);

    // URL Ingestion state
    const [urlForm, setUrlForm] = useState({ url: '', agent_name: AGENT_LIST[0]?.id || '' });
    const [urlLoading, setUrlLoading] = useState(false);

    // OneDrive Ingestion state
    const [odForm, setOdForm] = useState({ folder_id: '', token: '', agent_name: AGENT_LIST[0]?.id || '' });
    const [odLoading, setOdLoading] = useState(false);

    // ── URL Ingestion Handler ──
    const handleUrlIngest = async (e) => {
        e.preventDefault();
        if (!urlForm.url.trim()) return;

        setUrlLoading(true);
        setStatus(null);
        try {
            const res = await fetch(`${API_BASE}/ingest-url`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(urlForm),
            });
            const data = await res.json();

            if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

            setStatus({
                type: data.status === 'success' ? 'success' : 'warning',
                title: data.status === 'success' ? 'Ingestion Complete' : 'Warning',
                message: data.message,
            });
            if (data.status === 'success') setUrlForm(prev => ({ ...prev, url: '' }));
        } catch (err) {
            setStatus({ type: 'error', title: 'Ingestion Failed', message: err.message });
        } finally {
            setUrlLoading(false);
        }
    };

    // ── OneDrive Ingestion Handler ──
    const handleOneDriveIngest = async (e) => {
        e.preventDefault();
        if (!odForm.folder_id.trim() || !odForm.token.trim()) return;

        setOdLoading(true);
        setStatus(null);
        try {
            const res = await fetch(`${API_BASE}/ingest-onedrive`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(odForm),
            });
            const data = await res.json();

            if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

            setStatus({
                type: data.status === 'success' ? 'success' : data.status === 'warning' ? 'warning' : 'error',
                title: data.status === 'success' ? 'Ingestion Complete' : data.status === 'warning' ? 'Warning' : 'Error',
                message: data.message,
                files: data.files || [],
            });
            if (data.status === 'success') setOdForm(prev => ({ ...prev, folder_id: '', token: '' }));
        } catch (err) {
            setStatus({ type: 'error', title: 'Ingestion Failed', message: err.message });
        } finally {
            setOdLoading(false);
        }
    };

    const tabs = [
        { key: 'url', label: 'URL Ingestion', icon: '🌐' },
        { key: 'onedrive', label: 'OneDrive Sync', icon: '☁️' },
    ];

    return (
        <div className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 relative overflow-hidden">

            {/* Ambient bg */}
            <div className="absolute inset-0 pointer-events-none">
                <div className="absolute top-[-15%] left-[-10%] w-[500px] h-[500px] rounded-full bg-purple-500/[0.04] blur-3xl" />
                <div className="absolute bottom-[-10%] right-[-5%] w-[600px] h-[600px] rounded-full bg-cyan-500/[0.04] blur-3xl" />
            </div>

            <div className="relative z-10 max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-8">

                {/* ── Header ────────────────────────────────────── */}
                <motion.div
                    initial={{ opacity: 0, y: -20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.5 }}
                    className="mb-8"
                >
                    <a href="/admin" className="inline-flex items-center gap-2 text-white/40 hover:text-white/70 text-sm mb-6 transition-colors group">
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4 group-hover:-translate-x-0.5 transition-transform">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
                        </svg>
                        Back to Dashboard
                    </a>

                    <h1 className="text-3xl font-bold text-white tracking-tight">
                        Data Ingestion
                    </h1>
                    <p className="text-white/40 text-sm mt-1">
                        Ingest website URLs and OneDrive documents into agent knowledge bases
                    </p>
                </motion.div>

                {/* ── Tab Switcher ─────────────────────────────── */}
                <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.1 }}
                    className="flex gap-2 mb-6"
                >
                    {tabs.map(tab => (
                        <button
                            key={tab.key}
                            onClick={() => setActiveTab(tab.key)}
                            className={`flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium transition-all duration-200 ${activeTab === tab.key
                                ? 'bg-white/10 text-white border border-white/20 shadow-lg shadow-white/5'
                                : 'bg-white/[0.02] text-white/40 border border-white/[0.06] hover:bg-white/[0.05] hover:text-white/60'
                                }`}
                        >
                            <span>{tab.icon}</span>
                            {tab.label}
                        </button>
                    ))}
                </motion.div>

                {/* ── URL Ingestion Form ──────────────────────── */}
                <AnimatePresence mode="wait">
                    {activeTab === 'url' && (
                        <motion.div
                            key="url"
                            initial={{ opacity: 0, x: -20 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: 20 }}
                            transition={{ duration: 0.2 }}
                        >
                            <form onSubmit={handleUrlIngest} className="bg-white/[0.02] border border-white/[0.06] rounded-2xl p-6 space-y-5">
                                <div>
                                    <h2 className="text-white font-semibold text-lg mb-1">Ingest from URL</h2>
                                    <p className="text-white/30 text-xs">Scrape a web page, split into chunks, and embed into the agent's knowledge base</p>
                                </div>

                                {/* Agent Select */}
                                <div>
                                    <label htmlFor="url-agent" className="block text-white/50 text-xs uppercase tracking-wider font-medium mb-2">
                                        Target Agent
                                    </label>
                                    <select
                                        id="url-agent"
                                        value={urlForm.agent_name}
                                        onChange={(e) => setUrlForm(prev => ({ ...prev, agent_name: e.target.value }))}
                                        className="w-full bg-white/5 border border-white/10 text-white rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-purple-500/40 transition-all"
                                    >
                                        {AGENT_LIST.map(a => (
                                            <option key={a.id} value={a.id} className="bg-slate-800">{a.title}</option>
                                        ))}
                                    </select>
                                </div>

                                {/* URL Input */}
                                <div>
                                    <label htmlFor="url-input" className="block text-white/50 text-xs uppercase tracking-wider font-medium mb-2">
                                        Website URL
                                    </label>
                                    <input
                                        id="url-input"
                                        type="url"
                                        value={urlForm.url}
                                        onChange={(e) => setUrlForm(prev => ({ ...prev, url: e.target.value }))}
                                        placeholder="https://example.com/policy-document"
                                        required
                                        className="w-full bg-white/5 border border-white/10 text-white rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-purple-500/40 transition-all placeholder:text-white/20"
                                    />
                                </div>

                                {/* Submit */}
                                <button
                                    type="submit"
                                    disabled={urlLoading || !urlForm.url.trim()}
                                    className="w-full flex items-center justify-center gap-2 bg-gradient-to-r from-purple-600 to-purple-700 hover:from-purple-500 hover:to-purple-600 text-white font-medium py-3 rounded-xl transition-all disabled:opacity-40 disabled:cursor-not-allowed shadow-lg shadow-purple-500/20 hover:shadow-purple-500/30"
                                >
                                    {urlLoading ? (
                                        <>
                                            <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                                            Ingesting...
                                        </>
                                    ) : (
                                        <>
                                            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4">
                                                <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
                                            </svg>
                                            Ingest URL
                                        </>
                                    )}
                                </button>
                            </form>
                        </motion.div>
                    )}

                    {/* ── OneDrive Ingestion Form ────────────────── */}
                    {activeTab === 'onedrive' && (
                        <motion.div
                            key="onedrive"
                            initial={{ opacity: 0, x: -20 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: 20 }}
                            transition={{ duration: 0.2 }}
                        >
                            <form onSubmit={handleOneDriveIngest} className="bg-white/[0.02] border border-white/[0.06] rounded-2xl p-6 space-y-5">
                                <div>
                                    <h2 className="text-white font-semibold text-lg mb-1">Ingest from OneDrive</h2>
                                    <p className="text-white/30 text-xs">Download files from a OneDrive folder, chunk them semantically, and embed into the agent's knowledge base</p>
                                </div>

                                {/* Agent Select */}
                                <div>
                                    <label htmlFor="od-agent" className="block text-white/50 text-xs uppercase tracking-wider font-medium mb-2">
                                        Target Agent
                                    </label>
                                    <select
                                        id="od-agent"
                                        value={odForm.agent_name}
                                        onChange={(e) => setOdForm(prev => ({ ...prev, agent_name: e.target.value }))}
                                        className="w-full bg-white/5 border border-white/10 text-white rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-cyan-500/40 transition-all"
                                    >
                                        {AGENT_LIST.map(a => (
                                            <option key={a.id} value={a.id} className="bg-slate-800">{a.title}</option>
                                        ))}
                                    </select>
                                </div>

                                {/* Folder ID */}
                                <div>
                                    <label htmlFor="od-folder-id" className="block text-white/50 text-xs uppercase tracking-wider font-medium mb-2">
                                        OneDrive Folder ID
                                    </label>
                                    <input
                                        id="od-folder-id"
                                        type="text"
                                        value={odForm.folder_id}
                                        onChange={(e) => setOdForm(prev => ({ ...prev, folder_id: e.target.value }))}
                                        placeholder="e.g. 01ABCDEF23456789..."
                                        required
                                        className="w-full bg-white/5 border border-white/10 text-white rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-cyan-500/40 transition-all placeholder:text-white/20 font-mono"
                                    />
                                    <p className="text-white/20 text-[11px] mt-1.5">
                                        The item ID of the OneDrive folder containing your documents (PDFs, DOCX, PPTX, XLSX, images)
                                    </p>
                                </div>

                                {/* Access Token */}
                                <div>
                                    <label htmlFor="od-token" className="block text-white/50 text-xs uppercase tracking-wider font-medium mb-2">
                                        Graph API Access Token
                                    </label>
                                    <textarea
                                        id="od-token"
                                        value={odForm.token}
                                        onChange={(e) => setOdForm(prev => ({ ...prev, token: e.target.value }))}
                                        placeholder="eyJ0eXAiOiJKV1QiLCJub..."
                                        required
                                        rows={3}
                                        className="w-full bg-white/5 border border-white/10 text-white rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-cyan-500/40 transition-all placeholder:text-white/20 font-mono resize-none"
                                    />
                                    <p className="text-white/20 text-[11px] mt-1.5">
                                        A valid Microsoft Graph API Bearer token with Files.Read permissions
                                    </p>
                                </div>

                                {/* Supported Files Info */}
                                <div className="bg-white/[0.03] border border-white/[0.06] rounded-xl p-3">
                                    <p className="text-white/40 text-xs font-medium mb-2">Supported File Types</p>
                                    <div className="flex flex-wrap gap-1.5">
                                        {['.pdf', '.docx', '.pptx', '.xlsx', '.png', '.jpg', '.jpeg', '.eml'].map(ext => (
                                            <span key={ext} className="text-[10px] bg-white/[0.06] text-white/50 px-2 py-0.5 rounded-full font-mono">
                                                {ext}
                                            </span>
                                        ))}
                                    </div>
                                </div>

                                {/* Submit */}
                                <button
                                    type="submit"
                                    disabled={odLoading || !odForm.folder_id.trim() || !odForm.token.trim()}
                                    className="w-full flex items-center justify-center gap-2 bg-gradient-to-r from-cyan-600 to-cyan-700 hover:from-cyan-500 hover:to-cyan-600 text-white font-medium py-3 rounded-xl transition-all disabled:opacity-40 disabled:cursor-not-allowed shadow-lg shadow-cyan-500/20 hover:shadow-cyan-500/30"
                                >
                                    {odLoading ? (
                                        <>
                                            <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                                            Processing Files...
                                        </>
                                    ) : (
                                        <>
                                            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4">
                                                <path strokeLinecap="round" strokeLinejoin="round" d="M12 16.5V9.75m0 0l3 3m-3-3l-3 3M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z" />
                                            </svg>
                                            Start OneDrive Ingestion
                                        </>
                                    )}
                                </button>
                            </form>
                        </motion.div>
                    )}
                </AnimatePresence>
            </div>

            {/* Status Toast */}
            <AnimatePresence>
                <StatusToast status={status} onClose={() => setStatus(null)} />
            </AnimatePresence>
        </div>
    );
};

export default IngestionPanel;
