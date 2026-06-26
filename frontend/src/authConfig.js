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

// Microsoft Graph endpoint used to read the signed-in user's profile.
// `department` (and `jobTitle`) come from Azure AD directory attributes and
// require only the User.Read scope already requested at login.
export const graphConfig = {
    meEndpoint:
        "https://graph.microsoft.com/v1.0/me?$select=department,jobTitle,officeLocation"
};