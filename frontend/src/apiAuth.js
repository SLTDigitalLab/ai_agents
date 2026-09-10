import { InteractionRequiredAuthError } from "@azure/msal-browser";
import { loginRequest } from "./authConfig";

/**
 * Azure ID token for the Ask SLT API.
 *
 * Graph access tokens (User.Read) have the wrong audience. The backend
 * validates aud = MS_CLIENT_ID, which matches the ID token.
 */
export async function getApiIdToken(instance, account) {
    if (!instance || !account) return null;

    const request = {
        scopes: loginRequest.scopes,
        account,
    };

    try {
        const result = await instance.acquireTokenSilent(request);
        return result.idToken || null;
    } catch (err) {
        if (err instanceof InteractionRequiredAuthError) {
            const result = await instance.acquireTokenPopup(request);
            return result.idToken || null;
        }
        throw err;
    }
}

export async function getChatAuthHeaders(instance, account, extra = {}) {
    const headers = { ...extra };
    const token = await getApiIdToken(instance, account);
    if (token) {
        headers.Authorization = `Bearer ${token}`;
    }
    return headers;
}
