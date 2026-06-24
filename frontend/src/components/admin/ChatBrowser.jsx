import React, { useState, useEffect, useCallback, useRef } from 'react';
import { AGENTS } from '../../config/agents';
import { motion, AnimatePresence } from 'framer-motion';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import AdminLayout from './AdminLayout';

const API_BASE = `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/v1/admin/dashboard`;

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
    const [sessionMeta, setSessionMeta] = useState(null);

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
            fetch(`${API_BASE.replace('/admin/dashboard', '')}/feedback/${agent}/${session.session_id}`)
                .then(res => res.ok ? res.json() : { feedback: {} })
                .catch(() => ({ feedback: {} })),
        ])
            .then(([msgData, fbData]) => {
                setMessages(msgData.messages || []);
                setSessionMeta(msgData);
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

                                <div className="mt-2">
                                    <p className="text-cyan-300/90 text-sm font-semibold truncate max-w-md">
                                        {sessionMeta?.user_name && sessionMeta.user_name !== sessionMeta?.user_id
                                            ? sessionMeta.user_name
                                            : session?.user_name && session.user_name !== session?.user_id
                                                ? session.user_name
                                                : 'Name not saved'}
                                    </p>

                                    <p className="text-white/35 text-xs truncate max-w-md">
                                        {sessionMeta?.user_id || session?.user_id || 'Email not available'}
                                    </p>
                                </div>
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

    const fieldClass =
        'bg-white/[0.04] border border-white/[0.10] text-white rounded-2xl px-4 py-3 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-cyan-500/40 focus:border-cyan-400/50 transition-all placeholder:text-white/25';

    const statCardClass =
        'rounded-[24px] border border-white/[0.08] bg-white/[0.045] p-5 shadow-xl shadow-black/10 backdrop-blur-sm';

    const tableCardClass =
        'rounded-[28px] border border-white/[0.08] bg-white/[0.035] overflow-hidden shadow-2xl shadow-black/15 backdrop-blur-sm';

    const tableHeaderClass =
        'grid grid-cols-12 gap-4 px-6 py-4 border-b border-white/[0.08] bg-white/[0.035] text-white/45 text-xs uppercase tracking-wider font-bold';

    const tableRowClass =
        'grid grid-cols-12 gap-4 px-6 py-4 border-b border-white/[0.05] hover:bg-white/[0.045] cursor-pointer transition-colors group';

    return (
        <AdminLayout
            title="Chat Sessions"
            subtitle="Browse and review past conversations across all agents."
            backTo="/admin"
            backLabel="Back to Dashboard"
            backgroundVariant="legacy-dark"
        >
            <div className="relative z-10 max-w-7xl mx-auto">
                {/* Toolbar */}
                <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.05 }}
                    className="mb-6 rounded-[28px] border border-white/[0.08] bg-white/[0.035] p-4 shadow-xl shadow-black/10 backdrop-blur-sm"
                >
                    <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
                        <div>
                            <h2 className="text-white text-lg font-bold">
                                Conversation Browser
                            </h2>
                            <p className="text-white/40 text-sm">
                                Search sessions and filter conversations by agent.
                            </p>
                        </div>

                        <div className="flex flex-col sm:flex-row gap-3">
                            <div className="relative">
                                <svg
                                    xmlns="http://www.w3.org/2000/svg"
                                    fill="none"
                                    viewBox="0 0 24 24"
                                    strokeWidth={2}
                                    stroke="currentColor"
                                    className="w-4 h-4 text-white/30 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none"
                                >
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
                                </svg>

                                <input
                                    id="session-search"
                                    type="text"
                                    value={searchInput}
                                    onChange={(e) => handleSearchChange(e.target.value)}
                                    placeholder="Search conversations..."
                                    className={`${fieldClass} pl-9 w-full sm:w-72`}
                                />

                                {searchInput && (
                                    <button
                                        type="button"
                                        onClick={() => {
                                            setSearchInput('');
                                            setDebouncedSearch('');
                                            setSkip(0);
                                        }}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-white/35 hover:text-white/70 transition-colors"
                                    >
                                        ✕
                                    </button>
                                )}
                            </div>

                            <div className="relative">
                                <select
                                    id="agent-select"
                                    value={selectedAgent}
                                    onChange={(e) => handleAgentChange(e.target.value)}
                                    className={`${fieldClass} appearance-none pr-10 w-full sm:w-56 cursor-pointer`}
                                >
                                    {AGENT_LIST.map(agent => (
                                        <option
                                            key={agent.id}
                                            value={agent.id}
                                            className="bg-slate-900 text-white"
                                        >
                                            {agent.title}
                                        </option>
                                    ))}
                                </select>

                                <svg
                                    xmlns="http://www.w3.org/2000/svg"
                                    fill="none"
                                    viewBox="0 0 24 24"
                                    strokeWidth={2}
                                    stroke="currentColor"
                                    className="w-4 h-4 text-white/35 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none"
                                >
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                                </svg>
                            </div>
                        </div>
                    </div>
                </motion.div>

                {/* Stats Bar */}
                <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.1 }}
                    className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6"
                >
                    <div className={statCardClass}>
                        <p className="text-white/45 text-xs uppercase tracking-wider font-bold">
                            {debouncedSearch ? 'Matching Sessions' : 'Total Sessions'}
                        </p>
                        <p className="text-3xl font-bold text-white mt-2">
                            {total}
                            {debouncedSearch && (
                                <span className="text-sm font-medium text-cyan-300 ml-2">
                                    for "{debouncedSearch}"
                                </span>
                            )}
                        </p>
                    </div>

                    <div className={statCardClass}>
                        <p className="text-white/45 text-xs uppercase tracking-wider font-bold">
                            Current Agent
                        </p>
                        <p className="text-3xl font-bold text-cyan-300 mt-2">
                            {agentMeta?.title || selectedAgent}
                        </p>
                    </div>

                    <div className={statCardClass}>
                        <p className="text-white/45 text-xs uppercase tracking-wider font-bold">
                            Page
                        </p>
                        <p className="text-3xl font-bold text-white mt-2">
                            {currentPage}
                            <span className="text-white/30 text-base font-medium">
                                {' '} / {totalPages || 1}
                            </span>
                        </p>
                    </div>
                </motion.div>

                {/* Sessions Table */}
                <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.2 }}
                    className={tableCardClass}
                >
                    <div className={tableHeaderClass}>
                        <div className="col-span-1">#</div>
                        <div className="col-span-2">User</div>
                        <div className="col-span-3">Session ID</div>
                        <div className="col-span-2 text-center">Messages</div>
                        <div className="col-span-4">Preview</div>
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

                    {!loading && error && (
                        <div className="px-6 py-8 text-center">
                            <div className="inline-flex items-center gap-2 bg-red-500/10 border border-red-500/25 rounded-xl px-4 py-3 text-red-300 text-sm font-medium">
                                {error}
                            </div>
                        </div>
                    )}

                    {!loading && !error && sessions.length === 0 && (
                        <div className="px-6 py-16 text-center">
                            <p className="text-white/45 text-sm font-semibold">
                                No chat sessions found for this agent
                            </p>
                            <p className="text-white/25 text-xs mt-1">
                                Sessions will appear here once users start chatting.
                            </p>
                        </div>
                    )}

                    {!loading && !error && sessions.map((session, i) => (
                        <motion.div
                            key={session.session_id}
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            transition={{ delay: i * 0.03 }}
                            onClick={() => setSelectedSession(session)}
                            className={tableRowClass}
                        >
                            <div className="col-span-1 text-white/30 text-sm font-mono">
                                {skip + i + 1}
                            </div>

                            <div className="col-span-2 min-w-0">
                                <p className="text-white/75 text-sm font-semibold truncate">
                                    {session.user_name && session.user_name !== session.user_id
                                        ? session.user_name
                                        : 'Name not saved'}
                                </p>

                                <p className="text-white/30 text-[11px] truncate">
                                    {session.user_id || 'Email not available'}
                                </p>
                            </div>

                            <div className="col-span-3 text-white/70 text-sm font-mono truncate group-hover:text-cyan-300 transition-colors">
                                {session.session_id.substring(0, 20)}...
                            </div>

                            <div className="col-span-2 text-center">
                                <span className="inline-flex items-center gap-1.5 bg-white/[0.07] text-white/65 border border-white/[0.08] px-2.5 py-1 rounded-full text-xs font-bold">
                                    <svg
                                        xmlns="http://www.w3.org/2000/svg"
                                        fill="none"
                                        viewBox="0 0 24 24"
                                        strokeWidth={2}
                                        stroke="currentColor"
                                        className="w-3 h-3"
                                    >
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.129.166 2.27.293 3.423.379.35.026.67.21.865.501L12 21l2.755-4.133a1.14 1.14 0 01.865-.501 48.172 48.172 0 003.423-.379c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0012 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018z" />
                                    </svg>
                                    {session.message_count}
                                </span>
                            </div>

                            <div className="col-span-4 text-white/50 text-sm truncate">
                                {session.preview_text}
                            </div>
                        </motion.div>
                    ))}

                    {!loading && sessions.length > 0 && (
                        <div className="flex items-center justify-between px-6 py-4 bg-white/[0.025] border-t border-white/[0.08]">
                            <button
                                type="button"
                                onClick={() => setSkip(Math.max(0, skip - limit))}
                                disabled={skip === 0}
                                className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold text-white/55 hover:text-cyan-300 hover:bg-white/[0.06] border border-white/[0.10] transition-all disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:text-white/55"
                            >
                                Previous
                            </button>

                            <span className="text-white/35 text-sm font-medium">
                                Page {currentPage} of {totalPages || 1}
                            </span>

                            <button
                                type="button"
                                onClick={() => setSkip(skip + limit)}
                                disabled={skip + limit >= total}
                                className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold text-white/55 hover:text-cyan-300 hover:bg-white/[0.06] border border-white/[0.10] transition-all disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:text-white/55"
                            >
                                Next
                            </button>
                        </div>
                    )}
                </motion.div>

                <SessionDetail
                    session={selectedSession}
                    agent={selectedAgent}
                    onClose={() => setSelectedSession(null)}
                />
            </div>
        </AdminLayout>
    );
};

export default ChatBrowser;
