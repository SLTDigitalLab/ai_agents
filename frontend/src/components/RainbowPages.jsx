import React, { useState, useEffect, useRef } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { AuthenticatedTemplate, UnauthenticatedTemplate, useMsal } from "@azure/msal-react";
import { loginRequest } from '../authConfig';
import { AGENTS } from '../config/agents';
import { useTheme } from '../contexts/ThemeContext';
import { motion, AnimatePresence } from 'framer-motion';
import sltLogo from '../assets/slt-mobitel-logo.png';
import embryoLogo from '../assets/embryo-removebg.png';

// ── Microsoft SVG Icon ──────────────────────────────────
const MicrosoftIcon = () => (
  <svg width="20" height="20" viewBox="0 0 21 21" fill="none">
    <rect x="1" y="1" width="9" height="9" fill="#F25022" />
    <rect x="11" y="1" width="9" height="9" fill="#7FBA00" />
    <rect x="1" y="11" width="9" height="9" fill="#00A4EF" />
    <rect x="11" y="11" width="9" height="9" fill="#FFB900" />
  </svg>
);

// Faint film-grain texture
const GRAIN_DATA_URL = "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")";

// Avatar Initials utility
const getInitials = (name) => {
  if (!name) return '?';
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].charAt(0).toUpperCase();
  return (parts[0].charAt(0) + parts[parts.length - 1].charAt(0)).toUpperCase();
};

// Map agent ID to descriptive tags / areas of expertise
const AGENT_TAGS = {
  supervisor: ["All Topics", "Router", "General Help"],
  hr: ["Leave Balances", "EPF/ETF", "Salary & Allowances", "Policies"],
  finance: ["Payroll", "Invoices", "Procurement", "Budgets"],
  admin: ["Transport", "Facilities", "Security", "Key Keys"],
  it: ["Access Reset", "Hardware", "Software Help", "Network Login"],
  cia: ["Internal Audit", "Risk Control", "Compliance", "Charter"],
  process: ["SOP Checklists", "Workflows", "ISO Audit", "Processes"],
  enterprise: ["Strategic Insights", "CRM Integration", "B2B Deals"],
  askhrslm: ["DeepSeek-R1", "On-Prem", "Offline RAG", "Leave Policy"],
  lifestore: ["Products Info", "Smart Home", "Store Orders", "Inventory"],
  network: ["IP Routing", "WAN/LAN Setup", "NOC Alerts", "Fiber"],
  legal: ["Agreements", "Statutory Checks", "Court Files", "Contracts"],
  marketing: ["Brand Policy", "Campaigns", "Promotions", "Logo Rules"]
};

// Map agent ID to Category Grouping
const AGENT_CATEGORIES = {
  supervisor: "General",
  hr: "HR & Process",
  finance: "Finance & Legal",
  admin: "HR & Process",
  it: "Technical Support",
  cia: "Finance & Legal",
  process: "HR & Process",
  enterprise: "General",
  askhrslm: "HR & Process",
  lifestore: "Technical Support",
  network: "Technical Support",
  legal: "Finance & Legal",
  marketing: "General"
};

// Animation variants
const containerVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: { staggerChildren: 0.08, delayChildren: 0.1 }
  }
};

const cardVariants = {
  hidden: { opacity: 0, y: 20 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.5, ease: [0.22, 1, 0.36, 1] }
  }
};

