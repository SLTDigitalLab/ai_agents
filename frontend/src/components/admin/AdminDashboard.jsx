import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { AGENTS } from '../../config/agents';
import { motion } from 'framer-motion';

const API_BASE = `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/v1/admin/dashboard`;

// Map agent IDs to their display config
const AGENT_MAP = {};
Object.values(AGENTS).forEach(cfg => {
    AGENT_MAP[cfg.id] = cfg;
});

// Color palette for agent stat cards
const AGENT_COLORS = {
    hr: { bg: 'from-purple-500/10 to-purple-600/5', border: 'border-purple-500/20', text: 'text-purple-400', badge: 'bg-purple-500/20 text-purple-300' },
    finance: { bg: 'from-blue-500/10 to-blue-600/5', border: 'border-blue-500/20', text: 'text-blue-400', badge: 'bg-blue-500/20 text-blue-300' },
    admin: { bg: 'from-gray-500/10 to-gray-600/5', border: 'border-gray-500/20', text: 'text-gray-400', badge: 'bg-gray-500/20 text-gray-300' },
    process: { bg: 'from-emerald-500/10 to-emerald-600/5', border: 'border-emerald-500/20', text: 'text-emerald-400', badge: 'bg-emerald-500/20 text-emerald-300' },
    enterprise: { bg: 'from-indigo-500/10 to-indigo-600/5', border: 'border-indigo-500/20', text: 'text-indigo-400', badge: 'bg-indigo-500/20 text-indigo-300' },
    lifestore: { bg: 'from-orange-500/10 to-orange-600/5', border: 'border-orange-500/20', text: 'text-orange-400', badge: 'bg-orange-500/20 text-orange-300' },
    it: { bg: 'from-sky-500/10 to-sky-600/5', border: 'border-sky-500/20', text: 'text-sky-400', badge: 'bg-sky-500/20 text-sky-300' },
};

const DEFAULT_COLOR = { bg: 'from-cyan-500/10 to-cyan-600/5', border: 'border-cyan-500/20', text: 'text-cyan-400', badge: 'bg-cyan-500/20 text-cyan-300' };

