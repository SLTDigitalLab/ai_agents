import React, { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, useParams, useLocation, Navigate } from 'react-router-dom';
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
import { motion, AnimatePresence } from 'framer-motion';
import sltLogo from './assets/slt-mobitel-logo.png';

// Initialize MSAL outside the components
const msalInstance = new PublicClientApplication(msalConfig);

const RootRedirect = () => {
  const { inProgress } = useMsal();
  if (inProgress !== InteractionStatus.None) return null;
  return <Navigate to="/askslt" replace />;
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

const AgentWrapper = () => {
  const { agentType } = useParams();
  const { instance, accounts } = useMsal();
  const location = useLocation();
  const user = accounts[0] || {};
  const agentConfig = AGENTS[agentType];

  if (!agentConfig) {
    return (
      <div className="h-screen bg-slate-900 text-white flex items-center justify-center text-2xl">
        Agent Not Found
      </div>
    );
  }

  // Dynamic browser tab title
  useEffect(() => {
    document.title = `${agentConfig.title}`;
    return () => { document.title = 'SLTMobitel AI Agent'; };
  }, [agentConfig.title]);

  const handleLogin = () => {
    // 1. Set flags so the app knows this is a legitimate user action, not a back-button loop
    sessionStorage.setItem('intentionalLogin', 'true');
    sessionStorage.setItem('lastAgent', location.pathname);

    // 2. Use Redirect, NOT popup
    instance.loginRedirect(loginRequest).catch(e => console.error(e));
  };

  const handleLogout = () => {
    instance.logoutRedirect({
      postLogoutRedirectUri: window.location.origin + location.pathname
    }).catch(e => console.error(e));
  };

  return (
    <div className="h-screen flex flex-col bg-[#0b0c14] relative overflow-hidden">

      {/* ── Premium static background mesh ────────────────────────── */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none z-0">

        {/* Massive Ambient Center Wash (Fills the middle black gap seamlessly) */}
        <div
          className={`absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[140vw] h-[140vh] rounded-[100%] bg-gradient-to-b ${agentConfig.color} opacity-50 blur-[150px] mix-blend-normal`}
        />

        {/* Vibrant Top-Left Orb */}
        <div
          className={`absolute -top-[10%] -left-[10%] w-[65vw] h-[65vw] rounded-full bg-gradient-to-br ${agentConfig.color} opacity-90 blur-[120px] mix-blend-normal`}
        />

        {/* Deep Bottom-Right Orb */}
        <div
          className={`absolute -bottom-[10%] -right-[10%] w-[55vw] h-[55vw] rounded-full bg-gradient-to-tl ${agentConfig.color} opacity-70 blur-[120px] mix-blend-normal`}
        />

        {/* Enhanced Anti-Banding Noise Texture (Dithering) */}
        <div className="absolute inset-0 z-0 opacity-[0.05] pointer-events-none mix-blend-overlay" style={{ backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.75' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)'/%3E%3C/svg%3E")` }}></div>
      </div>

      {/* ── Floating Frosted-Glass Navbar ────────────────── */}
      <motion.nav
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
        className="glass-nav relative mx-4 sm:mx-8 mt-4 px-6 sm:px-8 py-3 flex justify-between items-center z-20 rounded-2xl border border-white/10 shadow-[0_20px_40px_-10px_rgba(0,0,0,0.3)]"
      >
        {/* Subtle Liquid Glass Neon Underglow */}
        <div className={`absolute inset-0 rounded-2xl bg-gradient-to-r ${agentConfig.color} opacity-20 blur-lg -z-10 transition-colors duration-700 pointer-events-none`} />

        <div className="flex items-center gap-2 relative z-10">
          <img src={sltLogo} alt="SLTMobitel" className="h-8 sm:h-10 w-auto" />
        </div>

        <div className="flex items-center gap-4">
          <UnauthenticatedTemplate>
            <motion.button
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.97 }}
              onClick={handleLogin}
              className="bg-white/10 hover:bg-white/20 text-white px-4 py-2 rounded-xl text-sm font-medium transition-colors border border-white/10"
            >
              Login
            </motion.button>
          </UnauthenticatedTemplate>

          <AuthenticatedTemplate>
            <div className="flex items-center gap-3 sm:gap-4">
              <motion.span
                initial={{ opacity: 0, x: 10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.5, delay: 0.3 }}
                className="text-white/90 text-sm font-medium hidden sm:inline"
              >
                Hi, {user.name}{' '}
                <span className="text-white/40 text-xs">({user.username})</span>
              </motion.span>
              <motion.button
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.97 }}
                onClick={handleLogout}
                className="border border-white/20 text-white/80 hover:bg-white/10 hover:border-white/40 hover:text-white px-4 py-2 rounded-xl text-sm font-medium transition-all duration-300"
              >
                Logout
              </motion.button>
            </div>
          </AuthenticatedTemplate>
        </div>
      </motion.nav>

      {/* ── Content Area with AnimatePresence ────────────── */}
      <AnimatePresence mode="wait">
        {/* ── Unauthenticated Login View ─────────────────── */}
        <UnauthenticatedTemplate key="unauth">
          <motion.div
            key="unauth-content"
            initial="hidden"
            animate="visible"
            exit={{ opacity: 0, y: -20 }}
            variants={containerVariants}
            className="flex-1 flex flex-col items-center justify-center px-4 z-10 -mt-6"
          >


            {/* Title */}
            <motion.h1
              variants={itemVariants}
              className="text-5xl sm:text-6xl lg:text-7xl font-extrabold text-white tracking-tight drop-shadow-lg uppercase text-center"
            >
              {agentConfig.title}
            </motion.h1>

            {/* Subtitle */}
            <motion.p
              variants={itemVariants}
              className="text-white/70 text-base sm:text-lg max-w-2xl mx-auto font-light text-center mt-3 mb-8"
            >
              {agentConfig.subtitle}
            </motion.p>

            {/* ── LIQUID GLASS LOGIN CARD ── */}
            <motion.div
              variants={cardVariants}
              className="relative w-full max-w-lg mt-4"
            >
              {/* Surrounding Neon Aura (Liquid Glass ambient spread behind the frosted glass) */}
              <div className={`absolute -inset-1 blur-2xl opacity-40 bg-gradient-to-br ${agentConfig.color} rounded-[2.5rem] -z-10 transition-colors duration-700`} />

              {/* Main Thick Glass Body */}
              <div className="relative glass-card-bright w-full rounded-[2rem] p-8 sm:p-10 flex flex-col items-center justify-center border border-white/20 shadow-[0_30px_60px_-15px_rgba(0,0,0,0.6),inset_0_1px_1px_rgba(255,255,255,0.3)] overflow-hidden">

                {/* Internal Diagonal Glare (Simulates thick polished glass Edge) */}
                <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/50 to-transparent" />

                <div className="w-16 h-16 rounded-2xl bg-white/[0.04] border border-white/10 flex items-center justify-center mb-6 shadow-inner relative overflow-hidden group">
                  <div className={`absolute inset-0 bg-gradient-to-b ${agentConfig.color} opacity-20 group-hover:opacity-40 transition-opacity`} />
                  <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="w-8 h-8 text-white relative z-10 drop-shadow-[0_0_8px_rgba(255,255,255,0.5)]">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z" />
                  </svg>
                </div>

                <p className="text-white/70 text-[0.8rem] font-bold mb-2 tracking-[0.2em] uppercase">
                  Secure Identity
                </p>
                <p className="text-white/40 text-sm mb-9 text-center font-light">
                  Authenticate securely through your corporate Microsoft tunnel
                </p>

                {/* LIQUID GLASS MICROSOFT BUTTON */}
                <motion.button
                  whileHover={{ scale: 1.04 }}
                  whileTap={{ scale: 0.97 }}
                  onClick={handleLogin}
                  className="group relative flex items-center justify-center w-full px-8 py-4 rounded-full transition-all duration-300"
                >
                  {/* Neon Glow Layer that brightens aggressively on hover */}
                  <div className={`absolute inset-0 rounded-full bg-gradient-to-r ${agentConfig.color} blur-xl opacity-40 group-hover:opacity-100 transition-opacity duration-500`} />

                  {/* The actual polished dark glass button surface */}
                  <div className="absolute inset-0 rounded-full bg-[#0b0c14]/30 backdrop-blur-xl border border-white/20 shadow-[inset_0_1px_1px_rgba(255,255,255,0.4)] group-hover:bg-white/[0.08] transition-all duration-300" />

                  <span className="relative z-10 flex items-center gap-3 font-semibold text-white tracking-wide">
                    <MicrosoftIcon />
                    Login with Microsoft
                  </span>
                </motion.button>
              </div>
            </motion.div>
          </motion.div>
        </UnauthenticatedTemplate>

        {/* ── Authenticated Chat View ────────────────────── */}
        <AuthenticatedTemplate key="auth">
          <motion.div
            key="auth-content"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.5 }}
            className="flex-1 flex flex-col min-h-0 z-10"
          >
            <ChatInterface agentConfig={agentConfig} />
          </motion.div>
        </AuthenticatedTemplate>
      </AnimatePresence>
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
      <div className="h-screen bg-[#0f172a] flex flex-col items-center justify-center gap-4">
        <div className="flex items-center gap-2 mb-2">
          <div className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
          <div className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse [animation-delay:200ms]" />
          <div className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse [animation-delay:400ms]" />
        </div>
        <span className="text-white/60 text-sm tracking-widest uppercase font-medium">
          Initializing Secure Environment
        </span>
      </div>
    );
  }

  return (
    <MsalProvider instance={msalInstance}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<RootRedirect />} />

          {/* We swapped the redirect for a holding screen so it doesn't push you to Ask HR! */}
          <Route path="/auth/callback" element={
            <div className="h-screen bg-[#0f172a] flex flex-col items-center justify-center gap-4">
              <div className="flex items-center gap-2 mb-2">
                <div className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
                <div className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse [animation-delay:200ms]" />
                <div className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse [animation-delay:400ms]" />
              </div>
              <span className="text-white/60 text-sm tracking-widest uppercase font-medium">
                Verifying Identity
              </span>
            </div>
          } />

          <Route element={<AdminRoute />}>
            <Route path="/admin" element={<AdminDashboard />} />
            <Route path="/admin/chats" element={<ChatBrowser />} />
            <Route path="/admin/ingestion" element={<IngestionPanel />} />
            <Route path="/admin/feedback" element={<FeedbackPanel />} />
          </Route>
          <Route path="/:agentType" element={<AgentWrapper />} />
        </Routes>
      </BrowserRouter>
    </MsalProvider>
  );
}

export default App;