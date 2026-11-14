// What the agent may order without anyone asking: the mandate the kitchen
// signs and Aimo enforces. Read it on the left, issue a new one on the right.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api } from "../api/client";
import { useAnnounce } from "../components/Announcer";
import { Banner, Button, Card, ErrorNote, PageTitle, Skeleton } from "../components/ui";
import { StandingRules } from "../features/rules/StandingRules";
import { dayTime, money, parseAmount } from "../lib/format";
import { useRole } from "../lib/session";

const DAY_CHOICES = [1, 7, 30];

export function RulesPage() {
  const { t } = useTranslation();
  const isOwner = useRole() === "owner";
  const client = useQueryClient();
  const announce = useAnnounce();
  const query = useQuery({
    queryKey: ["mandate"],
    queryFn: api.mandate,
    // A stopped agent has no mandate and the backend says 404. That is an
    // answer, not a failure, so it is not retried and not shown as an error.
    retry: (count, error) => !(error instanceof ApiError && error.kind === "missing") && count < 2,
  });
  const mandate = query.data;
  const stopped = query.isError && query.error instanceof ApiError
    && query.error.kind === "missing";
  const [confirmStop, setConfirmStop] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);

  const [perOrder, setPerOrder] = useState("");
  const [perDay, setPerDay] = useState("");
  const [perWeek, setPerWeek] = useState("");
  const [days, setDays] = useState(7);
  const [approval, setApproval] = useState(true);
  const [touched, setTouched] = useState(false);
  const orderRef = useRef<HTMLInputElement>(null), weekRef = useRef<HTMLInputElement>(null);
  const dayRef = useRef<HTMLInputElement>(null);

  useEffect(() => { document.title = `${t("nav.rules")} · ${t("app.name")}`; }, [t]);
  // Start the form from the mandate in force, so "issue" means "change", not "retype".
  useEffect(() => {
    if (!mandate) return;
    setPerOrder(String(mandate.limits.max_net_per_order));
    setPerDay(mandate.limits.max_net_per_day == null ? "" : String(mandate.limits.max_net_per_day));
    setPerWeek(String(mandate.limits.max_net_per_week));
    setApproval(mandate.approval_required);
  }, [mandate]);

  const order = parseAmount(perOrder), week = parseAmount(perWeek);
  // An empty daily box means no daily ceiling, which is a real choice rather
  // than a mistake, so it is not an error.
  const day = perDay.trim() ? parseAmount(perDay) : null;
  const dayError = day !== null && !(day > 0) ? t("rules.errPositive")
    : day !== null && day > week ? t("rules.errDayOverWeek") : "";
  const orderError = !(order > 0) ? t("rules.errPositive") : "";
  const weekError = !(week > 0) ? t("rules.errPositive") : week < order ? t("rules.errWeek") : "";

  const issue = useMutation({
    mutationFn: () => api.issueMandate({ max_net_per_order: order, max_net_per_day: day,
      max_net_per_week: week, approval_required: approval, days }),
    onSuccess: (m) => {
      client.setQueryData(["mandate"], m);
      announce(t("rules.issued", { date: dayTime(m.expires_at) }));
      heading.current?.focus();
    },
  });
  const stopAgent = useMutation({
    mutationFn: api.stopAgent,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["mandate"] });
      setConfirmStop(false);
      announce(t("rules.stopped"));
      heading.current?.focus();
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (orderError) { orderRef.current?.focus(); return; }
    if (weekError) { weekRef.current?.focus(); return; }
    if (dayError) { dayRef.current?.focus(); return; }
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
            {stopped && (
              <div className="mt-3">
                <Banner tone="warn" title={t("rules.stoppedTitle")}>{t("rules.stoppedBody")}</Banner>
              </div>
            )}
            {query.isError && !stopped && <div className="mt-3"><ErrorNote error={query.error} onRetry={() => void query.refetch()} /></div>}
            {mandate && (<>
              <dl className="mt-3 divide-y divide-border text-sm">
                {([
                  [t("rules.perOrder"), `${money(mandate.limits.max_net_per_order)} ${t("basket.net").toLowerCase()}`],
                  [t("rules.perDay"), mandate.limits.max_net_per_day == null ? t("rules.noDaily")
                    : `${money(mandate.limits.max_net_per_day)} ${t("basket.net").toLowerCase()}`],
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
            {mandate && isOwner && (
              <div className="mt-5 border-t border-border pt-4">
                {!confirmStop ? (
                  <Button variant="ghost" onClick={() => setConfirmStop(true)}>
                    {t("rules.stop")}
                  </Button>
                ) : (
                  <div>
                    <p className="text-sm font-semibold">{t("rules.stopConfirm")}</p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <Button variant="primary" loading={stopAgent.isPending}
                        onClick={() => stopAgent.mutate()}>{t("rules.stopYes")}</Button>
                      <Button variant="ghost" onClick={() => setConfirmStop(false)}>
                        {t("rules.stopNo")}
                      </Button>
                    </div>
                  </div>
                )}
                <p className="mt-2 text-xs text-ink-soft">{t("rules.stopHint")}</p>
                {stopAgent.isError && <div className="mt-2"><ErrorNote error={stopAgent.error} /></div>}
              </div>
            )}
          </Card>
          {mandate && (
            <Card className="p-5 sm:p-6">
              <h2 className="text-xl font-extrabold tracking-tight">{t("rules.roleLimits")}</h2>
              <dl className="mt-3 divide-y divide-border text-sm">
                {([
                  [t("rules.chefLimit"), `${money(mandate.role_limits?.chef ?? 500)} ${t("basket.net").toLowerCase()}`],
                  [t("rules.managerLimit"), `${money(mandate.role_limits?.manager ?? 1500)} ${t("basket.net").toLowerCase()}`],
                  [t("rules.ownerLimit"), t("rules.ownerLimitValue", { amount: money(mandate.limits.max_net_per_order) })],
                ] as const).map(([label, value]) => (
                  <div key={label} className="flex justify-between gap-4 py-2"><dt className="text-ink-soft">{label}</dt><dd className="tabular text-right font-semibold">{value}</dd></div>
                ))}
              </dl>
            </Card>
          )}
          {/* Inside the mandate, never outside: a rule only relaxes an approval
              the kitchen would ask for itself. Aimo never sees these. */}
          <StandingRules />
        </div>

        <Card className="p-5 sm:p-6">
          <h2 className="text-xl font-extrabold tracking-tight">{t("rules.issue")}</h2>
          <p className="text-sm text-ink-soft">{t("rules.issueHint")}</p>
          {!isOwner && <div className="mt-3"><Banner tone="info">{t("rules.ownerOnly")}</Banner></div>}
          <form onSubmit={submit} noValidate className="mt-4 space-y-4">
            <fieldset disabled={!isOwner} className="space-y-4 disabled:opacity-60">
            <div>
              <label htmlFor="per-order" className="text-sm font-semibold">{t("rules.perOrderLabel")}</label>
              <input id="per-order" ref={orderRef} inputMode="decimal" value={perOrder} onChange={(e) => setPerOrder(e.target.value)}
                aria-invalid={(touched && !!orderError) || undefined} aria-describedby="per-order-help"
                className={`${field} ${touched && orderError ? "border-2 border-danger" : "border-border-strong"}`} />
              <p id="per-order-help" className={`mt-1 text-xs ${touched && orderError ? "font-semibold text-danger" : "text-ink-soft"}`}>{touched && orderError ? orderError : t("rules.netHelp")}</p>
            </div>
            <div>
              <label htmlFor="per-day" className="text-sm font-semibold">{t("rules.perDayLabel")}</label>
              <input id="per-day" ref={dayRef} inputMode="decimal" value={perDay} onChange={(e) => setPerDay(e.target.value)}
                placeholder={t("rules.noDaily")}
                aria-invalid={(touched && !!dayError) || undefined} aria-describedby="per-day-help"
                className={`${field} ${touched && dayError ? "border-2 border-danger" : "border-border-strong"}`} />
              <p id="per-day-help" className={`mt-1 text-xs ${touched && dayError ? "font-semibold text-danger" : "text-ink-soft"}`}>{touched && dayError ? dayError : t("rules.dayHelp")}</p>
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
            </fieldset>
          </form>
        </Card>
      </div>
    </>
  );
}