export default function RainbowPages() {
  const { instance, accounts } = useMsal();
  const location = useLocation();
  const navigate = useNavigate();
  const user = accounts[0] || {};
  const { theme, setTheme } = useTheme();
  const [searchTerm, setSearchTerm] = useState('');
  const [activeCategory, setActiveCategory] = useState('All');
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  const isAuthed = accounts.length > 0;
  const effectiveTheme = isAuthed && theme === 'dark' ? 'dark' : 'light';

  // Synchronize <html> root class for tailwind styling
  useEffect(() => {
    const root = document.documentElement;
    if (effectiveTheme === 'dark') root.classList.add('dark');
    else root.classList.remove('dark');
    return () => { root.classList.remove('dark'); };
  }, [effectiveTheme]);

  // Handle click outside to close user menu
  useEffect(() => {
    if (!menuOpen) return;
    const handleClickOutside = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [menuOpen]);

  const handleLogin = () => {
    sessionStorage.setItem('intentionalLogin', 'true');
    sessionStorage.setItem('lastAgent', location.pathname);
    instance.loginRedirect(loginRequest).catch(e => console.error(e));
  };

  const handleLogout = () => {
    instance.logoutRedirect({
      postLogoutRedirectUri: window.location.origin + '/rainbowpages'
    }).catch(e => console.error(e));
  };

  // Filter agents based on Search Term and Category
  const filteredAgents = Object.entries(AGENTS).filter(([key, agent]) => {
    const nameMatch = agent.title.toLowerCase().includes(searchTerm.toLowerCase()) || 
                      agent.subtitle.toLowerCase().includes(searchTerm.toLowerCase());
    const tagsMatch = (AGENT_TAGS[agent.id] || []).some(tag => tag.toLowerCase().includes(searchTerm.toLowerCase()));
    const categoryMatch = activeCategory === 'All' || AGENT_CATEGORIES[agent.id] === activeCategory;
    
    return (nameMatch || tagsMatch) && categoryMatch;
  });

  const categories = ['All', 'General', 'HR & Process', 'Finance & Legal', 'Technical Support'];

  return (
    <div className="h-screen w-screen flex flex-col relative overflow-hidden bg-slate-50 dark:bg-[#111317] text-gray-900 dark:text-gray-100 transition-colors duration-300">
      
      {/* Ambient shifting rainbow backdrop */}
      <div className="absolute inset-0 z-0 pointer-events-none overflow-hidden">
        <div className="absolute top-[-20%] left-[-20%] w-[60%] h-[60%] rounded-full bg-purple-500/10 dark:bg-purple-600/5 blur-[120px] animate-pulse" />
        <div className="absolute bottom-[-10%] right-[-10%] w-[50%] h-[50%] rounded-full bg-cyan-500/10 dark:bg-cyan-600/5 blur-[120px] animate-pulse [animation-delay:2s]" />
        <div className="absolute top-[30%] right-[20%] w-[40%] h-[40%] rounded-full bg-rose-500/5 dark:bg-rose-600/3 blur-[100px] animate-pulse [animation-delay:4s]" />
        <div className="absolute bottom-[20%] left-[20%] w-[35%] h-[35%] rounded-full bg-emerald-500/5 dark:bg-emerald-600/3 blur-[90px] animate-pulse [animation-delay:1s]" />
      </div>

      {/* Premium film-grain overlay */}
      <div
        aria-hidden="true"
        className="absolute inset-0 pointer-events-none mix-blend-overlay opacity-[0.03] z-0"
        style={{ backgroundImage: GRAIN_DATA_URL }}
      />

      {/* ── UNAUTHENTICATED: PREMIUM LOGIN VIEW ── */}
      <UnauthenticatedTemplate>
        <div className="flex-1 flex flex-col items-center justify-center p-6 z-10">
          <motion.div 
            initial={{ opacity: 0, y: -20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6 }}
            className="flex flex-col items-center mb-8 text-center"
          >
            <img src={sltLogo} alt="SLTMobitel" className="h-16 w-auto mb-4 drop-shadow-sm" />
            <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight bg-gradient-to-r from-cyan-600 via-purple-600 to-rose-500 bg-clip-text text-transparent">
              Workmate AI Directory
            </h1>
            <p className="text-gray-500 dark:text-gray-400 mt-2 text-sm max-w-sm">
              Discover and chat with specialized enterprise AI assistants
            </p>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.6, delay: 0.1 }}
            className="relative w-full max-w-md"
          >
            <div className="absolute -inset-1.5 bg-gradient-to-r from-cyan-500 via-purple-500 to-rose-500 rounded-3xl blur-xl opacity-20 animate-pulse" />
            <div className="relative bg-white dark:bg-[#1a1d24] rounded-3xl p-8 sm:p-10 flex flex-col items-center border border-gray-200/50 dark:border-gray-800/40 shadow-2xl">
              <div className="w-16 h-16 rounded-2xl bg-gradient-to-tr from-cyan-500 via-purple-500 to-rose-500 flex items-center justify-center mb-6 shadow-lg">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor" className="w-8 h-8 text-white">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75c-2.676 0-5.216-.584-7.499-1.632z" />
                </svg>
              </div>

              <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100 mb-2">Corporate Credentials Required</h2>
              <p className="text-gray-400 dark:text-gray-500 text-xs text-center mb-8 max-w-xs leading-relaxed">
                Log in using your corporate Microsoft account to unlock the full directory of specialists.
              </p>

              <motion.button
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.98 }}
                onClick={handleLogin}
                className="flex items-center justify-center gap-3 w-full px-6 py-4 rounded-xl bg-gray-900 hover:bg-gray-800 dark:bg-white dark:hover:bg-gray-100 text-white dark:text-gray-900 font-semibold shadow-md transition-colors"
              >
                <MicrosoftIcon />
                Sign in with Microsoft
              </motion.button>
            </div>
          </motion.div>

          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.4 }}
            className="mt-12 flex items-center justify-center gap-1.5 select-none opacity-60"
          >
            <span className="text-[0.65rem] uppercase tracking-widest font-semibold text-gray-500">Powered by</span>
            <img src={embryoLogo} alt="Embryo Logo" className="h-[20px] w-auto object-contain" />
          </motion.div>
        </div>
      </UnauthenticatedTemplate>

      {/* ── AUTHENTICATED: PREMIUM DIRECTORY GRID ── */}
      <AuthenticatedTemplate>
        {/* Navigation Header */}
        <header className="w-full shrink-0 flex items-center justify-between px-6 sm:px-12 py-4 border-b border-gray-200/40 dark:border-gray-800/40 bg-white/70 dark:bg-[#16191f]/70 backdrop-blur-md z-20">
          <div className="flex items-center gap-3 cursor-pointer select-none" onClick={() => navigate('/workmateai')}>
            <img src={sltLogo} alt="SLTMobitel" className="h-8 sm:h-9 w-auto" />
            <span className="hidden sm:inline-block h-5 w-[1px] bg-gray-300 dark:bg-gray-700" />
            <h1 className="hidden sm:block text-base font-bold bg-gradient-to-r from-cyan-600 via-purple-600 to-rose-500 bg-clip-text text-transparent">
              Rainbow Pages
            </h1>
          </div>

          <div className="flex items-center gap-4">
            {/* Theme Toggle Button */}
            <button 
              onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
              className="p-2.5 rounded-lg bg-gray-100 hover:bg-gray-200 dark:bg-gray-850 dark:hover:bg-gray-805 text-gray-600 dark:text-gray-300 transition-colors"
              title="Toggle theme"
            >
              {theme === 'light' ? (
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor" className="w-5 h-5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21.752 15.002A9.718 9.718 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 009.002-5.998z" />
                </svg>
              ) : (
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor" className="w-5 h-5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v2.25m0 13.5V21m8.966-8.966h-2.25m-13.5 0h-2.25m11.966-7.218l-1.591 1.591M4.929 19.071l1.591-1.591m0-12.83l-1.591-1.591m12.83 12.83l1.591-1.591M12 18.75a6.75 6.75 0 100-13.5 6.75 6.75 0 000 13.5z" />
                </svg>
              )}
            </button>

            {/* Profile Dropdown */}
            <div className="relative" ref={menuRef}>
              <button
                onClick={() => setMenuOpen(!menuOpen)}
                className="w-10 h-10 rounded-full bg-gradient-to-tr from-cyan-600 via-purple-600 to-rose-500 text-white text-sm font-semibold flex items-center justify-center shadow-md ring-2 ring-white dark:ring-gray-800"
              >
                {getInitials(user.name)}
              </button>

              <AnimatePresence>
                {menuOpen && (
                  <motion.div
                    initial={{ opacity: 0, scale: 0.95, y: 10 }}
                    animate={{ opacity: 1, scale: 1, y: 0 }}
                    exit={{ opacity: 0, scale: 0.95, y: 10 }}
                    transition={{ duration: 0.15 }}
                    className="absolute right-0 mt-3 w-64 bg-white dark:bg-[#1a1d24] border border-gray-200 dark:border-gray-800 rounded-2xl shadow-xl z-50 overflow-hidden"
                  >
                    <div className="px-4 py-3 border-b border-gray-100 dark:border-gray-800 flex items-center gap-3">
                      <div className="w-9 h-9 rounded-full bg-gradient-to-tr from-cyan-600 via-purple-600 to-rose-500 text-white text-sm font-semibold flex items-center justify-center shadow-inner shrink-0">
                        {getInitials(user.name)}
                      </div>
                      <div className="min-w-0">
                        <p className="text-xs font-bold truncate">{user.name}</p>
                        <p className="text-[0.65rem] text-gray-500 truncate">{user.username}</p>
                      </div>
                    </div>

                    <button
                      onClick={handleLogout}
                      className="w-full flex items-center gap-2.5 px-4 py-3 text-xs text-red-600 hover:bg-red-50 dark:hover:bg-red-950/20 font-medium transition-colors"
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 text-red-500">
                        <path fillRule="evenodd" d="M3 4.25A2.25 2.25 0 015.25 2h5.5A2.25 2.25 0 0113 4.25v2a.75.75 0 01-1.5 0v-2a.75.75 0 00-.75-.75h-5.5a.75.75 0 00-.75.75v11.5c0 .414.336.75.75.75h5.5a.75.75 0 00.75-.75v-2a.75.75 0 011.5 0v2A2.25 2.25 0 0110.75 18h-5.5A2.25 2.25 0 013 15.75V4.25z" clipRule="evenodd" />
                        <path fillRule="evenodd" d="M19 10a.75.75 0 00-.22-.53l-2.75-2.75a.75.75 0 10-1.06 1.06l1.47 1.47H8.75a.75.75 0 000 1.5h7.69l-1.47 1.47a.75.75 0 101.06 1.06l2.75-2.75A.75.75 0 0019 10z" clipRule="evenodd" />
                      </svg>
                      Logout
                    </button>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>
        </header>

        {/* Directory Content Area */}
        <div className="flex-1 overflow-y-auto px-6 sm:px-12 py-8 z-10 custom-scrollbar">
          
          {/* Header Title section */}
          <div className="text-center max-w-2xl mx-auto mb-10">
            <motion.h2 
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              className="text-3xl sm:text-4xl font-extrabold tracking-tight bg-gradient-to-r from-cyan-600 via-purple-600 to-rose-500 bg-clip-text text-transparent"
            >
              Workmate AI specialists
            </motion.h2>
            <motion.p 
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.1 }}
              className="text-gray-500 dark:text-gray-400 mt-2 text-sm font-light leading-relaxed"
            >
              Access specialized HR, Admin, IT, Legal, Finance, Marketing, Compliance, and operational support. Select a specialist agent below to start your conversation.
            </motion.p>
          </div>

          {/* Search & Categories Bar */}
          <div className="max-w-4xl mx-auto mb-10 flex flex-col md:flex-row gap-4 items-center justify-between">
            {/* Search Input */}
            <div className="relative w-full md:w-80">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-5 h-5 absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400 dark:text-gray-500 pointer-events-none">
                <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.602 10.602z" />
              </svg>
              <input 
                type="text" 
                placeholder="Search capabilities, e.g. Leave, Salary, SOP..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="w-full pl-11 pr-4 py-2.5 rounded-xl border border-gray-200/80 dark:border-gray-800/40 bg-white/70 dark:bg-[#1a1d24]/70 backdrop-blur-md text-sm text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-cyan-500/55 dark:focus:ring-cyan-500/30 transition-shadow shadow-sm"
              />
            </div>

            {/* Filter Pills */}
            <div className="flex flex-wrap gap-1.5 w-full md:w-auto items-center justify-start md:justify-end">
              {categories.map(cat => (
                <button
                  key={cat}
                  onClick={() => setActiveCategory(cat)}
                  className={`px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                    activeCategory === cat
                      ? 'bg-gradient-to-r from-cyan-600 to-purple-600 text-white shadow-md'
                      : 'bg-white/70 dark:bg-[#1a1d24]/70 border border-gray-200/50 dark:border-gray-800/40 hover:bg-gray-100 dark:hover:bg-gray-850 text-gray-600 dark:text-gray-400'
                  }`}
                >
                  {cat}
                </button>
              ))}
            </div>
          </div>

          {/* Directory Grid */}
          <motion.div 
            variants={containerVariants}
            initial="hidden"
            animate="visible"
            className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6 max-w-7xl mx-auto"
          >
            {filteredAgents.length > 0 ? (
              filteredAgents.map(([agentKey, agent]) => {
                const isSLM = agent.id === 'askhrslm';
                return (
                  <motion.div
                    key={agentKey}
                    variants={cardVariants}
                    whileHover={{ y: -6, scale: 1.01 }}
                    className="relative group flex flex-col justify-between h-72 rounded-2xl border border-gray-200/60 dark:border-gray-800/40 bg-white/70 dark:bg-[#1a1d24]/70 backdrop-blur-md shadow-sm hover:shadow-[0_12px_24px_-10px_rgba(var(--card-shadow),0.12)] transition-all overflow-hidden p-6"
                    style={{
                      '--card-shadow': agent.id === 'supervisor' ? '6, 182, 212' : 
                                       agent.id === 'hr' ? '147, 51, 234' :
                                       agent.id === 'finance' ? '37, 99, 235' : 
                                       agent.id === 'admin' ? '107, 114, 128' :
                                       agent.id === 'it' ? '14, 165, 233' : 
                                       agent.id === 'cia' ? '225, 29, 72' :
                                       agent.id === 'process' ? '16, 185, 129' :
                                       agent.id === 'enterprise' ? '79, 70, 229' :
                                       agent.id === 'askhrslm' ? '192, 38, 211' :
                                       agent.id === 'lifestore' ? '234, 88, 12' :
                                       agent.id === 'network' ? '20, 184, 166' :
                                       agent.id === 'legal' ? '245, 158, 11' : '236, 72, 153'
                    }}
                  >
                    {/* Floating ambient colored dot behind agent icon */}
                    <div className={`absolute top-0 right-0 w-24 h-24 rounded-full bg-gradient-to-br ${agent.color} opacity-[0.04] group-hover:opacity-[0.08] blur-xl transition-opacity`} />
                    
                    <div>
                      {/* Agent Badge/Icon */}
                      <div className="flex items-center justify-between mb-4">
                        <div className={`w-10 h-10 rounded-xl bg-gradient-to-br ${agent.color} flex items-center justify-center text-white font-black text-sm shadow-md`}>
                          {agent.title.includes('ASK') ? agent.title.replace('ASK ', '').charAt(0) : 'W'}
                        </div>

                        {/* SLM / Supervisor Mini Tags */}
                        {isSLM && (
                          <span className="text-[0.6rem] font-bold px-2 py-0.5 rounded bg-fuchsia-100 dark:bg-fuchsia-950/40 text-fuchsia-600 dark:text-fuchsia-400 border border-fuchsia-200/50 dark:border-fuchsia-800/40">
                            ON-PREM SLM
                          </span>
                        )}
                        {agent.id === 'supervisor' && (
                          <span className="text-[0.6rem] font-bold px-2 py-0.5 rounded bg-cyan-100 dark:bg-cyan-950/40 text-cyan-600 dark:text-cyan-400 border border-cyan-200/50 dark:border-cyan-800/40">
                            SUPERVISOR
                          </span>
                        )}
                      </div>

                      {/* Title & description */}
                      <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100 leading-snug group-hover:text-cyan-600 dark:group-hover:text-cyan-400 transition-colors">
                        {agent.title.replace(/^ASK /i, 'Ask ')}
                      </h3>
                      <p className="text-gray-500 dark:text-gray-400 text-xs mt-1.5 line-clamp-3 font-light leading-relaxed">
                        {agent.subtitle}
                      </p>
                    </div>

                    <div className="mt-4">
                      {/* Expert tags */}
                      <div className="flex flex-wrap gap-1 mb-4">
                        {(AGENT_TAGS[agent.id] || []).slice(0, 3).map(tag => (
                          <span 
                            key={tag}
                            className="text-[0.6rem] font-medium px-2 py-0.5 rounded-md bg-gray-100/80 dark:bg-gray-805/60 text-gray-600 dark:text-gray-400"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>

                      {/* Action trigger button */}
                      <button
                        onClick={() => navigate(`/${agentKey}`)}
                        className={`w-full py-2 px-4 rounded-xl text-xs font-bold text-white transition-all shadow-sm flex items-center justify-center gap-1.5 ${agent.buttonColor}`}
                      >
                        Launch Assistant
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-3.5 h-3.5 group-hover:translate-x-0.5 transition-transform">
                          <path fillRule="evenodd" d="M3 10a.75.75 0 01.75-.75h10.638L10.23 5.29a.75.75 0 111.04-1.08l5.5 5.25a.75.75 0 010 1.08l-5.5 5.25a.75.75 0 11-1.04-1.08l4.158-3.96H3.75A.75.75 0 013 10z" clipRule="evenodd" />
                        </svg>
                      </button>
                    </div>
                  </motion.div>
                );
              })
            ) : (
              <div className="col-span-full py-16 flex flex-col items-center justify-center text-center">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.2} stroke="currentColor" className="w-12 h-12 text-gray-300 dark:text-gray-650 mb-3">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
                </svg>
                <h4 className="font-bold text-gray-700 dark:text-gray-300 text-sm">No specialists found</h4>
                <p className="text-gray-400 dark:text-gray-500 text-xs mt-1">Try expanding your search query or choosing another category</p>
              </div>
            )}
          </motion.div>

        </div>
      </AuthenticatedTemplate>

    </div>
  );
}
