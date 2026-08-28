import React, { useEffect, useRef, useState } from 'react';
import { BrowserRouter, Routes, Route, useParams, useLocation, Navigate, Link } from 'react-router-dom';
import { MsalProvider, AuthenticatedTemplate, UnauthenticatedTemplate, useMsal } from "@azure/msal-react";
import { PublicClientApplication, InteractionStatus } from "@azure/msal-browser";
import { msalConfig, loginRequest } from './authConfig';
import { AGENTS } from './config/agents';
import ChatInterface from './components/ChatInterface';
import ChatBrowser from './components/admin/ChatBrowser';
import AdminDashboard from './components/admin/AdminDashboard';
import IngestionPanel from './components/admin/IngestionPanel';
import FeedbackPanel from './components/admin/FeedbackPanel';
import AdminRoute from './components/admin/AdminRoute';
import IframeChatPage from './components/admin/IframeChatPage';
import { motion, AnimatePresence } from 'framer-motion';
import sltLogo from './assets/slt-mobitel-logo.png';
import embryoLogo from './assets/embryo-removebg.png';
import { ThemeProvider, useTheme } from './contexts/ThemeContext';
import ContactUsPage from './components/ContactUsPage';
import RainbowPages from './components/RainbowPages';

// Initialize MSAL outside the components
const msalInstance = new PublicClientApplication(msalConfig);

const RootRedirect = () => {
  const { inProgress } = useMsal();
  if (inProgress !== InteractionStatus.None) return null;
  return <Navigate to="/workmateai" replace />;
};

// ── Stagger animation variants ──────────────────────────
const containerVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: { staggerChildren: 0.15, delayChildren: 0.2 },
  },
};

const itemVariants = {
  hidden: { opacity: 0, y: 30 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.7, ease: [0.22, 1, 0.36, 1] },
  },
};

const cardVariants = {
  hidden: { opacity: 0, y: 40, scale: 0.96 },
  visible: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { duration: 0.8, ease: [0.22, 1, 0.36, 1], delay: 0.1 },
  },
};

// ── Microsoft SVG Icon ──────────────────────────────────
const MicrosoftIcon = () => (
  <svg width="20" height="20" viewBox="0 0 21 21" fill="none">
    <rect x="1" y="1" width="9" height="9" fill="#F25022" />
    <rect x="11" y="1" width="9" height="9" fill="#7FBA00" />
    <rect x="1" y="11" width="9" height="9" fill="#00A4EF" />
    <rect x="11" y="11" width="9" height="9" fill="#FFB900" />
  </svg>
);

// ── Agent → RGB triplet for ambient radial glows ──────────────────────
const COLOR_RGB = {
  cyan:    '6, 182, 212',
  purple:  '147, 51, 234',
  blue:    '37, 99, 235',
  gray:    '107, 114, 128',
  sky:     '14, 165, 233',
  rose:    '225, 29, 72',
  emerald: '16, 185, 129',
  indigo:  '79, 70, 229',
  orange:  '234, 88, 12',
  fuchsia: '192, 38, 211',
  teal:    '20, 184, 166',
  amber:   '245, 158, 11',
  pink:    '236, 72, 153',
  violet:  '124, 58, 237',
  green:   '22, 163, 74',
  red:     '220, 38, 38',
};
const getAgentRGB = (colorStr) => {
  const m = colorStr?.match(/from-(\w+)-/);
  return (m && COLOR_RGB[m[1]]) || '120, 120, 120';
};

// Flat background per theme. No agent tinting, no patterns, no gradients.
const buildAmbientBackground = (_rgb, theme = 'light') => ({
  backgroundColor: theme === 'dark' ? '#14171c' : '#fbfbfd',
});

// Faint film-grain texture — adds tactile depth, masks gradient banding.
const GRAIN_DATA_URL = "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")";

// ── Avatar utilities ──────────────────────────────────────────────────
const getInitials = (name) => {
  if (!name) return '?';
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].charAt(0).toUpperCase();
  return (parts[0].charAt(0) + parts[parts.length - 1].charAt(0)).toUpperCase();
};

