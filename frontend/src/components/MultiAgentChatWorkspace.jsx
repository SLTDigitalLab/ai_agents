import React, { useState, useRef, useEffect } from 'react';
import { useMsal } from "@azure/msal-react";
import { motion } from 'framer-motion';
import { v4 as uuidv4 } from 'uuid';
import LifestoreForm from './forms/LifestoreForm';
import EnterpriseForm from './forms/EnterpriseForm';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const FORM_TOKENS = {
    '[RENDER_LIFESTORE_FORM]': 'lifestore',
    '[RENDER_ENTERPRISE_FORM]': 'enterprise',
};

const sanitizeMarkdownBold = (text) => {
    if (!text) return text;
    const positions = [];
    const regex = /\*\*/g;
    let m;
    while ((m = regex.exec(text)) !== null) positions.push(m.index);
    if (positions.length % 2 === 0) return text;
    const last = positions[positions.length - 1];
    return text.slice(0, last) + text.slice(last + 2);
};

const appendChunkSmartly = (current, incoming) => {
    return (current || "") + (incoming || "");
};

// ── Source UI Components ──────────────────────────────────────
const SourceBadge = ({ name, url, color }) => (
    <motion.a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        whileHover={{ scale: 1.05, y: -2 }}
        whileTap={{ scale: 0.95 }}
        className={`flex items-center gap-2 px-3 py-1.5 rounded-full bg-white border border-gray-100 shadow-sm transition-all hover:shadow-md hover:border-gray-200 group`}
    >
        <div className={`p-1 rounded-full bg-gradient-to-br ${color} text-white`}>
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-3 h-3">
                <path d="M3 3.5A1.5 1.5 0 014.5 2h6.879a1.5 1.5 0 011.06.44l4.122 4.12A1.5 1.5 0 0117 7.622V16.5a1.5 1.5 0 01-1.5 1.5h-11A1.5 1.5 0 013 16.5v-13z" />
            </svg>
        </div>
        <span className="text-xs font-medium text-gray-600 group-hover:text-gray-900 truncate max-w-[150px]">
            {name}
        </span>
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-3 h-3 text-gray-300 group-hover:text-gray-500">
            <path fillRule="evenodd" d="M5.22 14.78a.75.75 0 001.06 0l7.22-7.22v5.69a.75.75 0 001.5 0v-7.5a.75.75 0 00-.75-.75h-7.5a.75.75 0 000 1.5h5.69l-7.22 7.22a.75.75 0 000 1.06z" clipRule="evenodd" />
        </svg>
    </motion.a>
);

const SourcesSection = ({ sources, color }) => {
    if (!sources || sources.length === 0) return null;

    return (
        <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-4 pt-3 border-t border-gray-100/60"
        >
            <div className="flex items-center gap-2 mb-2.5">
                <div className={`w-1 h-3.5 rounded-full bg-gradient-to-b ${color}`} />
                <span className="text-[0.7rem] uppercase tracking-wider font-bold text-gray-400">Sources</span>
            </div>
            <div className="flex flex-wrap gap-2">
                {sources.map((src, i) => (
                    <SourceBadge key={i} name={src.name} url={src.url} color={color} />
                ))}
            </div>
        </motion.div>
    );
};

