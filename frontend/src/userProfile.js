import { InteractionRequiredAuthError } from "@azure/msal-browser";
import { graphConfig, loginRequest } from "./authConfig";

const EMPTY_PROFILE = { department: null, jobTitle: null };

/**
 * Fetch the signed-in user's department and job title from Microsoft Graph.
 *
 * The login (UPN) is only `<employeeNumber>@intranet.slt.com.lk` and carries no
 * department/title, so we read them from the Azure AD directory via Graph
 * `/me`. The result is cached in sessionStorage per account so we hit Graph at
 * most once per session. Any failure resolves to nulls — this is best-effort
 * attribution and must never block the chat flow.
 *
 * @returns {Promise<{department: string|null, jobTitle: string|null}>}
 */
export async function fetchUserProfile(instance, account) {
    if (!instance || !account) return EMPTY_PROFILE;

    const cacheKey = `slt_profile:${account.homeAccountId || account.username}`;

    const cached = sessionStorage.getItem(cacheKey);
    if (cached !== null) {
        try {
            return JSON.parse(cached);
        } catch {
            // Corrupt cache entry — fall through and refetch.
        }
    }

    try {
        const { accessToken } = await instance.acquireTokenSilent({
            ...loginRequest,
            account,
        });

        const res = await fetch(graphConfig.meEndpoint, {
            headers: { Authorization: `Bearer ${accessToken}` },
        });

        if (!res.ok) return EMPTY_PROFILE;

        const profile = await res.json();
        const result = {
            department: (profile.department || "").trim() || null,
            jobTitle: (profile.jobTitle || "").trim() || null,
        };

        // Cache even an empty result so we don't refetch every turn.
        sessionStorage.setItem(cacheKey, JSON.stringify(result));
        return result;
    } catch (err) {
        // Silent-token failure (e.g. consent / interaction required) is
        // non-fatal: we simply proceed without a profile this session.
        if (!(err instanceof InteractionRequiredAuthError)) {
            console.warn("Failed to fetch user profile from Graph:", err);
        }
        return EMPTY_PROFILE;
    }
}
