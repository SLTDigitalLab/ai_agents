import React, { useState, useEffect, useCallback, useRef } from 'react';
import { AGENTS } from '../../config/agents';
import { motion, AnimatePresence } from 'framer-motion';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const API_BASE = 'http://localhost:8000/api/v1/admin/dashboard';

// Build a flat list of agents from the config
const AGENT_LIST = Object.entries(AGENTS).map(([key, cfg]) => ({
    routeKey: key,
    id: cfg.id,
    title: cfg.title,
    color: cfg.color,
}));

// ── Slide-over Detail Panel ─────────────────────────────────────────────
const SessionDetail = ({ session, agent, onClose }) => {
    const [messages, setMessages] = useState([]);
    const [feedback, setFeedback] = useState({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    useEffect(() => {
        if (!session) return;
        setLoading(true);
        setError(null);
        setFeedback({});

        Promise.all([
            fetch(`${API_BASE}/sessions/${agent}/${session.session_id}`)
                .then(res => {
                    if (!res.ok) throw new Error(`HTTP ${res.status}`);
                    return res.json();
                }),
            fetch(`http://localhost:8000/api/v1/feedback/${agent}/${session.session_id}`)
                .then(res => res.ok ? res.json() : { feedback: {} })
                .catch(() => ({ feedback: {} })),
        ])
            .then(([msgData, fbData]) => {
                setMessages(msgData.messages || []);
                // Flatten feedback: { index: "up" | "down" } (take the first user's rating)
                const fbMap = {};
                for (const [idx, users] of Object.entries(fbData.feedback || {})) {
                    const ratings = Object.values(users);
                    if (ratings.length > 0) fbMap[idx] = ratings[0];
                }
                setFeedback(fbMap);
                setLoading(false);
            })
            .catch(err => {
                setError(err.message);
                setLoading(false);
            });
    }, [session, agent]);

    return (
        <AnimatePresence>
            {session && (
                <>
                    {/* Backdrop */}
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        onClick={onClose}
                        className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40"
                    />

                    {/* Slide-over panel */}
                    <motion.div
                        initial={{ x: '100%' }}
                        animate={{ x: 0 }}
                        exit={{ x: '100%' }}
                        transition={{ type: 'spring', damping: 30, stiffness: 300 }}
                        className="fixed right-0 top-0 h-full w-full max-w-2xl bg-slate-900 border-l border-white/10 shadow-2xl z-50 flex flex-col"
                    >
                        {/* Header */}
                        <div className="flex items-center justify-between px-6 py-4 border-b border-white/10 bg-slate-900/80 backdrop-blur">
                            <div>
                                <h3 className="text-white font-semibold text-lg">Session Detail</h3>
                                <p className="text-white/40 text-xs font-mono mt-0.5 truncate max-w-md">
                                    {session.session_id}
                                </p>
                            </div>
                            <button
                                onClick={onClose}
                                className="p-2 rounded-lg text-white/50 hover:text-white hover:bg-white/10 transition-colors"
                            >
                                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-5 h-5">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                                </svg>
                            </button>
                        </div>

                        {/* Messages */}
                        <div className="flex-1 overflow-y-auto p-6 space-y-4">
                            {loading && (
                                <div className="flex items-center justify-center py-20">
                                    <div className="flex items-center gap-3">
                                        <div className="w-2 h-2 rounded-full bg-cyan-400 animate-bounce" />
                                        <div className="w-2 h-2 rounded-full bg-cyan-400 animate-bounce [animation-delay:150ms]" />
                                        <div className="w-2 h-2 rounded-full bg-cyan-400 animate-bounce [animation-delay:300ms]" />
                                    </div>
                                </div>
                            )}

                            {error && (
                                <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 text-red-400 text-sm">
                                    Failed to load session: {error}
                                </div>
                            )}

                            {!loading && !error && messages.length === 0 && (
                                <p className="text-white/30 text-center py-10">No messages in this session.</p>
                            )}

                            {!loading && messages.map((msg, i) => (
                                <motion.div
                                    key={i}
                                    initial={{ opacity: 0, y: 8 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    transition={{ delay: i * 0.03 }}
                                    className={`flex ${msg.type === 'human' ? 'justify-end' : 'justify-start'}`}
                                >
                                    <div className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${msg.type === 'human'
                                        ? 'bg-cyan-600/20 border border-cyan-500/20 text-cyan-100 rounded-tr-md'
                                        : 'bg-white/5 border border-white/10 text-white/80 rounded-tl-md'
                                        }`}>
                                        {/* Role label */}
                                        <p className={`text-[10px] font-semibold uppercase tracking-wider mb-1.5 ${msg.type === 'human' ? 'text-cyan-400/70' : 'text-purple-400/70'
                                            }`}>
                                            {msg.type === 'human' ? '👤 User' : '🤖 AI'}
                                        </p>
                                        <div className="prose prose-sm prose-invert max-w-none">
                                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                                {msg.content}
                                            </ReactMarkdown>
                                        </div>
                                        {/* Feedback indicator for AI messages */}
                                        {msg.type === 'ai' && feedback[i] && (
                                            <div className={`flex items-center gap-1 mt-2 text-xs ${feedback[i] === 'up' ? 'text-emerald-400/70' : 'text-red-400/70'}`}>
                                                {feedback[i] === 'up' ? (
                                                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-3 h-3">
                                                        <path d="M1 8.998a1 1 0 011-1h.764a1.483 1.483 0 00-.076.506v5.996a1.483 1.483 0 00.076.506H2a1 1 0 01-1-1V8.998zM5.25 7.726a2 2 0 01.944-1.697l3.476-2.14a1.5 1.5 0 012.33 1.25v2.363h2.5a2 2 0 011.96 2.4l-.782 3.908A2 2 0 0113.72 15.5H5.25V7.726z" />
                                                    </svg>
                                                ) : (
                                                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-3 h-3">
                                                        <path d="M19 11.002a1 1 0 01-1 1h-.764a1.483 1.483 0 00.076-.506V5.5a1.483 1.483 0 00-.076-.506H18a1 1 0 011 1v5.008zM14.75 12.274a2 2 0 01-.944 1.697l-3.476 2.14a1.5 1.5 0 01-2.33-1.25V12.5h-2.5a2 2 0 01-1.96-2.4l.782-3.908A2 2 0 016.28 4.5h8.47v7.774z" />
                                                    </svg>
                                                )}
                                                <span>{feedback[i] === 'up' ? 'Helpful' : 'Not helpful'}</span>
                                            </div>
                                        )}
                                    </div>
                                </motion.div>
                            ))}
                        </div>

                        {/* Footer */}
                        <div className="px-6 py-3 border-t border-white/10 bg-slate-900/80">
                            <p className="text-white/30 text-xs text-center">
                                {messages.length} message{messages.length !== 1 ? 's' : ''} in this conversation
                            </p>
                        </div>
                    </motion.div>
                </>
            )}
        </AnimatePresence>
    );
};


// ── Main ChatBrowser Component ──────────────────────────────────────────
const ChatBrowser = () => {
    const [selectedAgent, setSelectedAgent] = useState(AGENT_LIST[0]?.id || '');
    const [sessions, setSessions] = useState([]);
    const [total, setTotal] = useState(0);
    const [skip, setSkip] = useState(0);
    const [limit] = useState(20);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [selectedSession, setSelectedSession] = useState(null);

    // Search state with debounce
    const [searchInput, setSearchInput] = useState('');
    const [debouncedSearch, setDebouncedSearch] = useState('');
    const debounceRef = useRef(null);

    // Debounce search input (500ms delay)
    const handleSearchChange = (value) => {
        setSearchInput(value);
        if (debounceRef.current) clearTimeout(debounceRef.current);
        debounceRef.current = setTimeout(() => {
            setDebouncedSearch(value.trim());
            setSkip(0); // Reset pagination on new search
        }, 500);
    };

    // Cleanup debounce timer on unmount
    useEffect(() => {
        return () => {
            if (debounceRef.current) clearTimeout(debounceRef.current);
        };
    }, []);

    // Fetch sessions whenever agent, pagination, or search changes
    const fetchSessions = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const searchParam = debouncedSearch ? `&search=${encodeURIComponent(debouncedSearch)}` : '';
            const res = await fetch(
                `${API_BASE}/sessions?agent=${selectedAgent}&skip=${skip}&limit=${limit}${searchParam}`
            );
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            setSessions(data.sessions || []);
            setTotal(data.total || 0);
        } catch (err) {
            setError(err.message);
            setSessions([]);
        } finally {
            setLoading(false);
        }
    }, [selectedAgent, skip, limit, debouncedSearch]);

    useEffect(() => {
        fetchSessions();
    }, [fetchSessions]);

    // Agent change resets pagination and search
    const handleAgentChange = (agentId) => {
        setSelectedAgent(agentId);
        setSkip(0);
        setSearchInput('');
        setDebouncedSearch('');
        setSelectedSession(null);
    };

    const currentPage = Math.floor(skip / limit) + 1;
    const totalPages = Math.ceil(total / limit);

    const agentMeta = AGENT_LIST.find(a => a.id === selectedAgent);

    return (
        <div className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 relative overflow-hidden">

            {/* Ambient bg effects */}
            <div className="absolute inset-0 pointer-events-none">
                <div className="absolute top-[-15%] left-[-10%] w-[500px] h-[500px] rounded-full bg-cyan-500/[0.04] blur-3xl" />
                <div className="absolute bottom-[-10%] right-[-5%] w-[600px] h-[600px] rounded-full bg-purple-500/[0.04] blur-3xl" />
            </div>

            <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">

                {/* ── Header ────────────────────────────────────── */}
                <motion.div
                    initial={{ opacity: 0, y: -20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.5 }}
                    className="mb-8"
                >
                    {/* Back link */}
                    <a href="/admin" className="inline-flex items-center gap-2 text-white/40 hover:text-white/70 text-sm mb-6 transition-colors group">
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4 group-hover:-translate-x-0.5 transition-transform">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
                        </svg>
                        Back to Dashboard
                    </a>

                    <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                        <div>
                            <h1 className="text-3xl font-bold text-white tracking-tight">
                                Chat Sessions
                            </h1>
                            <p className="text-white/40 text-sm mt-1">
                                Browse and review past conversations across all agents
                            </p>
                        </div>

                        <div className="flex items-center gap-3">
                            {/* Search input */}
                            <div className="relative">
                                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4 text-white/30 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
                                </svg>
                                <input
                                    id="session-search"
                                    type="text"
                                    value={searchInput}
                                    onChange={(e) => handleSearchChange(e.target.value)}
                                    placeholder="Search conversations..."
                                    className="bg-white/5 border border-white/10 text-white rounded-xl pl-9 pr-4 py-2.5 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-cyan-500/40 focus:border-cyan-500/40 transition-all placeholder:text-white/25 w-48 sm:w-64 hover:bg-white/10"
                                />
                                {searchInput && (
                                    <button
                                        onClick={() => { setSearchInput(''); setDebouncedSearch(''); setSkip(0); }}
                                        className="absolute right-2.5 top-1/2 -translate-y-1/2 p-0.5 rounded text-white/30 hover:text-white/60 transition-colors"
                                    >
                                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-3.5 h-3.5">
                                            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                                        </svg>
                                    </button>
                                )}
                            </div>

                            {/* Agent dropdown */}
                            <div className="relative">
                                <select
                                    id="agent-select"
                                    value={selectedAgent}
                                    onChange={(e) => handleAgentChange(e.target.value)}
                                    className="appearance-none bg-white/5 border border-white/10 text-white rounded-xl px-4 py-2.5 pr-10 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-cyan-500/40 focus:border-cyan-500/40 transition-all cursor-pointer hover:bg-white/10"
                                >
                                    {AGENT_LIST.map(agent => (
                                        <option key={agent.id} value={agent.id} className="bg-slate-800 text-white">
                                            {agent.title}
                                        </option>
                                    ))}
                                </select>
                                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4 text-white/40 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                                </svg>
                            </div>
                        </div>
                    </div>
                </motion.div>

                {/* ── Stats Bar ──────────────────────────────────── */}
                <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.1 }}
                    className="grid grid-cols-2 sm:grid-cols-3 gap-4 mb-6"
                >
                    <div className="bg-white/[0.03] border border-white/[0.06] rounded-2xl p-4">
                        <p className="text-white/40 text-xs uppercase tracking-wider font-medium">{debouncedSearch ? 'Matching Sessions' : 'Total Sessions'}</p>
                        <p className="text-2xl font-bold text-white mt-1">
                            {total}
                            {debouncedSearch && <span className="text-sm font-normal text-cyan-400/60 ml-2">for "{debouncedSearch}"</span>}
                        </p>
                    </div>
                    <div className="bg-white/[0.03] border border-white/[0.06] rounded-2xl p-4">
                        <p className="text-white/40 text-xs uppercase tracking-wider font-medium">Current Agent</p>
                        <p className="text-2xl font-bold text-cyan-400 mt-1">{agentMeta?.title || selectedAgent}</p>
                    </div>
                    <div className="bg-white/[0.03] border border-white/[0.06] rounded-2xl p-4 hidden sm:block">
                        <p className="text-white/40 text-xs uppercase tracking-wider font-medium">Page</p>
                        <p className="text-2xl font-bold text-white mt-1">{currentPage} <span className="text-white/30 text-base font-normal">/ {totalPages || 1}</span></p>
                    </div>
                </motion.div>

                {/* ── Sessions Table ─────────────────────────────── */}
                <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.2 }}
                    className="bg-white/[0.02] border border-white/[0.06] rounded-2xl overflow-hidden"
                >
                    {/* Table Header */}
                    <div className="grid grid-cols-12 gap-4 px-6 py-3 border-b border-white/[0.06] bg-white/[0.02] text-white/40 text-xs uppercase tracking-wider font-semibold">
                        <div className="col-span-1">#</div>
                        <div className="col-span-4">Session ID</div>
                        <div className="col-span-2 text-center">Messages</div>
                        <div className="col-span-5">Preview</div>
                    </div>

                    {/* Loading */}
                    {loading && (
                        <div className="flex items-center justify-center py-20">
                            <div className="flex items-center gap-3">
                                <div className="w-2 h-2 rounded-full bg-cyan-400 animate-bounce" />
                                <div className="w-2 h-2 rounded-full bg-cyan-400 animate-bounce [animation-delay:150ms]" />
                                <div className="w-2 h-2 rounded-full bg-cyan-400 animate-bounce [animation-delay:300ms]" />
                            </div>
                        </div>
                    )}

                    {/* Error */}
                    {!loading && error && (
                        <div className="px-6 py-8 text-center">
                            <div className="inline-flex items-center gap-2 bg-red-500/10 border border-red-500/20 rounded-xl px-4 py-3 text-red-400 text-sm">
                                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
                                </svg>
                                {error}
                            </div>
                        </div>
                    )}

                    {/* Empty State */}
                    {!loading && !error && sessions.length === 0 && (
                        <div className="px-6 py-16 text-center">
                            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1} stroke="currentColor" className="w-12 h-12 text-white/10 mx-auto mb-4">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M20.25 8.511c.884.284 1.5 1.128 1.5 2.097v4.286c0 1.136-.847 2.1-1.98 2.193-.34.027-.68.052-1.02.072v3.091l-3-3c-1.354 0-2.694-.055-4.02-.163a2.115 2.115 0 01-.825-.242m9.345-8.334a2.126 2.126 0 00-.476-.095 48.64 48.64 0 00-8.048 0c-1.131.094-1.976 1.057-1.976 2.192v4.286c0 .837.46 1.58 1.155 1.951m9.345-8.334V6.637c0-1.621-1.152-3.026-2.76-3.235A48.455 48.455 0 0011.25 3c-2.115 0-4.198.137-6.24.402-1.608.209-2.76 1.614-2.76 3.235v6.226c0 1.621 1.152 3.026 2.76 3.235.577.075 1.157.14 1.74.194V21l4.155-4.155" />
                            </svg>
                            <p className="text-white/30 text-sm">No chat sessions found for this agent</p>
                            <p className="text-white/15 text-xs mt-1">Sessions will appear here once users start chatting</p>
                        </div>
                    )}

                    {/* Session Rows */}
                    {!loading && !error && sessions.map((session, i) => (
                        <motion.div
                            key={session.session_id}
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            transition={{ delay: i * 0.03 }}
                            onClick={() => setSelectedSession(session)}
                            className="grid grid-cols-12 gap-4 px-6 py-4 border-b border-white/[0.04] hover:bg-white/[0.04] cursor-pointer transition-colors group"
                        >
                            <div className="col-span-1 text-white/30 text-sm font-mono">
                                {skip + i + 1}
                            </div>
                            <div className="col-span-4 text-white/70 text-sm font-mono truncate group-hover:text-cyan-400 transition-colors">
                                {session.session_id.substring(0, 20)}...
                            </div>
                            <div className="col-span-2 text-center">
                                <span className="inline-flex items-center gap-1.5 bg-white/[0.06] text-white/60 px-2.5 py-1 rounded-full text-xs font-medium">
                                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-3 h-3">
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.129.166 2.27.293 3.423.379.35.026.67.21.865.501L12 21l2.755-4.133a1.14 1.14 0 01.865-.501 48.172 48.172 0 003.423-.379c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0012 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018z" />
                                    </svg>
                                    {session.message_count}
                                </span>
                            </div>
                            <div className="col-span-5 text-white/50 text-sm truncate">
                                {session.preview_text}
                            </div>
                        </motion.div>
                    ))}

                    {/* Pagination Footer */}
                    {!loading && sessions.length > 0 && (
                        <div className="flex items-center justify-between px-6 py-4 bg-white/[0.02] border-t border-white/[0.06]">
                            <button
                                onClick={() => setSkip(Math.max(0, skip - limit))}
                                disabled={skip === 0}
                                className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium text-white/60 hover:text-white hover:bg-white/10 border border-white/10 transition-all disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:text-white/60"
                            >
                                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
                                </svg>
                                Previous
                            </button>

                            <span className="text-white/30 text-sm">
                                Page {currentPage} of {totalPages || 1}
                            </span>

                            <button
                                onClick={() => setSkip(skip + limit)}
                                disabled={skip + limit >= total}
                                className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium text-white/60 hover:text-white hover:bg-white/10 border border-white/10 transition-all disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:text-white/60"
                            >
                                Next
                                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                                </svg>
                            </button>
                        </div>
                    )}
                </motion.div>
            </div>

            {/* ── Slide-over Detail Panel ─────────────────────── */}
            <SessionDetail
                session={selectedSession}
                agent={selectedAgent}
                onClose={() => setSelectedSession(null)}
            />
        </div>
    );
};

export default ChatBrowser;
