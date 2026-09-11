import { useEffect, useRef, useState } from "react";
// Simli 3.0.2's index imports './Client', but ships lowercase 'client.js'.
// Use the actual entry on case-sensitive Docker/Linux filesystems.
import { SimliClient, LogLevel } from "simli-client/dist/client.js";
import Avatar from "./Avatar";
import "./SimliAvatarPanel.css";

// Docker's IPv6 localhost listener and the Windows IPv4 backend can coexist.
// Use the working local FastAPI instance explicitly; deployed sites keep their
// same-origin reverse proxy. This setting is isolated from Workmate voice APIs.
const sessionUrl = import.meta.env.VITE_SIMLI_SESSION_URL || (
  import.meta.env.DEV && ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname)
    ? 'http://127.0.0.1:8000/api/simli/session'
    : '/api/simli/session'
);

export default function SimliAvatarPanel({ audioOutput = null, showControls = true }) {
  const mountRef = useRef(null);
  const stopRef = useRef(null);
  const cleanupRef = useRef(Promise.resolve());
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState("Connecting...");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    let client;
    let timeout;
    const host = mountRef.current;
    const controller = new AbortController();
    const stop = () => {
      if (cancelled) return;
      cancelled = true;
      audioOutput?.detach();
      controller.abort();
      clearTimeout(timeout);
      cleanupRef.current = Promise.resolve().then(() => client?.stop()).catch(() => {});
      host.querySelectorAll("video, audio").forEach((element) => { element.srcObject = null; });
    };
    stopRef.current = stop;
    const fail = (message) => {
      if (cancelled) return;
      stop(); setStatus("Disconnected"); setError(message);
    };
    const onPlaying = () => {
      if (!cancelled) { clearTimeout(timeout); setStatus("Connected"); }
    };
    const video = host.querySelector("video");
    video.addEventListener("playing", onPlaying);
    const timer = setTimeout(async () => {
      await cleanupRef.current;
      if (cancelled) return;
      setStatus("Connecting..."); setError("");
      timeout = setTimeout(() => fail("The avatar timed out. Please reconnect."), 90000);
      try {
        const response = await fetch(sessionUrl, { method: "POST", signal: controller.signal });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "Unable to start the avatar.");
        if (cancelled) return;
        client = new SimliClient(payload.session_token, video, host.querySelector("audio"), null, LogLevel.INFO, "livekit");
        client.on("stop", () => fail("Avatar preview ended. Reconnect to view it again."));
        client.on("error", () => fail("Avatar connection failed. Check your network and reconnect."));
        client.on("startup_error", () => fail("Simli could not start the avatar. Check your face ID and available minutes."));
        await client.start();
        if (cancelled) await client.stop();
        else audioOutput?.attach(client, host.querySelector("audio"));
      } catch (cause) { if (!cancelled) fail(cause.message || "Unable to start the avatar."); }
    }, 0);
    return () => { clearTimeout(timer); video.removeEventListener("playing", onPlaying); stop(); };
  }, [attempt, audioOutput]);

  const connected = status === "Connected";
  return <section className="workmate-simli" aria-label="Visual avatar">
    <Avatar mountRef={mountRef} status={status} connected={connected} />
    {error && <p className="avatar-notice" role="status">{error}</p>}
    {showControls && <div className="avatar-controls">
      <span>{status}</span>
      {status === "Disconnected"
        ? <button type="button" onClick={() => setAttempt(value => value + 1)}>Reconnect avatar</button>
        : <button type="button" onClick={() => { stopRef.current?.(); setStatus("Disconnected"); setError(""); }}>Stop avatar</button>}
    </div>}
  </section>;
}
