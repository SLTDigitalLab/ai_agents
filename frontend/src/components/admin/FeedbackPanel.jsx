import { useState, useEffect } from 'react';
import { AGENTS } from '../../config/agents';
import { motion } from 'framer-motion';

const API_BASE = 'http://localhost:8000/api/v1/admin/dashboard';

const AGENT_MAP = {};
Object.values(AGENTS).forEach(cfg => {
    AGENT_MAP[cfg.id] = cfg;
});

const AGENT_COLORS = {
    hr: { bg: 'bg-purple-500/10', border: 'border-purple-500/20', text: 'text-purple-400' },
    finance: { bg: 'bg-blue-500/10', border: 'border-blue-500/20', text: 'text-blue-400' },
    admin: { bg: 'bg-gray-500/10', border: 'border-gray-500/20', text: 'text-gray-400' },
    process: { bg: 'bg-emerald-500/10', border: 'border-emerald-500/20', text: 'text-emerald-400' },
    enterprise: { bg: 'bg-indigo-500/10', border: 'border-indigo-500/20', text: 'text-indigo-400' },
    lifestore: { bg: 'bg-orange-500/10', border: 'border-orange-500/20', text: 'text-orange-400' },
};

const DEFAULT_COLOR = { bg: 'bg-cyan-500/10', border: 'border-cyan-500/20', text: 'text-cyan-400' };

