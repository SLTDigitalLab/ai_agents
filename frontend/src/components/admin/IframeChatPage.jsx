import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import EnterpriseForm from "../forms/EnterpriseForm";
import LifestoreForm from "../forms/LifestoreForm";
import "./IframeChatPage.css";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "";

const API_URL = import.meta.env.VITE_API_URL || API_BASE_URL;

const AGENT_CONFIG = {
  asklifestore: {
    agentId: "lifestore",
    title: "Ask LifeStore",
    placeholder: "Ask about LifeStore products...",
    formToken: "[RENDER_LIFESTORE_FORM]",
  },
  askenterprise: {
    agentId: "enterprise",
    title: "Welcome To SLT-Mobitel Enterprise",
    placeholder: "Ask about Enterprise services...",
    formToken: "[RENDER_ENTERPRISE_FORM]",
  },
  workmateai: {
    agentId: "supervisor",
    title: "Welcome to Workmate AI",
    placeholder: "Ask about HR, Finance, IT, Admin, Network...",
    formToken: null,
  },
};

function createThreadId(agentId) {
  return `${agentId}-iframe-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2)}`;
}

function cleanBotMessage(text, formToken) {
  const value = String(text || "");
  if (!formToken) return value.trim();
  return value.replace(formToken, "").trim();
}

function sanitizeMarkdownBold(text) {
  if (!text) return text;

  const positions = [];
  const regex = /\*\*/g;
  let match;

  while ((match = regex.exec(text)) !== null) {
    positions.push(match.index);
  }

  if (positions.length % 2 === 0) return text;

  const last = positions[positions.length - 1];
  return text.slice(0, last) + text.slice(last + 2);
}

function extractAnswerFromText(responseText) {
  const text = String(responseText || "").trim();

  if (!text) return "";

  try {
    const data = JSON.parse(text);

    return String(
      data.answer ||
      data.response ||
      data.message ||
      data.content ||
      data.output ||
      ""
    ).trim();
  } catch {
    // Continue to SSE/plain text handling.
  }

  if (text.includes("data:")) {
    const lines = text
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);

    const chunks = [];

    for (const line of lines) {
      if (!line.startsWith("data:")) continue;

      const payload = line.replace(/^data:\s*/, "").trim();

      if (!payload || payload === "[DONE]") continue;

      try {
        const data = JSON.parse(payload);

        const value =
          data.answer ||
          data.response ||
          data.message ||
          data.content ||
          data.output ||
          data.token ||
          data.delta ||
          "";

        if (value) chunks.push(String(value));
      } catch {
        chunks.push(payload);
      }
    }

    return chunks.join("").trim();
  }

  return text;
}

function formatMessageTime(date) {
  const diffMs = Date.now() - new Date(date).getTime();
  const diffMinutes = Math.floor(diffMs / 60000);

  if (diffMinutes < 1) return "Just now";
  if (diffMinutes === 1) return "1 minute ago";
  return `${diffMinutes} minutes ago`;
}

function ThumbsUpIcon() {
  return (
    <svg viewBox="0 0 20 20" className="iframe-chat__feedback-icon">
      <path
        fill="currentColor"
        d="M1 8.998a1 1 0 0 1 1-1h.764a1.483 1.483 0 0 0-.076.506v5.996c0 .176.03.347.076.506H2a1 1 0 0 1-1-1V8.998ZM5.25 7.726a2 2 0 0 1 .944-1.697l3.476-2.14A1.5 1.5 0 0 1 12 5.139v2.363h2.5a2 2 0 0 1 1.96 2.4l-.782 3.908A2 2 0 0 1 13.72 15.5H5.25V7.726Z"
      />
    </svg>
  );
}

