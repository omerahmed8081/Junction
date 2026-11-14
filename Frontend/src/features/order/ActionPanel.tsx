// Totals, what extra check applies, the signed-price state, and the one
// primary action for the basket's current status. Rendered in the right-hand
// column on wide screens and again, compact, as a sticky bar on a phone.
import { clsx } from "clsx";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import type { Basket } from "../../api/types";
import { Banner, Button } from "../../components/ui";
import { money, time, timeLeft } from "../../lib/format";
import { canApprove, useRole } from "../../lib/session";

type Props = { basket: Basket; busy: boolean; confirming: boolean; approving: boolean; onConfirm: () => void; onApprove: () => void };

/** Re-render every 30 s so countdowns and expiry stay true without a refresh. */
export function useNow(): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const id = window.setInterval(() => setNow(Date.now()), 30_000); return () => window.clearInterval(id); }, []);
  return now;
}

function usePrimary({ basket, busy, confirming, approving, onConfirm, onApprove }: Props, now: number) {
  const { t } = useTranslation();
  const role = useRole();
  const expired = basket.status === "quoted" && !!basket.quote_expires && new Date(basket.quote_expires).getTime() <= now;
  if (!basket.lines?.length || basket.status === "ordered" || basket.status === "blocked") return { expired, button: null };
  if (basket.status === "quoted" && !expired) {
    // Above this person's limit the signed basket waits for someone more senior.
    if (!canApprove(role, basket.policy?.level)) return { expired, button: null, waiting: true };
    return { expired, button: <Button variant="accent" size="lg" loading={approving} disabled={busy} onClick={onApprove} className="w-full">{approving ? t("action.approving") : t("action.approve")}</Button> };
  }
  return { expired, button: <Button variant="accent" size="lg" loading={confirming} disabled={busy} onClick={onConfirm} className="w-full">{confirming ? t("action.confirming") : expired ? t("action.confirmAgain") : t("action.confirm")}</Button> };
}

export function ActionPanel(props: Props) {
  const { t } = useTranslation();
  const now = useNow();
  const { basket } = props;
  const { expired, button, waiting } = usePrimary(props, now);
  const policy = basket.policy;
  const reasons = policy?.reasons ?? [];
  const level = policy?.level ?? "chef";
  const who = t(`role.gen_${level}`, { defaultValue: level });
  // Whether the person at the screen may place it themselves: the same basket
  // "needs a manager" for the chef and is simply within the owner's limit.
  const role = useRole();
  const mine = canApprove(role, level);

  return (
    <section aria-labelledby="totals-title" className="space-y-3">
      <h2 id="totals-title" className="text-xl font-extrabold tracking-tight">{t("basket.total")}</h2>
      <dl className="tabular space-y-1 text-sm">
        <div className="flex justify-between"><dt>{t("basket.net")}</dt><dd className="font-semibold">{money(basket.net_total)}</dd></div>
        <div className="flex justify-between"><dt>{t("basket.vat")}</dt><dd>{money(basket.vat_total)}</dd></div>
        <div className="flex items-baseline justify-between border-t border-border pt-2 font-extrabold tracking-tight"><dt className="text-base">{t("basket.total")}</dt><dd className="text-2xl">{money(basket.gross_total)}</dd></div>
      </dl>

      {policy && basket.status !== "ordered" && (
        level === "auto_ok" ? (
          <Banner tone="ok" title={t("policy.ruleTitle")}><span lang="en">{reasons.join(" ")}</span></Banner>
        ) : reasons.length > 0 && mine ? (
          <Banner tone="ok" title={t("policy.yoursTitle", { role: t(`role.${role}`) })}>
            <ul className="list-disc pl-4" lang="en">{reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            <p className="mt-1 text-xs text-ink-soft">{t("policy.yoursHint")}</p>
          </Banner>
        ) : reasons.length > 0 ? (
          <Banner tone="warn" title={level === "manager" || level === "owner" ? t("policy.needsTitle", { who }) : t("policy.title")}>
            <ul className="list-disc pl-4" lang="en">{reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            <p className="mt-1 text-xs text-ink-soft">{policy.binding ? t("policy.binding") : t("policy.preview")}</p>
          </Banner>
        ) : <Banner tone="ok" title={t("policy.chefTitle")}>{t("policy.none")}</Banner>
      )}

      {basket.status === "quoted" && basket.quote_expires && (expired ? (
        <Banner tone="warn" alert title={t("quote.expired", { time: time(basket.quote_expires) })}>{t("quote.expiredHint")}</Banner>
      ) : (
        <Banner tone="ok" title={t("quote.signed")}>
          <span className="tabular">{t("quote.validUntil", { time: time(basket.quote_expires) })}{timeLeft(basket.quote_expires, now) ? ` (${timeLeft(basket.quote_expires, now)})` : ""}</span>
          <span className="block text-xs text-ink-soft">{basket.quote_verified ? `${t("quote.verified")} · ` : ""}{t("quote.id", { id: basket.quote_id })}</span>
        </Banner>
      ))}

      {basket.status === "draft" && !!basket.lines?.length && <p className="text-xs text-ink-soft">{t("action.draftHint")}</p>}
      {basket.status === "quoted" && !expired && <p className="text-xs text-ink-soft">{t("action.editVoids")}</p>}
      {waiting && (
        <Banner tone="info" title={t("policy.waiting", { who })}>
          {t("policy.waitingHint", { role: t(`role.${level}`) })}{" "}
          <Link to="/approvals" className="font-semibold underline">{t("nav.approvals")}</Link>
        </Banner>
      )}
      {button}
      {basket.status === "ordered" && basket.order_id && (
        <Link to={`/activity?order=${basket.order_id}`} className="flex min-h-12 items-center justify-center rounded-full border border-border bg-surface px-5 text-sm font-semibold hover:bg-sunken">{t("action.viewOrder")}</Link>
      )}
    </section>
  );
}

/** Phone only: total and the primary action, pinned above the tab bar. */
export function MobileActionBar(props: Props) {
  const { t } = useTranslation();
  const now = useNow();
  const { button } = usePrimary(props, now);
  if (!button) return null;
  return (
    <div className={clsx("fixed inset-x-0 bottom-14 z-20 flex items-center gap-3 border-t border-border bg-surface px-4 py-2 shadow-[0_-4px_12px_rgb(0_0_0/0.08)] md:hidden",
      "mb-[env(safe-area-inset-bottom)]")}>
      <p className="tabular min-w-0 flex-1 leading-tight">
        <span className="block text-xs text-ink-soft">{t("basket.total")}</span>
        <span className="block text-lg font-extrabold tracking-tight">{money(props.basket.gross_total)}</span>
      </p>
      <div className="w-[58%] max-w-64">{button}</div>
    </div>
  );
}