const FeedbackPanel = () => {
    const [stats, setStats] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [expandedId, setExpandedId] = useState(null);

    useEffect(() => {
        fetch(`${API_BASE}/feedback`)
            .then(res => {
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                return res.json();
            })
            .then(data => {
                setStats(data);
                setLoading(false);
            })
            .catch(err => {
                setError(err.message);
                setLoading(false);
            });
    }, []);

    const satisfactionRate = stats && stats.total_feedback > 0
        ? Math.round((stats.thumbs_up / stats.total_feedback) * 100)
        : null;

    return (
        <div className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 relative overflow-hidden">
            {/* Ambient bg effects */}
            <div className="absolute inset-0 pointer-events-none">
                <div className="absolute top-[-15%] left-[-10%] w-[500px] h-[500px] rounded-full bg-emerald-500/[0.04] blur-3xl" />
                <div className="absolute bottom-[-10%] right-[-5%] w-[600px] h-[600px] rounded-full bg-red-500/[0.03] blur-3xl" />
            </div>

            <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
                {/* Header */}
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
                    <h1 className="text-3xl font-bold text-white tracking-tight">User Feedback</h1>
                    <p className="text-white/40 text-sm mt-1">Review thumbs up/down ratings from users on AI responses</p>
                </motion.div>

                {/* Summary Stats */}
                <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.1 }}
                    className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8"
                >
                    <div className="bg-white/[0.03] border border-white/[0.06] rounded-2xl p-5">
                        <p className="text-white/40 text-xs uppercase tracking-wider font-medium">Total Ratings</p>
                        <p className="text-3xl font-bold text-white mt-2">
                            {loading ? '—' : stats?.total_feedback ?? 0}
                        </p>
                    </div>
                    <div className="bg-white/[0.03] border border-white/[0.06] rounded-2xl p-5">
                        <p className="text-white/40 text-xs uppercase tracking-wider font-medium">Positive</p>
                        <p className="text-3xl font-bold text-emerald-400 mt-2">
                            {loading ? '—' : stats?.thumbs_up ?? 0}
                        </p>
                        <p className="text-white/25 text-xs mt-1">thumbs up</p>
                    </div>
                    <div className="bg-white/[0.03] border border-white/[0.06] rounded-2xl p-5">
                        <p className="text-white/40 text-xs uppercase tracking-wider font-medium">Negative</p>
                        <p className="text-3xl font-bold text-red-400 mt-2">
                            {loading ? '—' : stats?.thumbs_down ?? 0}
                        </p>
                        <p className="text-white/25 text-xs mt-1">thumbs down</p>
                    </div>
                    <div className="bg-white/[0.03] border border-white/[0.06] rounded-2xl p-5">
                        <p className="text-white/40 text-xs uppercase tracking-wider font-medium">Satisfaction</p>
                        <p className={`text-3xl font-bold mt-2 ${satisfactionRate !== null && satisfactionRate >= 70 ? 'text-emerald-400' : satisfactionRate !== null && satisfactionRate >= 40 ? 'text-yellow-400' : 'text-red-400'}`}>
                            {loading ? '—' : satisfactionRate !== null ? `${satisfactionRate}%` : 'N/A'}
                        </p>
                        <p className="text-white/25 text-xs mt-1">approval rate</p>
                    </div>
                </motion.div>

                {/* Per-Agent Breakdown */}
                <motion.div
                    initial={{ opacity: 0, y: 15 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.2 }}
                    className="mb-8"
                >
                    <h2 className="text-lg font-semibold text-white/70 mb-4">Per-Agent Breakdown</h2>
                    {error && (
                        <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-4 text-red-400 text-sm mb-4">
                            Failed to load feedback: {error}
                        </div>
                    )}
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                        {loading ? (
                            [...Array(6)].map((_, i) => (
                                <div key={i} className="bg-white/[0.02] border border-white/[0.06] rounded-2xl p-5 animate-pulse">
                                    <div className="h-3 bg-white/5 rounded w-20 mb-3" />
                                    <div className="h-8 bg-white/5 rounded w-24 mb-2" />
                                </div>
                            ))
                        ) : (
                            stats?.per_agent?.map((agent, i) => {
                                const colors = AGENT_COLORS[agent.agent_id] || DEFAULT_COLOR;
                                const title = AGENT_MAP[agent.agent_id]?.title || agent.agent_id.toUpperCase();
                                const rate = agent.total > 0 ? Math.round((agent.thumbs_up / agent.total) * 100) : null;

                                return (
                                    <motion.div
                                        key={agent.agent_id}
                                        initial={{ opacity: 0, scale: 0.95 }}
                                        animate={{ opacity: 1, scale: 1 }}
                                        transition={{ delay: 0.2 + i * 0.05 }}
                                        className={`${colors.bg} border ${colors.border} rounded-2xl p-5`}
                                    >
                                        <span className={`text-xs font-semibold uppercase tracking-wider ${colors.text}`}>
                                            {title}
                                        </span>
                                        <div className="flex items-center gap-4 mt-3">
                                            <div className="flex items-center gap-1.5">
                                                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 text-emerald-400">
                                                    <path d="M1 8.998a1 1 0 011-1h.764a1.483 1.483 0 00-.076.506v5.996a1.483 1.483 0 00.076.506H2a1 1 0 01-1-1V8.998zM5.25 7.726a2 2 0 01.944-1.697l3.476-2.14a1.5 1.5 0 012.33 1.25v2.363h2.5a2 2 0 011.96 2.4l-.782 3.908A2 2 0 0113.72 15.5H5.25V7.726z" />
                                                </svg>
                                                <span className="text-lg font-bold text-white">{agent.thumbs_up}</span>
                                            </div>
                                            <div className="flex items-center gap-1.5">
                                                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 text-red-400">
                                                    <path d="M19 11.002a1 1 0 01-1 1h-.764a1.483 1.483 0 00.076-.506V5.5a1.483 1.483 0 00-.076-.506H18a1 1 0 011 1v5.008zM14.75 12.274a2 2 0 01-.944 1.697l-3.476 2.14a1.5 1.5 0 01-2.33-1.25V12.5h-2.5a2 2 0 01-1.96-2.4l.782-3.908A2 2 0 016.28 4.5h8.47v7.774z" />
                                                </svg>
                                                <span className="text-lg font-bold text-white">{agent.thumbs_down}</span>
                                            </div>
                                            {rate !== null && (
                                                <span className={`ml-auto text-sm font-medium ${rate >= 70 ? 'text-emerald-400' : rate >= 40 ? 'text-yellow-400' : 'text-red-400'}`}>
                                                    {rate}%
                                                </span>
                                            )}
                                        </div>
                                        <p className="text-white/25 text-xs mt-2">{agent.total} total ratings</p>
                                    </motion.div>
                                );
                            })
                        )}
                        {!loading && (!stats?.per_agent || stats.per_agent.length === 0) && (
                            <div className="col-span-full text-center py-10">
                                <p className="text-white/30 text-sm">No feedback data yet</p>
                                <p className="text-white/15 text-xs mt-1">Feedback will appear here once users start rating responses</p>
                            </div>
                        )}
                    </div>
                </motion.div>

                {/* Recent Feedback */}
                <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.3 }}
                >
                    <h2 className="text-lg font-semibold text-white/70 mb-4">Recent Feedback</h2>
                    <div className="bg-white/[0.02] border border-white/[0.06] rounded-2xl overflow-hidden">
                        {/* Table Header */}
                        <div className="grid grid-cols-12 gap-4 px-6 py-3 border-b border-white/[0.06] bg-white/[0.02] text-white/40 text-xs uppercase tracking-wider font-semibold">
                            <div className="col-span-2">Agent</div>
                            <div className="col-span-1 text-center">Rating</div>
                            <div className="col-span-2">User</div>
                            <div className="col-span-4">Message</div>
                            <div className="col-span-1 text-center">Msg #</div>
                            <div className="col-span-2 text-right">Time</div>
                        </div>

                        {loading && (
                            <div className="flex items-center justify-center py-20">
                                <div className="flex items-center gap-3">
                                    <div className="w-2 h-2 rounded-full bg-cyan-400 animate-bounce" />
                                    <div className="w-2 h-2 rounded-full bg-cyan-400 animate-bounce [animation-delay:150ms]" />
                                    <div className="w-2 h-2 rounded-full bg-cyan-400 animate-bounce [animation-delay:300ms]" />
                                </div>
                            </div>
                        )}

                        {!loading && (!stats?.recent || stats.recent.length === 0) && (
                            <div className="px-6 py-16 text-center">
                                <p className="text-white/30 text-sm">No feedback entries yet</p>
                            </div>
                        )}

                        {!loading && stats?.recent?.map((entry, i) => {
                            const colors = AGENT_COLORS[entry.agent_id] || DEFAULT_COLOR;
                            const title = AGENT_MAP[entry.agent_id]?.title || entry.agent_id.toUpperCase();
                            const time = new Date(entry.created_at).toLocaleString();
                            const isExpanded = expandedId === entry.id;
                            const preview = entry.message_content
                                ? entry.message_content.substring(0, 80) + (entry.message_content.length > 80 ? '...' : '')
                                : '(message unavailable)';

                            return (
                                <motion.div
                                    key={entry.id}
                                    initial={{ opacity: 0 }}
                                    animate={{ opacity: 1 }}
                                    transition={{ delay: i * 0.02 }}
                                >
                                    {/* Main Row */}
                                    <div
                                        className="grid grid-cols-12 gap-4 px-6 py-3.5 border-b border-white/[0.04] hover:bg-white/[0.03] transition-colors cursor-pointer"
                                        onClick={() => setExpandedId(isExpanded ? null : entry.id)}
                                    >
                                        <div className="col-span-2">
                                            <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${colors.bg} ${colors.border} border ${colors.text}`}>
                                                {title}
                                            </span>
                                        </div>
                                        <div className="col-span-1 text-center">
                                            {entry.rating === 'up' ? (
                                                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 text-emerald-400 inline-block">
                                                    <path d="M1 8.998a1 1 0 011-1h.764a1.483 1.483 0 00-.076.506v5.996a1.483 1.483 0 00.076.506H2a1 1 0 01-1-1V8.998zM5.25 7.726a2 2 0 01.944-1.697l3.476-2.14a1.5 1.5 0 012.33 1.25v2.363h2.5a2 2 0 011.96 2.4l-.782 3.908A2 2 0 0113.72 15.5H5.25V7.726z" />
                                                </svg>
                                            ) : (
                                                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 text-red-400 inline-block">
                                                    <path d="M19 11.002a1 1 0 01-1 1h-.764a1.483 1.483 0 00.076-.506V5.5a1.483 1.483 0 00-.076-.506H18a1 1 0 011 1v5.008zM14.75 12.274a2 2 0 01-.944 1.697l-3.476 2.14a1.5 1.5 0 01-2.33-1.25V12.5h-2.5a2 2 0 01-1.96-2.4l.782-3.908A2 2 0 016.28 4.5h8.47v7.774z" />
                                                </svg>
                                            )}
                                        </div>
                                        <div className="col-span-2 text-white/50 text-sm truncate">
                                            {entry.user_id}
                                        </div>
                                        <div className="col-span-4 text-white/40 text-sm truncate">
                                            {preview}
                                        </div>
                                        <div className="col-span-1 text-center text-white/50 text-sm">
                                            {entry.message_index}
                                        </div>
                                        <div className="col-span-2 text-right text-white/30 text-xs flex items-center justify-end gap-2">
                                            {time}
                                            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className={`w-3.5 h-3.5 text-white/20 transition-transform ${isExpanded ? 'rotate-180' : ''}`}>
                                                <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                                            </svg>
                                        </div>
                                    </div>

                                    {/* Expanded Message Content */}
                                    {isExpanded && entry.message_content && (
                                        <motion.div
                                            initial={{ opacity: 0, height: 0 }}
                                            animate={{ opacity: 1, height: 'auto' }}
                                            exit={{ opacity: 0, height: 0 }}
                                            className="px-6 py-4 bg-white/[0.02] border-b border-white/[0.06]"
                                        >
                                            {entry.user_question && (
                                                <div className="mb-3">
                                                    <span className="text-xs font-semibold text-blue-400 uppercase tracking-wider">User Question</span>
                                                    <p className="text-white/50 text-sm mt-1 leading-relaxed whitespace-pre-wrap">
                                                        {entry.user_question}
                                                    </p>
                                                </div>
                                            )}
                                            <div>
                                                <span className={`text-xs font-semibold uppercase tracking-wider ${entry.rating === 'up' ? 'text-emerald-400' : 'text-red-400'}`}>
                                                    AI Response {entry.rating === 'up' ? '(Helpful)' : '(Not Helpful)'}
                                                </span>
                                                <p className="text-white/60 text-sm mt-1 leading-relaxed whitespace-pre-wrap max-h-60 overflow-y-auto">
                                                    {entry.message_content}
                                                </p>
                                            </div>
                                            <div className="mt-3 text-white/20 text-xs font-mono">
                                                Session: {entry.thread_id}
                                            </div>
                                        </motion.div>
                                    )}
                                </motion.div>
                            );
                        })}
                    </div>
                </motion.div>
            </div>
        </div>
    );
};

export default FeedbackPanel;
