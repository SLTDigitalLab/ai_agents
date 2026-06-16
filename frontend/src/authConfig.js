export const msalConfig = {
    auth: {
        clientId: import.meta.env.VITE_MSAL_CLIENT_ID,
        authority: import.meta.env.VITE_MSAL_AUTHORITY,
        redirectUri: window.location.origin + '/auth/callback',
        navigateToLoginRequestUrl: false
    },
    cache: {
        cacheLocation: "sessionStorage",
        storeAuthStateInCookie: false,
    }
};

export const loginRequest = {
    scopes: ["User.Read"],
    prompt: "select_account"
};

// Used only for automatic OneDrive ingestion.
// This reads files accessible to the currently logged-in Microsoft account.
export const graphTokenRequest = {
    scopes: ["Files.Read"]
};