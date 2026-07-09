// frontend/src/components/LifestoreLogin.jsx
//
// Sign-in gate for the public Ask LifeStore agent. Offers three methods:
//   1. Google Sign-In        — fully working (see lifestoreAuth.jsx)
//   2. Email + password      — FRONTEND ONLY for now (backend wired later)
//   3. WSO2 Identity Provider — FRONTEND ONLY for now (backend wired later)

import { useState } from "react";
import { motion } from "framer-motion";
import { useGoogleLogin } from "@react-oauth/google";
import { useAuthContext } from "@asgardeo/auth-react";
import { useLifestoreAuth } from "../auth/lifestoreAuth";
import { isWso2Configured } from "../auth/wso2Config";

// Official multicolour Google "G".
const GoogleIcon = () => (
  <svg className="w-5 h-5" viewBox="0 0 48 48" aria-hidden="true">
    <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
    <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
    <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
    <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
  </svg>
);

export default function LifestoreLogin({ agentConfig }) {
  const { signInWithProfile, isConfigured } = useLifestoreAuth();
  const { signIn: wso2SignIn } = useAuthContext();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [notice, setNotice] = useState("");

  const isValidEmail = (v) => /\S+@\S+\.\S+/.test(v.trim());

  // ── Google (custom button via the token flow) ───────────────────────
  const googleLogin = useGoogleLogin({
    scope: "openid email profile",
    onSuccess: async (tokenResponse) => {
      setNotice("");
      try {
        const res = await fetch("https://www.googleapis.com/oauth2/v3/userinfo", {
          headers: { Authorization: `Bearer ${tokenResponse.access_token}` },
        });
        if (!res.ok) throw new Error("userinfo failed");
        const profile = await res.json(); // { sub, email, name, picture }
        const exp = tokenResponse.expires_in
          ? Math.floor(Date.now() / 1000) + Number(tokenResponse.expires_in)
          : null;
        signInWithProfile(profile, tokenResponse.access_token, exp);
      } catch {
        setNotice("Google Sign-In failed. Please try again.");
      }
    },
    onError: () => setNotice("Google Sign-In failed. Please try again."),
  });

  // ── Email + password ────────────────────────────────────────────────
  // FRONTEND STUB: no real authentication yet. When the backend endpoint is
  // ready, replace the body below with a fetch to it and, on success, call the
  // auth context to establish the session.
  const handleEmailPassword = async (e) => {
    e.preventDefault();
    setNotice("");

    if (!isValidEmail(email) || !password) {
      setNotice("Enter a valid email and password.");
      return;
    }

    // TODO(backend): POST { email, password } to the LifeStore auth endpoint,
    // then persist the returned session (e.g. add a signInWithSession() helper
    // to lifestoreAuth and call it here).
    setNotice("Email sign-in isn’t connected yet — coming soon.");
  };

  // ── WSO2 Identity Provider ──────────────────────────────────────────
  // Triggers the WSO2 (Asgardeo) OIDC redirect. On return to /asklifestore,
  // Wso2Bridge decodes the token and establishes the LifeStore session.
  const handleWso2 = () => {
    setNotice("");
    if (!isWso2Configured) {
      setNotice("WSO2 sign-in is not configured yet.");
      return;
    }
    // prompt: "login" forces WSO2 to show its login form instead of silently
    // reusing an existing SSO session — so customers can choose their account.
    wso2SignIn({ prompt: "login" }).catch(() =>
      setNotice("Could not start WSO2 sign-in. Please try again.")
    );
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
      className="flex-1 flex flex-col items-center justify-center px-4 py-8 z-10 overflow-y-auto"
    >
      <h1 className="text-3xl sm:text-4xl lg:text-5xl font-bold text-gray-900 tracking-tight text-center">
        {agentConfig.title.replace(/^ASK /i, "Ask ")}
      </h1>

      <div className="relative w-full max-w-md mt-6">
        <div
          className={`absolute -inset-1 blur-2xl opacity-20 bg-gradient-to-br ${agentConfig.color} rounded-[2.5rem] -z-10 pointer-events-none`}
        />

        <div className="relative bg-white w-full rounded-3xl p-7 sm:p-9 flex flex-col items-center border border-gray-200/80 shadow-[0_20px_50px_-15px_rgba(0,0,0,0.12)]">
          <div
            className={`w-12 h-12 rounded-2xl bg-gradient-to-br ${agentConfig.color} flex items-center justify-center mb-4 shadow-md`}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth={1.8}
              stroke="currentColor"
              className="w-6 h-6 text-white"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M2.25 3h1.386c.51 0 .955.343 1.087.836l.383 1.437M7.5 14.25a3 3 0 00-3 3h15.75m-12.75-3h11.218c1.121-2.3 2.1-4.684 2.924-7.138a60.114 60.114 0 00-16.536-1.84M7.5 14.25L5.106 5.272M6 20.25a.75.75 0 11-1.5 0 .75.75 0 011.5 0zm12.75 0a.75.75 0 11-1.5 0 .75.75 0 011.5 0z"
              />
            </svg>
          </div>

          <p className="text-gray-700 text-[0.8rem] font-bold mb-1 tracking-[0.2em] uppercase">
            Customer Sign In
          </p>
          <p className="text-gray-500 text-sm mb-6 text-center font-light">
            Sign in to browse products and place orders.
          </p>

          {/* ── Email + password (frontend stub) ───────────────────────── */}
          <form onSubmit={handleEmailPassword} className="w-full space-y-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@email.com"
                className="w-full border border-gray-300 rounded-lg p-2.5 text-sm placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-orange-300"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="w-full border border-gray-300 rounded-lg p-2.5 text-sm placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-orange-300"
              />
            </div>
            <button
              type="submit"
              className={`w-full rounded-full py-2.5 text-sm font-semibold text-white bg-gradient-to-r ${agentConfig.color} shadow-md hover:shadow-lg transition-shadow`}
            >
              Sign in
            </button>
          </form>

          {notice && (
            <p className="mt-3 text-xs text-center text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2 w-full">
              {notice}
            </p>
          )}

          {/* ── Divider ────────────────────────────────────────────────── */}
          <div className="my-5 flex items-center gap-3 w-full">
            <div className="h-px flex-1 bg-gray-200" />
            <span className="text-[0.7rem] font-medium text-gray-400 uppercase tracking-wider">or</span>
            <div className="h-px flex-1 bg-gray-200" />
          </div>

          {/* ── Social / IdP buttons (identical styling) ───────────────── */}
          <div className="w-full space-y-3">
            <button
              type="button"
              onClick={() => (isConfigured ? googleLogin() : setNotice("Google Sign-In is not configured (missing VITE_GOOGLE_CLIENT_ID)."))}
              className="relative w-full h-11 flex items-center justify-center rounded-full text-sm font-semibold text-gray-800 bg-white border border-gray-300 hover:bg-gray-50 transition-colors shadow-sm"
            >
              <span className="absolute left-4 flex items-center">
                <GoogleIcon />
              </span>
              Continue with Google
            </button>

            <button
              type="button"
              onClick={handleWso2}
              className="relative w-full h-11 flex items-center justify-center rounded-full text-sm font-semibold text-gray-800 bg-white border border-gray-300 hover:bg-gray-50 transition-colors shadow-sm"
            >
              <span className="absolute left-4 inline-flex h-5 w-5 items-center justify-center rounded bg-orange-500 text-white text-[11px] font-bold">
                W
              </span>
              Continue with WSO2
            </button>
          </div>
        </div>
      </div>

      <p className="mt-6 text-[0.65rem] uppercase tracking-widest font-semibold text-gray-400">
        Secured sign in
      </p>
    </motion.div>
  );
}
