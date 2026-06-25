import { InteractionRequiredAuthError } from "@azure/msal-browser";
import { graphConfig, loginRequest } from "./authConfig";

/**
 * Fetch the signed-in user's department from Microsoft Graph.
 *
 * The login (UPN) is only `<employeeNumber>@intranet.slt.com.lk` and carries no
 * department, so we read it from the Azure AD directory via Graph `/me`. The
 * result is cached in sessionStorage per account so we hit Graph at most once
 * per session. Any failure resolves to `null` — department is best-effort
 * attribution and must never block the chat flow.
 *
 * @returns {Promise<string|null>} department name, or null if unavailable
 */
export async function fetchUserDepartment(instance, account) {
    if (!instance || !account) return null;

    const cacheKey = `slt_dept:${account.homeAccountId || account.username}`;

    const cached = sessionStorage.getItem(cacheKey);
    if (cached !== null) {
        return cached || null; // empty string cached => no department in directory
    }

    try {
        const { accessToken } = await instance.acquireTokenSilent({
            ...loginRequest,
            account,
        });

        const res = await fetch(graphConfig.meEndpoint, {
            headers: { Authorization: `Bearer ${accessToken}` },
        });

        if (!res.ok) return null;

        const profile = await res.json();
        const department = (profile.department || "").trim();

        // Cache even an empty result so we don't refetch every turn.
        sessionStorage.setItem(cacheKey, department);
        return department || null;
    } catch (err) {
        // Silent-token failure (e.g. consent / interaction required) is
        // non-fatal: we simply proceed without a department this session.
        if (!(err instanceof InteractionRequiredAuthError)) {
            console.warn("Failed to fetch user department from Graph:", err);
        }
        return null;
    }
}
