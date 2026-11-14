// What happened: placed orders (from the backend) and the agent's steps (kept
// by this browser, because the backend does not store them yet). The toggle
// filters the steps to what crossed over to Aimo, with a note on what never does.
import { useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import { ShieldAlert } from "lucide-react";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { Banner, Button, Card, EmptyState, ErrorNote, PageTitle, Skeleton } from "../components/ui";
import { amount, day, dayTime, money, time } from "../lib/format";
import { clearSteps, useSteps } from "../lib/session";

export function ActivityPage() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const aimoOnly = params.get("view") === "aimo";
  const openOrder = params.get("order");
  const orders = useQuery({ queryKey: ["orders"], queryFn: api.orders });
  const steps = useSteps();
  const shown = [...(aimoOnly ? steps.filter((s) => s.crossed_gateway) : steps)].reverse();
  useEffect(() => { document.title = `${t("nav.activity")} · ${t("app.name")}`; }, [t]);

  const setView = (view: "all" | "aimo") => { const next = new URLSearchParams(params); if (view === "aimo") next.set("view", "aimo"); else next.delete("view"); setParams(next, { replace: true }); };

  return (
    <>
      <PageTitle aside={
        <div role="group" aria-label={t("activity.show")} className="inline-flex overflow-hidden rounded-lg border border-border-strong">
          {(["all", "aimo"] as const).map((v) => (
            <button key={v} type="button" aria-pressed={aimoOnly === (v === "aimo")} onClick={() => setView(v)}
              className={clsx("min-h-11 cursor-pointer px-3 text-sm font-semibold", aimoOnly === (v === "aimo") ? "bg-primary text-on-primary" : "bg-surface hover:bg-sunken")}>
              {t(`activity.${v}`)}
            </button>
          ))}
        </div>}>
        {t("activity.title")}
      </PageTitle>
      {aimoOnly && <Banner tone="info" className="mb-4">{t("activity.aimoNote")}</Banner>}

      <div className="grid gap-5 xl:grid-cols-2">
        <Card className="p-4">
          <h2 className="text-lg font-bold">{t("activity.orders")}</h2>
          {orders.isLoading && <div className="mt-3"><Skeleton /></div>}
          {orders.isError && <div className="mt-3"><ErrorNote error={orders.error} onRetry={() => void orders.refetch()} /></div>}
          {orders.data?.length === 0 && <p className="mt-3 text-sm text-ink-soft">{t("activity.noOrders")}</p>}
          <ul className="mt-3 space-y-2">
            {(orders.data ?? []).map((o) => (
              <li key={o.order_id}>
                <details open={openOrder === o.order_id} className="rounded-lg border border-border">
                  <summary className="flex min-h-11 cursor-pointer flex-wrap items-center justify-between gap-x-4 gap-y-1 px-3 py-2 text-sm">
                    <span><span className="font-semibold">{o.order_id}</span> <span className="text-ink-soft">· {dayTime(o.placed_at)}</span></span>
                    <span className="tabular">{t("basket.delivery")} {day(o.delivery_date)} · <span className="font-semibold">{money(o.net_total)}</span> {t("basket.net").toLowerCase()}</span>
                  </summary>
                  <div className="border-t border-border p-3 text-sm">
                    <p className="font-semibold">{t("activity.lines")}</p>
                    <ul className="mt-1 space-y-1">
                      {(o.lines ?? []).map((l) => <li key={l.product_id} className="tabular"><span lang="en">{l.name}</span> · {amount(l.quantity_sales_units, l.sales_unit)} · {money(l.line_net)}</li>)}
                    </ul>
                    <p className="tabular mt-2 text-ink-soft">{t("basket.vat")} {money(o.vat_total)} · {t("basket.total")} {money(o.gross_total)} · {t("quote.id", { id: o.quote_id })}</p>
                    {o.confirmation_jws && (
                      <details className="mt-1">
                        <summary className="inline-flex min-h-9 cursor-pointer items-center font-semibold text-primary">{t("activity.documents")}</summary>
                        <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-sunken p-2 text-xs">{o.confirmation_jws}</pre>
                      </details>
                    )}
                  </div>
                </details>
              </li>
            ))}
          </ul>
        </Card>

        <Card className="p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-lg font-bold">{t("activity.steps")}</h2>
            {steps.length > 0 && <Button size="sm" variant="ghost" onClick={clearSteps}>{t("activity.clear")}</Button>}
          </div>
          <p className="text-xs text-ink-soft">{t("activity.session")}</p>
          {shown.length === 0 ? <div className="mt-3"><EmptyState title={t("activity.noSteps")} /></div> : (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-[30rem] text-sm">
                <caption className="sr-only">{t("activity.steps")}</caption>
                <thead className="text-left text-xs uppercase tracking-wide text-ink-soft">
                  <tr><th scope="col" className="py-2 pr-3">{t("activity.time")}</th><th scope="col" className="py-2 pr-3">{t("activity.step")}</th><th scope="col" className="py-2 pr-3">{t("activity.detail")}</th><th scope="col" className="py-2">{t("activity.toAimo")}</th></tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {shown.map((s, i) => {
                    const security = s.security || /REFUSED|security/i.test(s.detail ?? "");
                    return (
                      <tr key={i} className={security ? "bg-danger-soft" : undefined}>
                        <td className="tabular py-2 pr-3 align-top text-ink-soft">{time(s.at)}</td>
                        <td className="py-2 pr-3 align-top font-semibold">
                          {security && <span className="mr-1 inline-flex items-center gap-1 text-danger"><ShieldAlert className="size-4" aria-hidden />{t("activity.security")}:</span>}{s.step}
                        </td>
                        <td className="py-2 pr-3 align-top" lang="en">{s.detail}</td>
                        <td className="py-2 align-top">{s.crossed_gateway ? <span className="font-semibold">→ {t("activity.yes")}</span> : <span className="text-ink-soft">{t("activity.no")}</span>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
