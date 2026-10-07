// Independent bearer credential for guest conversations; never use the thread ID.
let memorySession;
export function getGuestChatHeaders() {
    let session;
    try {
        session = sessionStorage.getItem('chat_guest_session_v2');
    } catch {
        // Some embedded browsers disallow storage. Preserve the credential in
        // memory for this page; a reload safely starts a new guest session.
    }
    if (!/^[0-9a-f]{64}$/.test(session || '')) {
        session = memorySession || Array.from(crypto.getRandomValues(new Uint8Array(32)),
            value => value.toString(16).padStart(2, '0')).join('');
        try {
            sessionStorage.setItem('chat_guest_session_v2', session);
        } catch { /* Memory fallback above also works in embedded pages. */ }
    }
    memorySession = session;
    return { 'X-Chat-Session': session };
}