// ── User Avatar + Popover Menu ─────────────────────────────────────────
// `placement` controls where the popover opens relative to the avatar.
//   "upRight"   → opens above-right (sidebar/desktop default).
//   "downLeft"  → opens below-left (mobile top-right header).
const UserAvatarMenu = ({ user, onLogout, agentColor, placement = 'upRight' }) => {
  const [open, setOpen] = useState(false);
  const { theme, setTheme } = useTheme();
  const menuRef = useRef(null);
  const initials = getInitials(user?.name);

  // Click-outside to close
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setOpen(false);
    };
    const onEsc = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onEsc);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onEsc);
    };
  }, [open]);

  const popoverPositionClass = placement === 'downLeft'
    ? 'top-full right-0 mt-2'
    : 'bottom-0 left-full ml-3';
  const popoverInitialX = placement === 'downLeft' ? 0 : -8;
  const popoverInitialY = placement === 'downLeft' ? -8 : 0;

  return (
    <div ref={menuRef} className="relative">
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, x: popoverInitialX, y: popoverInitialY, scale: 0.96 }}
            animate={{ opacity: 1, x: 0, y: 0, scale: 1 }}
            exit={{ opacity: 0, x: popoverInitialX, y: popoverInitialY, scale: 0.96 }}
            transition={{ duration: 0.18, ease: 'easeOut' }}
            className={`absolute ${popoverPositionClass} w-64 bg-white dark:bg-gray-900 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-[0_20px_50px_-15px_rgba(0,0,0,0.18)] dark:shadow-[0_20px_50px_-15px_rgba(0,0,0,0.6)] overflow-hidden z-40`}
          >
            {/* Identity header */}
            <div className="px-4 py-3 border-b border-gray-100 dark:border-gray-800 flex items-center gap-3">
              <div className={`w-9 h-9 rounded-full bg-gradient-to-br ${agentColor} text-white text-sm font-semibold flex items-center justify-center shadow-sm shrink-0`}>
                {initials}
              </div>
              <div className="min-w-0">
                <p className="text-sm font-semibold text-gray-900 dark:text-gray-100 truncate">{user.name}</p>
                <p className="text-[0.7rem] text-gray-500 dark:text-gray-400 truncate">{user.username}</p>
              </div>
            </div>

            {/* Theme toggle */}
            <div className="px-4 py-3 border-b border-gray-100 dark:border-gray-800">
              <p className="text-[0.7rem] uppercase tracking-wider font-semibold text-gray-400 dark:text-gray-500 mb-2">Theme</p>
              <div className="grid grid-cols-2 gap-1 bg-gray-100 dark:bg-gray-800 rounded-lg p-1">
                {['light', 'dark'].map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setTheme(mode)}
                    className={`flex items-center justify-center gap-1.5 py-1.5 rounded-md text-xs font-medium transition-all ${
                      theme === mode
                        ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm'
                        : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'
                    }`}
                  >
                    {mode === 'light' ? (
                      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-3.5 h-3.5">
                        <path d="M10 2a.75.75 0 01.75.75v1.5a.75.75 0 01-1.5 0v-1.5A.75.75 0 0110 2zM10 15a.75.75 0 01.75.75v1.5a.75.75 0 01-1.5 0v-1.5A.75.75 0 0110 15zM10 7a3 3 0 100 6 3 3 0 000-6zM15.657 5.404a.75.75 0 10-1.06-1.06l-1.061 1.06a.75.75 0 001.06 1.06l1.06-1.06zM6.464 14.596a.75.75 0 10-1.06-1.06l-1.06 1.06a.75.75 0 001.06 1.06l1.06-1.06zM18 10a.75.75 0 01-.75.75h-1.5a.75.75 0 010-1.5h1.5A.75.75 0 0118 10zM5 10a.75.75 0 01-.75.75h-1.5a.75.75 0 010-1.5h1.5A.75.75 0 015 10zM14.596 15.657a.75.75 0 001.06-1.06l-1.06-1.061a.75.75 0 10-1.06 1.06l1.06 1.061zM5.404 6.464a.75.75 0 001.06-1.06l-1.06-1.06a.75.75 0 10-1.06 1.06l1.06 1.06z" />
                      </svg>
                    ) : (
                      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-3.5 h-3.5">
                        <path fillRule="evenodd" d="M7.455 2.004a.75.75 0 01.26.77 7 7 0 009.958 7.967.75.75 0 011.067.853A8.5 8.5 0 116.647 1.921a.75.75 0 01.808.083z" clipRule="evenodd" />
                      </svg>
                    )}
                    {mode === 'light' ? 'Light' : 'Dark'}
                  </button>
                ))}
              </div>
            </div>
            <Link
              to="/contact-us"
              onClick={() => setOpen(false)}
              className="w-full flex items-center gap-2.5 px-4 py-3 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors border-b border-gray-100 dark:border-gray-800"
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 text-gray-500 dark:text-gray-400">
                <path d="M2.94 6.34A2.5 2.5 0 015.34 4.5h9.32a2.5 2.5 0 012.4 1.84L10 10.58 2.94 6.34z" />
                <path d="M2.75 7.66v5.59a2.5 2.5 0 002.5 2.5h9.5a2.5 2.5 0 002.5-2.5V7.66l-6.86 4.12a.75.75 0 01-.78 0L2.75 7.66z" />
              </svg>
              Contact Us
            </Link>

            {/* Logout */}
            <button
              type="button"
              onClick={onLogout}
              className="w-full flex items-center gap-2.5 px-4 py-3 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 text-gray-500 dark:text-gray-400">
                <path fillRule="evenodd" d="M3 4.25A2.25 2.25 0 015.25 2h5.5A2.25 2.25 0 0113 4.25v2a.75.75 0 01-1.5 0v-2a.75.75 0 00-.75-.75h-5.5a.75.75 0 00-.75.75v11.5c0 .414.336.75.75.75h5.5a.75.75 0 00.75-.75v-2a.75.75 0 011.5 0v2A2.25 2.25 0 0110.75 18h-5.5A2.25 2.25 0 013 15.75V4.25z" clipRule="evenodd" />
                <path fillRule="evenodd" d="M19 10a.75.75 0 00-.22-.53l-2.75-2.75a.75.75 0 10-1.06 1.06l1.47 1.47H8.75a.75.75 0 000 1.5h7.69l-1.47 1.47a.75.75 0 101.06 1.06l2.75-2.75A.75.75 0 0019 10z" clipRule="evenodd" />
              </svg>
              Logout
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      <motion.button
        type="button"
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.4, delay: 0.4 }}
        whileHover={{ scale: 1.05 }}
        whileTap={{ scale: 0.95 }}
        onClick={() => setOpen(v => !v)}
        title={user.name}
        className={`w-10 h-10 rounded-full bg-gradient-to-br ${agentColor} text-white text-sm font-semibold flex items-center justify-center shadow-md hover:shadow-lg transition-shadow ring-2 ring-white dark:ring-gray-900`}
      >
        {initials}
      </motion.button>
    </div>
  );
};

