import { useCallback } from 'react';
import { useMsal } from '@azure/msal-react';
import { getApiIdToken } from './apiAuth';

/** Every admin request must carry a Microsoft token for the Ask SLT API. */
export function useAdminFetch() {
    const { instance, accounts } = useMsal();
    const account = accounts[0];

    return useCallback(async (url, options = {}) => {
        const token = await getApiIdToken(instance, account);
        if (!token) throw new Error('Please sign in with your administrator Microsoft account.');
        const headers = new Headers(options.headers);
        headers.set('Authorization', `Bearer ${token}`);
        const response = await fetch(url, { ...options, headers });
        if (response.status === 401) throw new Error('Your sign-in has expired. Please sign in again.');
        if (response.status === 403) throw new Error('Your account does not have permission for this admin action.');
        return response;
    }, [instance, account]);
}
