/**
 * VoiceAgentPage.jsx
 * Live voice conversation with Workmate AI using OpenAI Realtime API (WebRTC).
 *
 * Flow:
 * 1. User lands on /voice (must be authenticated via MSAL)
 * 2. Clicks "Start Conversation" → fetches ephemeral token from backend
 * 3. Opens WebRTC connection to OpenAI using the token
 * 4. Sends session.update to inject system prompt, tools, turn detection
 * 5. Live audio streams both ways — user speaks, AI responds
 * 6. Live transcript shown on screen
 * 7. User clicks "End Call" → connection closes → back to idle
 * any one can undestand clearly what is a voice agent flow 
 */

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useMsal } from '@azure/msal-react';
import { motion, AnimatePresence } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import sltLogo from '../assets/slt-mobitel-logo.png';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

//  System prompt sent via session.update after WebRTC connects 
const SYSTEM_PROMPT = `You are Workmate AI, the intelligent voice assistant for SLTMobitel employees.
You help employees with questions about HR policies, Finance, IT support, Admin procedures,
internal audit (CIA), and business processes.

You are having a live voice conversation. Keep your responses:
- Concise and clear — this is a spoken conversation, not a chat interface
- Natural sounding — no bullet points, no markdown, no lists
- Accurate — use the search_knowledge_base function when answering any company-specific question

When you don't know something specific to SLTMobitel, call search_knowledge_base before answering.
If a question is completely outside SLTMobitel workplace topics, politely say you can only help with work-related questions.
Greet the user warmly when the conversation starts.`;

//  Phase constants
const PHASE = {
  IDLE: 'idle',
  CONNECTING: 'connecting',
  CONNECTED: 'connected',
  ERROR: 'error',
};

//  Transcript entry component
const TranscriptEntry = ({ entry }) => (
  <motion.div
    initial={{ opacity: 0, y: 8 }}
    animate={{ opacity: 1, y: 0 }}
    transition={{ duration: 0.3 }}
    className={`flex ${entry.role === 'user' ? 'justify-end' : 'justify-start'}`}
  >
    <div className={`max-w-[80%] px-4 py-2.5 rounded-2xl text-sm leading-relaxed ${
      entry.role === 'user'
        ? 'bg-white/20 text-white rounded-tr-sm'
        : 'bg-white/10 text-white/90 rounded-tl-sm border border-white/10'
    }`}>
      <span className={`text-[0.65rem] uppercase tracking-wider font-semibold block mb-1 ${
        entry.role === 'user' ? 'text-white/60' : 'text-teal-300/80'
      }`}>
        {entry.role === 'user' ? 'You' : 'Workmate AI'}
      </span>
      {entry.text}
    </div>
  </motion.div>
);