// ── Left Sidebar Rail (hidden on mobile) ──────────────────────────────
const SidebarRail = ({ onNewChat, user, onLogout, agentColor }) => (
  <aside className="hidden sm:flex w-16 sm:w-[68px] shrink-0 flex-col items-center py-4 bg-white dark:bg-[#23272e] border-r border-gray-200/70 dark:border-[#33373f] z-30">
    <motion.button
      type="button"
      onClick={onNewChat}
      whileHover={{ scale: 1.05 }}
      whileTap={{ scale: 0.95 }}
      title="New chat"
      className="group/new flex items-center justify-center w-11 h-11 rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 transition-colors"
    >
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
        <path d="M5.433 13.917l1.262-3.155A4 4 0 017.58 9.42l6.92-6.918a2.121 2.121 0 013 3l-6.92 6.918c-.383.383-.84.685-1.343.886l-3.154 1.262a.5.5 0 01-.65-.65z" />
        <path d="M3.5 5.75c0-.69.56-1.25 1.25-1.25H10A.75.75 0 0010 3H4.75A2.75 2.75 0 002 5.75v9.5A2.75 2.75 0 004.75 18h9.5A2.75 2.75 0 0017 15.25V10a.75.75 0 00-1.5 0v5.25c0 .69-.56 1.25-1.25 1.25h-9.5c-.69 0-1.25-.56-1.25-1.25v-9.5z" />
      </svg>
    </motion.button>

    <div className="flex-1" />

    <UserAvatarMenu user={user} onLogout={onLogout} agentColor={agentColor} />
  </aside>
);