const AdminDashboard = () => {
    const navigate = useNavigate();
    const [stats, setStats] = useState(null);
    const [feedbackStats, setFeedbackStats] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    useEffect(() => {
        Promise.all([
            fetch(`${API_BASE}/stats`).then(res => {
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                return res.json();
            }),
            fetch(`${API_BASE}/feedback`).then(res => {
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                return res.json();
            }).catch(() => null),
        ])
            .then(([statsData, fbData]) => {
                setStats(statsData);
                setFeedbackStats(fbData);
                setLoading(false);
            })
            .catch(err => {
                setError(err.message);
                setLoading(false);
            });
    }, []);

    return (
        <div className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 relative overflow-hidden">

            {/* Ambient bg effects */}
            <div className="absolute inset-0 pointer-events-none">
                <div className="absolute top-[-20%] left-[-10%] w-[600px] h-[600px] rounded-full bg-cyan-500/[0.03] blur-3xl" />
                <div className="absolute bottom-[-15%] right-[-8%] w-[700px] h-[700px] rounded-full bg-purple-500/[0.03] blur-3xl" />
                <div className="absolute top-[40%] right-[20%] w-[300px] h-[300px] rounded-full bg-indigo-500/[0.02] blur-3xl" />
            </div>

            <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">

                {/* ── Header ────────────────────────────────────── */}
                <motion.div
                    initial={{ opacity: 0, y: -20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.5 }}
                    className="mb-10"
                >

                    <h1 className="text-4xl font-bold text-white tracking-tight">
                        Admin Dashboard
                    </h1>
                    <p className="text-white/40 text-sm mt-2">
                        Monitor agent activity, browse chat history, and manage data ingestion
                    </p>
                </motion.div>

                {/* ── Summary Stats ──────────────────────────────── */}
                <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.1 }}
                    className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-10"
                >
                    <div className="bg-white/[0.03] border border-white/[0.06] rounded-2xl p-5">
                        <p className="text-white/40 text-xs uppercase tracking-wider font-medium">Total Sessions</p>
                        <p className="text-3xl font-bold text-white mt-2">
                            {loading ? '—' : stats?.total_sessions ?? 0}
                        </p>
                        <p className="text-white/25 text-xs mt-1">across all agents</p>
                    </div>
                    <div className="bg-white/[0.03] border border-white/[0.06] rounded-2xl p-5">
                        <p className="text-white/40 text-xs uppercase tracking-wider font-medium">Active Agents</p>
                        <p className="text-3xl font-bold text-cyan-400 mt-2">
                            {loading ? '—' : stats?.agent_count ?? 0}
                        </p>
                        <p className="text-white/25 text-xs mt-1">deployed & running</p>
                    </div>
                    <div className="bg-white/[0.03] border border-white/[0.06] rounded-2xl p-5">
                        <p className="text-white/40 text-xs uppercase tracking-wider font-medium">User Feedback</p>
                        <div className="flex items-center gap-3 mt-2">
                            <span className="text-2xl font-bold text-emerald-400">
                                {loading ? '—' : feedbackStats?.thumbs_up ?? 0}
                            </span>
                            <span className="text-white/20">/</span>
                            <span className="text-2xl font-bold text-red-400">
                                {loading ? '—' : feedbackStats?.thumbs_down ?? 0}
                            </span>
                        </div>
                        <p className="text-white/25 text-xs mt-1">
                            {loading ? '' : `${feedbackStats?.total_feedback ?? 0} total ratings`}
                        </p>
                    </div>
                    <div className="bg-white/[0.03] border border-white/[0.06] rounded-2xl p-5">
                        <p className="text-white/40 text-xs uppercase tracking-wider font-medium">Status</p>
                        <div className="flex items-center gap-2 mt-2">
                            <div className={`w-2.5 h-2.5 rounded-full ${error ? 'bg-red-500' : 'bg-emerald-400 animate-pulse'}`} />
                            <p className={`text-xl font-bold ${error ? 'text-red-500' : 'text-emerald-400'}`}>
                                {error ? 'Offline' : 'Online'}
                            </p>
                        </div>
                        <p className="text-white/25 text-xs mt-1">
                            {error ? 'backend disconnected' : 'system operational'}
                        </p>
                    </div>
                </motion.div>

                {/* ── Quick Actions (Nav Cards) ────────────────── */}
                <motion.div
                    initial={{ opacity: 0, y: 15 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.2 }}
                    className="mb-10"
                >
                    <h2 className="text-lg font-semibold text-white/70 mb-4">Quick Actions</h2>
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">

                        {/* Chat History Card */}
                        <button
                            onClick={() => navigate('/admin/chats')}
                            className="group bg-white/[0.02] border border-white/[0.06] rounded-2xl p-6 text-left hover:bg-white/[0.05] hover:border-cyan-500/20 transition-all duration-300 cursor-pointer"
                        >
                            <div className="flex items-start justify-between">
                                <div className="p-3 rounded-xl bg-cyan-500/10 border border-cyan-500/20 group-hover:bg-cyan-500/20 transition-colors">
                                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="w-6 h-6 text-cyan-400">
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M20.25 8.511c.884.284 1.5 1.128 1.5 2.097v4.286c0 1.136-.847 2.1-1.98 2.193-.34.027-.68.052-1.02.072v3.091l-3-3c-1.354 0-2.694-.055-4.02-.163a2.115 2.115 0 01-.825-.242m9.345-8.334a2.126 2.126 0 00-.476-.095 48.64 48.64 0 00-8.048 0c-1.131.094-1.976 1.057-1.976 2.192v4.286c0 .837.46 1.58 1.155 1.951m9.345-8.334V6.637c0-1.621-1.152-3.026-2.76-3.235A48.455 48.455 0 0011.25 3c-2.115 0-4.198.137-6.24.402-1.608.209-2.76 1.614-2.76 3.235v6.226c0 1.621 1.152 3.026 2.76 3.235.577.075 1.157.14 1.74.194V21l4.155-4.155" />
                                    </svg>
                                </div>
                                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-5 h-5 text-white/20 group-hover:text-cyan-400 group-hover:translate-x-1 transition-all">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                                </svg>
                            </div>
                            <h3 className="text-white font-semibold text-lg mt-4 group-hover:text-cyan-300 transition-colors">Chat Sessions</h3>
                            <p className="text-white/40 text-sm mt-1">Browse, search, and review past conversations across all agents</p>
                            <div className="mt-4 flex items-center gap-2">
                                <span className="text-xs font-medium bg-white/[0.06] text-white/50 px-2.5 py-1 rounded-full">
                                    {loading ? '...' : `${stats?.total_sessions ?? 0} sessions`}
                                </span>
                                <span className="text-xs font-medium bg-white/[0.06] text-white/50 px-2.5 py-1 rounded-full">
                                    {loading ? '...' : `${stats?.agent_count ?? 0} agents`}
                                </span>
                            </div>
                        </button>

                        {/* Feedback Card */}
                        <button
                            onClick={() => navigate('/admin/feedback')}
                            className="group bg-white/[0.02] border border-white/[0.06] rounded-2xl p-6 text-left hover:bg-white/[0.05] hover:border-emerald-500/20 transition-all duration-300 cursor-pointer"
                        >
                            <div className="flex items-start justify-between">
                                <div className="p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/20 group-hover:bg-emerald-500/20 transition-colors">
                                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="w-6 h-6 text-emerald-400">
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M6.633 10.25c.806 0 1.533-.446 2.031-1.08a9.041 9.041 0 012.861-2.4c.723-.384 1.35-.956 1.653-1.715a4.498 4.498 0 00.322-1.672V2.75a.75.75 0 01.75-.75 2.25 2.25 0 012.25 2.25c0 1.152-.26 2.243-.723 3.218-.266.558.107 1.282.725 1.282h3.126c1.026 0 1.945.694 2.054 1.715.045.422.068.85.068 1.285a11.95 11.95 0 01-2.649 7.521c-.388.482-.987.729-1.605.729H14.23c-.483 0-.964-.078-1.423-.23l-3.114-1.04a4.501 4.501 0 00-1.423-.23H5.904m.729-10.055a3 3 0 00-2.346-1.174H3.75A1.5 1.5 0 002.25 6v9.75A1.5 1.5 0 003.75 17.25h.537a3 3 0 012.346 1.126l.177.222" />
                                    </svg>
                                </div>
                                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-5 h-5 text-white/20 group-hover:text-emerald-400 group-hover:translate-x-1 transition-all">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                                </svg>
                            </div>
                            <h3 className="text-white font-semibold text-lg mt-4 group-hover:text-emerald-300 transition-colors">User Feedback</h3>
                            <p className="text-white/40 text-sm mt-1">Review thumbs up/down ratings from users on AI responses</p>
                            <div className="mt-4 flex items-center gap-2">
                                <span className="text-xs font-medium bg-emerald-500/10 text-emerald-400/70 px-2.5 py-1 rounded-full">
                                    {loading ? '...' : `${feedbackStats?.thumbs_up ?? 0} positive`}
                                </span>
                                <span className="text-xs font-medium bg-red-500/10 text-red-400/70 px-2.5 py-1 rounded-full">
                                    {loading ? '...' : `${feedbackStats?.thumbs_down ?? 0} negative`}
                                </span>
                            </div>
                        </button>

                        {/* Ingestion Card */}
                        <button
                            onClick={() => navigate('/admin/ingestion')}
                            className="group bg-white/[0.02] border border-white/[0.06] rounded-2xl p-6 text-left hover:bg-white/[0.05] hover:border-purple-500/20 transition-all duration-300 cursor-pointer"
                        >
                            <div className="flex items-start justify-between">
                                <div className="p-3 rounded-xl bg-purple-500/10 border border-purple-500/20 group-hover:bg-purple-500/20 transition-colors">
                                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="w-6 h-6 text-purple-400">
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
                                    </svg>
                                </div>
                                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-5 h-5 text-white/20 group-hover:text-purple-400 group-hover:translate-x-1 transition-all">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                                </svg>
                            </div>
                            <h3 className="text-white font-semibold text-lg mt-4 group-hover:text-purple-300 transition-colors">Data Ingestion</h3>
                            <p className="text-white/40 text-sm mt-1">Ingest website URLs and OneDrive documents into agent knowledge bases</p>
                            <div className="mt-4 flex items-center gap-2">
                                <span className="text-xs font-medium bg-white/[0.06] text-white/50 px-2.5 py-1 rounded-full">
                                    URL Ingestion
                                </span>
                                <span className="text-xs font-medium bg-white/[0.06] text-white/50 px-2.5 py-1 rounded-full">
                                    OneDrive Sync
                                </span>
                            </div>
                        </button>
                    </div>
                </motion.div>

                {/* ── Per-Agent Breakdown ─────────────────────────── */}
                <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.3 }}
                >
                    <h2 className="text-lg font-semibold text-white/70 mb-4">Agent Activity</h2>
                    {error && (
                        <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-4 text-red-400 text-sm mb-4">
                            Failed to load stats: {error}
                        </div>
                    )}

                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                        {loading ? (
                            // Skeleton loaders
                            [...Array(6)].map((_, i) => (
                                <div key={i} className="bg-white/[0.02] border border-white/[0.06] rounded-2xl p-5 animate-pulse">
                                    <div className="h-3 bg-white/5 rounded w-20 mb-3" />
                                    <div className="h-8 bg-white/5 rounded w-12 mb-2" />
                                    <div className="h-2 bg-white/5 rounded w-16" />
                                </div>
                            ))
                        ) : (
                            stats?.agents?.map((agent, i) => {
                                const colors = AGENT_COLORS[agent.agent_id] || DEFAULT_COLOR;
                                const title = AGENT_MAP[agent.agent_id]?.title || agent.agent_id.toUpperCase();

                                return (
                                    <motion.button
                                        key={agent.agent_id}
                                        initial={{ opacity: 0, scale: 0.95 }}
                                        animate={{ opacity: 1, scale: 1 }}
                                        transition={{ delay: 0.3 + i * 0.05 }}
                                        onClick={() => navigate(`/admin/chats?agent=${agent.agent_id}`)}
                                        className={`bg-gradient-to-br ${colors.bg} border ${colors.border} rounded-2xl p-5 text-left hover:scale-[1.02] transition-all duration-200 cursor-pointer group`}
                                    >
                                        <div className="flex items-center justify-between">
                                            <span className={`text-xs font-semibold uppercase tracking-wider ${colors.text}`}>
                                                {title}
                                            </span>
                                            <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${colors.badge}`}>
                                                {agent.session_count > 0 ? 'Active' : 'No data'}
                                            </span>
                                        </div>
                                        <p className="text-3xl font-bold text-white mt-3">
                                            {agent.session_count}
                                        </p>
                                        <p className="text-white/30 text-xs mt-1">sessions recorded</p>
                                    </motion.button>
                                );
                            })
                        )}
                    </div>
                </motion.div>
            </div>
        </div>
    );
};

export default AdminDashboard;
