// frontend/src/auth/lifestoreAuth.jsx
//
// Lightweight Google Sign-In for the public Ask LifeStore agent ONLY.
// This is completely separate from the Microsoft (MSAL) login used by the
// internal agents — the two never interact.
//
// Flow: <GoogleLogin> button returns a Google ID token (a JWT). We decode its
// claims on the client (email, name, picture, sub) and keep a small session in
// localStorage so a page refresh stays signed in. The raw credential (JWT) is
// also kept so backend calls can send it as a Bearer token ("decode & trust").

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { GoogleOAuthProvider } from "@react-oauth/google";

const STORAGE_KEY = "lifestore.google.session";
const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || "";

const LifestoreAuthCtx = createContext(null);

// Decode a JWT payload without verifying the signature (client-side display only).
function decodeJwt(token) {
  try {
    const payload = token.split(".")[1];
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(decodeURIComponent(escape(json)));
  } catch {
    return null;
  }
}

function loadSession() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const session = JSON.parse(raw);
    // Drop expired Google tokens (exp is in seconds).
    if (session?.user?.exp && session.user.exp * 1000 < Date.now()) return null;
    return session;
  } catch {
    return null;
  }
}

export function LifestoreAuthProvider({ children }) {
  const [session, setSession] = useState(() => loadSession());

  const signIn = useCallback((credential) => {
    const claims = decodeJwt(credential);
    if (!claims) return;
    const user = {
      sub: claims.sub,
      email: claims.email || "",
      name: claims.name || "",
      picture: claims.picture || null,
      exp: claims.exp || null,
    };
    const next = { credential, user };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    setSession(next);
  }, []);

  // Establish a session from an already-resolved profile (e.g. Google userinfo,
  // or later a backend email/password or WSO2 response). `credential` is the
  // raw token to send to the backend; `exp` is a unix-seconds expiry (optional).
  const signInWithProfile = useCallback((profile, credential = null, exp = null) => {
    if (!profile?.email && !profile?.sub) return;
    const user = {
      sub: profile.sub || null,
      email: profile.email || "",
      name: profile.name || "",
      picture: profile.picture || null,
      exp: exp || null,
    };
    const next = { credential, user };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    setSession(next);
  }, []);

  const signOut = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY);
    setSession(null);
  }, []);

  // Auto-expire the session in-memory when the token's exp passes.
  useEffect(() => {
    if (!session?.user?.exp) return;
    const ms = session.user.exp * 1000 - Date.now();
    if (ms <= 0) {
      signOut();
      return;
    }
    const t = setTimeout(signOut, ms);
    return () => clearTimeout(t);
  }, [session, signOut]);

  const value = useMemo(
    () => ({
      isConfigured: !!GOOGLE_CLIENT_ID,
      isAuthenticated: !!session?.user,
      user: session?.user || null,
      credential: session?.credential || null,
      signIn,
      signInWithProfile,
      signOut,
    }),
    [session, signIn, signInWithProfile, signOut]
  );

  return (
    <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>
      <LifestoreAuthCtx.Provider value={value}>{children}</LifestoreAuthCtx.Provider>
    </GoogleOAuthProvider>
  );
}

export function useLifestoreAuth() {
  const ctx = useContext(LifestoreAuthCtx);
  if (!ctx) throw new Error("useLifestoreAuth must be used inside <LifestoreAuthProvider>");
  return ctx;
}
