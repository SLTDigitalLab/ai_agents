import { useState, useEffect } from 'react';
import { AGENTS } from '../../config/agents';
import { motion } from 'framer-motion';
import AdminLayout from './AdminLayout';

const API_BASE = `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/v1/admin/dashboard`;

const AGENT_MAP = {};
Object.values(AGENTS).forEach(cfg => {
    AGENT_MAP[cfg.id] = cfg;
});

const AGENT_COLORS = {
    hr: {
        card: 'bg-purple-500/10 border-purple-500/20',
        badge: 'bg-purple-500/20 border-purple-500/25 text-purple-200',
        text: 'text-purple-300',
    },
    finance: {
        card: 'bg-blue-500/10 border-blue-500/20',
        badge: 'bg-blue-500/20 border-blue-500/25 text-blue-200',
        text: 'text-blue-300',
    },
    admin: {
        card: 'bg-slate-400/10 border-slate-400/20',
        badge: 'bg-slate-400/20 border-slate-400/25 text-slate-200',
        text: 'text-slate-300',
    },
    process: {
        card: 'bg-emerald-500/10 border-emerald-500/20',
        badge: 'bg-emerald-500/20 border-emerald-500/25 text-emerald-200',
        text: 'text-emerald-300',
    },
    enterprise: {
        card: 'bg-indigo-500/10 border-indigo-500/20',
        badge: 'bg-indigo-500/20 border-indigo-500/25 text-indigo-200',
        text: 'text-indigo-300',
    },
    lifestore: {
        card: 'bg-orange-500/10 border-orange-500/20',
        badge: 'bg-orange-500/20 border-orange-500/25 text-orange-200',
        text: 'text-orange-300',
    },
    it: {
        card: 'bg-sky-500/10 border-sky-500/20',
        badge: 'bg-sky-500/20 border-sky-500/25 text-sky-200',
        text: 'text-sky-300',
    },
    cia: {
        card: 'bg-rose-500/10 border-rose-500/20',
        badge: 'bg-rose-500/20 border-rose-500/25 text-rose-200',
        text: 'text-rose-300',
    },
    network: {
        card: 'bg-teal-500/10 border-teal-500/20',
        badge: 'bg-teal-500/20 border-teal-500/25 text-teal-200',
        text: 'text-teal-300',
    },
    legal: {
        card: 'bg-amber-500/10 border-amber-500/20',
        badge: 'bg-amber-500/20 border-amber-500/25 text-amber-200',
        text: 'text-amber-300',
    },
    marketing: {
        card: 'bg-pink-500/10 border-pink-500/20',
        badge: 'bg-pink-500/20 border-pink-500/25 text-pink-200',
        text: 'text-pink-300',
    },
    enterprise_business: {
        card: 'bg-violet-500/10 border-violet-500/20',
        badge: 'bg-violet-500/20 border-violet-500/25 text-violet-200',
        text: 'text-violet-300',
    },
    consumer_business: {
        card: 'bg-green-500/10 border-green-500/20',
        badge: 'bg-green-500/20 border-green-500/25 text-green-200',
        text: 'text-green-300',
    },
};

