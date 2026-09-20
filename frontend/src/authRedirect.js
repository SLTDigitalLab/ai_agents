const DEFAULT_ROUTE = '/workmateai';
const CALLBACK_PATH = '/auth/callback';

function returnPath(browser) {
  const saved = browser.sessionStorage.getItem('lastAgent');
  if (!saved) return DEFAULT_ROUTE;
  try {
    const target = new URL(saved, browser.location.origin);
    if (target.origin === browser.location.origin && target.pathname !== CALLBACK_PATH) {
      return target.pathname + target.search;
    }
  } catch { /* Fall back to the Workmate login page. */ }
  return DEFAULT_ROUTE;
}

// Run before BrowserRouter mounts so it sees the recovered route.
export async function handleAuthRedirect(instance, browser = window) {
  const target = returnPath(browser);
  try {
    const response = await instance.handleRedirectPromise();
    if (response) {
      if (response.account) instance.setActiveAccount(response.account);
      browser.sessionStorage.removeItem('intentionalLogin');
      browser.location.replace(target);
      return null;
    }
    if (browser.location.pathname === CALLBACK_PATH) {
      // A revisited callback is not a logout. Preserve MSAL's cache and other
      // application state instead of clearing all session storage.
      browser.history.replaceState(null, '', target);
    }
    return null;
  } catch (error) {
    if (error?.errorCode !== 'no_token_request_cache_error') throw error;
    browser.sessionStorage.removeItem('intentionalLogin');
    const destination = browser.location.pathname === CALLBACK_PATH
      ? target : browser.location.pathname;
    // MSAL has rejected this response. Remove its stale code/state from the URL
    // so refresh does not replay it; leave authentication validation to MSAL.
    browser.history.replaceState(null, '', destination);
    return 'This Microsoft sign-in request expired or was opened in a different browser session. Please sign in again from this tab and keep the same site address.';
  }
}
