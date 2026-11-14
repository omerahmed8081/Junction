// The home screen: chat on the left, the basket in the middle, totals and
// "running low" on the right. On a tablet the chat moves into a drawer; on a
// phone everything stacks and the primary action sticks above the tab bar.
// The basket id lives in the URL (?basket=…), so refresh, Back and shared links work.
import * as Dialog from "@radix-ui/react-dialog";
import { MessageSquare, Plus, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import type { LineEdit, OrderResponse } from "../api/types";
import { Banner, Button, Card, EmptyState, ErrorNote, Skeleton } from "../components/ui";
import { ActionPanel, MobileActionBar, useNow } from "../features/order/ActionPanel";
import { BasketLine } from "../features/order/BasketLine";
import { ChatPanel } from "../features/order/ChatPanel";
import { StatusSteps } from "../features/order/StatusSteps";
import { SuggestionsPanel } from "../features/order/SuggestionsPanel";
import { useBasketActions, useBasketQuery } from "../features/order/useBasket";
import { codeLabel, day, dayTime, timeLeft } from "../lib/format";
import { clearChat, forgetBasket, getCurrentBasket, setCurrentBasket } from "../lib/session";

export function OrderPage() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  // The address wins; without one, reopen the basket this browser was working on.
  const basketId = params.get("basket") ?? getCurrentBasket();
  useEffect(() => {
    if (!params.get("basket") && basketId) setParams({ basket: basketId }, { replace: true });
  }, [params, basketId, setParams]);
  useEffect(() => { if (basketId) setCurrentBasket(basketId); }, [basketId]);
  const [chatOpen, setChatOpen] = useState(false);
  const [outcome, setOutcome] = useState<OrderResponse | null>(null);
  const now = useNow();

  const open = (id: string) => setParams({ basket: id }, { replace: !basketId });
  const query = useBasketQuery(basketId);
  const basket = query.data;
  // The chat keeps adding to the open basket until it is ordered. Adding to a
  // basket whose price Aimo signed voids the signature; the reply says so.
  const chatTarget = basket && basket.status !== "draft" && basket.status !== "quoted" ? null : basketId;
  const [confirmDelete, setConfirmDelete] = useState(false);
  const actions = useBasketActions(basketId, (b) => { if (b.basket_id !== basketId) open(b.basket_id); },
                                   chatTarget, () => { setConfirmDelete(false); setParams({}); });

  useEffect(() => { document.title = `${basket ? t(`status.${basket.status}`) + " · " : ""}${t("basket.title")} · ${t("app.name")}`; }, [basket?.status, t, basket]);
  // A new basket clears the last outcome, except right after an order is placed:
  // the page then empties itself but keeps the "Order placed" note and its link.
  const keepOutcome = useRef(false);
  useEffect(() => {
    if (keepOutcome.current) { keepOutcome.current = false; return; }
    setOutcome(null);
  }, [basketId]);

  const gone = query.error instanceof ApiError && query.error.kind === "missing";
  useEffect(() => {
    if (gone && basketId) { forgetBasket(basketId); if (getCurrentBasket() === basketId) setCurrentBasket(null); }
  }, [gone, basketId]);

  const busy = actions.edit.isPending || actions.confirm.isPending || actions.approve.isPending || actions.remove.isPending;
  const deletable = basket?.status === "draft";
  const editable = !!basket && basket.status !== "ordered" && basket.status !== "blocked";
  const lines = basket?.lines ?? [];
  const cutoff = lines.map((l) => l.delivery?.closing_time).filter((c): c is string => !!c).sort()[0];
  const left = timeLeft(cutoff, now);

  const send = (message: string) => { setOutcome(null); actions.chat.mutate(message); };
  const edit = (e: LineEdit) => { setOutcome(null); actions.edit.mutate({ edits: [e] }); };
  const confirm = () => { setOutcome(null); actions.confirm.mutate(); };
  // Once the order is placed the basket is done: clear it and the chat so the next
  // message starts a fresh order. Adjusted or refused baskets stay open to fix.
  const approve = () => actions.approve.mutate(undefined, {
    onSuccess: (r) => {
      setOutcome(r);
      if (r.outcome !== "ordered") return;
      keepOutcome.current = true;
      setCurrentBasket(null);
      clearChat();
      actions.chat.reset();
      setParams({});
    },
  });
  const startNew = () => { setCurrentBasket(null); setParams({}); clearChat(); setOutcome(null); actions.chat.reset(); };
  const panelProps = basket && { basket, busy, confirming: actions.confirm.isPending, approving: actions.approve.isPending, onConfirm: confirm, onApprove: approve };
  const actionError = actions.edit.error || actions.confirm.error || actions.approve.error || actions.draftSuggestion.error || actions.remove.error;

  const chat = <ChatPanel pending={actions.chat.isPending} error={actions.chat.error} onSend={(m) => { send(m); }} />;

  return (
    <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_19rem] xl:grid-cols-[21rem_minmax(0,1fr)_20rem]">
      {/* Chat: its own column on a wide screen, a drawer everywhere else. */}
      <aside className="hidden xl:block">
        <Card className="sticky top-20 flex h-[calc(100dvh-7rem)] flex-col p-5">{chat}</Card>
      </aside>
      <Dialog.Root open={chatOpen} onOpenChange={setChatOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-40 bg-black/45 xl:hidden" />
          <Dialog.Content aria-describedby={undefined} className="fixed inset-y-0 left-0 z-50 flex w-[min(26rem,100vw)] flex-col bg-surface p-4 shadow-xl xl:hidden">
            <Dialog.Title className="sr-only">{t("chat.title")}</Dialog.Title>
            <Dialog.Close asChild><Button variant="ghost" aria-label={t("chat.close")} className="absolute right-2 top-2 min-w-11 px-0"><X className="size-5" aria-hidden /></Button></Dialog.Close>
            <div className="min-h-0 flex-1">{chat}</div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      {/* The basket. */}
      <section aria-labelledby="basket-title" className="min-w-0 space-y-4">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <h1 id="basket-title" className="text-3xl font-extrabold leading-none tracking-[-0.04em] sm:text-4xl">{t("basket.title")}</h1>
          {basket && <StatusSteps status={basket.status} />}
          <div className="ml-auto flex gap-2">
            <Button className="xl:hidden" icon={<MessageSquare className="size-4" aria-hidden />} onClick={() => setChatOpen(true)}>{t("chat.open")}</Button>
            {basket && <Button variant="ghost" icon={<Plus className="size-4" aria-hidden />} onClick={startNew}>{t("basket.new")}</Button>}
            {deletable && (
              <Button variant="ghost" className="text-danger hover:bg-danger-soft" disabled={busy}
                icon={<Trash2 className="size-4" aria-hidden />} onClick={() => setConfirmDelete(true)}>
                {t("basket.delete")}
              </Button>
            )}
          </div>
        </div>

        {/* Deleting cannot be undone, so it asks once. Focus starts on Cancel. */}
        <Dialog.Root open={confirmDelete} onOpenChange={(o) => { if (!actions.remove.isPending) setConfirmDelete(o); }}>
          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 z-40 bg-black/45" />
            <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(28rem,92vw)] -translate-x-1/2 -translate-y-1/2 rounded-[1.75rem] bg-surface p-6 shadow-2xl"
              onOpenAutoFocus={(e) => { e.preventDefault(); (e.currentTarget as HTMLElement).querySelector<HTMLButtonElement>("[data-cancel]")?.focus(); }}>
              <Dialog.Title className="text-2xl font-extrabold tracking-[-0.03em]">{t("basket.deleteTitle")}</Dialog.Title>
              <Dialog.Description className="mt-1 text-sm text-ink-soft">{t("basket.deleteBody", { count: lines.length })}</Dialog.Description>
              <div className="mt-5 flex flex-wrap justify-end gap-2">
                <Dialog.Close asChild><Button data-cancel disabled={actions.remove.isPending}>{t("service.cancel")}</Button></Dialog.Close>
                <Button variant="primary" className="bg-danger hover:bg-danger/90" loading={actions.remove.isPending}
                  icon={<Trash2 className="size-4" aria-hidden />} onClick={() => actions.remove.mutate()}>
                  {actions.remove.isPending ? t("basket.deleting") : t("basket.deleteConfirm")}
                </Button>
              </div>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>

        {basket?.demo_scenario && <Banner tone="warn" title={`DEMO SCENARIO: ${basket.demo_scenario}`} />}

        {/* What approving produced: ordered, adjusted, refused, or a security stop. */}
        {outcome?.outcome === "ordered" && outcome.order && (
          <Banner tone="ok" alert title={t("outcome.ordered", { id: outcome.order.order_id })}
            actions={<Link className="font-semibold underline" to={`/activity?order=${outcome.order.order_id}`}>{t("action.viewOrder")}</Link>}>
            {t("outcome.orderedHint", { date: day(outcome.order.delivery_date) })}
          </Banner>
        )}
        {outcome?.outcome === "adjusted" && <Banner tone="warn" alert title={t("outcome.adjusted")}><span lang="en">{outcome.reply}</span></Banner>}
        {outcome?.outcome === "refused" && (outcome.refusal?.security_event ? (
          <Banner tone="danger" alert title={t("outcome.security")}
            actions={<Link className="font-semibold underline" to="/activity">{t("action.seeActivity")}</Link>}>
            <span lang="en">{outcome.refusal.detail}</span> {t("outcome.securityHint")} {t("outcome.nothingOrdered")}
          </Banner>
        ) : (
          <Banner tone="danger" alert title={`${t("outcome.refused")}: ${codeLabel("refusal", outcome.refusal?.reason ?? "")}`}
            actions={<Link className="font-semibold underline" to="/rules">{t("action.openRules")}</Link>}>
            <span lang="en">{outcome.refusal?.detail ?? outcome.reply}</span> {t("outcome.nothingOrdered")}
          </Banner>
        ))}
        {!!actionError && <ErrorNote error={actionError} />}

        {!basketId && <EmptyState title={t("basket.empty")} hint={t("basket.emptyHint")}
          action={<Button className="xl:hidden" variant="primary" icon={<MessageSquare className="size-4" aria-hidden />} onClick={() => setChatOpen(true)}>{t("chat.title")}</Button>} />}
        {basketId && query.isLoading && <Card className="p-4"><Skeleton lines={5} /></Card>}
        {gone && <EmptyState title={t("basket.notFound")} action={<Button variant="primary" onClick={startNew}>{t("basket.new")}</Button>} />}
        {query.isError && !gone && <ErrorNote error={query.error} onRetry={() => void query.refetch()} />}

        {basket && (
          <>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
              <label htmlFor="deliver-by" className="font-semibold">{t("basket.deliverBy")}</label>
              <input id="deliver-by" type="date" value={basket.requested_date ?? ""} disabled={!editable || busy}
                onChange={(e) => e.target.value && actions.edit.mutate({ edits: [], requestedDate: e.target.value })}
                className="tabular min-h-11 rounded-full border border-border-strong bg-surface px-4 text-base disabled:opacity-60" />
              {cutoff && basket.status !== "ordered" && (
                <p className={left ? "text-ink-soft" : "font-semibold text-danger"}>
                  {left ? <>{t("basket.orderBefore", { when: dayTime(cutoff) })} · <span className="tabular">{t("basket.timeLeft", { time: left })}</span></> : t("basket.cutoffPassed")}
                </p>
              )}
            </div>

            <ul className="space-y-3">
              {lines.map((line) => <li key={line.product_id}><BasketLine line={line} editable={editable} busy={busy} onEdit={edit} /></li>)}
            </ul>
            {editable && <p className="rounded-(--radius-card) border border-dashed border-border-strong p-4 text-center text-sm text-ink-soft">{t("basket.addMore")}</p>}
          </>
        )}
      </section>

      {/* Totals, the action, and what is running low. */}
      <aside className="space-y-5 md:sticky md:top-20 md:max-h-[calc(100dvh-6rem)] md:self-start md:overflow-y-auto md:col-start-2 xl:col-start-3">
        {panelProps && <Card className="p-5"><ActionPanel {...panelProps} /></Card>}
        <Card className="p-5">
          <SuggestionsPanel
            draftingId={actions.draftSuggestion.isPending ? actions.draftSuggestion.variables ?? null : null}
            onDraft={(id) => { setOutcome(null); actions.draftSuggestion.mutate(id); }}
            onServiceBasket={open}
          />
        </Card>
      </aside>

      {panelProps && <MobileActionBar {...panelProps} />}
    </div>
  );
}
