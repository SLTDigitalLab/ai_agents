import { useEffect, useRef, useState } from 'react';
import { createWorkmateBridge } from './WorkmateBridge';
import { API_URL } from '../voice_agent/constants';


const STARTUP_ERROR = 'Napster could not start the avatar. Check your agent configuration.';
const CONNECTION_ERROR = 'Avatar connection failed. Check your network and reconnect.';
const SESSION_ENDED = 'Napster disconnected. Reconnect to continue.';
const AUTOPLAY_ERROR = 'Your browser paused playback. Select Reconnect to resume.';
const CONNECTION_TIMEOUT = 45_000;

// Napster exports a singleton. Serialize init/teardown across component remounts.
let sdkQueue = Promise.resolve();

function releaseMedia(container) {
  container?.querySelectorAll('video, audio').forEach((element) => {
    element.pause();
    element.srcObject?.getTracks?.().forEach((track) => track.stop());
    element.srcObject = null;
    element.removeAttribute('src');
    element.load();
  });
}

export default function useNapsterAvatar(user) {
  const mountRef = useRef(null);
  const stopRef = useRef(null);
  const cleanupRef = useRef(null);
  const resumeRef = useRef(null);
  const sendTextRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [status, setStatus] = useState('idle');
  const [voiceStatus, setVoiceStatus] = useState('Ready to connect');
  const [notification, setNotification] = useState('');
  const [autoplayBlocked, setAutoplayBlocked] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [exchange, setExchange] = useState(null);
  const userId = user.username;
  const userName = user.name;

  useEffect(() => {
    if (!attempt) return undefined;
    const mount = mountRef.current;
    const container = mount?.container;
    if (!container) return undefined;

    let disposed = false;
    let instance = null;
    let bridge = null;
    let connectedOnce = false;
    let blocked = false;
    let timeout;
    let stallTimeout;
    let observer;
    const abort = new AbortController();
    const removers = [];
    const boundElements = new WeakSet();
    const boundTracks = new WeakSet();
    const attemptedPlayback = new WeakMap();

    const listen = (target, event, handler) => {
      target.addEventListener(event, handler);
      removers.push(() => target.removeEventListener(event, handler));
    };

    const cleanup = () => {
      if (disposed) return;
      disposed = true;
      abort.abort();
      bridge?.dispose();
      sendTextRef.current = null;
      clearTimeout(timeout);
      clearTimeout(stallTimeout);
      observer?.disconnect();
      removers.forEach((remove) => remove());
      releaseMedia(container);
      try { instance?.destroy(); } catch { /* Media is still released locally. */ }
      instance = null;
      container.replaceChildren();
    };
    cleanupRef.current = cleanup;

    const fail = (message) => {
      if (disposed) return;
      console.warn('[Napster/Session]', { step: 'disconnect', reason: message });
      cleanup();
      setAutoplayBlocked(false);
      setNotification(message);
      setStatus('disconnected');
      setVoiceStatus('Disconnected');
      setBusy(false);
    };
    stopRef.current = () => fail('Conversation ended. Reconnect to start again.');
    bridge = createWorkmateBridge({
      apiUrl: API_URL, user: { username: userId, name: userName },
      threadId: `voice_napster_${crypto.randomUUID()}`,
      sendCommand: (command) => {
        if (!instance) throw new Error('Napster is not connected.');
        instance.sendCommand(command);
      },
      onState: setVoiceStatus, onError: setNotification, onExchange: setExchange,
      onBusy: setBusy,
      onWaiting: setWaiting,
      stopSpeaking: () => instance?.stopAvatarTalking(),
      onSpeechAllowed: (allowed) => {
        // Keep remote audio open continuously. Muting it while listening made
        // playback depend on talk-state event ordering; the avatar could begin
        // animating before this handler unmuted the audio.
        if (mount.audio) mount.audio.muted = false;
        instance?.unmuteAudio();
        if (allowed) {
          // Prevent the avatar's own speaker output from being transcribed as a
          // new customer turn and recursively sent back to Workmate.
          instance?.muteMic();
        } else {
          instance?.unmuteMic();
        }
      },
      onFatal: fail,
      onDiagnostic: (event) => console.info('[Napster/Workmate]', event),
    });
    sendTextRef.current = (text) => {
      if (disposed || !instance || !connectedOnce || blocked) return false;
      if (instance.isUserTalking) {
        setNotification('Please finish speaking before sending a typed question.');
        return false;
      }
      return bridge.sendText(text);
    };
    const markPlaying = () => {
      if (disposed || blocked) return;
      connectedOnce = true;
      clearTimeout(timeout);
      clearTimeout(stallTimeout);
      setStatus('connected');
      setNotification('');
    };
    const handlePlayError = (error) => {
      if (disposed || error?.name === 'AbortError') return;
      if (error?.name === 'NotAllowedError') {
        blocked = true;
        clearTimeout(timeout);
        clearTimeout(stallTimeout);
        setAutoplayBlocked(true);
        setStatus('disconnected');
        setNotification(AUTOPLAY_ERROR);
        // Keep the stream briefly so Reconnect can play it within a user gesture.
        timeout = setTimeout(() => fail(CONNECTION_ERROR), 60_000);
      } else {
        fail(CONNECTION_ERROR);
      }
    };
    const play = (element) => {
      if (element?.srcObject) element.play()?.catch(handlePlayError);
    };
    const watchTracks = (element) => {
      element.srcObject?.getTracks?.().forEach((track) => {
        if (boundTracks.has(track)) return;
        boundTracks.add(track);
        listen(track, 'ended', () => fail(SESSION_ENDED));
      });
    };
    const bindMedia = () => {
      if (disposed) return;
      [mount.video, mount.audio].filter(Boolean).forEach((element) => {
        element.autoplay = true;
        element.controls = false;
        if (element.tagName === 'VIDEO') {
          element.playsInline = true;
          element.muted = true; // Output audio is supplied by the SDK's audio node.
        } else {
          element.muted = false;
        }
        if (!boundElements.has(element)) {
          boundElements.add(element);
          listen(element, 'loadedmetadata', () => {
            watchTracks(element);
            play(element);
          });
          listen(element, 'ended', () => fail(SESSION_ENDED));
          listen(element, 'error', () => fail(CONNECTION_ERROR));
          if (element.tagName === 'VIDEO') {
            listen(element, 'playing', markPlaying);
            const stalled = () => {
              if (!connectedOnce || blocked || disposed) return;
              setStatus('connecting');
              clearTimeout(stallTimeout);
              stallTimeout = setTimeout(() => fail(CONNECTION_ERROR), 15_000);
            };
            listen(element, 'waiting', stalled);
            listen(element, 'stalled', stalled);
          }
        }
        watchTracks(element);
        if (element.srcObject && attemptedPlayback.get(element) !== element.srcObject) {
          attemptedPlayback.set(element, element.srcObject);
          play(element);
        }
      });
    };

    resumeRef.current = () => {
      if (disposed) return;
      blocked = false;
      setAutoplayBlocked(false);
      setNotification('');
      setStatus('connecting');
      clearTimeout(timeout);
      timeout = setTimeout(() => fail(CONNECTION_ERROR), CONNECTION_TIMEOUT);
      // Invoke play synchronously during the click; fetching a new token would
      // lose browser user activation and can reproduce the autoplay restriction.
      const media = [mount.video, mount.audio].filter((element) => element?.srcObject);
      Promise.all(media.map((element) => element.play())).then(() => {
        if (mount.video && !mount.video.paused && mount.video.readyState >= 3) markPlaying();
      }).catch(handlePlayError);
    };

    setStatus('connecting');
    setBusy(false);
    setVoiceStatus('Listening...');
    setExchange(null);
    setNotification('');
    setAutoplayBlocked(false);
    listen(window, 'offline', () => fail(CONNECTION_ERROR));
    listen(window, 'pagehide', cleanup);
    observer = new MutationObserver(bindMedia);
    observer.observe(container, { childList: true, subtree: true });

    const start = async () => {
      if (disposed) return;
      timeout = setTimeout(() => fail(CONNECTION_ERROR), CONNECTION_TIMEOUT);
      let sdk;
      try {
        sdk = (await import('@touchcastllc/napster-companion-api')).NapsterCompanionApiSdk;
        // Reuse the SDK's cached microphone stream, before minting a short-lived token.
        await sdk.requestMicrophoneAccess();
      } catch {
        fail(STARTUP_ERROR);
        return;
      }
      if (disposed) return;
      let response;
      try {
        response = await fetch(`${API_URL}/api/napster/session`, {
          method: 'POST', signal: abort.signal, cache: 'no-store',
          headers: { Accept: 'application/json' },
        });
      } catch {
        if (!disposed) fail(CONNECTION_ERROR);
        return;
      }
      if (disposed) return;
      if (!response.ok) {
        let detail;
        try { ({ detail } = await response.json()); } catch { /* Proxy/network errors may not contain JSON. */ }
        if (disposed) return;
        // The backend supplies curated error messages, never raw Napster bodies.
        fail(typeof detail === 'string' && detail.trim()
          ? detail.slice(0, 500)
          : response.status >= 502 && response.status !== 503 ? CONNECTION_ERROR : STARTUP_ERROR);
        return;
      }
      let token;
      try { ({ token } = await response.json()); } catch { /* Invalid backend response. */ }
      if (disposed) return;
      if (typeof token !== 'string' || !token.trim()) {
        fail(STARTUP_ERROR);
        return;
      }
      try {
        instance = await sdk.init(token, {
          mountContainer: container,
          layout: 'inline',
          avatarStyle: { view: 'rectangle', borderWidth: '0px' },
          persistence: { enabled: false },
          analytics: { enabled: false },
          debug: false,
          features: {
            controls: { enabled: false },
            disclaimer: { enabled: false },
            showSDKLoader: { enabled: false },
            inactiveTimeout: { enabled: false },
            backgroundRemoval: { enabled: false },
            pictureInPicture: { enabled: false },
            screenShare: { enabled: false },
            faceTracking: { enabled: false },
          },
          onAvatarReady: (isReady) => {
            if (isReady === false) return;
            bindMedia();
          },
          onData: (event) => {
            if (!disposed) void bridge.handleEvent(event).catch(() => fail(CONNECTION_ERROR));
          },
          onError: (error) => {
            console.warn('[Napster/Session]', {
              step: 'sdk_error', name: error?.name, code: error?.code,
            });
            if (error?.name === 'NotAllowedError' && error?.code !== 'MEDIA_ACCESS_DENIED') {
              handlePlayError(error);
            } else {
              const mediaError = ['MEDIA_ACCESS_DENIED', 'MEDIA_DEVICE_ERROR'].includes(error?.code);
              fail(mediaError
                ? 'Napster requires browser microphone permission to initialize. Allow access in site settings, then reconnect.'
                : CONNECTION_ERROR);
            }
          },
          onDestroy: () => {
            console.warn('[Napster/Session]', { step: 'sdk_destroyed' });
            fail(SESSION_ENDED);
          },
        });
        if (disposed) {
          instance.destroy();
          instance = null;
          releaseMedia(container);
          container.replaceChildren();
          return;
        }
        instance.unmuteMic();
        instance.unmuteAudio();
        instance.setAudioVolume(1);
        bindMedia();
      } catch {
        if (!disposed) fail(STARTUP_ERROR);
      }
    };

    // StrictMode's probe unmount cancels this timer before a token is minted.
    const startTimer = setTimeout(() => {
      sdkQueue = sdkQueue.catch(() => {}).then(start);
    }, 0);
    return () => {
      clearTimeout(startTimer);
      cleanup();
      if (cleanupRef.current === cleanup) cleanupRef.current = null;
    };
  }, [attempt, userId, userName]);

  const reconnect = () => {
    if (autoplayBlocked) resumeRef.current?.();
    else {
      cleanupRef.current?.();
      setAttempt((value) => value + 1);
    }
  };
  return { mountRef, status, displayStatus: status === 'connecting' ? 'Connecting...' : voiceStatus,
    errorMessage: notification, exchange, reconnect, busy, waiting,
    sendText: (text) => status === 'connected' && (sendTextRef.current?.(text) ?? false),
    stop: () => stopRef.current?.() };
}
