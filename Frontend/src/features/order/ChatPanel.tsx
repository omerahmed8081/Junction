// The conversation with the agent: transcript, example requests, and the input.
// One way to start or add to a basket; the basket never depends on it.
import { ChevronDown, Send } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { Button, ErrorNote } from "../../components/ui";
import { time } from "../../lib/format";
import { useChat } from "../../lib/session";

type Props = { pending: boolean; error: unknown; onSend: (message: string) => void; notice?: string | null };

export function ChatPanel({ pending, error, onSend, notice }: Props) {
  const { t } = useTranslation();
  const messages = useChat();
  const [text, setText] = useState("");
  const [slow, setSlow] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [messages.length, pending]);
  // After 8 seconds say so: the gateway waits out Aimo's rate limit rather than failing.
  useEffect(() => {
    if (!pending) { setSlow(false); return; }
    const id = window.setTimeout(() => setSlow(true), 8000);
    return () => window.clearTimeout(id);
  }, [pending]);

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    const message = text.trim();
    if (!message || pending) return;
    onSend(message);
    setText("");
  };
  const onKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(); }
  };

  return (
    <section aria-labelledby="chat-title" className="flex h-full min-h-0 flex-col">
      <h2 id="chat-title" className="mb-3 text-lg font-bold">{t("chat.title")}</h2>

      <div className="min-h-40 flex-1 space-y-3 overflow-y-auto pr-1" aria-live="polite" aria-relevant="additions">
        {messages.length === 0 && (notice
          ? <p className="rounded-lg border border-ok/30 bg-ok-soft px-3 py-2 text-sm">{notice}</p>
          : <p className="text-sm text-ink-soft">{t("chat.empty")}</p>)}
        {messages.map((m, i) => m.role === "you" ? (
          <div key={i} className="ml-6 rounded-lg bg-primary-soft px-3 py-2 text-sm">
            <span className="sr-only">{t("chat.you")}: </span>{m.text}
          </div>
        ) : (
          <div key={i} className="mr-4 rounded-lg border border-border bg-surface px-3 py-2 text-sm">
            <p className="text-xs font-semibold text-ink-soft">{t("chat.agent")}</p>
            {/* The agent's sentences come from the backend in English. */}
            <p lang="en">{m.text}</p>
            {!!m.steps?.length && (
              <details className="group mt-2">
                <summary className="inline-flex min-h-9 cursor-pointer list-none items-center gap-1 rounded text-xs font-semibold text-primary">
                  <ChevronDown className="size-4 transition-transform group-open:rotate-180" aria-hidden />
                  {t("chat.steps", { count: m.steps.length })}
                </summary>
                <ol className="mt-1 space-y-1 border-l-2 border-border pl-3 text-xs text-ink-soft">
                  {m.steps.map((s, j) => (
                    <li key={j}><span className="tabular">{time(s.at)}</span> · <span className="font-semibold text-ink">{s.step}</span>{s.crossed_gateway ? " → Aimo" : ""}<br /><span lang="en">{s.detail}</span></li>
                  ))}
                </ol>
              </details>
            )}
          </div>
        ))}
        {pending && (
          <div className="mr-4 flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink-soft" role="status">
            <span className="flex gap-1" aria-hidden>
              {[0, 1, 2].map((i) => <span key={i} className="size-1.5 animate-pulse rounded-full bg-ink-soft" style={{ animationDelay: `${i * 150}ms` }} />)}
            </span>
            {slow ? t("chat.slow") : t("chat.working")}
          </div>
        )}
        {!!error && !pending && <ErrorNote error={error} />}
        <div ref={end} />
      </div>

      <div className="mt-3">
        <p className="mb-1 text-xs text-ink-soft">{t("chat.try")}</p>
        <div className="mb-3 flex flex-wrap gap-1.5">
          {(["ex1", "ex2", "ex3"] as const).map((k) => (
            <button key={k} type="button" lang="en" disabled={pending} onClick={() => onSend(t(`chat.${k}`))}
              className="min-h-9 cursor-pointer rounded-full border border-border-strong bg-surface px-3 text-left text-xs hover:bg-sunken disabled:opacity-55">
              {t(`chat.${k}`)}
            </button>
          ))}
        </div>
        <form onSubmit={submit}>
          <label htmlFor="chat-input" className="mb-1 block text-sm font-semibold">{t("chat.label")}</label>
          <div className="flex items-end gap-2">
            <textarea id="chat-input" rows={2} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey}
              placeholder={t("chat.placeholder")} enterKeyHint="send"
              className="min-h-11 flex-1 resize-none rounded-lg border border-border-strong bg-surface px-3 py-2 text-base placeholder:text-ink-soft/70" />
            <Button type="submit" variant="primary" loading={pending} disabled={!text.trim()} icon={<Send className="size-4" aria-hidden />}>{t("chat.send")}</Button>
          </div>
        </form>
      </div>
    </section>
  );
}