const DEFAULT_COLOR = {
    card: 'bg-cyan-500/10 border-cyan-500/20',
    badge: 'bg-cyan-500/20 border-cyan-500/25 text-cyan-200',
    text: 'text-cyan-300',
};

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

    const statCardClass =
        'rounded-[24px] border border-white/[0.08] bg-white/[0.045] p-5 shadow-xl shadow-black/10 backdrop-blur-sm';

    const sectionCardClass =
        'rounded-[28px] border border-white/[0.08] bg-white/[0.035] p-6 shadow-2xl shadow-black/15 backdrop-blur-sm';

    const tableCardClass =
        'rounded-[28px] border border-white/[0.08] bg-white/[0.035] overflow-hidden shadow-2xl shadow-black/15 backdrop-blur-sm';

    const tableHeaderClass =
        'grid grid-cols-12 gap-4 px-6 py-4 border-b border-white/[0.08] bg-white/[0.035] text-white/45 text-xs uppercase tracking-wider font-bold';

    const tableRowClass =
        'grid grid-cols-12 gap-4 px-6 py-4 border-b border-white/[0.05] hover:bg-white/[0.045] transition-colors cursor-pointer';  

    return (
        <AdminLayout
            title="User Feedback"
            subtitle="Review thumbs up/down ratings from users on AI responses."
            backTo="/admin"
            backLabel="Back to Dashboard"
            backgroundVariant="legacy-dark"
        >
            <div className="relative z-10 max-w-7xl mx-auto">
                {/* Summary Stats */}
                <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.1 }}
                    className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8"
                >
                    <div className={statCardClass}>
                        <p className="text-white/45 text-xs uppercase tracking-wider font-bold">
                            Total Ratings
                        </p>
                        <p className="text-3xl font-bold text-white mt-2">
                            {loading ? '—' : stats?.total_feedback ?? 0}
                        </p>
                    </div>

                    <div className={statCardClass}>
                        <p className="text-white/45 text-xs uppercase tracking-wider font-bold">
                            Positive
                        </p>
                        <p className="text-3xl font-bold text-emerald-300 mt-2">
                            {loading ? '—' : stats?.thumbs_up ?? 0}
                        </p>
                        <p className="text-white/30 text-xs mt-1">thumbs up</p>
                    </div>

                    <div className={statCardClass}>
                        <p className="text-white/45 text-xs uppercase tracking-wider font-bold">
                            Negative
                        </p>
                        <p className="text-3xl font-bold text-red-300 mt-2">
                            {loading ? '—' : stats?.thumbs_down ?? 0}
                        </p>
                        <p className="text-white/30 text-xs mt-1">thumbs down</p>
                    </div>

                    <div className={statCardClass}>
                        <p className="text-white/45 text-xs uppercase tracking-wider font-bold">
                            Satisfaction
                        </p>
                        <p
                            className={`text-3xl font-bold mt-2 ${
                                satisfactionRate !== null && satisfactionRate >= 70
                                    ? 'text-emerald-300'
                                    : satisfactionRate !== null && satisfactionRate >= 40
                                        ? 'text-yellow-300'
                                        : 'text-red-300'
                            }`}
                        >
                            {loading ? '—' : satisfactionRate !== null ? `${satisfactionRate}%` : 'N/A'}
                        </p>
                        <p className="text-white/30 text-xs mt-1">approval rate</p>
                    </div>
                </motion.div>

                {/* Per-Agent Breakdown */}
                <motion.div
                    initial={{ opacity: 0, y: 15 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.2 }}
                    className="mb-8"
                >
                    <div className={sectionCardClass}>
                        <div className="mb-5">
                            <h2 className="text-white text-xl font-bold">
                                Per-Agent Breakdown
                            </h2>
                            <p className="text-white/40 text-sm mt-1">
                                Feedback ratings grouped by each assistant.
                            </p>
                        </div>

                        {error && (
                            <div className="bg-red-500/10 border border-red-500/25 rounded-2xl p-4 text-red-300 text-sm font-medium mb-4">
                                Failed to load feedback: {error}
                            </div>
                        )}

                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                            {loading ? (
                                [...Array(6)].map((_, i) => (
                                    <div
                                        key={i}
                                        className="bg-white/[0.035] border border-white/[0.08] rounded-2xl p-5 animate-pulse"
                                    >
                                        <div className="h-3 bg-white/10 rounded w-20 mb-3" />
                                        <div className="h-8 bg-white/10 rounded w-24 mb-2" />
                                    </div>
                                ))
                            ) : (
                                stats?.per_agent?.map((agent, i) => {
                                    const colors = AGENT_COLORS[agent.agent_id] || DEFAULT_COLOR;
                                    const title = AGENT_MAP[agent.agent_id]?.title || agent.agent_id.toUpperCase();
                                    const rate = agent.total > 0
                                        ? Math.round((agent.thumbs_up / agent.total) * 100)
                                        : null;

                                    return (
                                        <motion.div
                                            key={agent.agent_id}
                                            initial={{ opacity: 0, scale: 0.95 }}
                                            animate={{ opacity: 1, scale: 1 }}
                                            transition={{ delay: 0.2 + i * 0.05 }}
                                            className={`${colors.card} border rounded-2xl p-5`}
                                        >
                                            <span className={`text-xs font-bold uppercase tracking-wider ${colors.text}`}>
                                                {title}
                                            </span>

                                            <div className="flex items-center gap-4 mt-4">
                                                <div className="flex items-center gap-1.5">
                                                    <span className="text-emerald-600 text-sm">👍</span>
                                                    <span className="text-xl font-bold text-white">
                                                        {agent.thumbs_up}
                                                    </span>
                                                </div>

                                                <div className="flex items-center gap-1.5">
                                                    <span className="text-red-600 text-sm">👎</span>
                                                    <span className="text-xl font-bold text-white">
                                                        {agent.thumbs_down}
                                                    </span>
                                                </div>

                                                {rate !== null && (
                                                    <span
                                                        className={`ml-auto text-sm font-bold ${
                                                            rate >= 70
                                                                ? 'text-emerald-300'
                                                                : rate >= 40
                                                                    ? 'text-yellow-300'
                                                                    : 'text-red-300'
                                                        }`}
                                                    >
                                                        {rate}%
                                                    </span>
                                                )}
                                            </div>

                                            <p className="text-white/35 text-xs mt-3">
                                                {agent.total} total ratings
                                            </p>
                                        </motion.div>
                                    );
                                })
                            )}

                            {!loading && (!stats?.per_agent || stats.per_agent.length === 0) && (
                                <div className="col-span-full text-center py-10">
                                    <p className="text-white/45 text-sm font-semibold">
                                        No feedback data yet
                                    </p>
                                    <p className="text-white/30 text-xs mt-1">
                                        Feedback will appear here once users start rating responses.
                                    </p>
                                </div>
                            )}
                        </div>
                    </div>
                </motion.div>

                {/* Recent Feedback */}
                <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.3 }}
                >
                    <div className="mb-4">
                        <h2 className="text-white text-xl font-bold">Recent Feedback</h2>
                        <p className="text-white/60 text-sm mt-1">
                            Latest response ratings submitted by users.
                        </p>
                    </div>

                    <div className={tableCardClass}>
                        <div className={tableHeaderClass}>
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
                                <p className="text-white/45 text-sm font-semibold">
                                    No feedback entries yet
                                </p>
                                <p className="text-white/30 text-xs mt-1">
                                    Recent feedback will appear here after users rate AI responses.
                                </p>
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
                                    <div
                                        className={tableRowClass}
                                        onClick={() => setExpandedId(isExpanded ? null : entry.id)}
                                    >
                                        <div className="col-span-2">
                                            <span className={`text-xs font-bold px-2.5 py-1 rounded-full border ${colors.badge}`}>
                                                {title}
                                            </span>
                                        </div>

                                        <div className="col-span-1 text-center">
                                            {entry.rating === 'up' ? (
                                                <span className="text-emerald-600 text-lg">👍</span>
                                            ) : (
                                                <span className="text-red-600 text-lg">👎</span>
                                            )}
                                        </div>
                                        <div className="col-span-2 truncate">
                                            <span className="block text-white/70 text-sm truncate">
                                                {entry.user_name || entry.user_id}
                                            </span>
                                            {entry.user_name && (
                                                <span className="block text-white/30 text-xs truncate">
                                                    {entry.user_id}
                                                </span>
                                            )}
                                        </div>

                                        <div className="col-span-4 text-white/50 text-sm truncate">
                                            {preview}
                                        </div>

                                        <div className="col-span-1 text-center text-white/45 text-sm">
                                            {entry.message_index}
                                        </div>

                                        <div className="col-span-2 text-right text-white/35 text-xs flex items-center justify-end gap-2">
                                            {time}
                                            <svg
                                                xmlns="http://www.w3.org/2000/svg"
                                                fill="none"
                                                viewBox="0 0 24 24"
                                                strokeWidth={2}
                                                stroke="currentColor"
                                                className={`w-3.5 h-3.5 text-white/30 transition-transform ${
                                                    isExpanded ? 'rotate-180' : ''
                                                }`}
                                            >
                                                <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                                            </svg>
                                        </div>
                                    </div>

                                    {isExpanded && entry.message_content && (
                                        <motion.div
                                            initial={{ opacity: 0, height: 0 }}
                                            animate={{ opacity: 1, height: 'auto' }}
                                            exit={{ opacity: 0, height: 0 }}
                                            className="px-6 py-5 bg-white/[0.025] border-b border-white/[0.08]"
                                        >
                                            {entry.user_question && (
                                                <div className="mb-4">
                                                    <span className="text-xs font-bold text-cyan-300 uppercase tracking-wider">
                                                        User Question
                                                    </span>
                                                    <p className="text-white/55 text-sm mt-1 leading-relaxed whitespace-pre-wrap">
                                                        {entry.user_question}
                                                    </p>
                                                </div>
                                            )}

                                            <div>
                                                <span
                                                    className={`text-xs font-bold uppercase tracking-wider ${
                                                        entry.rating === 'up'
                                                            ? 'text-emerald-300'
                                                            : 'text-red-300'
                                                    }`}
                                                >
                                                    AI Response {entry.rating === 'up' ? '(Helpful)' : '(Not Helpful)'}
                                                </span>
                                                <p className="text-white/60 text-sm mt-1 leading-relaxed whitespace-pre-wrap max-h-60 overflow-y-auto">
                                                    {entry.message_content}
                                                </p>
                                            </div>

                                            <div className="mt-4 text-white/25 text-xs font-mono">
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
        </AdminLayout>
    );
};

export default FeedbackPanel;
