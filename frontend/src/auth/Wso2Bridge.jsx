// frontend/src/auth/Wso2Bridge.jsx
//
// Bridges an Asgardeo (WSO2) sign-in into the unified LifeStore session.
// When WSO2 finishes the OIDC redirect and marks the user authenticated, we
// decode the ID token and hand the profile to lifestoreAuth.signInWithProfile
// — the exact same session shape Google produces. Renders nothing.

import { useEffect, useRef } from "react";
import { useAuthContext } from "@asgardeo/auth-react";
import { useLifestoreAuth } from "./lifestoreAuth";

export default function Wso2Bridge() {
  const { state, getDecodedIDToken, getIDToken } = useAuthContext();
  const { isAuthenticated, signInWithProfile } = useLifestoreAuth();
  const syncedRef = useRef(false);

  useEffect(() => {
    if (!state?.isAuthenticated || isAuthenticated || syncedRef.current) return;
    syncedRef.current = true;

    (async () => {
      try {
        const decoded = await getDecodedIDToken();
        let idToken = null;
        try {
          idToken = await getIDToken();
        } catch {
          /* token still usable via decoded claims */
        }

        const name =
          decoded?.name ||
          [decoded?.given_name, decoded?.family_name].filter(Boolean).join(" ") ||
          decoded?.username ||
          "";

        signInWithProfile(
          {
            sub: decoded?.sub || null,
            email: decoded?.email || decoded?.username || "",
            name,
            picture: decoded?.picture || null,
          },
          idToken,
          decoded?.exp || null
        );
      } catch {
        syncedRef.current = false; // allow a retry on the next state change
      }
    })();
  }, [state?.isAuthenticated, isAuthenticated, getDecodedIDToken, getIDToken, signInWithProfile]);

  return null;
}
