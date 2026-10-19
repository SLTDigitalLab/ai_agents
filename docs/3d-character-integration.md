# Workmate 3D page integration

The separate avatar page is `/workmateai/voice/agent`. `/voice` and
`/workmateai/voice` retain the original orb. The new page uses existing verified
voice authentication and provider selection, including verified Graph profile
forwarding. Telemetry and admin/session access controls are preserved.

Application changes were selectively integrated from feature commit `5f2132e`.
Chrome test profiles, caches and diagnostic screenshots in that commit were
excluded from the integration commit and its ancestry. The feature branch itself
is unchanged; its owner should review and remove those browser artifacts there.

The 35 MB `frontend/public/models/luma.glb` is loaded only when the avatar page
opens. Initial load may take time. The character uses an English grapheme-based
lip-sync approximation, not provider phoneme timestamps. The environment lighting
preset also needs access to its remote asset. Live authenticated microphone/provider
operation must be smoke-tested after deployment; offline tests do not certify it.

## Deployment

Fast-forward the existing production integration branch after preserving local
configuration. Backend source changed but backend requirements did not: restart
`slt_backend` after the pull (the source is bind-mounted). Preserve METRICS_ENABLED
and METRICS_BEARER_TOKEN. Do not overwrite the active host Nginx configuration with
the repository copy: the host already has the IP-restricted HTTPS scrape route.

Frontend dependencies changed. Build the frontend image and recreate ONLY the
frontend with its anonymous node_modules volume renewed (`--renew-anon-volumes`),
or install the exact lockfile dependencies in its existing node_modules volume.
A restart alone will not install Three.js dependencies. Never use `down -v`.
Prepare the frontend image before updating the live bind-mounted source if possible
so dependency installation does not interrupt other pages.

Verify the new page and `/models/luma.glb`, a signed-in voice call, `/voice`, normal
chat, anonymous admin rejection and the existing authenticated metrics scrape.