// ── Feedback Buttons Component ──────────────────────────────────────
const FeedbackButtons = ({ messageIndex, agentId, threadId, userId, existingRating, onFeedback }) => {
    const [rating, setRating] = useState(existingRating || null);
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => {
        setRating(existingRating || null);
    }, [existingRating]);

    const handleFeedback = async (newRating) => {
        if (submitting) return;
        const finalRating = rating === newRating ? null : newRating;
        setSubmitting(true);
        try {
            if (!finalRating) {
                const res = await fetch(`${API_URL}/api/v1/feedback`, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        agent_id: agentId,
                        thread_id: threadId,
                        message_index: messageIndex,
                        rating: newRating,
                        user_id: userId,
                    }),
                });
                if (res.ok) {
                    setRating(null);
                    onFeedback?.(messageIndex, null);
                }
            } else {
                const res = await fetch(`${API_URL}/api/v1/feedback`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        agent_id: agentId,
                        thread_id: threadId,
                        message_index: messageIndex,
                        rating: finalRating,
                        user_id: userId,
                    }),
                });
                if (res.ok) {
                    setRating(finalRating);
                    onFeedback?.(messageIndex, finalRating);
                }
            }
        } catch (err) {
            console.error('Feedback submission failed:', err);
        } finally{
            setSubmitting(false);
        }
    };

    return (
        <div className="flex items-center gap-2 mt-2 -mb-1">
            <button
                onClick={() => handleFeedback('up')}
                disabled={submitting}
                className={`p-1.5 rounded-md transition-all duration-200 ${
                    rating === 'up' ? 'text-emerald-500 bg-emerald-50' : 'text-gray-300 hover:text-emerald-400 hover:bg-emerald-50/50'
                }`}
                title="Helpful"
            >
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                    <path d="M1 8.998a1 1 0 011-1h.764a1.483 1.483 0 00-.076.506v5.996a1.483 1.483 0 00.076.506H2a1 1 0 01-1-1V8.998zM5.25 7.726a2 2 0 01.944-1.697l3.476-2.14a1.5 1.5 0 012.33 1.25v2.363h2.5a2 2 0 011.96 2.4l-.782 3.908A2 2 0 0113.72 15.5H5.25V7.726z" />
                </svg>
            </button>
            <button
                onClick={() => handleFeedback('down')}
                disabled={submitting}
                className={`p-1.5 rounded-md transition-all duration-200 ${
                    rating === 'down' ? 'text-red-400 bg-red-50' : 'text-gray-300 hover:text-red-400 hover:bg-red-50/50'
                }`}
                title="Not helpful"
            >
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                    <path d="M19 11.002a1 1 0 01-1 1h-.764a1.483 1.483 0 00.076-.506V5.5a1.483 1.483 0 00-.076-.506H18a1 1 0 011 1v5.008zM14.75 12.274a2 2 0 01-.944 1.697l-3.476 2.14a1.5 1.5 0 01-2.33-1.25V12.5h-2.5a2 2 0 01-1.96-2.4l.782-3.908A2 2 0 016.28 4.5h8.47v7.774z" />
                </svg>
            </button>
        </div>
    );
};