function ThumbsDownIcon() {
  return (
    <svg viewBox="0 0 20 20" className="iframe-chat__feedback-icon">
      <path
        fill="currentColor"
        d="M19 11.002a1 1 0 0 1-1 1h-.764c.047-.159.076-.33.076-.506V5.5c0-.176-.03-.347-.076-.506H18a1 1 0 0 1 1 1v5.008ZM14.75 12.274a2 2 0 0 1-.944 1.697l-3.476 2.14A1.5 1.5 0 0 1 8 14.861V12.5H5.5a2 2 0 0 1-1.96-2.4l.782-3.908A2 2 0 0 1 6.28 4.5h8.47v7.774Z"
      />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg viewBox="0 0 24 24" className="iframe-chat__send-icon">
      <path
        d="M4 12L20 4L15.5 20L12 13L4 12Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SuccessIcon() {
  return (
    <svg viewBox="0 0 24 24" className="iframe-chat__success-icon">
      <path
        d="M5 12.5L9.2 16.7L19 6.8"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function MarkdownMessage({ content }) {
  return (
    <div className="iframe-chat__content iframe-chat__markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ node, ...props }) => (
            <p className="iframe-chat__md-p" {...props} />
          ),
          ul: ({ node, ...props }) => (
            <ul className="iframe-chat__md-ul" {...props} />
          ),
          ol: ({ node, ...props }) => (
            <ol className="iframe-chat__md-ol" {...props} />
          ),
          li: ({ node, ...props }) => (
            <li className="iframe-chat__md-li" {...props} />
          ),
          h1: ({ node, ...props }) => (
            <h1 className="iframe-chat__md-heading" {...props} />
          ),
          h2: ({ node, ...props }) => (
            <h2 className="iframe-chat__md-heading" {...props} />
          ),
          h3: ({ node, ...props }) => (
            <h3 className="iframe-chat__md-heading" {...props} />
          ),
          a: ({ node, ...props }) => (
            <a
              className="iframe-chat__md-link"
              target="_blank"
              rel="noopener noreferrer"
              {...props}
            />
          ),
          table: ({ node, ...props }) => (
            <div className="iframe-chat__table-wrap">
              <table className="iframe-chat__table" {...props} />
            </div>
          ),
          th: ({ node, ...props }) => (
            <th className="iframe-chat__table-th" {...props} />
          ),
          td: ({ node, ...props }) => (
            <td className="iframe-chat__table-td" {...props} />
          ),
          tr: ({ node, ...props }) => (
            <tr className="iframe-chat__table-row" {...props} />
          ),
          code: ({ node, inline, children, ...props }) =>
            inline ? (
              <code className="iframe-chat__inline-code" {...props}>
                {children}
              </code>
            ) : (
              <code className="iframe-chat__block-code" {...props}>
                {children}
              </code>
            ),
        }}
      >
        {sanitizeMarkdownBold(content)}
      </ReactMarkdown>
    </div>
  );
}

function FeedbackButtons({
  messageIndex,
  agentId,
  threadId,
  userId,
  existingRating,
  onFeedback,
}) {
  const [rating, setRating] = useState(existingRating || null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    setRating(existingRating || null);
  }, [existingRating]);

  async function handleFeedback(newRating) {
    if (submitting) return;

    const finalRating = rating === newRating ? null : newRating;
    setSubmitting(true);

    try {
      if (!finalRating) {
        const response = await fetch(`${API_URL}/api/v1/feedback`, {
          method: "DELETE",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            agent_id: agentId,
            thread_id: threadId,
            message_index: messageIndex,
            rating: newRating,
            user_id: userId,
          }),
        });

        if (response.ok) {
          setRating(null);
          onFeedback?.(messageIndex, null);
        }

        return;
      }

      const response = await fetch(`${API_URL}/api/v1/feedback`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          agent_id: agentId,
          thread_id: threadId,
          message_index: messageIndex,
          rating: finalRating,
          user_id: userId,
        }),
      });

      if (response.ok) {
        setRating(finalRating);
        onFeedback?.(messageIndex, finalRating);
      }
    } catch (error) {
      console.error("Feedback submission failed:", error);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="iframe-chat__feedback">
      <button
        type="button"
        aria-label="Helpful"
        title="Helpful"
        disabled={submitting}
        className={
          rating === "up"
            ? "iframe-chat__feedback-button iframe-chat__feedback-button--active-like"
            : "iframe-chat__feedback-button"
        }
        onClick={() => handleFeedback("up")}
      >
        <ThumbsUpIcon />
      </button>

      <button
        type="button"
        aria-label="Not helpful"
        title="Not helpful"
        disabled={submitting}
        className={
          rating === "down"
            ? "iframe-chat__feedback-button iframe-chat__feedback-button--active-dislike"
            : "iframe-chat__feedback-button"
        }
        onClick={() => handleFeedback("down")}
      >
        <ThumbsDownIcon />
      </button>
    </div>
  );
}

