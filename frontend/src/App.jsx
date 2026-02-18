import React, { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, useParams, useLocation, Navigate } from 'react-router-dom';
import { MsalProvider, AuthenticatedTemplate, UnauthenticatedTemplate, useMsal } from "@azure/msal-react";
import { PublicClientApplication, InteractionStatus } from "@azure/msal-browser";
import { msalConfig, loginRequest } from './authConfig';
import { AGENTS } from './config/agents';
import ChatInterface from './components/ChatInterface';
import { motion, AnimatePresence } from 'framer-motion';
import sltLogo from './assets/slt-mobitel-logo.png';

// Initialize MSAL outside the components
const msalInstance = new PublicClientApplication(msalConfig);

const RootRedirect = () => {
  const { inProgress } = useMsal();
  if (inProgress !== InteractionStatus.None) return null;
  return <Navigate to="/askhr" replace />;
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
    <div className={`h-screen flex flex-col bg-gradient-to-br ${agentConfig.color} breathing-bg relative overflow-hidden`}>

      {/* ── Ambient floating orbs ────────────────────────── */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none z-0">
        <div className="absolute top-[-10%] left-[-5%] w-[500px] h-[500px] rounded-full bg-white/[0.03] blur-3xl animate-float-slow" />
        <div className="absolute bottom-[-15%] right-[-10%] w-[600px] h-[600px] rounded-full bg-white/[0.04] blur-3xl animate-float-slower" />
        <div className="absolute top-[30%] right-[15%] w-[300px] h-[300px] rounded-full bg-cyan-400/[0.03] blur-3xl animate-pulse-glow" />
      </div>

      {/* ── Floating Frosted-Glass Navbar ────────────────── */}
      <motion.nav
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
        className="glass-nav mx-4 sm:mx-8 mt-4 px-6 sm:px-8 py-3 flex justify-between items-center z-20 rounded-2xl"
      >
        <div className="flex items-center gap-2">
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

            {/* Glassmorphism Login Card */}
            <motion.div
              variants={cardVariants}
              className="glass-card-bright w-full max-w-lg rounded-3xl p-8 sm:p-10 flex flex-col items-center justify-center"
            >
              <div className="w-14 h-14 rounded-2xl bg-white/[0.06] border border-white/10 flex items-center justify-center mb-5">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.2} stroke="currentColor" className="w-7 h-7 text-cyan-400">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z" />
                </svg>
              </div>

              <p className="text-white/50 text-sm font-medium mb-1 tracking-wide uppercase">
                Secure Access
              </p>
              <p className="text-white/30 text-sm mb-7 text-center">
                Authenticate with your Microsoft account to continue
              </p>

              <motion.button
                whileHover={{ scale: 1.04 }}
                whileTap={{ scale: 0.98 }}
                onClick={handleLogin}
                className={`glow-sweep-btn group relative flex items-center gap-3 px-7 py-3.5 rounded-xl text-white font-semibold text-base shadow-lg bg-gradient-to-r ${agentConfig.color} hover:shadow-2xl transition-all duration-300 border border-white/10`}
              >
                <span className="relative z-10 flex items-center gap-3">
                  <MicrosoftIcon />
                  Login with Microsoft
                </span>
              </motion.button>
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

      {/* ── Footer ──────────────────────────────────────── */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 1.2, duration: 0.8 }}
        className="fixed bottom-0 left-0 right-0 text-center py-3 text-white/30 text-xs font-medium tracking-widest uppercase z-10 pointer-events-none"
      >
        Powered by The Embryo Innovation Centre
      </motion.div>
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

          <Route path="/:agentType" element={<AgentWrapper />} />
        </Routes>
      </BrowserRouter>
    </MsalProvider>
  );
}

export default App;