// ── NEW COMPONENT: MultiAgentChatWorkspace ─────────────────────────
const MultiAgentChatWorkspace = ({ agentConfig }) => {
    const { accounts } = useMsal();
    const user = accounts[0] || { name: "User" };

    const [threadId, setThreadId] = useState('');
    const [messages, setMessages] = useState([]);
    const [isLoadingHistory, setIsLoadingHistory] = useState(false);
    const [feedbackMap, setFeedbackMap] = useState({});
    const [input, setInput] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const messagesEndRef = useRef(null);
    const [isHistoryOpen, setIsHistoryOpen] = useState(true);
    const [chatList, setChatList] = useState(() => {
        const saved = sessionStorage.getItem("chat_list");
        return saved ? JSON.parse(saved) : [
            { id: threadId, title: "Current Chat", active: true }
        ];
    });
    const createNewChat = () => {
    const newId = uuidv4();

    const newChat = {
        id: newId,
        title: `Chat ${chatList.length + 1}`,
        active: true
    };

    const updated = [newChat, ...chatList.map(c => ({ ...c, active: false }))];

    setChatList(updated);
    sessionStorage.setItem("chat_list", JSON.stringify(updated));
    sessionStorage.setItem(`thread_${agentConfig.id}`, newId);

    window.location.reload();
   };

   const deleteChat = (id) => {
    const updated = chatList.filter(c => c.id !== id);

    setChatList(updated);
    sessionStorage.setItem("chat_list", JSON.stringify(updated));
  };

    // Dynamic state loading with Clean-up architecture to block race conditions
    useEffect(() => {
        if (!agentConfig?.id) return;

        let isCurrentRequest = true;

        // Reset UI immediately to prevent cross-agent view leaking
        setThreadId('');
        setMessages([]);
        setFeedbackMap({});
        setIsLoadingHistory(true);

        const loadAgentState = async () => {
            const storageKey = `thread_${agentConfig.id}`;
            const storedThreadId = sessionStorage.getItem(storageKey);
            const isExistingSession = !!storedThreadId;

            const currentThreadId = storedThreadId || uuidv4();
            if (!isExistingSession) {
                sessionStorage.setItem(storageKey, currentThreadId);
            }

            if (!isCurrentRequest) return;
            setThreadId(currentThreadId);

            if (isExistingSession) {
                try {
                    const response = await fetch(`${API_URL}/api/v1/chat/${agentConfig.id}/${currentThreadId}`);
                    if (!response.ok) throw new Error("Failed to fetch history");

                    const data = await response.json();
                    
                    // Prevent applying state if user has already switched agents mid-fetch
                    if (!isCurrentRequest) return;

                    if (data.messages && data.messages.length > 0) {
                        const mappedMessages = data.messages.map(msg => {
                            let text = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content);
                            let formType = null;
                            for (const [token, type] of Object.entries(FORM_TOKENS)) {
                                if (text.includes(token)) {
                                    formType = type;
                                    text = text.replace(token, '').trim();
                                    break;
                                }
                            }
                            return {
                                type: msg.type === 'human' ? 'user' : 'bot',
                                text,
                                formType,
                            };
                        });
                        setMessages(mappedMessages);

                        // Load feedback
                        try {
                            const fbRes = await fetch(`${API_URL}/api/v1/feedback/${agentConfig.id}/${currentThreadId}`);
                            if (fbRes.ok && isCurrentRequest) {
                                const fbData = await fbRes.json();
                                const userId = user.username || "anonymous";
                                const map = {};
                                for (const [idx, users] of Object.entries(fbData.feedback || {})) {
                                    if (users[userId]) {
                                        map[idx] = users[userId];
                                    }
                                }
                                setFeedbackMap(map);
                            }
                        } catch (fbErr) {
                            console.error("Error fetching feedback:", fbErr);
                        }
                    } else {
                        setMessages([{
                            type: 'bot',
                            text: `Hello ${user.name.split(" ")[0]}! I am your ${agentConfig.title} assistant. How can I help you today?`
                        }]);
                    }
                } catch (error) {
                    console.error("Error fetching history:", error);
                    if (isCurrentRequest) {
                        setMessages([{
                            type: 'bot',
                            text: `Welcome back! I had trouble retrieving our last conversation, but I'm ready to help.`
                        }]);
                    }
                }
            } else {
                if (isCurrentRequest) {
                    setMessages([{
                        type: 'bot',
                        text: `Hello ${user.name.split(" ")[0]}! I am your ${agentConfig.title} assistant. How can I help you today?`
                    }]);
                }
            }

            if (isCurrentRequest) {
                setIsLoadingHistory(false);
            }
        };

        loadAgentState();

        // Cleanup: Cancels any active async operations state-binding when dependencies change
        return () => {
            isCurrentRequest = false;
        };
    }, [agentConfig.id, agentConfig.title, user.name]);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages]);

    const handleSend = async (e) => {
        e.preventDefault();
        if (!input.trim() || !threadId || isLoadingHistory) return;

        const userMessage = { type: 'user', text: input };
        setMessages(prev => [...prev, userMessage]);
        setInput("");
        setIsLoading(true);

        try {
            const response = await fetch(`${API_URL}/api/v1/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: userMessage.text,
                    agent_id: agentConfig.id,
                    user_id: user.username || "anonymous",
                    thread_id: threadId
                })
            });

            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let accumulatedText = "";

            setMessages(prev => [...prev, { type: 'bot', text: "", formType: null }]);

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value, { stream: true }).replace(/\r/g, '');
                accumulatedText = appendChunkSmartly(accumulatedText, chunk);

                let currentFormType = null;
                let cleanText = accumulatedText;
                for (const [token, type] of Object.entries(FORM_TOKENS)) {
                    if (cleanText.includes(token)) {
                        currentFormType = type;
                        cleanText = cleanText.replace(token, '').trim();
                        break;
                    }
                }

                setMessages(prev => {
                    const newMessages = [...prev];
                    const lastIdx = newMessages.length - 1;
                    newMessages[lastIdx] = {
                        ...newMessages[lastIdx],
                        text: cleanText,
                        formType: currentFormType || newMessages[lastIdx].formType
                    };
                    return newMessages;
                });
            }
        } catch (error) {
            console.error("Error:", error);
            setMessages(prev => [...prev, { type: 'bot', text: "Sorry, I'm having trouble connecting to the server. Is the backend running?" }]);
        } finally {
            setIsLoading(false);
        }
    };

    return (
<motion.div
  initial={{ opacity: 0 }}
  animate={{ opacity: 1 }}
  className="h-screen w-screen flex bg-gradient-to-br from-slate-50 via-gray-100 to-slate-200 text-gray-900 overflow-hidden"
>

  {/* ───────── SIDEBAR ───────── */}
  <div
    className={`relative flex flex-col border-r border-gray-200 bg-white/70 backdrop-blur-xl shadow-xl transition-all duration-300 ${
      isHistoryOpen ? "w-[320px]" : "w-[70px]"
    }`}
  >

    {/* HEADER */}
    <div className="p-3 flex items-center justify-between border-b bg-gradient-to-r from-indigo-500 to-purple-500 text-white">
      {isHistoryOpen && (
        <div>
          <h2 className="font-bold text-sm">Chat History</h2>
          <p className="text-[11px] opacity-80">{agentConfig.title}</p>
        </div>
      )}

      <button
        onClick={() => setIsHistoryOpen(!isHistoryOpen)}
        className="p-2 rounded-lg bg-white/20 hover:bg-white/30 transition"
      >
        {isHistoryOpen ? "⮜" : "⮞"}
      </button>
    </div>

    {/* NEW CHAT */}
    <motion.button
      whileTap={{ scale: 0.95 }}
      onClick={createNewChat}
      className="m-2 py-2 rounded-xl bg-gradient-to-r from-indigo-600 to-purple-600 text-white text-xs shadow-lg hover:shadow-xl transition"
    >
      {isHistoryOpen ? "+ New Chat" : "+"}
    </motion.button>

    {/* CHAT LIST */}
    <div className="flex-1 overflow-y-auto px-2 space-y-2">
      {chatList.map((chat, index) => (
        <motion.div
          key={chat.id}
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          whileHover={{ scale: 1.02 }}
          className="group flex items-center justify-between p-2 rounded-xl bg-white hover:bg-indigo-50 border border-gray-100 shadow-sm cursor-pointer transition"
        >
          {isHistoryOpen && (
            <span className="text-sm text-gray-700 truncate">
              {chat.title}
            </span>
          )}

          {isHistoryOpen && (
            <button
              onClick={() => deleteChat(chat.id)}
              className="opacity-0 group-hover:opacity-100 text-xs text-red-500 hover:text-red-700"
            >
              Delete
            </button>
          )}
        </motion.div>
      ))}
    </div>
  </div>

  {/* ───────── MAIN CHAT ───────── */}
  <div className="flex-1 flex flex-col">

    {/* TOP BAR */}
    <div className="h-16 bg-white/80 backdrop-blur-xl border-b flex items-center justify-between px-6 shadow-sm">
      <div>
        <h1 className="font-bold text-lg bg-gradient-to-r from-indigo-600 to-purple-600 text-transparent bg-clip-text">
          {agentConfig.title}
        </h1>
        <p className="text-xs text-gray-500">{agentConfig.subtitle}</p>
      </div>

      <span className="text-xs px-3 py-1 rounded-full bg-green-100 text-green-600 animate-pulse">
        AI Online
      </span>
    </div>

    {/* MESSAGES */}
    <div className="flex-1 overflow-y-auto px-6 py-6 space-y-5">

      {messages.map((msg, index) => (
        <motion.div
          key={index}
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.25 }}
          className={`flex ${msg.type === "user" ? "justify-end" : "justify-start"}`}
        >
          <div className="relative group max-w-[70%]">

            {/* MESSAGE BOX */}
            <div
              className={`px-4 py-3 rounded-2xl text-sm shadow-md border transition-all duration-200 ${
                msg.type === "user"
                  ? "bg-gradient-to-r from-indigo-600 to-purple-600 text-white rounded-br-md"
                  : "bg-white/90 backdrop-blur border-gray-200 rounded-bl-md hover:shadow-lg"
              }`}
            >

              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {msg.text}
              </ReactMarkdown>

              {msg.formType === "lifestore" && <LifestoreForm />}
              {msg.formType === "enterprise" && <EnterpriseForm />}
            </div>

            {/* COPY / EDIT */}
            <div className="absolute top-2 right-2 hidden group-hover:flex gap-2">
              <button
                onClick={() => navigator.clipboard.writeText(msg.text)}
                className="text-xs bg-gray-100 hover:bg-gray-200 px-2 py-1 rounded-md"
              >
                Copy
              </button>

              {msg.type === "user" && (
                <button
                  onClick={() => {
                    const newText = prompt("Edit message:", msg.text);
                    if (!newText) return;

                    setMessages(prev =>
                      prev.map((m, i) =>
                        i === index ? { ...m, text: newText } : m
                      )
                    );
                  }}
                  className="text-xs bg-gray-100 hover:bg-gray-200 px-2 py-1 rounded-md"
                >
                  Edit
                </button>
              )}
            </div>

          </div>
        </motion.div>
      ))}

      {isLoading && (
        <div className="flex items-center gap-2 text-gray-500 animate-pulse">
          <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" />
          <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce delay-150" />
          <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce delay-300" />
        </div>
      )}

      <div ref={messagesEndRef} />
    </div>

    {/* INPUT */}
    <div className="border-t bg-white/80 backdrop-blur-xl p-4">
      <form onSubmit={handleSend} className="flex gap-3">

        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={`Message ${agentConfig.title}...`}
          className="flex-1 border border-gray-200 rounded-xl px-4 py-3 text-sm outline-none focus:ring-2 focus:ring-indigo-400 transition"
        />

        <button
          type="submit"
          disabled={!input.trim() || isLoading}
          className="px-5 py-3 rounded-xl bg-gradient-to-r from-indigo-600 to-purple-600 text-white text-sm hover:scale-105 transition disabled:opacity-40"
        >
          Send
        </button>
      </form>

      <p className="text-center text-[11px] text-gray-400 mt-2">
        {agentConfig.disclaimer}
      </p>
    </div>

  </div>
</motion.div>
);
};

export default MultiAgentChatWorkspace;