// Stock as the kitchen sees it: in stock, on order (never added together),
// what the menu will use, and when each product runs out. Soonest first.
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { Card, Chip, ErrorNote, PageTitle, Skeleton } from "../components/ui";
import { amount, day, productName } from "../lib/format";

export function InventoryPage() {
  const { t } = useTranslation();
  const query = useQuery({ queryKey: ["inventory"], queryFn: api.inventory });
  useEffect(() => { document.title = `${t("nav.inventory")} · ${t("app.name")}`; }, [t]);
  const rows = [...(query.data?.rows ?? [])].sort((a, b) => (a.runs_out_on ?? "9999").localeCompare(b.runs_out_on ?? "9999"));

  return (
    <>
      <PageTitle aside={query.data && <p className="text-sm text-ink-soft">{t("inventory.asOf", { date: day(query.data.as_of), until: day(query.data.window_until) })}</p>}>
        {t("inventory.title")}
      </PageTitle>
      {query.isLoading && <Skeleton lines={6} />}
      {query.isError && <ErrorNote error={query.error} onRetry={() => void query.refetch()} />}
      {query.data && (
        <Card className="overflow-x-auto">
          <table className="w-full min-w-[44rem] text-sm">
            <caption className="sr-only">{t("inventory.title")}</caption>
            <thead className="bg-sunken text-left text-xs uppercase tracking-wide text-ink-soft">
              <tr>
                <th scope="col" className="px-4 py-2.5">{t("inventory.product")}</th>
                <th scope="col" className="px-4 py-2.5 text-right">{t("inventory.stock")}</th>
                <th scope="col" className="px-4 py-2.5 text-right">{t("inventory.onOrder")}</th>
                <th scope="col" className="px-4 py-2.5 text-right">{t("inventory.usage")}</th>
                <th scope="col" className="px-4 py-2.5">{t("inventory.runsOut")}</th>
                <th scope="col" className="px-4 py-2.5">{t("inventory.usedIn")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((r) => (
                <tr key={r.product_id}>
                  <th scope="row" className="px-4 py-2.5 text-left font-semibold">{productName(r.product_id, r.name)}</th>
                  <td className="tabular px-4 py-2.5 text-right">{amount(r.stock, r.unit, 1)}</td>
                  <td className="tabular px-4 py-2.5 text-right">{amount(r.on_order, r.unit, 1)}</td>
                  <td className="tabular px-4 py-2.5 text-right">{amount(r.expected_usage, r.unit, 1)}</td>
                  <td className="px-4 py-2.5">{r.runs_out_on ? <Chip tone="warn">{day(r.runs_out_on)}</Chip> : t("inventory.enough")}</td>
                  <td className="px-4 py-2.5 text-xs text-ink-soft" lang="en">{(r.dishes ?? []).join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      <p className="mt-3 text-xs text-ink-soft">{t("app.demoData")}</p>
    </>
  );
}