//  Animated orb — pulses when AI is speaking
const VoiceOrb = ({ phase, isSpeaking }) => {
  const isActive = phase === PHASE.CONNECTED;

  return (
    <div className="relative flex items-center justify-center w-48 h-48">
      {/* Outer ripple rings — only when connected */}
      {isActive && (
        <>
          <div className={`absolute w-48 h-48 rounded-full border border-teal-400/20 ${isSpeaking ? 'animate-ping' : ''}`} style={{ animationDuration: '2s' }} />
          <div className={`absolute w-40 h-40 rounded-full border border-teal-400/30 ${isSpeaking ? 'animate-ping' : ''}`} style={{ animationDuration: '1.5s', animationDelay: '0.3s' }} />
        </>
      )}

      {/* Core orb */}
      <motion.div
        animate={{
          scale: isSpeaking ? [1, 1.12, 1.06, 1.1, 1] : isActive ? [1, 1.03, 1] : 1,
        }}
        transition={{
          duration: isSpeaking ? 0.6 : 2,
          repeat: isActive ? Infinity : 0,
          ease: 'easeInOut',
        }}
        className={`relative w-32 h-32 rounded-full flex items-center justify-center shadow-2xl ${
          phase === PHASE.ERROR
            ? 'bg-gradient-to-br from-red-500/40 to-red-700/40 border border-red-400/30'
            : isActive
            ? 'bg-gradient-to-br from-teal-400/30 to-cyan-600/30 border border-teal-400/40'
            : 'bg-gradient-to-br from-white/10 to-white/5 border border-white/20'
        }`}
        style={{
          boxShadow: isActive
            ? isSpeaking
              ? '0 0 60px rgba(45,212,191,0.4), 0 0 120px rgba(45,212,191,0.15)'
              : '0 0 40px rgba(45,212,191,0.2), 0 0 80px rgba(45,212,191,0.08)'
            : '0 0 30px rgba(255,255,255,0.05)',
        }}
      >
        {/* Icon inside orb */}
        {phase === PHASE.CONNECTING ? (
          <svg className="w-10 h-10 text-white/60 animate-spin" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
          </svg>
        ) : phase === PHASE.ERROR ? (
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-10 h-10 text-red-400">
            <path fillRule="evenodd" d="M9.401 3.003c1.155-2 4.043-2 5.197 0l7.355 12.748c1.154 2-.29 4.5-2.599 4.5H4.645c-2.309 0-3.752-2.5-2.598-4.5L9.4 3.003zM12 8.25a.75.75 0 01.75.75v3.75a.75.75 0 01-1.5 0V9a.75.75 0 01.75-.75zm0 8.25a.75.75 0 100-1.5.75.75 0 000 1.5z" clipRule="evenodd" />
          </svg>
        ) : isActive ? (
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className={`w-10 h-10 ${isSpeaking ? 'text-teal-300' : 'text-white/70'}`}>
            <path d="M8.25 4.5a3.75 3.75 0 117.5 0v8.25a3.75 3.75 0 11-7.5 0V4.5z" />
            <path d="M6 10.5a.75.75 0 01.75.75v1.5a5.25 5.25 0 1010.5 0v-1.5a.75.75 0 011.5 0v1.5a6.751 6.751 0 01-6 6.709v2.291h3a.75.75 0 010 1.5h-7.5a.75.75 0 010-1.5h3v-2.291a6.751 6.751 0 01-6-6.709v-1.5A.75.75 0 016 10.5z" />
          </svg>
        ) : (
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-10 h-10 text-white/40">
            <path d="M8.25 4.5a3.75 3.75 0 117.5 0v8.25a3.75 3.75 0 11-7.5 0V4.5z" />
            <path d="M6 10.5a.75.75 0 01.75.75v1.5a5.25 5.25 0 1010.5 0v-1.5a.75.75 0 011.5 0v1.5a6.751 6.751 0 01-6 6.709v2.291h3a.75.75 0 010 1.5h-7.5a.75.75 0 010-1.5h3v-2.291a6.751 6.751 0 01-6-6.709v-1.5A.75.75 0 016 10.5z" />
          </svg>
        )}
      </motion.div>
    </div>
  );
};