function RequestSuccessCard({ isLifeStore, referenceNumber }) {
  if (isLifeStore) {
    return (
      <article className="iframe-chat__success-card">
        <div className="iframe-chat__success-panel">
          <SuccessIcon />
          <h2>Order Submitted!</h2>
          <p>Your LifeStore order request was submitted successfully.</p>
        </div>
      </article>
    );
  }

  return (
    <article className="iframe-chat__success-card">
      <div className="iframe-chat__success-panel">
        <SuccessIcon />
        <h2>Request Submitted!</h2>
        <p>The Enterprise team will contact you shortly.</p>

        {referenceNumber && (
          <div className="iframe-chat__reference-box">
            <span>YOUR REFERENCE NUMBER</span>
            <strong>{referenceNumber}</strong>
          </div>
        )}
      </div>
    </article>
  );
}

function InvalidIframeRoute() {
  return (
    <main className="iframe-chat">
      <header className="iframe-chat__header">
        <h1>Invalid iframe link</h1>
      </header>

      <section className="iframe-chat__body">
        <div className="iframe-chat__messages">
          <article className="iframe-chat__message iframe-chat__message--assistant">
            <div className="iframe-chat__bubble">
              <div className="iframe-chat__content">
                <p className="iframe-chat__md-p">
                  This iframe link is not available. Please use one of these:
                </p>

                <ul className="iframe-chat__md-ul">
                  <li className="iframe-chat__md-li">/asklifestore/iframe</li>
                  <li className="iframe-chat__md-li">/askenterprise/iframe</li>
                </ul>
              </div>
            </div>
          </article>
        </div>
      </section>
    </main>
  );
}

