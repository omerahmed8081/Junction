// "Running low": what the kitchen will run out of, with the sum behind it,
// and a button that turns one into a draft basket. Reads the ledger only,
// so it reloads freely without calling Aimo.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import { Button, Card, Chip, EmptyState, ErrorNote, Skeleton } from "../../components/ui";
import { amount, day, productName } from "../../lib/format";
import { ServiceDialog } from "./ServiceDialog";

type Props = { draftingId: string | null; onDraft: (productId: string) => void; onServiceBasket: (basketId: string) => void };

export function SuggestionsPanel({ draftingId, onDraft, onServiceBasket }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const query = useQuery({ queryKey: ["suggestions"], queryFn: api.suggestions });
  const list = query.data?.suggestions ?? [];

  return (
    <section aria-labelledby="low-title" className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h2 id="low-title" className="text-lg font-bold">{t("suggest.title")}</h2>
        {list.length > 0 && (
          <span className="inline-flex min-w-6 items-center justify-center rounded-full bg-warn px-2 text-xs font-bold text-white">
            <span aria-hidden>{list.length}</span><span className="sr-only">{t("suggest.count", { count: list.length })}</span>
          </span>
        )}
      </div>
      {query.data && <p className="text-xs text-ink-soft">{t("suggest.asOf", { date: day(query.data.as_of), until: day(query.data.window_until) })}</p>}

      {query.isLoading && <Skeleton lines={4} />}
      {query.isError && <ErrorNote error={query.error} onRetry={() => void query.refetch()} />}
      {query.data && list.length === 0 && <EmptyState title={t("suggest.none")} hint={t("suggest.noneHint", { until: day(query.data.window_until) })} />}

      <ul className="space-y-3">
        {list.map((s) => (
          <li key={s.product_id}>
            <Card className="p-3">
              <p className="text-sm font-semibold">{productName(s.product_id, s.name)}</p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                <Chip tone="warn">{t("suggest.short", { amount: amount(s.shortfall, s.unit, 1) })}</Chip>
                {s.runs_out_on && <Chip>{t("suggest.runsOut", { date: day(s.runs_out_on) })}</Chip>}
              </div>
              {/* The sum, as a small table of four numbers: readable, and read aloud in order. */}
              <dl className="tabular mt-2 grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs text-ink-soft">
                <dt>{t("suggest.needs")}</dt><dd className="text-right text-ink">{amount(s.needed, s.unit, 1)}</dd>
                <dt>+ {t("suggest.cover")}</dt><dd className="text-right text-ink">{amount(s.buffer, s.unit, 1)}</dd>
                <dt>− {t("suggest.stock")}</dt><dd className="text-right text-ink">{amount(s.stock, s.unit, 1)}</dd>
                <dt>− {t("suggest.onOrder")}</dt><dd className="text-right text-ink">{amount(s.on_order, s.unit, 1)}</dd>
              </dl>
              {!!s.dishes?.length && <p className="mt-2 text-xs text-ink-soft" lang="en">{t("suggest.usedIn", { dishes: s.dishes.join(", ") })}</p>}
              <Button size="sm" variant="primary" className="mt-3 w-full" loading={draftingId === s.product_id} disabled={!!draftingId} onClick={() => onDraft(s.product_id)}>
                {draftingId === s.product_id ? t("suggest.drafting") : t("suggest.draft")}
              </Button>
            </Card>
          </li>
        ))}
      </ul>

      <Button className="w-full" onClick={() => setOpen(true)}>{t("suggest.cooked")}</Button>
      <ServiceDialog open={open} onOpenChange={setOpen} onBasket={onServiceBasket} />
      <p className="text-xs text-ink-soft">{t("app.demoData")}</p>
    </section>
  );
}