//  Main component
const VoiceAgentPage = () => {
  const { accounts, instance } = useMsal();
  const navigate = useNavigate();
  const user = accounts[0] || {};

  //  State
  const [phase, setPhase] = useState(PHASE.IDLE);
  const [isSpeaking, setIsSpeaking] = useState(false);   // AI is speaking
  const [isListening, setIsListening] = useState(false);  // user is speaking
  const [transcript, setTranscript] = useState([]);
  const [statusText, setStatusText] = useState('Ready to connect');
  const [errorMessage, setErrorMessage] = useState('');

  //  Refs
  const pcRef = useRef(null);          // RTCPeerConnection
  const dcRef = useRef(null);          // RTCDataChannel
  const audioElRef = useRef(null);     // <audio> element for AI voice output
  const transcriptEndRef = useRef(null);

  //  Auto-scroll transcript
  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [transcript]);

  //  Cleanup on unmount 
  useEffect(() => {
    return () => { cleanupConnection(); };
  }, []);

  const cleanupConnection = () => {
    if (dcRef.current) { dcRef.current.close(); dcRef.current = null; }
    if (pcRef.current) { pcRef.current.close(); pcRef.current = null; }
    if (audioElRef.current) { audioElRef.current.srcObject = null; }
  };

  //  Send event over data channel
  const sendEvent = useCallback((event) => {
    if (dcRef.current && dcRef.current.readyState === 'open') {
      dcRef.current.send(JSON.stringify(event));
    }
  }, []);

  //  Session update — inject system prompt + tools after connection
  const configureSession = useCallback(() => {
  sendEvent({
  type: 'session.update',
  session: {
    type: 'realtime',
    instructions: SYSTEM_PROMPT,
    output_modalities: ['audio'],
    audio: {
      input: {
        turn_detection: {
          type: 'server_vad',
          threshold: 0.5,
          prefix_padding_ms: 300,
          silence_duration_ms: 600,
        },
      },
      output: {
        voice: 'alloy',
      },
    },
    tool_choice: 'auto',
    tools: [
      {
        type: 'function',
        name: 'search_knowledge_base',
        description:
          'Search the SLTMobitel internal knowledge base for HR policies, leave, benefits, ' +
          'finance, IT support, admin procedures, or CIA compliance. ' +
          'Always call this before answering any company-specific question.',
        parameters: {
          type: 'object',
          properties: {
            query: {
              type: 'string',
              description: 'The search query',
            },
            agent_id: {
              type: 'string',
              description: 'Which knowledge base: hr, finance, admin, it, cia, process',
              enum: ['supervisor', 'hr', 'finance', 'admin', 'it', 'cia', 'process'],
            },
          },
          required: ['query'],
        },
      },
    ],
  },
});
  }, [sendEvent]);

  //  Handle incoming data channel events 
  const handleDataChannelMessage = useCallback(async (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    
    // degug ==> log all incoming events 
    console.log('Realtime event:', msg.type, msg);
    switch (msg.type) {

      // Session is ready — configure it now
      case 'session.created':
        configureSession();
        setStatusText('Connected — speak to Workmate AI');
        setPhase(PHASE.CONNECTED);
        break;

      // AI started speaking
      case 'response.audio.delta':
        setIsSpeaking(true);
        break;

      // AI finished speaking
      case 'response.audio.done':
        setIsSpeaking(false);
        break;

      // User speech detected
      case 'input_audio_buffer.speech_started':
        setIsListening(true);
        setStatusText('Listening...');
        break;

      // User stopped speaking
      case 'input_audio_buffer.speech_stopped':
        setIsListening(false);
        setStatusText('Processing...');
        break;

      // Live transcript — user's words
      case 'conversation.item.input_audio_transcription.completed':
        if (msg.transcript?.trim()) {
          setTranscript(prev => [...prev, { role: 'user', text: msg.transcript.trim() }]);
          setStatusText('Connected — speak to Workmate AI');
        }
        break;

      // AI text transcript delta (build up AI response text)
      case 'response.text.delta':
        setTranscript(prev => {
          const last = prev[prev.length - 1];
          if (last && last.role === 'assistant' && last.partial) {
            return [...prev.slice(0, -1), { ...last, text: last.text + msg.delta }];
          }
          return [...prev, { role: 'assistant', text: msg.delta, partial: true }];
        });
        break;

      // AI finished text response — mark as complete
      case 'response.text.done':
        setTranscript(prev => {
          const last = prev[prev.length - 1];
          if (last && last.role === 'assistant' && last.partial) {
            return [...prev.slice(0, -1), { role: 'assistant', text: last.text }];
          }
          return prev;
        });
        setStatusText('Connected — speak to Workmate AI');
        break;

      // AI output transcript (audio transcript — use this if text delta not available)
      case 'response.audio_transcript.delta':
        setTranscript(prev => {
          const last = prev[prev.length - 1];
          if (last && last.role === 'assistant' && last.partial) {
            return [...prev.slice(0, -1), { ...last, text: last.text + msg.delta }];
          }
          return [...prev, { role: 'assistant', text: msg.delta, partial: true }];
        });
        break;

      case 'response.audio_transcript.done':
        setTranscript(prev => {
          const last = prev[prev.length - 1];
          if (last && last.role === 'assistant' && last.partial) {
            return [...prev.slice(0, -1), { role: 'assistant', text: last.text }];
          }
          return prev;
        });
        setIsSpeaking(false);
        setStatusText('Connected — speak to Workmate AI');
        break;

      // Function call — RAG search
      case 'response.function_call_arguments.done':
        if (msg.name === 'search_knowledge_base') {
          try {
            const args = JSON.parse(msg.arguments);
            setStatusText('Searching knowledge base...');

            const res = await fetch(`${API_URL}/api/v1/realtime/rag-search`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                query: args.query,
                agent_id: args.agent_id || 'supervisor',
              }),
            });

            const data = res.ok ? await res.json() : { results: [] };

            // Return function result back to the Realtime API
            sendEvent({
              type: 'conversation.item.create',
              item: {
                type: 'function_call_output',
                call_id: msg.call_id,
                output: JSON.stringify({
                  results: data.results?.slice(0, 5).map(r => ({
                    content: r.content || '',
                    source: r.source || '',
                  })) || [],
                }),
              },
            });

            // Tell the model to generate a response using the results
            sendEvent({ type: 'response.create' });
            setStatusText('Connected — speak to Workmate AI');
          } catch (err) {
            console.error('RAG search error:', err);
            sendEvent({
              type: 'conversation.item.create',
              item: {
                type: 'function_call_output',
                call_id: msg.call_id,
                output: JSON.stringify({ results: [] }),
              },
            });
            sendEvent({ type: 'response.create' });
          }
        }
        break;

      case 'error':
        console.error('Realtime API error:', msg.error);
        setErrorMessage(msg.error?.message || 'An error occurred');
        setPhase(PHASE.ERROR);
        setStatusText('Error occurred');
        break;

      default:
        break;
    }
  }, [configureSession, sendEvent]);

  //  Start conversation 
  const startConversation = async () => {
    setPhase(PHASE.CONNECTING);
    setStatusText('Connecting...');
    setTranscript([]);
    setErrorMessage('');

    try {
      // Step 1 — Get ephemeral token from our backend
      const tokenRes = await fetch(`${API_URL}/api/v1/realtime/token`);
      if (!tokenRes.ok) throw new Error('Failed to get session token');
      const tokenData = await tokenRes.json();

      const ephemeralKey = tokenData.value;
      if (!ephemeralKey) throw new Error('No ephemeral token in response');

      setStatusText('Setting up audio...');

      // Step 2 — Get microphone access
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

      // Step 3 — Create RTCPeerConnection
      const pc = new RTCPeerConnection();
      pcRef.current = pc;

      // Step 4 — Set up audio output element for AI voice
      if (!audioElRef.current) {
        audioElRef.current = document.createElement('audio');
        audioElRef.current.autoplay = true;
        document.body.appendChild(audioElRef.current);
      }
      pc.ontrack = (e) => {
        audioElRef.current.srcObject = e.streams[0];
      };

      // Step 5 — Add microphone track
      stream.getTracks().forEach(track => pc.addTrack(track, stream));

      // Step 6 — Create data channel for events
      const dc = pc.createDataChannel('oai-events');
      dcRef.current = dc;
      dc.onmessage = handleDataChannelMessage;
      dc.onerror = (e) => {
        console.error('Data channel error:', e);
        setErrorMessage('Connection error. Please try again.');
        setPhase(PHASE.ERROR);
      };

      // Step 7 — Create SDP offer
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      setStatusText('Negotiating connection...');

      // Step 8 — Send SDP offer to OpenAI Realtime API
      const sdpRes = await fetch(
        `https://api.openai.com/v1/realtime/calls?model=gpt-realtime-2`,
        {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${ephemeralKey}`,
            'Content-Type': 'application/sdp',
          },
          body: offer.sdp,
        }
      );

      if (!sdpRes.ok) {
        const errText = await sdpRes.text();
        throw new Error(`SDP negotiation failed: ${errText}`);
      }

      // Step 9 — Set remote SDP answer
      const answerSdp = await sdpRes.text();
      await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });

      setStatusText('Establishing voice channel...');
      // session.created event will fire on data channel → configureSession() called there

    } catch (err) {
      console.error('Voice connection error:', err);
      setErrorMessage(err.message || 'Failed to start voice session');
      setPhase(PHASE.ERROR);
      setStatusText('Connection failed');
      cleanupConnection();
    }
  };

  //  End conversation 
  const endConversation = () => {
    cleanupConnection();
    setPhase(PHASE.IDLE);
    setIsSpeaking(false);
    setIsListening(false);
    setStatusText('Ready to connect');
  };

  //  Handle logout 
  const handleLogout = () => {
    endConversation();
    instance.logoutRedirect({ postLogoutRedirectUri: window.location.origin + '/voice' });
  };

  return (
    <div className="h-screen flex flex-col bg-[#0b0c14] relative overflow-hidden">

      {/*  Background mesh — matches existing agent pages  */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none z-0">
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[140vw] h-[140vh] rounded-[100%] bg-gradient-to-b from-teal-600/30 to-cyan-800/20 opacity-50 blur-[150px]" />
        <div className="absolute -top-[10%] -left-[10%] w-[65vw] h-[65vw] rounded-full bg-gradient-to-br from-teal-500/40 to-cyan-600/30 opacity-60 blur-[120px]" />
        <div className="absolute -bottom-[10%] -right-[10%] w-[55vw] h-[55vw] rounded-full bg-gradient-to-tl from-cyan-500/30 to-teal-700/20 opacity-50 blur-[120px]" />
      </div>

      {/*  Navbar  */}
      <motion.nav
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6 }}
        className="relative mx-4 sm:mx-8 mt-4 px-6 sm:px-8 py-3 flex justify-between items-center z-20 rounded-2xl border border-white/10 shadow-[0_20px_40px_-10px_rgba(0,0,0,0.3)] bg-white/5 backdrop-blur-xl"
      >
        {/* Back to chat */}
        <div className="flex items-center gap-4">
          <img src={sltLogo} alt="SLTMobitel" className="h-8 sm:h-10 w-auto" />
          <motion.button
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.97 }}
            onClick={() => { endConversation(); navigate('/workmateai'); }}
            className="flex items-center gap-2 bg-white/10 hover:bg-white/20 border border-white/10 text-white/80 hover:text-white px-3 py-1.5 rounded-xl text-sm font-medium transition-all duration-300"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4">
              <path fillRule="evenodd" d="M17 10a.75.75 0 01-.75.75H5.612l4.158 3.96a.75.75 0 11-1.04 1.08l-5.5-5.25a.75.75 0 010-1.08l5.5-5.25a.75.75 0 111.04 1.08L5.612 9.25H16.25A.75.75 0 0117 10z" clipRule="evenodd" />
            </svg>
            Chat
          </motion.button>
        </div>

        {/* User info + logout */}
        <div className="flex items-center gap-3">
          <span className="text-white/70 text-sm hidden sm:inline">
            Hi, {user.name?.split(' ')[0]}
          </span>
          <motion.button
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.97 }}
            onClick={handleLogout}
            className="border border-white/20 text-white/80 hover:bg-white/10 hover:text-white px-4 py-2 rounded-xl text-sm font-medium transition-all duration-300"
          >
            Logout
          </motion.button>
        </div>
      </motion.nav>

      {/*  Main content  */}
      <div className="flex-1 flex flex-col items-center justify-start z-10 px-4 pt-6 pb-4 min-h-0">

        {/* Title */}
        <motion.div
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.2 }}
          className="text-center mb-6"
        >
          <h1 className="text-4xl sm:text-5xl font-extrabold text-white tracking-tight drop-shadow-lg uppercase">
            Voice Agent
          </h1>
          <p className="text-white/50 text-sm mt-1">Workmate AI — live voice conversation</p>
        </motion.div>

        {/*  Glass panel */}
        <motion.div
          initial={{ opacity: 0, y: 25, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1], delay: 0.3 }}
          className="relative w-full max-w-2xl flex-1 min-h-0 rounded-3xl overflow-hidden"
        >
          {/* Ambient glow */}
          <div className="absolute -inset-2 blur-[30px] opacity-25 bg-gradient-to-br from-teal-400 to-cyan-600 rounded-[2.5rem] -z-10 pointer-events-none" />

          <div className="relative bg-[#fbfcff]/[0.06] backdrop-blur-2xl w-full h-full rounded-3xl border border-white/10 shadow-[0_20px_50px_-10px_rgba(0,0,0,0.4)] flex flex-col overflow-hidden">

            {/*  Orb + status area  */}
            <div className="flex flex-col items-center pt-8 pb-4 px-6 shrink-0">
              <VoiceOrb phase={phase} isSpeaking={isSpeaking} />

              {/* Status text */}
              <motion.p
                key={statusText}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                className={`mt-4 text-sm font-medium tracking-wide ${
                  phase === PHASE.ERROR
                    ? 'text-red-400'
                    : isListening
                    ? 'text-teal-300'
                    : isSpeaking
                    ? 'text-cyan-300'
                    : 'text-white/50'
                }`}
              >
                {isListening ? '🎤 Listening...' : isSpeaking ? '🔊 Speaking...' : statusText}
              </motion.p>

              {/* Error message */}
              {phase === PHASE.ERROR && errorMessage && (
                <p className="mt-2 text-xs text-red-400/80 text-center max-w-sm">{errorMessage}</p>
              )}

              {/*  Action buttons  */}
              <div className="flex items-center gap-3 mt-5">
                {phase === PHASE.IDLE || phase === PHASE.ERROR ? (
                  <motion.button
                    whileHover={{ scale: 1.05 }}
                    whileTap={{ scale: 0.97 }}
                    onClick={startConversation}
                    className="flex items-center gap-2 px-6 py-3 rounded-full bg-gradient-to-r from-teal-500 to-cyan-500 text-white font-semibold text-sm shadow-lg hover:shadow-teal-500/30 transition-all duration-300"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5">
                      <path d="M8.25 4.5a3.75 3.75 0 117.5 0v8.25a3.75 3.75 0 11-7.5 0V4.5z" />
                      <path d="M6 10.5a.75.75 0 01.75.75v1.5a5.25 5.25 0 1010.5 0v-1.5a.75.75 0 011.5 0v1.5a6.751 6.751 0 01-6 6.709v2.291h3a.75.75 0 010 1.5h-7.5a.75.75 0 010-1.5h3v-2.291a6.751 6.751 0 01-6-6.709v-1.5A.75.75 0 016 10.5z" />
                    </svg>
                    {phase === PHASE.ERROR ? 'Try Again' : 'Start Conversation'}
                  </motion.button>
                ) : phase === PHASE.CONNECTING ? (
                  <div className="flex items-center gap-2 px-6 py-3 rounded-full bg-white/10 text-white/60 text-sm">
                    <svg className="w-4 h-4 animate-spin" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                    </svg>
                    Connecting...
                  </div>
                ) : (
                  <motion.button
                    whileHover={{ scale: 1.05 }}
                    whileTap={{ scale: 0.97 }}
                    onClick={endConversation}
                    className="flex items-center gap-2 px-6 py-3 rounded-full bg-red-500/20 hover:bg-red-500/30 border border-red-500/30 text-red-400 hover:text-red-300 font-semibold text-sm transition-all duration-300"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5">
                      <path fillRule="evenodd" d="M12 2.25c-5.385 0-9.75 4.365-9.75 9.75s4.365 9.75 9.75 9.75 9.75-4.365 9.75-9.75S17.385 2.25 12 2.25zm-1.72 6.97a.75.75 0 10-1.06 1.06L10.94 12l-1.72 1.72a.75.75 0 101.06 1.06L12 13.06l1.72 1.72a.75.75 0 101.06-1.06L13.06 12l1.72-1.72a.75.75 0 10-1.06-1.06L12 10.94l-1.72-1.72z" clipRule="evenodd" />
                    </svg>
                    End Call
                  </motion.button>
                )}
              </div>
            </div>

            {/*  Transcript area  */}
            <div className="flex-1 min-h-0 mx-4 mb-4 rounded-2xl bg-black/20 border border-white/5 overflow-hidden flex flex-col">
              <div className="px-4 py-2.5 border-b border-white/5 shrink-0">
                <span className="text-[0.65rem] uppercase tracking-widest font-semibold text-white/30">
                  Live Transcript
                </span>
              </div>

              <div className="flex-1 overflow-y-auto p-4 space-y-3">
                <AnimatePresence>
                  {transcript.length === 0 ? (
                    <motion.p
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      className="text-white/20 text-sm text-center mt-4"
                    >
                      {phase === PHASE.CONNECTED
                        ? 'Conversation transcript will appear here...'
                        : 'Start a conversation to see the transcript'}
                    </motion.p>
                  ) : (
                    transcript.map((entry, i) => (
                      <TranscriptEntry key={i} entry={entry} />
                    ))
                  )}
                </AnimatePresence>
                <div ref={transcriptEndRef} />
              </div>
            </div>

          </div>
        </motion.div>

        {/* Disclaimer */}
        <p className="text-center text-[0.65rem] text-white/25 mt-3 font-light">
          Workmate AI Voice provides internal workplace information. Please verify critical details with the relevant department.
        </p>
      </div>
    </div>
  );
};

export default VoiceAgentPage;