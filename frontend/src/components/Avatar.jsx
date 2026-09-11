import { memo } from "react";

function Avatar({ mountRef, status, connected }) {
  const label = connected ? "Live" : status === "Connecting..." ? "Connecting" : "Offline";
  return <section className="avatar-card" aria-label="Aria live AI avatar">
    <div ref={mountRef} className="simli-avatar"><video className="avatar-video" autoPlay playsInline muted /><audio autoPlay muted /></div>
    {!connected && <div className="avatar-placeholder" aria-hidden="true"><div className="avatar-glow" /><div className="avatar-silhouette"><span /><span /></div><p>{label === "Connecting" ? "Connecting to Aria..." : "Your AI assistant is ready when you are"}</p></div>}
    <div className={`live-label ${label.toLowerCase()}`}><span /> {connected ? "Live AI Avatar" : label}</div>
  </section>;
}

export default memo(Avatar);



