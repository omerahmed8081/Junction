// Baskets whose price Aimo has signed and that are waiting for approval.
// Each card answers: how much, why it needs a check, and how long the price holds.
import { ArrowRight } from "lucide-react";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Card, Chip, EmptyState, ErrorNote, PageTitle, Skeleton } from "../components/ui";
import { useWaitingBaskets } from "../features/approvals/useWaitingBaskets";
import { day, money, time } from "../lib/format";

export function ApprovalsPage() {
  const { t } = useTranslation();
  const { baskets, loading, error } = useWaitingBaskets();
  useEffect(() => { document.title = `${t("nav.approvals")} · ${t("app.name")}`; }, [t]);
  const sorted = [...baskets].sort((a, b) => (a.quote_expires ?? "").localeCompare(b.quote_expires ?? ""));

  return (
    <>
      <PageTitle>{t("approvals.title")}</PageTitle>
      {loading && <Skeleton lines={4} />}
      {!!error && <ErrorNote error={error} />}
      {!loading && sorted.length === 0 && <EmptyState title={t("approvals.empty")} hint={t("approvals.emptyHint")} />}
      <ul className="grid gap-3 lg:grid-cols-2">
        {sorted.map((b) => (
          <li key={b.basket_id}>
            <Card className="p-4">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="tabular text-xl font-bold">{money(b.net_total)} <span className="text-sm font-normal text-ink-soft">{t("basket.net")}</span></p>
                <p className="tabular text-sm text-ink-soft">{t("basket.total")} {money(b.gross_total)}</p>
              </div>
              <p className="mt-1 text-sm">{t("approvals.lines", { count: b.lines?.length ?? 0 })} · {t("basket.delivery")} {day(b.lines?.[0]?.delivery?.delivery_date)}</p>
              <ul className="mt-1 text-sm text-ink-soft" lang="en">{(b.lines ?? []).slice(0, 3).map((l) => <li key={l.product_id} className="truncate">{l.name}</li>)}</ul>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {(b.policy?.reasons ?? []).map((r) => <Chip key={r} tone="warn"><span lang="en">{r}</span></Chip>)}
                {b.quote_expires && <Chip>{t("approvals.expires", { time: time(b.quote_expires) })}</Chip>}
              </div>
              <Link to={`/?basket=${b.basket_id}`} className="mt-3 inline-flex min-h-11 items-center gap-2 rounded-lg bg-primary px-4 text-sm font-semibold text-on-primary hover:bg-primary-hover">
                {t("approvals.open")} <ArrowRight className="size-4" aria-hidden />
              </Link>
            </Card>
          </li>
        ))}
      </ul>
      <p className="mt-4 text-xs text-ink-soft">{t("approvals.thisBrowser")}</p>
    </>
  );
}
