// Baskets whose price Aimo has signed and that are waiting to be placed.
// "You can approve" lists what the current role may pass; the rest wait for
// someone more senior. Each card answers: how much, who started it, why it
// needs a check, and how long the price holds.
import { ArrowRight } from "lucide-react";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import type { Basket } from "../api/types";
import { Card, Chip, EmptyState, ErrorNote, PageTitle, Skeleton } from "../components/ui";
import { useWaitingBaskets } from "../features/approvals/useWaitingBaskets";
import { day, money, time } from "../lib/format";

const byExpiry = (a: Basket, b: Basket) => (a.quote_expires ?? "").localeCompare(b.quote_expires ?? "");

function BasketCard({ b, canAct }: { b: Basket; canAct: boolean }) {
  const { t } = useTranslation();
  const level = b.policy?.level ?? "chef";
  return (
    <Card className="p-5 sm:p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="tabular text-3xl font-extrabold tracking-[-0.03em]">{money(b.net_total)} <span className="text-sm font-normal text-ink-soft">{t("basket.net")}</span></p>
        <p className="tabular text-sm text-ink-soft">{t("basket.total")} {money(b.gross_total)}</p>
      </div>
      <p className="mt-1 text-sm">{t("approvals.lines", { count: b.lines?.length ?? 0 })} · {t("basket.delivery")} {day(b.lines?.[0]?.delivery?.delivery_date)} · {t("approvals.startedBy", { who: t(`role.${b.created_by ?? "chef"}`) })}</p>
      <ul className="mt-1 text-sm text-ink-soft" lang="en">{(b.lines ?? []).slice(0, 3).map((l) => <li key={l.product_id} className="truncate">{l.name}</li>)}</ul>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {level !== "chef" && <Chip tone="info">{t("approvals.needs", { who: t(`role.gen_${level}`, { defaultValue: level }) })}</Chip>}
        {(b.policy?.reasons ?? []).map((r) => <Chip key={r} tone="warn"><span lang="en">{r}</span></Chip>)}
        {b.quote_expires && <Chip>{t("approvals.expires", { time: time(b.quote_expires) })}</Chip>}
      </div>
      {canAct && (
        <Link to={`/?basket=${b.basket_id}`} className="mt-3 inline-flex min-h-11 items-center gap-2 rounded-full bg-primary px-5 text-sm font-semibold text-on-primary hover:bg-primary-hover">
          {t("approvals.open")} <ArrowRight className="size-4" aria-hidden />
        </Link>
      )}
    </Card>
  );
}

export function ApprovalsPage() {
  const { t } = useTranslation();
  const { baskets, others, loading, error } = useWaitingBaskets();
  useEffect(() => { document.title = `${t("nav.approvals")} · ${t("app.name")}`; }, [t]);

  return (
    <>
      <PageTitle>{t("approvals.title")}</PageTitle>
      {loading && <Skeleton lines={4} />}
      {!!error && <ErrorNote error={error} />}
      {!loading && !error && (<>
        <h2 className="mb-3 text-xl font-extrabold tracking-tight">{t("approvals.forYou")}</h2>
        {baskets.length === 0
          ? <EmptyState title={t("approvals.forYouEmpty")} hint={t("approvals.emptyHint")} />
          : <ul className="grid gap-4 lg:grid-cols-2">{[...baskets].sort(byExpiry).map((b) => <li key={b.basket_id}><BasketCard b={b} canAct /></li>)}</ul>}
        {others.length > 0 && (<>
          <h2 className="mb-3 mt-8 text-xl font-extrabold tracking-tight">{t("approvals.forOthers")}</h2>
          <ul className="grid gap-4 lg:grid-cols-2">{[...others].sort(byExpiry).map((b) => <li key={b.basket_id}><BasketCard b={b} canAct={false} /></li>)}</ul>
        </>)}
      </>)}
    </>
  );
}