export default function IframeChatPage() {
  const { agentKey } = useParams();

  const config = useMemo(() => {
    return AGENT_CONFIG[agentKey] || null;
  }, [agentKey]);

  const [threadId, setThreadId] = useState(() =>
    createThreadId(AGENT_CONFIG[agentKey]?.agentId || "invalid")
  );
  const [userId] = useState(() => "iframe-user");
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content: "How can I assist you today?",
      timestamp: new Date(),
    },
  ]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [showRequestForm, setShowRequestForm] = useState(false);
  const [successInfo, setSuccessInfo] = useState(null);
  const [feedbackMap, setFeedbackMap] = useState({});

  const bottomRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    if (!config) return;

    setThreadId(createThreadId(config.agentId));
    setMessages([
      {
        role: "assistant",
        content: "How can I assist you today?",
        timestamp: new Date(),
      },
    ]);
    setInput("");
    setIsLoading(false);
    setShowRequestForm(false);
    setSuccessInfo(null);
    setFeedbackMap({});
  }, [config]);

  const isLifeStore = config?.agentId === "lifestore";
  const isEnterprise = config?.agentId === "enterprise";

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading, showRequestForm, successInfo]);

  function handleFormClose() {
    setShowRequestForm(false);
  }

  function handleFormSubmitted(payload = {}) {
    const referenceNumber =
      payload.referenceNumber ||
      payload.reference_number ||
      payload.lead_id ||
      payload.leadId ||
      payload?.bitrix_response?.result ||
      payload?.bitrixResponse?.result ||
      null;

    setShowRequestForm(false);
    setSuccessInfo({
      isLifeStore,
      referenceNumber,
    });
  }

  async function sendMessage(event) {
    event?.preventDefault();

    if (!config) return;

    const userMessage = input.trim();

    if (!userMessage || isLoading) return;

    setInput("");
    setIsLoading(true);
    setShowRequestForm(false);
    setSuccessInfo(null);

    setMessages((prev) => [
      ...prev,
      {
        role: "user",
        content: userMessage,
        timestamp: new Date(),
      },
    ]);

    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json, text/plain, text/event-stream",
        },
        body: JSON.stringify({
          agent_id: config.agentId,
          thread_id: threadId,
          user_id: userId,
          message: userMessage,
        }),
      });

      if (!response.ok) {
        throw new Error(`Request failed with status ${response.status}`);
      }

      const responseText = await response.text();
      const rawAnswer = extractAnswerFromText(responseText);

      const hasFormToken = Boolean(config.formToken) && rawAnswer.includes(config.formToken);
      const cleanedAnswer = cleanBotMessage(rawAnswer, config.formToken);

      if (hasFormToken) {
        setShowRequestForm(true);
      }

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content:
            cleanedAnswer ||
            "Sure, I can help you start the request. Please complete the form below.",
          timestamp: new Date(),
        },
      ]);
    } catch (error) {
      console.error("Iframe chat error:", error);

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content:
            "Sorry, I’m having trouble connecting to the server right now.",
          timestamp: new Date(),
          isError: true,
        },
      ]);
    } finally {
      setIsLoading(false);
      inputRef.current?.focus();
    }
  }

  if (!config) {
    return <InvalidIframeRoute />;
  }

  return (
    <main className="iframe-chat">
      <header className="iframe-chat__header">
        <h1>{config.title}</h1>
      </header>

      <section className="iframe-chat__body">
        <div className="iframe-chat__messages">
          {messages.map((message, index) => (
            <article
              key={`${message.role}-${index}`}
              className={`iframe-chat__message iframe-chat__message--${message.role} ${message.isError ? "iframe-chat__message--error" : ""
                }`}
            >
              <div className="iframe-chat__bubble">
                <MarkdownMessage content={message.content} />

                {message.role === "assistant" &&
                  index > 0 &&
                  message.content &&
                  !isLoading && (
                    <FeedbackButtons
                      messageIndex={index}
                      agentId={config.agentId}
                      threadId={threadId}
                      userId={userId}
                      existingRating={feedbackMap[index] || null}
                      onFeedback={(idx, rating) =>
                        setFeedbackMap((prev) => ({
                          ...prev,
                          [idx]: rating,
                        }))
                      }
                    />
                  )}
              </div>

              <time className="iframe-chat__time">
                {formatMessageTime(message.timestamp)}
              </time>
            </article>
          ))}

          {isLoading && (
            <article className="iframe-chat__message iframe-chat__message--assistant">
              <div className="iframe-chat__bubble iframe-chat__bubble--typing">
                <span />
                <span />
                <span />
              </div>
            </article>
          )}

          {showRequestForm && isLifeStore && (
            <div className="iframe-chat__embedded-form">
              <LifestoreForm
                onClose={handleFormClose}
                onCancel={handleFormClose}
                onSuccess={handleFormSubmitted}
                onSubmitted={handleFormSubmitted}
                onSubmitSuccess={handleFormSubmitted}
              />
            </div>
          )}

          {showRequestForm && isEnterprise && (
            <div className="iframe-chat__embedded-form">
              <EnterpriseForm
                onClose={handleFormClose}
                onCancel={handleFormClose}
                onSuccess={handleFormSubmitted}
                onSubmitted={handleFormSubmitted}
                onSubmitSuccess={handleFormSubmitted}
              />
            </div>
          )}

          {successInfo && (
            <RequestSuccessCard
              isLifeStore={successInfo.isLifeStore}
              referenceNumber={successInfo.referenceNumber}
            />
          )}

          <div ref={bottomRef} />
        </div>
      </section>

      <form className="iframe-chat__inputbar" onSubmit={sendMessage}>
        <input
          ref={inputRef}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={config.placeholder}
          disabled={isLoading}
        />

        <button
          type="submit"
          aria-label="Send message"
          disabled={isLoading || !input.trim()}
        >
          <SendIcon />
        </button>
      </form>
    </main>
  );
}
