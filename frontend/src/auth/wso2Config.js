// frontend/src/auth/wso2Config.js
//
// WSO2 Identity Server (Asgardeo SDK) config for the Ask LifeStore customer
// login. The Client ID of a Single-Page Application is public (not a secret),
// so — like the LMS project — it is committed with an env override.
//
// App: "Ask LifeStore" under the carbon.super (Root) organization.

const CLIENT_ID = import.meta.env.VITE_WSO2_CLIENT_ID || "wJBITDksJ_Rb0gOybCS075Gfb4ca";
const BASE_URL = import.meta.env.VITE_WSO2_BASE_URL || "https://idp.sltdigitallab.lk";

// LifeStore lives at /asklifestore, which is registered as the authorized
// redirect URL in WSO2. Deriving it from the current origin makes the same
// build work on localhost:3000 and on the production domain.
const origin = typeof window !== "undefined" ? window.location.origin : "";
const lifestoreUrl = `${origin}/asklifestore`;

export const wso2Config = {
  clientID: CLIENT_ID,
  baseUrl: BASE_URL,
  signInRedirectURL: lifestoreUrl,
  signOutRedirectURL: lifestoreUrl,
  scope: ["openid", "email", "profile"],
};

export const isWso2Configured = !!CLIENT_ID && !!BASE_URL;
