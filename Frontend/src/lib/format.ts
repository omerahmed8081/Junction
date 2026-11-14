// Every number, price and date on screen goes through here, so the two
// languages differ in one place only. The frontend formats; it never computes totals.
import i18n from "../i18n";
import { PRODUCT_NAMES } from "../fixtures/productNames";

const TZ = "Europe/Helsinki";
const locale = () => (i18n.language === "fi" ? "fi-FI" : "en-GB");

export function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return i18n.t("basket.unknown");
  return new Intl.NumberFormat(locale(), { style: "currency", currency: "EUR" }).format(value);
}

export function number(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return i18n.t("basket.unknown");
  return new Intl.NumberFormat(locale(), { maximumFractionDigits: digits }).format(value);
}

/** "1,000 L", "4 cases": an amount with its unit in words. */
export function amount(value: number | null | undefined, unit: string, digits = 2): string {
  return `${number(value, digits)} ${unitLabel(unit)}`;
}

/** `one` picks the singular, for "per case" rather than "per cases". */
export function unitLabel(code: string, one = false): string {
  const key = `${one ? "unitOne" : "unit"}.${(code || "").toUpperCase()}`;
  return i18n.exists(key) ? i18n.t(key) : code;
}

/** A coded value (flag, claim, refusal reason) as words, or a readable fallback. */
export function codeLabel(group: "flag" | "claim" | "refusal", code: string): string {
  const key = `${group}.${code}`;
  if (i18n.exists(key)) return i18n.t(key);
  const words = code.replace(/_/g, " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// A date-only string ("2026-11-16") is a calendar day, not an instant: build it
// at noon so no time zone can push it onto the neighbouring day.
function asDate(value: string | Date): Date {
  if (value instanceof Date) return value;
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00:00`) : new Date(value);
}

export function day(value: string | Date | null | undefined): string {
  if (!value) return i18n.t("basket.unknown");
  return new Intl.DateTimeFormat(locale(), { weekday: "short", day: "numeric", month: "short", timeZone: TZ }).format(asDate(value));
}

export function time(value: string | Date | null | undefined): string {
  if (!value) return i18n.t("basket.unknown");
  return new Intl.DateTimeFormat(locale(), { hour: "2-digit", minute: "2-digit", timeZone: TZ }).format(asDate(value));
}

export function dayTime(value: string | Date | null | undefined): string {
  if (!value) return i18n.t("basket.unknown");
  return `${day(value)} ${time(value)}`;
}

/** "1 d 2 h", "45 min", or null when the moment has passed. */
export function timeLeft(until: string | null | undefined, now = Date.now()): string | null {
  if (!until) return null;
  const minutes = Math.floor((new Date(until).getTime() - now) / 60000);
  if (minutes <= 0) return null;
  const d = Math.floor(minutes / 1440), h = Math.floor((minutes % 1440) / 60), m = minutes % 60;
  if (d > 0) return h > 0 ? `${d} d ${h} h` : `${d} d`;
  if (h > 0) return `${h} h ${m} min`;
  return `${m} min`;
}

/** Product name for places where the API sends none (inventory, suggestions). */
export function productName(productId: string, fromApi?: string | null): string {
  if (fromApi) return fromApi;
  const known = PRODUCT_NAMES[productId];
  if (known) return i18n.language === "fi" ? known.fi : known.en;
  return `#${productId}`;
}

/**
 * A typed amount as a number, whichever way the person writes it:
 * "100000", "100 000" (Finnish), "100,000" or "100.000" (thousands), "12,50" or
 * "12.50" (decimals). A comma or point followed by exactly three digits, more than
 * once or after 1-3 leading digits, is a thousands separator; otherwise it is the decimal mark.
 * Returns NaN for anything else, so the form can say so.
 */
export function parseAmount(text: string): number {
  const s = text.replace(/[\s  €]/g, "");
  if (!s) return NaN;
  if (/^\d{1,3}([.,]\d{3})+$/.test(s)) return Number(s.replace(/[.,]/g, ""));
  if (/^\d{1,3}(,\d{3})+\.\d+$/.test(s)) return Number(s.replace(/,/g, ""));
  if (/^\d{1,3}(\.\d{3})+,\d+$/.test(s)) return Number(s.replace(/\./g, "").replace(",", "."));
  if (/^\d+([.,]\d+)?$/.test(s)) return Number(s.replace(",", "."));
  return NaN;
}
