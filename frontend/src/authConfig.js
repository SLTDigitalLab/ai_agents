import { BrowserCacheLocation } from "@azure/msal-browser";

const isNativePlatform = typeof window !== "undefined" && Boolean(window?.Capacitor?.isNativePlatform?.());

const trimTrailingSlash = (value) => value?.replace(/\/+$/, "");

const appBaseUrl = trimTrailingSlash(
    isNativePlatform
        ? import.meta.env.VITE_CAPACITOR_APP_BASE_URL
        : import.meta.env.VITE_APP_BASE_URL
) || (typeof window !== "undefined" ? window.location.origin : "");

const redirectUri = `${appBaseUrl}/auth/callback`;
const workmateRoute = `${appBaseUrl}/workmateai`;

export const authFlowStorage = isNativePlatform ? window.localStorage : (typeof window !== "undefined" ? window.sessionStorage : null);

export const msalConfig = {
    auth: {
        clientId: import.meta.env.VITE_MSAL_CLIENT_ID,
        authority: import.meta.env.VITE_MSAL_AUTHORITY,

        // Same redirect URI for web and Capacitor local testing
        redirectUri,

        // Important: we manually control where to go after callback
        navigateToLoginRequestUrl: false,

        postLogoutRedirectUri: workmateRoute,
    },

    cache: {
        // Web keeps old behavior: sessionStorage
        // Capacitor Android uses localStorage so auth cache survives full-page redirect
        cacheLocation: isNativePlatform
            ? BrowserCacheLocation.LocalStorage
            : BrowserCacheLocation.SessionStorage,

        // Important for Capacitor redirect flow:
        // MSAL temporary transaction data should also survive the redirect in mobile WebView
        temporaryCacheLocation: isNativePlatform
            ? BrowserCacheLocation.LocalStorage
            : BrowserCacheLocation.SessionStorage,

        storeAuthStateInCookie: false,
    },
};

export const loginRequest = {
    scopes: ["User.Read"],
    prompt: "select_account",

    // Keep request redirect explicit
    redirectUri,

    // After login start, MSAL knows original app page
    redirectStartPage: workmateRoute,
};
