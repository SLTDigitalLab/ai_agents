import { useImperativeHandle, useRef } from 'react';

export default function Avatar({ mountRef, status }) {
  const containerRef = useRef(null);

  // SDK 1.5 owns and connects the actual video/audio nodes inside this container.
  // Getters stay current when it creates or replaces those nodes on reconnect.
  useImperativeHandle(mountRef, () => ({
    get container() { return containerRef.current; },
    get video() { return containerRef.current?.querySelector('video') ?? null; },
    get audio() { return containerRef.current?.querySelector('audio') ?? null; },
  }), []);

  return (
    <section className={`avatar-card avatar-card--${status}`} aria-label="Napster live avatar">
      <div ref={containerRef} className="napster-avatar" />
      {status !== 'connected' && (
        <div className="avatar-placeholder" aria-hidden="true">
          <p>{status === 'connecting' ? 'Connecting to Napster' : 'Napster is offline'}</p>
          <span>{status === 'connecting' ? 'Establishing a secure connection' : 'Start a conversation when you’re ready'}</span>
        </div>
      )}
    </section>
  );
}