const AgentWrapper = () => {
  const { agentType } = useParams();
  const { instance, accounts } = useMsal();
  const location = useLocation();
  const user = accounts[0] || {};
  const agentConfig = AGENTS[agentType];
  const { theme } = useTheme();

  if (!agentConfig) {
    return (
      <div className="h-screen bg-[#fafafa] text-gray-700 flex items-center justify-center text-2xl">
        Agent Not Found
      </div>
    );
  }

  const isAuthed = accounts.length > 0;
  const isPublicAgent = agentConfig.public === true;

  // Dark mode only applies in the authenticated chat surface; login stays light.
  // Public agents stay light because they are customer-facing.
  const effectiveTheme =
    !isPublicAgent && isAuthed && theme === "dark" ? "dark" : "light";

  // Dynamic browser tab title
  useEffect(() => {
    document.title = `${agentConfig.title}`;
    return () => {
      document.title = "SLTMobitel AI Agent";
    };
  }, [agentConfig.title]);

  // Toggle the `dark` class on <html> so Tailwind's class strategy applies
  // reliably across the entire tree.
  // Public LifeStore/Enterprise agents do not use authenticated dark mode.
  useEffect(() => {
    const root = document.documentElement;

    if (effectiveTheme === "dark") root.classList.add("dark");
    else root.classList.remove("dark");

    return () => {
      root.classList.remove("dark");
    };
  }, [effectiveTheme]);

  const handleLogin = () => {
    // 1. Set flags so the app knows this is a legitimate user action, not a back-button loop.
    sessionStorage.setItem("intentionalLogin", "true");
    sessionStorage.setItem("lastAgent", location.pathname);

    // 2. Use Redirect, NOT popup.
    instance.loginRedirect(loginRequest).catch((e) => console.error(e));
  };

  const handleLogout = () => {
    instance
      .logoutRedirect({
        postLogoutRedirectUri: window.location.origin + location.pathname,
      })
      .catch((e) => console.error(e));
  };

  const chatRef = useRef(null);
  const ambientBg = buildAmbientBackground(
    getAgentRGB(agentConfig.color),
    effectiveTheme
  );

  return (
    <div
      className="h-screen flex flex-row relative overflow-hidden text-gray-900 dark:text-gray-100"
      style={ambientBg}
    >
      {/* Premium film-grain overlay — adds subtle tactile depth */}
      <div
        aria-hidden="true"
        className="absolute inset-0 pointer-events-none mix-blend-overlay opacity-[0.035] z-0"
        style={{ backgroundImage: GRAIN_DATA_URL }}
      />

      {/* ── Left Sidebar: internal authenticated agents only ─────────── */}
      {!isPublicAgent && (
        <AuthenticatedTemplate>
          <SidebarRail
            onNewChat={() => chatRef.current?.clearChat()}
            user={user}
            onLogout={handleLogout}
            agentColor={agentConfig.color}
          />
        </AuthenticatedTemplate>
      )}

      {/* ── Main Column ───────────────────────────────── */}
      <div className="flex-1 flex flex-col min-h-0 min-w-0 relative">
        {/* ── Top Bar ────────────────────────────────────── */}
        <motion.header
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
          className="relative z-20 flex items-center gap-2 sm:gap-4 px-3 sm:px-14 py-3 sm:py-4"
        >
          {/* Mobile-only new chat button: internal authenticated agents only */}
          {!isPublicAgent && (
            <AuthenticatedTemplate>
              <motion.button
                type="button"
                onClick={() => chatRef.current?.clearChat()}
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.95 }}
                title="New chat"
                className="sm:hidden flex items-center justify-center w-9 h-9 rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-600 dark:text-gray-300 shrink-0"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  viewBox="0 0 20 20"
                  fill="currentColor"
                  className="w-5 h-5"
                >
                  <path d="M5.433 13.917l1.262-3.155A4 4 0 017.58 9.42l6.92-6.918a2.121 2.121 0 013 3l-6.92 6.918c-.383.383-.84.685-1.343.886l-3.154 1.262a.5.5 0 01-.65-.65z" />
                  <path d="M3.5 5.75c0-.69.56-1.25 1.25-1.25H10A.75.75 0 0010 3H4.75A2.75 2.75 0 002 5.75v9.5A2.75 2.75 0 004.75 18h9.5A2.75 2.75 0 0017 15.25V10a.75.75 0 00-1.5 0v5.25c0 .69-.56 1.25-1.25 1.25h-9.5c-.69 0-1.25-.56-1.25-1.25v-9.5z" />
                </svg>
              </motion.button>
            </AuthenticatedTemplate>
          )}

          {/* Agent identity: logo if provided, otherwise title text */}
          <div className="min-w-0 flex-1 flex flex-col">
            {agentConfig.logo ? (
              <img
                src={agentConfig.logo}
                alt={agentConfig.title}
                className="h-8 sm:h-10 w-auto self-start max-w-full object-contain"
              />
            ) : (
              <h1 className="text-lg sm:text-xl font-bold text-gray-950 dark:text-gray-100 tracking-tight leading-tight truncate">
                {agentConfig.title.replace(/^ASK /i, "Ask ")}
              </h1>
            )}
          </div>

          {/* Right cluster: SLT logo + mobile avatar for internal authenticated agents */}
          <div className="flex items-center gap-2 sm:gap-0 shrink-0">
            {agentConfig.externalAppUrl && (
              <a
                href={agentConfig.externalAppUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center justify-center min-h-9 mr-1 sm:mr-3 px-3 sm:px-4 rounded-full border border-cyan-700/20 bg-white dark:bg-gray-800 text-cyan-800 dark:text-cyan-200 text-xs font-semibold shadow-sm hover:shadow-md hover:border-cyan-600/40 transition-all"
              >
                <span className="sm:hidden">ABP</span>
                <span className="hidden sm:inline">Open ABP Agent</span>
              </a>
            )}
            <img
              src={sltLogo}
              alt="SLTMobitel"
              className="h-7 sm:h-10 w-auto drop-shadow-sm"
            />

            {!isPublicAgent && (
              <AuthenticatedTemplate>
                <div className="sm:hidden">
                  <UserAvatarMenu
                    user={user}
                    onLogout={handleLogout}
                    agentColor={agentConfig.color}
                    placement="downLeft"
                  />
                </div>
              </AuthenticatedTemplate>
            )}
          </div>
        </motion.header>

        {/* ── Content Area ───────────────────────────────── */}
        <AnimatePresence mode="wait">
          {isPublicAgent ? (
            /*
              Public customer-facing agents:
              - Ask LifeStore
              - Ask Enterprise

              These bypass Microsoft authentication completely.
            */
            <motion.div
              key="public-chat-content"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.4 }}
              className="flex-1 flex flex-col min-h-0 z-10"
            >
              <ChatInterface ref={chatRef} agentConfig={agentConfig} />
            </motion.div>
          ) : (
            /*
              Internal agents:
              - Workmate AI
              - HR
              - Finance
              - Admin
              - IT
              - CIA
              - Process
              - Network
              - Legal
              - Marketing
              - HR SLM

              These still require Microsoft authentication.
            */
            <>
              {/* ── Unauthenticated Login View: internal agents only ─────────────── */}
              <UnauthenticatedTemplate key="unauth">
                <motion.div
                  key="unauth-content"
                  initial="hidden"
                  animate="visible"
                  exit={{ opacity: 0, y: -20 }}
                  variants={containerVariants}
                  className="flex-1 flex flex-col items-center justify-center px-4 z-10"
                >
                  {/* Title */}
                  <motion.h1
                    variants={itemVariants}
                    className="text-4xl sm:text-5xl lg:text-6xl font-bold text-gray-900 tracking-tight text-center"
                  >
                    {agentConfig.title.replace(/^ASK /i, "Ask ")}
                  </motion.h1>

                  {/* Login card */}
                  <motion.div
                    variants={cardVariants}
                    className="relative w-full max-w-md mt-8"
                  >
                    <div
                      className={`absolute -inset-1 blur-2xl opacity-20 bg-gradient-to-br ${agentConfig.color} rounded-[2.5rem] -z-10 pointer-events-none`}
                    />

                    <div className="relative bg-white w-full rounded-3xl p-8 sm:p-10 flex flex-col items-center justify-center border border-gray-200/80 shadow-[0_20px_50px_-15px_rgba(0,0,0,0.12)]">
                      <div
                        className={`w-14 h-14 rounded-2xl bg-gradient-to-br ${agentConfig.color} flex items-center justify-center mb-6 shadow-md`}
                      >
                        <svg
                          xmlns="http://www.w3.org/2000/svg"
                          fill="none"
                          viewBox="0 0 24 24"
                          strokeWidth={1.8}
                          stroke="currentColor"
                          className="w-7 h-7 text-white"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z"
                          />
                        </svg>
                      </div>

                      <p className="text-gray-700 text-[0.8rem] font-bold mb-2 tracking-[0.2em] uppercase">
                        Secure Identity
                      </p>

                      <p className="text-gray-500 text-sm mb-8 text-center font-light">
                        Authenticate securely through your corporate Microsoft
                        tunnel
                      </p>

                      <motion.button
                        whileHover={{ scale: 1.03 }}
                        whileTap={{ scale: 0.97 }}
                        onClick={handleLogin}
                        className="relative flex items-center justify-center gap-3 w-full px-8 py-3.5 rounded-full bg-gray-900 hover:bg-gray-800 text-white font-medium shadow-md transition-colors"
                      >
                        <MicrosoftIcon />
                        Login with Microsoft
                      </motion.button>
                    </div>
                  </motion.div>

                  {/* Bottom branding footer */}
                  <motion.div
                    variants={itemVariants}
                    className="mt-12 flex items-center justify-center gap-1.5 select-none cursor-default"
                  >
                    <span className="text-[0.65rem] uppercase tracking-widest font-semibold text-gray-500">
                      Powered by
                    </span>
                    <img
                      src={embryoLogo}
                      alt="Embryo Logo"
                      className="h-[20px] w-auto object-contain"
                    />
                  </motion.div>
                </motion.div>
              </UnauthenticatedTemplate>

              {/* ── Authenticated Chat View: internal agents only ────────────────────── */}
              <AuthenticatedTemplate key="auth">
                <motion.div
                  key="auth-content"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.4 }}
                  className="flex-1 flex flex-col min-h-0 z-10"
                >
                  <ChatInterface ref={chatRef} agentConfig={agentConfig} />
                </motion.div>
              </AuthenticatedTemplate>
            </>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
};

