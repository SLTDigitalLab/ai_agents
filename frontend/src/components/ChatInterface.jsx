import React, { useState, useRef, useEffect, useMemo } from 'react';
import { useMsal } from "@azure/msal-react";
import { motion } from 'framer-motion';
import { v4 as uuidv4 } from 'uuid';
import LifestoreForm from './forms/LifestoreForm';
import EnterpriseForm from './forms/EnterpriseForm';

// Generative UI trigger tokens emitted by the backend
const FORM_TOKENS = {
    '[RENDER_LIFESTORE_FORM]': 'lifestore',
    '[RENDER_ENTERPRISE_FORM]': 'enterprise',
};

/**
 * Get or create a unique thread_id for this agent session.
 * Stored in sessionStorage so it survives page refreshes but
 * is unique per agent (e.g. "thread_askhr", "thread_askfinance").
 */
const getOrCreateThreadId = (agentId) => {
    const storageKey = `thread_${agentId}`;
    let threadId = sessionStorage.getItem(storageKey);
    if (!threadId) {
        threadId = uuidv4();
        sessionStorage.setItem(storageKey, threadId);
    }
    return threadId;
};

const ChatInterface = ({ agentConfig }) => {
    const { accounts } = useMsal();
    const user = accounts[0] || { name: "User" };

    // Stable thread ID for this agent – survives re-renders and page refreshes
    const threadId = useMemo(() => getOrCreateThreadId(agentConfig.id), [agentConfig.id]);

    const [messages, setMessages] = useState([
        {
            type: 'bot',
            text: `Hello ${user.name.split(" ")[0]}! I am your ${agentConfig.title} assistant. How can I help you today?`
        }
    ]);
    const [input, setInput] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const messagesEndRef = useRef(null);

    // Auto-scroll to bottom
    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages]);

    // Handle Sending Message
    const handleSend = async (e) => {
        e.preventDefault();
        if (!input.trim()) return;

        // 1. Add User Message to UI
        const userMessage = { type: 'user', text: input };
        setMessages(prev => [...prev, userMessage]);
        setInput("");
        setIsLoading(true);

        try {
            // 2. Send to Real FastAPI Backend
            const response = await fetch('http://localhost:8000/api/v1/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: userMessage.text,
                    agent_id: agentConfig.id, // e.g., "finance"
                    user_id: user.username || "anonymous",
                    thread_id: threadId
                })
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const data = await response.json();

            // 3. Add AI Response to UI
            // Ensure the response is a string to prevent React rendering crashes
            let safeText = data.response;
            if (typeof safeText !== 'string') {
                console.warn("Received non-string response from backend:", safeText);
                safeText = JSON.stringify(safeText);
            }

            // Detect and strip Generative UI trigger tokens
            let formType = null;
            for (const [token, type] of Object.entries(FORM_TOKENS)) {
                if (safeText.includes(token)) {
                    formType = type;
                    safeText = safeText.replace(token, '').trim();
                    break;
                }
            }

            const botMessage = {
                type: 'bot',
                text: safeText,
                formType: formType,
            };
            setMessages(prev => [...prev, botMessage]);

        } catch (error) {
            console.error("Error:", error);
            setMessages(prev => [...prev, { type: 'bot', text: "Sorry, I'm having trouble connecting to the server. Is the backend running?" }]);
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1], delay: 0.1 }}
            className="flex-1 flex flex-col w-full max-w-6xl mx-auto px-4 z-10 pt-6 pb-0 min-h-0 overflow-hidden"
        >
            {/* Title Section */}
            <motion.div
                initial={{ opacity: 0, y: 15 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.2 }}
                className="text-center mb-4 space-y-2"
            >
                <h1 className="text-5xl sm:text-6xl font-extrabold text-white tracking-tight drop-shadow-lg uppercase">{agentConfig.title}</h1>
                <p className="text-white/70 text-base sm:text-lg max-w-4xl mx-auto font-light">{agentConfig.subtitle}</p>
            </motion.div>

            {/* ── Premium Chat Workspace ─────────────────────── */}
            <motion.div
                initial={{ opacity: 0, y: 25, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1], delay: 0.3 }}
                className="relative h-[63vh] min-h-0 rounded-2xl sm:rounded-3xl overflow-hidden"
                style={{
                    boxShadow: `0 0 0 1px rgba(255,255,255,0.06), 0 0 40px -10px rgba(255,255,255,0.05), inset 0 1px 0 rgba(255,255,255,0.04)`
                }}
            >
                {/* Inset glass container */}
                <div className="absolute inset-0 bg-white/[0.03] backdrop-blur-sm rounded-2xl sm:rounded-3xl pointer-events-none" />

                <div className="relative bg-white w-full h-full rounded-2xl sm:rounded-3xl shadow-2xl flex flex-col overflow-hidden">

                    {/* Messages Area */}
                    <div className="flex-1 overflow-y-auto p-6 sm:p-8 space-y-5 chat-scrollbar">
                        {messages.map((msg, index) => (
                            <motion.div
                                key={index}
                                initial={{ opacity: 0, y: 10 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ duration: 0.35, ease: 'easeOut' }}
                                className={`flex ${msg.type === 'user' ? 'justify-end' : 'justify-start'}`}
                            >
                                <div className={`max-w-[75%] sm:max-w-[70%] rounded-2xl px-5 sm:px-6 py-3.5 sm:py-4 text-[0.9375rem] leading-relaxed ${msg.type === 'user'
                                    ? `bg-gradient-to-br ${agentConfig.color} text-white rounded-tr-md shadow-lg`
                                    : 'bg-gray-50 border border-gray-100 text-gray-700 rounded-tl-md shadow-sm'
                                    }`}>
                                    <p className="whitespace-pre-wrap">{msg.text}</p>
                                    {/* Render Generative UI form if triggered */}
                                    {msg.formType === 'lifestore' && <LifestoreForm />}
                                    {msg.formType === 'enterprise' && <EnterpriseForm />}
                                </div>
                            </motion.div>
                        ))}
                        {isLoading && (
                            <div className="flex justify-start">
                                <div className="bg-gray-50 border border-gray-100 rounded-2xl rounded-tl-md px-6 py-4 shadow-sm flex gap-1.5 items-center">
                                    <div className="w-2 h-2 rounded-full bg-gray-300 animate-bounce" />
                                    <div className="w-2 h-2 rounded-full bg-gray-300 animate-bounce [animation-delay:150ms]" />
                                    <div className="w-2 h-2 rounded-full bg-gray-300 animate-bounce [animation-delay:300ms]" />
                                </div>
                            </div>
                        )}
                        <div ref={messagesEndRef} />
                    </div>

                    {/* Input Area */}
                    <div className="p-4 sm:p-6 bg-white border-t border-gray-100/80">
                        <form onSubmit={handleSend} className="relative flex items-center">
                            <input
                                type="text"
                                value={input}
                                onChange={(e) => setInput(e.target.value)}
                                placeholder="Type a message..."
                                className="w-full bg-gray-50/80 text-gray-800 rounded-2xl pl-5 sm:pl-6 pr-14 py-3.5 sm:py-4 focus:outline-none focus:ring-2 focus:ring-gray-200/80 transition-all border border-gray-200/70 placeholder:text-gray-400 text-[0.9375rem]"
                            />
                            <button
                                type="submit"
                                disabled={!input.trim() || isLoading}
                                className="absolute right-2 p-2.5 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
                            >
                                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5 sm:w-6 sm:h-6">
                                    <path d="M3.478 2.405a.75.75 0 00-.926.94l2.432 7.905H13.5a.75.75 0 010 1.5H4.984l-2.432 7.905a.75.75 0 00.926.94 60.519 60.519 0 0018.445-8.986.75.75 0 000-1.218A60.517 60.517 0 003.478 2.405z" />
                                </svg>
                            </button>
                        </form>
                        <p className="text-center text-xs text-gray-400/80 mt-3 font-light">
                            {agentConfig.disclaimer}
                        </p>
                    </div>
                </div>
            </motion.div>
        </motion.div>
    );
};

export default ChatInterface;