// One order's whole story, both halves: what the kitchen's agent did, and what
// Aimo recorded. Each row says which side it came from, because the difference
// is the point - most of what the kitchen does never leaves the building.
import { useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import { ShieldAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import { Banner, EmptyState, ErrorNote, Skeleton } from "../../components/ui";
import { readableDetail, stepLabel, time } from "../../lib/format";

export function OrderAudit({ orderId, open }: { orderId: string; open: boolean }) {
  const { t } = useTranslation();
  // Only asked for once the order is actually expanded.
  const query = useQuery({
    queryKey: ["order-audit", orderId],
    queryFn: () => api.orderAudit(orderId),
    enabled: open,
  });

  if (!open) return null;
  if (query.isLoading) return <div className="mt-3"><Skeleton lines={3} /></div>;
  if (query.isError) {
    return <div className="mt-3"><ErrorNote error={query.error} onRetry={() => void query.refetch()} /></div>;
  }
  const data = query.data;
  if (!data || data.records.length === 0) {
    return <div className="mt-3"><EmptyState title={t("audit.none")} /></div>;
  }

  return (
    <div className="mt-3">
      <p className="text-xs text-ink-soft">
        {t("audit.counts", { kitchen: data.kitchen_rows, aimo: data.aimo_rows })}
      </p>
      {data.degraded && <Banner tone="warn" className="mt-2">{t("audit.degraded")}</Banner>}
      <ol className="mt-2 space-y-1.5">
        {data.records.map((row, i) => {
          const fromAimo = row.side === "aimo";
          const flagged = row.security_event === true;
          return (
            <li key={`${row.side}-${row.id ?? i}`}
              className={clsx("rounded-2xl px-4 py-2.5 text-sm", flagged ? "bg-danger-soft" : "bg-surface")}>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="tabular text-xs text-ink-soft">{time(row.at)}</span>
                <span className={clsx("rounded-full px-2 py-0.5 text-xs font-semibold",
                  fromAimo ? "bg-primary text-on-primary" : "bg-card")}>
                  {fromAimo ? t("audit.aimo") : t("audit.kitchen")}
                </span>
                <span className="font-semibold">{stepLabel(row.step ?? row.action)}</span>
                {flagged && (
                  <span className="inline-flex items-center gap-1 text-xs font-semibold text-danger">
                    <ShieldAlert className="size-4" aria-hidden />{t("activity.security")}
                  </span>
                )}
                {row.approver_role && <span className="text-xs text-ink-soft">· {row.approver_role}</span>}
              </div>
              {row.detail && <p className="mt-1" lang="en">{readableDetail(row.detail)}</p>}
              {row.reason && !row.detail?.includes(row.reason) && (
                <p className="mt-0.5 text-xs text-ink-soft" lang="en">{readableDetail(row.reason)}</p>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
