import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { AGENTS } from '../../config/agents';
import { motion } from 'framer-motion';
import AdminLayout from './AdminLayout';

const API_BASE = `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/v1/admin/dashboard`;

const AGENT_MAP = {};
Object.values(AGENTS).forEach(cfg => {
    AGENT_MAP[cfg.id] = cfg;
});

const AGENT_COLORS = {
  hr: { bg: 'from-purple-500/10 to-purple-600/5', border: 'border-purple-500/20', text: 'text-purple-300', badge: 'bg-purple-500/20 text-purple-200' },
  finance: { bg: 'from-blue-500/10 to-blue-600/5', border: 'border-blue-500/20', text: 'text-blue-300', badge: 'bg-blue-500/20 text-blue-200' },
  admin: { bg: 'from-slate-400/10 to-slate-500/5', border: 'border-slate-400/20', text: 'text-slate-300', badge: 'bg-slate-400/20 text-slate-200' },
  process: { bg: 'from-emerald-500/10 to-emerald-600/5', border: 'border-emerald-500/20', text: 'text-emerald-300', badge: 'bg-emerald-500/20 text-emerald-200' },
  enterprise: { bg: 'from-indigo-500/10 to-indigo-600/5', border: 'border-indigo-500/20', text: 'text-indigo-300', badge: 'bg-indigo-500/20 text-indigo-200' },
  lifestore: { bg: 'from-orange-500/10 to-orange-600/5', border: 'border-orange-500/20', text: 'text-orange-300', badge: 'bg-orange-500/20 text-orange-200' },
  it: { bg: 'from-sky-500/10 to-sky-600/5', border: 'border-sky-500/20', text: 'text-sky-300', badge: 'bg-sky-500/20 text-sky-200' },
  cia: { bg: 'from-rose-500/10 to-rose-600/5', border: 'border-rose-500/20', text: 'text-rose-300', badge: 'bg-rose-500/20 text-rose-300' },
  network: { bg: 'from-teal-500/10 to-teal-600/5', border: 'border-teal-500/20', text: 'text-teal-300', badge: 'bg-teal-500/20 text-teal-300' },
  legal: { bg: 'from-amber-500/10 to-amber-600/5', border: 'border-amber-500/20', text: 'text-amber-300', badge: 'bg-amber-500/20 text-amber-300' },
  marketing: { bg: 'from-pink-500/10 to-pink-600/5', border: 'border-pink-500/20', text: 'text-pink-300', badge: 'bg-pink-500/20 text-pink-300' },
  enterprise_business: { bg: 'from-violet-500/10 to-violet-600/5', border: 'border-violet-500/20', text: 'text-violet-300', badge: 'bg-violet-500/20 text-violet-300' },
  consumer_business: { bg: 'from-green-500/10 to-green-600/5', border: 'border-green-500/20', text: 'text-green-300', badge: 'bg-green-500/20 text-green-300' },
};

const DEFAULT_COLOR = {
    bg: 'from-cyan-500/10 to-cyan-600/5',
    border: 'border-cyan-500/20',
    text: 'text-cyan-300',
    badge: 'bg-cyan-500/20 text-cyan-200',
};

