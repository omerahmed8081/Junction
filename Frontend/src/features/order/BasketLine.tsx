// One product in the basket: what was asked, what is ordered, what arrives,
// what it costs, when it comes, why it was picked, and the alternatives.
// Used editable on the Order page and read-only once a basket is ordered.
import { ArrowLeftRight, ChevronDown, Minus, Plus, Trash2, Truck } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import type { BasketLine as Line, LineEdit } from "../../api/types";
import { Button, Card, Chip } from "../../components/ui";
import { amount, codeLabel, day, money, number, unitLabel } from "../../lib/format";

const WARNING_FLAGS = new Set(["partial_split", "differs_from_request", "size_disagrees_with_price", "variable_weight", "size_unknown"]);
const SHOWN_CLAIMS = 3;

type Props = { line: Line; editable: boolean; busy: boolean; onEdit: (edit: LineEdit) => void };

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[0.6875rem] font-semibold uppercase tracking-wide text-ink-soft">{label}</dt>
      <dd className="tabular text-sm">{children}</dd>
    </div>
  );
}

export function BasketLine({ line, editable, busy, onEdit }: Props) {
  const { t } = useTranslation();
  const id = useId();
  const [draft, setDraft] = useState(String(line.quantity_sales_units));
  useEffect(() => setDraft(String(line.quantity_sales_units)), [line.quantity_sales_units]);

  const commit = (value: number) => {
    if (!Number.isFinite(value) || value <= 0) { setDraft(String(line.quantity_sales_units)); return; }
    if (value !== line.quantity_sales_units) onEdit({ product_id: line.product_id, quantity_sales_units: value, remove: false });
  };

  const delivery = line.delivery;
  const flags = line.flags ?? [];
  const claims = [...(line.claims ?? []), ...(line.labels ?? [])];
  const known = claims.filter((c) => t(`claim.${c}`, { defaultValue: "" }) !== "");
  const shown = known.slice(0, SHOWN_CLAIMS);
  const alternatives = line.alternatives ?? [];
  const inBulk = line.need_unit !== line.sales_unit;       // asked in litres or kilograms, sold in units or cases

  return (
    <Card className="p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <h3 className="min-w-0 flex-1 basis-64 text-lg font-bold leading-snug tracking-tight" lang="en">{line.name}</h3>
        <p className="text-right">
          <span className="tabular block text-2xl font-extrabold tracking-tight">{money(line.line_net)}</span>
          <span className="tabular block text-xs text-ink-soft">{t("basket.net")} · {t("basket.vat")} {money(line.line_vat)}</span>
        </p>
      </div>

      {/* The unit sum, in words: the part a chef checks first. */}
      {inBulk && (
        <p className="tabular mt-3 rounded-2xl bg-surface px-4 py-3 text-sm">
          {amount(line.need_value, line.need_unit)} → <strong>{amount(line.quantity_sales_units, line.sales_unit)}</strong>
          {line.size_per_sales_unit ? <> × {amount(line.size_per_sales_unit, line.size_unit)}</> : null}
          {" = "}<strong>{amount(line.delivered_amount, line.need_unit)}</strong>
          {line.overshoot > 0.0001 && <span className="text-ink-soft"> ({t("basket.extra", { amount: amount(line.overshoot, line.need_unit) })})</span>}
        </p>
      )}

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-[auto_1fr_1fr]">
        <div className="col-span-2 sm:col-span-1">
          <dt className="text-[0.6875rem] font-semibold uppercase tracking-wide text-ink-soft"><label htmlFor={`${id}-q`}>{t("basket.order")}</label></dt>
          <dd className="mt-0.5 flex items-center gap-1.5">
            {editable ? (<>
              <Button aria-label={t("basket.decrease")} disabled={busy || line.quantity_sales_units <= 1} onClick={() => commit(line.quantity_sales_units - 1)} className="min-w-11 px-0"><Minus className="size-4" aria-hidden /></Button>
              <input id={`${id}-q`} inputMode="decimal" value={draft} disabled={busy}
                aria-label={t("basket.quantityOf", { name: line.name })}
                onChange={(e) => setDraft(e.target.value)}
                onBlur={() => commit(Number(draft.replace(",", ".")))}
                onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); if (e.key === "Escape") setDraft(String(line.quantity_sales_units)); }}
                className="tabular min-h-11 w-24 rounded-full border border-border-strong bg-surface px-2 text-center text-base font-semibold" />
              <Button aria-label={t("basket.increase")} disabled={busy} onClick={() => commit(line.quantity_sales_units + 1)} className="min-w-11 px-0"><Plus className="size-4" aria-hidden /></Button>
            </>) : <span id={`${id}-q`} className="tabular text-base font-semibold">{number(line.quantity_sales_units)}</span>}
            <span className="text-sm text-ink-soft">{unitLabel(line.sales_unit)}</span>
          </dd>
        </div>
        <Fact label={t("basket.price")}>
          {money(line.price_per_sales_unit)} <span className="text-ink-soft">{t("basket.per", { unit: unitLabel(line.price_unit || line.sales_unit, true) })}</span>
          {line.price_per_comparison_unit != null && <span className="block text-xs text-ink-soft">{money(line.price_per_comparison_unit)} {t("basket.per", { unit: unitLabel(line.comparison_unit, true) })}</span>}
        </Fact>
        <Fact label={t("basket.delivery")}>
          <span className="inline-flex items-center gap-1.5"><Truck className="size-4 text-ink-soft" aria-hidden />{delivery?.delivery_date ? day(delivery.delivery_date) : t("basket.unknown")}</span>
          <span className="block text-xs text-ink-soft">
            {!delivery ? t("basket.unknown") : delivery.can_fulfil === "full" ? t("basket.full")
              : delivery.can_fulfil === "partial" ? t("basket.partial", { amount: amount(delivery.fulfil_amount, delivery.sales_unit) }) : t("basket.none")}
          </span>
        </Fact>
      </dl>

      {(flags.length > 0 || shown.length > 0) && (
        <ul className="mt-3 flex flex-wrap gap-1.5" aria-label={t("basket.details")}>
          {flags.map((f) => <li key={f}><Chip tone={WARNING_FLAGS.has(f) || f === "availability_unknown" ? "warn" : "info"}>{codeLabel("flag", f)}</Chip></li>)}
          {shown.map((c) => <li key={c}><Chip>{codeLabel("claim", c)}</Chip></li>)}
          {known.length > shown.length && <li><Chip>{t("basket.moreClaims", { count: known.length - shown.length })}</Chip></li>}
        </ul>
      )}

      <p className="mt-3 text-sm text-ink-soft"><span className="font-semibold text-ink">{t("basket.why")}: </span><span lang="en">{line.reason}</span></p>

      {(alternatives.length > 0 || editable) && (
        <div className="mt-3 flex flex-wrap items-start gap-2">
          {alternatives.length > 0 && (
            <details className="group min-w-0 flex-1 basis-72">
              <summary className="inline-flex min-h-11 cursor-pointer list-none items-center gap-1.5 rounded-full border border-border bg-surface px-4 text-sm font-semibold hover:bg-sunken">
                <ChevronDown className="size-4 transition-transform group-open:rotate-180" aria-hidden />
                {t("basket.alternatives", { count: alternatives.length })}
              </summary>
              <ul className="mt-2 divide-y divide-border rounded-2xl bg-surface">
                {alternatives.map((a) => (
                  <li key={a.product_id} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 p-3">
                    <div className="min-w-0 flex-1 basis-56">
                      <p className="text-sm font-semibold" lang="en">{a.name}</p>
                      <p className="tabular text-xs text-ink-soft">
                        {amount(a.quantity_sales_units, a.sales_unit)} · {money(a.line_net)} {t("basket.net")}
                        {a.price_per_comparison_unit != null && <> · {money(a.price_per_comparison_unit)} {t("basket.per", { unit: unitLabel(a.comparison_unit, true) })}</>}
                      </p>
                      <p className="text-xs text-ink-soft" lang="en">{a.reason}</p>
                      {(a.flags ?? []).length > 0 && <div className="mt-1 flex flex-wrap gap-1">{(a.flags ?? []).map((f) => <Chip key={f} tone="info">{codeLabel("flag", f)}</Chip>)}</div>}
                    </div>
                    {editable && (
                      <Button size="sm" disabled={busy} icon={<ArrowLeftRight className="size-4" aria-hidden />}
                        onClick={() => onEdit({ product_id: line.product_id, swap_to_product_id: a.product_id, remove: false })}>
                        {t("basket.swap")}
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {editable && (
            <Button variant="ghost" disabled={busy} aria-label={t("basket.removeNamed", { name: line.name })} icon={<Trash2 className="size-4" aria-hidden />}
              onClick={() => onEdit({ product_id: line.product_id, remove: true })} className="ml-auto">
              {t("basket.remove")}
            </Button>
          )}
        </div>
      )}
    </Card>
  );
}
