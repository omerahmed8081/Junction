// What the agent may order without anyone asking: the mandate the kitchen
// signs and Aimo enforces. Read it on the left, issue a new one on the right.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { useAnnounce } from "../components/Announcer";
import { Banner, Button, Card, ErrorNote, PageTitle, Skeleton } from "../components/ui";
import { StandingRules } from "../features/rules/StandingRules";
import { dayTime, money } from "../lib/format";

const DAY_CHOICES = [1, 7, 30];
const toNumber = (text: string) => Number(text.replace(/\s/g, "").replace(",", "."));

export function RulesPage() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const announce = useAnnounce();
  const query = useQuery({ queryKey: ["mandate"], queryFn: api.mandate });
  const mandate = query.data;
  const heading = useRef<HTMLHeadingElement>(null);

  const [perOrder, setPerOrder] = useState("");
  const [perWeek, setPerWeek] = useState("");
  const [days, setDays] = useState(7);
  const [approval, setApproval] = useState(true);
  const [touched, setTouched] = useState(false);
  const orderRef = useRef<HTMLInputElement>(null), weekRef = useRef<HTMLInputElement>(null);

  useEffect(() => { document.title = `${t("nav.rules")} · ${t("app.name")}`; }, [t]);
  // Start the form from the mandate in force, so "issue" means "change", not "retype".
  useEffect(() => {
    if (!mandate) return;
    setPerOrder(String(mandate.limits.max_net_per_order));
    setPerWeek(String(mandate.limits.max_net_per_week));
    setApproval(mandate.approval_required);
  }, [mandate]);

  const order = toNumber(perOrder), week = toNumber(perWeek);
  const orderError = !(order > 0) ? t("rules.errPositive") : "";
  const weekError = !(week > 0) ? t("rules.errPositive") : week < order ? t("rules.errWeek") : "";

  const issue = useMutation({
    mutationFn: () => api.issueMandate({ max_net_per_order: order, max_net_per_week: week, approval_required: approval, days }),
    onSuccess: (m) => {
      client.setQueryData(["mandate"], m);
      announce(t("rules.issued", { date: dayTime(m.expires_at) }));
      heading.current?.focus();
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (orderError) { orderRef.current?.focus(); return; }
    if (weekError) { weekRef.current?.focus(); return; }
    issue.mutate();
  };

  const field = "tabular mt-1 min-h-12 w-full rounded-full border bg-surface px-5 text-base";
  return (
    <>
      <PageTitle>{t("rules.title")}</PageTitle>
      <div className="grid gap-5 lg:grid-cols-2">
        <div className="space-y-5">
          <Card className="p-5 sm:p-6">
            <h2 ref={heading} tabIndex={-1} className="text-xl font-extrabold tracking-tight">{t("rules.inForce")}</h2>
            {query.isLoading && <div className="mt-3"><Skeleton lines={5} /></div>}
            {query.isError && <div className="mt-3"><ErrorNote error={query.error} onRetry={() => void query.refetch()} /></div>}
            {mandate && (<>
              <dl className="mt-3 divide-y divide-border text-sm">
                {([
                  [t("rules.perOrder"), `${money(mandate.limits.max_net_per_order)} ${t("basket.net").toLowerCase()}`],
                  [t("rules.perWeek"), `${money(mandate.limits.max_net_per_week)} ${t("basket.net").toLowerCase()}`],
                  [t("rules.groups"), mandate.allowed_categories?.length ? mandate.allowed_categories.join(", ") : t("rules.allGroups")],
                  [t("rules.approval"), mandate.approval_required ? t("rules.yesEvery") : t("rules.no")],
                  [t("rules.validUntil"), dayTime(mandate.expires_at)],
                  [t("rules.agent"), (mandate.agent_name || mandate.agent_id)],
                  [t("rules.customer"), mandate.customer],
                ] as const).map(([label, value]) => (
                  <div key={label} className="flex justify-between gap-4 py-2"><dt className="text-ink-soft">{label}</dt><dd className="tabular text-right font-semibold">{value}</dd></div>
                ))}
              </dl>
              <p className="mt-3 text-xs text-ink-soft">{t("rules.explain")}</p>
              <details className="mt-2">
                <summary className="inline-flex min-h-11 cursor-pointer items-center text-sm font-semibold text-link">{t("rules.showSigned")}</summary>
                <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-2xl bg-surface p-4 text-xs">{mandate.mandate_jws}</pre>
              </details>
            </>)}
          </Card>
          <Card className="p-5 sm:p-6">
            <h2 className="text-xl font-extrabold tracking-tight">{t("rules.checks")}</h2>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
              {(["check1", "check2", "check3", "check4"] as const).map((k) => <li key={k}>{t(`rules.${k}`)}</li>)}
            </ul>
          </Card>
          {/* Inside the mandate, never outside: a rule only relaxes an approval
              the kitchen would ask for itself. Aimo never sees these. */}
          <StandingRules />
        </div>

        <Card className="p-5 sm:p-6">
          <h2 className="text-xl font-extrabold tracking-tight">{t("rules.issue")}</h2>
          <p className="text-sm text-ink-soft">{t("rules.issueHint")}</p>
          <form onSubmit={submit} noValidate className="mt-4 space-y-4">
            <div>
              <label htmlFor="per-order" className="text-sm font-semibold">{t("rules.perOrderLabel")}</label>
              <input id="per-order" ref={orderRef} inputMode="decimal" value={perOrder} onChange={(e) => setPerOrder(e.target.value)}
                aria-invalid={(touched && !!orderError) || undefined} aria-describedby="per-order-help"
                className={`${field} ${touched && orderError ? "border-2 border-danger" : "border-border-strong"}`} />
              <p id="per-order-help" className={`mt-1 text-xs ${touched && orderError ? "font-semibold text-danger" : "text-ink-soft"}`}>{touched && orderError ? orderError : t("rules.netHelp")}</p>
            </div>
            <div>
              <label htmlFor="per-week" className="text-sm font-semibold">{t("rules.perWeekLabel")}</label>
              <input id="per-week" ref={weekRef} inputMode="decimal" value={perWeek} onChange={(e) => setPerWeek(e.target.value)}
                aria-invalid={(touched && !!weekError) || undefined} aria-describedby="per-week-help"
                className={`${field} ${touched && weekError ? "border-2 border-danger" : "border-border-strong"}`} />
              <p id="per-week-help" className={`mt-1 text-xs ${touched && weekError ? "font-semibold text-danger" : "text-ink-soft"}`}>{touched && weekError ? weekError : t("rules.netHelp")}</p>
            </div>
            <fieldset>
              <legend className="text-sm font-semibold">{t("rules.validFor")}</legend>
              <div className="mt-1 flex flex-wrap gap-2">
                {DAY_CHOICES.map((d) => (
                  <label key={d} className={`inline-flex min-h-11 cursor-pointer items-center rounded-full border px-5 text-sm font-semibold has-focus-visible:outline-3 has-focus-visible:outline-blue-700 ${days === d ? "border-primary bg-primary text-on-primary" : "border-border bg-surface hover:bg-sunken"}`}>
                    <input type="radio" name="days" value={d} checked={days === d} onChange={() => setDays(d)} className="sr-only" />
                    {t("rules.days", { count: d })}
                  </label>
                ))}
              </div>
            </fieldset>
            <label className="flex min-h-11 cursor-pointer items-center gap-3 text-sm">
              <input type="checkbox" checked={approval} onChange={(e) => setApproval(e.target.checked)} className="size-5 accent-(--color-primary)" />
              {t("rules.approvalLabel")}
            </label>

            {/* The consent moment: what is about to be signed, in plain words. */}
            {!orderError && !weekError && (
              <Banner tone="info">{t("rules.summary", { order: money(order), week: money(week), days })} {approval ? t("rules.summaryApproval") : t("rules.summaryNoApproval")}</Banner>
            )}
            {issue.isError && <ErrorNote error={issue.error} />}
            {issue.isSuccess && <Banner tone="ok" alert>{t("rules.issued", { date: dayTime(issue.data.expires_at) })}</Banner>}
            <div className="flex justify-end"><Button type="submit" variant="accent" size="lg" loading={issue.isPending}>{issue.isPending ? t("rules.signing") : t("rules.sign")}</Button></div>
          </form>
        </Card>
      </div>
    </>
  );
}