const StatCard = ({ label, value, helper, accent = 'text-white', children }) => (
    <div className="rounded-3xl border border-white/[0.08] bg-white/[0.045] p-6 shadow-xl shadow-black/10 backdrop-blur-sm">
        <p className="text-white/45 text-xs uppercase tracking-[0.18em] font-semibold">{label}</p>
        {children || (
            <p className={`text-4xl font-bold mt-3 ${accent}`}>{value}</p>
        )}
        <p className="text-white/30 text-xs mt-2">{helper}</p>
    </div>
);

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
        <AdminLayout
            title="Admin Dashboard"
            subtitle="Monitor agent activity, browse chat history, review feedback, and manage knowledge base ingestion."
            activePage="dashboard"
        >
            {/* Summary Stats */}
            <motion.div
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.05 }}
                className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-5 mb-10"
            >
                <StatCard
                    label="Total Sessions"
                    value={loading ? '—' : stats?.total_sessions ?? 0}
                    helper="Across all connected agents"
                />

                <StatCard
                    label="Active Agents"
                    value={loading ? '—' : stats?.agent_count ?? 0}
                    helper="Deployed and running"
                    accent="text-cyan-300"
                />

                <StatCard label="User Feedback" helper={loading ? '' : `${feedbackStats?.total_feedback ?? 0} total ratings`}>
                    <div className="flex items-center gap-3 mt-3">
                        <span className="text-4xl font-bold text-emerald-300">
                            {loading ? '—' : feedbackStats?.thumbs_up ?? 0}
                        </span>
                        <span className="text-white/20 text-2xl">/</span>
                        <span className="text-4xl font-bold text-red-300">
                            {loading ? '—' : feedbackStats?.thumbs_down ?? 0}
                        </span>
                    </div>
                </StatCard>

                <StatCard label="System Status" helper={error ? 'Backend disconnected' : 'System operational'}>
                    <div className="flex items-center gap-3 mt-4">
                        <span className={`h-3 w-3 rounded-full ${error ? 'bg-red-400' : 'bg-emerald-400 animate-pulse'}`} />
                        <span className={`text-3xl font-bold ${error ? 'text-red-300' : 'text-emerald-300'}`}>
                            {error ? 'Offline' : 'Online'}
                        </span>
                    </div>
                </StatCard>
            </motion.div>

            {/* Quick Actions */}
            <motion.section
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.12 }}
                className="mb-10"
            >
                <div className="flex items-end justify-between gap-4 mb-5">
                    <div>
                        <h2 className="text-xl font-semibold text-white">Quick Actions</h2>
                        <p className="text-white/35 text-sm mt-1">Common admin operations and monitoring tools.</p>
                    </div>
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
                    <button
                        onClick={() => navigate('/admin/chats')}
                        className="group rounded-3xl border border-white/[0.08] bg-white/[0.035] p-6 text-left hover:bg-cyan-400/[0.06] hover:border-cyan-400/25 transition-all duration-300 shadow-xl shadow-black/10"
                    >
                        <div className="flex items-start justify-between">
                            <div className="p-3 rounded-2xl bg-cyan-400/[0.10] border border-cyan-400/20">
                                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.6} stroke="currentColor" className="w-6 h-6 text-cyan-300">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M20.25 8.511c.884.284 1.5 1.128 1.5 2.097v4.286c0 1.136-.847 2.1-1.98 2.193-.34.027-.68.052-1.02.072v3.091l-3-3c-1.354 0-2.694-.055-4.02-.163a2.115 2.115 0 01-.825-.242m9.345-8.334a2.126 2.126 0 00-.476-.095 48.64 48.64 0 00-8.048 0c-1.131.094-1.976 1.057-1.976 2.192v4.286c0 .837.46 1.58 1.155 1.951m9.345-8.334V6.637c0-1.621-1.152-3.026-2.76-3.235A48.455 48.455 0 0011.25 3c-2.115 0-4.198.137-6.24.402-1.608.209-2.76 1.614-2.76 3.235v6.226c0 1.621 1.152 3.026 2.76 3.235.577.075 1.157.14 1.74.194V21l4.155-4.155" />
                                </svg>
                            </div>
                            <span className="text-white/25 group-hover:text-cyan-300 group-hover:translate-x-1 transition-all">→</span>
                        </div>

                        <h3 className="text-white font-semibold text-lg mt-5">Chat Sessions</h3>
                        <p className="text-white/42 text-sm mt-2 leading-relaxed">
                            Browse, search, and review past conversations across all agents.
                        </p>

                        <div className="mt-5 flex flex-wrap gap-2">
                            <span className="text-xs font-medium bg-white/[0.07] text-white/55 px-3 py-1 rounded-full">
                                {loading ? '...' : `${stats?.total_sessions ?? 0} sessions`}
                            </span>
                            <span className="text-xs font-medium bg-white/[0.07] text-white/55 px-3 py-1 rounded-full">
                                {loading ? '...' : `${stats?.agent_count ?? 0} agents`}
                            </span>
                        </div>
                    </button>

                    <button
                        onClick={() => navigate('/admin/feedback')}
                        className="group rounded-3xl border border-white/[0.08] bg-white/[0.035] p-6 text-left hover:bg-emerald-400/[0.06] hover:border-emerald-400/25 transition-all duration-300 shadow-xl shadow-black/10"
                    >
                        <div className="flex items-start justify-between">
                            <div className="p-3 rounded-2xl bg-emerald-400/[0.10] border border-emerald-400/20">
                                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.6} stroke="currentColor" className="w-6 h-6 text-emerald-300">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M6.633 10.25c.806 0 1.533-.446 2.031-1.08a9.041 9.041 0 012.861-2.4c.723-.384 1.35-.956 1.653-1.715a4.498 4.498 0 00.322-1.672V2.75a.75.75 0 01.75-.75 2.25 2.25 0 012.25 2.25c0 1.152-.26 2.243-.723 3.218-.266.558.107 1.282.725 1.282h3.126c1.026 0 1.945.694 2.054 1.715.045.422.068.85.068 1.285a11.95 11.95 0 01-2.649 7.521c-.388.482-.987.729-1.605.729H14.23c-.483 0-.964-.078-1.423-.23l-3.114-1.04a4.501 4.501 0 00-1.423-.23H5.904m.729-10.055a3 3 0 00-2.346-1.174H3.75A1.5 1.5 0 002.25 6v9.75A1.5 1.5 0 003.75 17.25h.537a3 3 0 012.346 1.126l.177.222" />
                                </svg>
                            </div>
                            <span className="text-white/25 group-hover:text-emerald-300 group-hover:translate-x-1 transition-all">→</span>
                        </div>

                        <h3 className="text-white font-semibold text-lg mt-5">User Feedback</h3>
                        <p className="text-white/42 text-sm mt-2 leading-relaxed">
                            Review thumbs up/down ratings from users on AI responses.
                        </p>

                        <div className="mt-5 flex flex-wrap gap-2">
                            <span className="text-xs font-medium bg-emerald-400/[0.10] text-emerald-300 px-3 py-1 rounded-full">
                                {loading ? '...' : `${feedbackStats?.thumbs_up ?? 0} positive`}
                            </span>
                            <span className="text-xs font-medium bg-red-400/[0.10] text-red-300 px-3 py-1 rounded-full">
                                {loading ? '...' : `${feedbackStats?.thumbs_down ?? 0} negative`}
                            </span>
                        </div>
                    </button>

                    <button
                        onClick={() => navigate('/admin/ingestion')}
                        className="group rounded-3xl border border-purple-400/20 bg-purple-400/[0.06] p-6 text-left hover:bg-purple-400/[0.10] hover:border-purple-300/35 transition-all duration-300 shadow-xl shadow-purple-950/20"
                    >
                        <div className="flex items-start justify-between">
                            <div className="p-3 rounded-2xl bg-purple-400/[0.14] border border-purple-300/20">
                                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.6} stroke="currentColor" className="w-6 h-6 text-purple-300">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
                                </svg>
                            </div>
                            <span className="text-purple-200/60 group-hover:text-purple-200 group-hover:translate-x-1 transition-all">→</span>
                        </div>

                        <h3 className="text-purple-100 font-semibold text-lg mt-5">Data Ingestion</h3>
                        <p className="text-white/45 text-sm mt-2 leading-relaxed">
                            Ingest website URLs, OneDrive documents, and SharePoint folders into agent knowledge bases.
                        </p>

                        <div className="mt-5 flex flex-wrap gap-2">
                            {['URL', 'OneDrive', 'SharePoint'].map((item) => (
                                <span key={item} className="text-xs font-medium bg-white/[0.08] text-white/60 px-3 py-1 rounded-full">
                                    {item}
                                </span>
                            ))}
                        </div>
                    </button>
                </div>
            </motion.section>

            {/* Agent Activity */}
            <motion.section
                initial={{ opacity: 0, y: 18 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.18 }}
            >
                <div className="flex items-end justify-between gap-4 mb-5">
                    <div>
                        <h2 className="text-xl font-semibold text-white">Agent Activity</h2>
                        <p className="text-white/35 text-sm mt-1">Session count by deployed assistant.</p>
                    </div>
                </div>

                {error && (
                    <div className="bg-red-500/10 border border-red-500/20 rounded-2xl p-4 text-red-300 text-sm mb-4">
                        Failed to load stats: {error}
                    </div>
                )}

                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
                    {loading ? (
                        [...Array(6)].map((_, i) => (
                            <div key={i} className="bg-white/[0.035] border border-white/[0.08] rounded-3xl p-5 animate-pulse">
                                <div className="h-3 bg-white/5 rounded w-24 mb-4" />
                                <div className="h-9 bg-white/5 rounded w-14 mb-3" />
                                <div className="h-2 bg-white/5 rounded w-20" />
                            </div>
                        ))
                    ) : (
                        stats?.agents?.map((agent, i) => {
                            const colors = AGENT_COLORS[agent.agent_id] || DEFAULT_COLOR;
                            const title = AGENT_MAP[agent.agent_id]?.title || agent.agent_id.toUpperCase();

                            return (
                                <motion.button
                                    key={agent.agent_id}
                                    initial={{ opacity: 0, scale: 0.96 }}
                                    animate={{ opacity: 1, scale: 1 }}
                                    transition={{ delay: 0.22 + i * 0.04 }}
                                    onClick={() => navigate(`/admin/chats?agent=${agent.agent_id}`)}
                                    className={`bg-gradient-to-br ${colors.bg} border ${colors.border} rounded-3xl p-5 text-left hover:scale-[1.015] transition-all duration-200 cursor-pointer group shadow-xl shadow-black/10`}
                                >
                                    <div className="flex items-center justify-between">
                                        <span className={`text-xs font-bold uppercase tracking-[0.18em] ${colors.text}`}>
                                            {title}
                                        </span>
                                        <span className={`text-[10px] px-2.5 py-1 rounded-full font-semibold ${colors.badge}`}>
                                            {agent.session_count > 0 ? 'Active' : 'No data'}
                                        </span>
                                    </div>

                                    <p className="text-4xl font-bold text-white mt-4">
                                        {agent.session_count}
                                    </p>

                                    <p className="text-white/35 text-xs mt-2">
                                        Sessions recorded
                                    </p>
                                </motion.button>
                            );
                        })
                    )}
                </div>
            </motion.section>
        </AdminLayout>
    );
};

export default AdminDashboard;