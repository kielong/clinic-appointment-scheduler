"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Loader2, Send } from "lucide-react";
import { CHAT_STORAGE_PREFIX, useAuth } from "@/components/auth-provider";

interface Message {
  role: "user" | "assistant";
  content: string;
}

const SUGGESTIONS = {
  patient: [
    "Book a checkup next Tuesday at 10am",
    "What appointments do I have?",
    "I need to reschedule my appointment",
  ],
  admin: [
    "What appointments are coming up?",
    "Schedule a vaccination for Ethan Nguyen tomorrow at 2pm",
    "Find patients named Maria",
  ],
};

// The server's confirmation prompts end with this instruction.
const AWAITING_CONFIRMATION = /Reply "yes" to/;
const MAX_HISTORY = 30;

export function ChatInterface() {
  const { user } = useAuth();
  const role = user?.role ?? "patient";
  const storageKey = `${CHAT_STORAGE_PREFIX}${user?.id ?? "anon"}`;

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    try {
      const saved = sessionStorage.getItem(storageKey);
      if (saved) setMessages(JSON.parse(saved));
    } catch {
      // storage unavailable
    }
  }, [storageKey]);

  useEffect(() => {
    if (isStreaming) return;
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(messages.slice(-MAX_HISTORY)));
    } catch {
      // storage unavailable
    }
  }, [messages, isStreaming, storageKey]);

  const updateLastAssistantMessage = (transform: (content: string) => string) => {
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (!last || last.role !== "assistant") {
        return prev;
      }

      return [...prev.slice(0, -1), { ...last, content: transform(last.content) }];
    });
  };

  const scrollToBottom = useCallback(() => {
    setTimeout(() => {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    }, 50);
  }, []);

  useEffect(scrollToBottom, [messages.length, scrollToBottom]);

  const sendMessage = async (raw?: string) => {
    const text = (raw ?? input).trim();
    if (!text || isStreaming) return;

    const history = messages.filter((m) => m.content.trim()).slice(-MAX_HISTORY);
    setInput("");
    setMessages((prev) => [
      ...prev,
      { role: "user", content: text },
      { role: "assistant", content: "" },
    ]);
    setIsStreaming(true);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ message: text, history }),
      });

      if (res.status === 401) {
        window.location.href = "/login";
        return;
      }
      if (!res.ok || !res.body) {
        throw new Error("Chat request failed");
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const rawData = line.slice(6).trim();
          if (!rawData) continue;

          let data: { type?: string; content?: string; error?: string };
          try {
            data = JSON.parse(rawData);
          } catch {
            // skip malformed lines
            continue;
          }

          if (data.type === "reset") {
            updateLastAssistantMessage(() => "");
          } else if (data.type === "token" && data.content) {
            updateLastAssistantMessage((content) => content + data.content);
            scrollToBottom();
          } else if (data.type === "error") {
            throw new Error(data.error || "Streaming failed");
          }
        }
      }
      updateLastAssistantMessage((content) =>
        content.trim() ? content : "Sorry, I didn't catch that. Could you rephrase?"
      );
    } catch (err) {
      const message = err instanceof Error && err.message !== "Chat request failed" ? err.message : "";
      updateLastAssistantMessage(
        () => message || "Sorry, something went wrong. Please try again."
      );
    } finally {
      setIsStreaming(false);
      inputRef.current?.focus();
    }
  };

  const last = messages[messages.length - 1];
  const awaitingConfirmation =
    !isStreaming && last?.role === "assistant" && AWAITING_CONFIRMATION.test(last.content);

  return (
    <div className="flex h-full flex-col">
      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
            <p className="text-gray-500">
              {role === "patient"
                ? `Hi ${user?.full_name.split(" ")[0] ?? "there"}! Tell me what kind of visit you need and when.`
                : "Ask about patients, or schedule, reschedule, or cancel appointments."}
            </p>
            <div className="flex max-w-xl flex-wrap justify-center gap-2">
              {SUGGESTIONS[role].map((s) => (
                <button
                  key={s}
                  onClick={() => sendMessage(s)}
                  className="rounded-full border border-gray-200 bg-white px-3 py-1.5 text-sm text-gray-600 transition-colors hover:border-indigo-300 hover:text-indigo-700"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((msg, i) => {
          const isThinking =
            msg.role === "assistant" && !msg.content && isStreaming;
          return (
            <div
              key={i}
              className={`flex animate-fade-in-up ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[75%] rounded-lg px-4 py-2 text-sm whitespace-pre-wrap ${
                  msg.role === "user"
                    ? "bg-indigo-500 text-white"
                    : "bg-slate-100 text-gray-900"
                }`}
              >
                {isThinking ? (
                  <span className="inline-flex items-center gap-1.5 py-0.5">
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-indigo-400" />
                    <span className="text-gray-400 text-xs">Thinking…</span>
                  </span>
                ) : (
                  msg.content
                )}
              </div>
            </div>
          );
        })}
        {awaitingConfirmation && (
          <div className="flex gap-2">
            <Button size="sm" onClick={() => sendMessage("yes")}>
              Confirm
            </Button>
            <Button size="sm" variant="outline" onClick={() => sendMessage("no")}>
              Don&apos;t confirm
            </Button>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-gray-200 p-4">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            sendMessage();
          }}
          className="flex gap-2"
        >
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            maxLength={4000}
            placeholder={
              role === "patient"
                ? "e.g. I'd like a checkup next Tuesday at 10am"
                : "Ask about patients or appointments..."
            }
            aria-label="Message"
            className="flex-1 rounded-lg border border-gray-200 px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
            disabled={isStreaming}
          />
          <Button type="submit" size="icon" disabled={isStreaming || !input.trim()} aria-label="Send">
            {isStreaming ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </Button>
        </form>
      </div>
    </div>
  );
}