// The Main App Component with the Interceptor
function App() {
  const [isInitialized, setIsInitialized] = useState(false);

  useEffect(() => {
    msalInstance.initialize().then(() => {
      msalInstance.handleRedirectPromise().then((response) => {
        // Grab the agent the user originally wanted to log into
        const targetRoute = sessionStorage.getItem('lastAgent') || '/';

        if (response) {
          // 1. Legitimate, intentional login. 
          sessionStorage.removeItem('intentionalLogin');
          window.location.replace(targetRoute);
          return;
        }

        // 2. THE KILL SWITCH for the back-button loop
        // If response is null, but we are stuck on /auth/callback, it means Microsoft
        // automatically bounced us here from a Back-button press. 
        if (window.location.pathname === '/auth/callback') {
          // Wipe the local session storage so React forgets the authenticated state
          sessionStorage.clear();

          // Send you cleanly to the unauthenticated view
          window.location.replace(targetRoute);
          return;
        }

        setIsInitialized(true);
      }).catch(e => {
        console.error("MSAL Auth Error:", e);
        setIsInitialized(true);
      });
    });
  }, []);

  if (!isInitialized) {
    return (
      <div className="h-screen bg-[#fafafa] flex flex-col items-center justify-center gap-4">
        <div className="flex items-center gap-2 mb-2">
          <div className="w-2 h-2 rounded-full bg-gray-400 animate-pulse" />
          <div className="w-2 h-2 rounded-full bg-gray-400 animate-pulse [animation-delay:200ms]" />
          <div className="w-2 h-2 rounded-full bg-gray-400 animate-pulse [animation-delay:400ms]" />
        </div>
        <span className="text-gray-500 text-sm tracking-widest uppercase font-medium">
          Initializing Secure Environment
        </span>
      </div>
    );
  }

  return (
    <MsalProvider instance={msalInstance}>
      <ThemeProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<RootRedirect />} />

          {/* We swapped the redirect for a holding screen so it doesn't push you to Ask HR! */}
          <Route path="/auth/callback" element={
            <div className="h-screen bg-[#fafafa] flex flex-col items-center justify-center gap-4">
              <div className="flex items-center gap-2 mb-2">
                <div className="w-2 h-2 rounded-full bg-gray-400 animate-pulse" />
                <div className="w-2 h-2 rounded-full bg-gray-400 animate-pulse [animation-delay:200ms]" />
                <div className="w-2 h-2 rounded-full bg-gray-400 animate-pulse [animation-delay:400ms]" />
              </div>
              <span className="text-gray-500 text-sm tracking-widest uppercase font-medium">
                Verifying Identity
              </span>
            </div>
          } />

          {/* Public iframe routes for embedding Ask LifeStore and Ask Enterprise */}
          <Route path="/:agentKey/iframe" element={<IframeChatPage />} />

          <Route element={<AdminRoute />}>
            <Route path="/admin" element={<AdminDashboard />} />
            <Route path="/admin/chats" element={<ChatBrowser />} />
            <Route path="/admin/ingestion" element={<IngestionPanel />} />
            <Route path="/admin/feedback" element={<FeedbackPanel />} />
          </Route>

          <Route path="/rainbowpages" element={<RainbowPages />} />
          <Route path="/contact-us" element={<ContactUsPage />} />

          <Route path="/:agentType" element={<AgentWrapper />} />
        </Routes>
      </BrowserRouter>
      </ThemeProvider>
    </MsalProvider>
  );
}

export default App;
