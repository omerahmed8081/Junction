// The kitchen's standing rules: what may go through without anyone looking.
// These are the kitchen's own and Aimo never sees them - what Aimo enforces is
// the signed mandate, which still sits above every rule here.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import type { RuleMatch } from "../../api/types";
import { useAnnounce } from "../../components/Announcer";
import { Banner, Button, Card, EmptyState, ErrorNote, Skeleton } from "../../components/ui";
import { parseAmount } from "../../lib/format";
import { useRole } from "../../lib/session";

const MATCHES: RuleMatch[] = ["any", "name", "product", "claim", "category"];

export function StandingRules() {
  const { t } = useTranslation();
  // Rules decide what goes through without anyone approving, so only the owner sets them.
  const isOwner = useRole() === "owner";
  const client = useQueryClient();
  const announce = useAnnounce();
  const query = useQuery({ queryKey: ["rules"], queryFn: api.rules });

  const [match, setMatch] = useState<RuleMatch>("name");
  const [value, setValue] = useState("");
  const [ceiling, setCeiling] = useState("");
  const [touched, setTouched] = useState(false);

  const needsValue = match !== "any";
  const valueError = needsValue && !value.trim() ? t("standing.errValue") : "";
  const amount = ceiling.trim() ? parseAmount(ceiling) : null;
  const ceilingError = amount !== null && !(amount > 0) ? t("standing.errAmount") : "";

  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["rules"] });
    // A new rule can change the approval a basket on screen needs.
    void client.invalidateQueries({ queryKey: ["basket"] });
  };

  const add = useMutation({
    mutationFn: () => api.addRule({
      name: value.trim() || match, match, value: value.trim(),
      max_net_per_order: amount,
    }),
    onSuccess: (rule) => { setValue(""); setCeiling(""); setTouched(false); refresh(); announce(rule.said); },
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.removeRule(id),
    onSuccess: () => { refresh(); announce(t("standing.removed")); },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (valueError || ceilingError) return;
    add.mutate();
  };

  const field = "mt-1 min-h-12 w-full rounded-full border bg-surface px-5 text-base";
  return (
    <Card className="p-5 sm:p-6">
      <h2 className="text-xl font-extrabold tracking-tight">{t("standing.title")}</h2>
      <p className="mt-1 text-sm text-ink-soft">{t("standing.intro")}</p>

      {query.isLoading && <div className="mt-3"><Skeleton lines={2} /></div>}
      {query.isError && <div className="mt-3"><ErrorNote error={query.error} onRetry={() => void query.refetch()} /></div>}
      {query.data?.degraded && <Banner tone="warn" className="mt-3">{query.data.note}</Banner>}

      {query.data && query.data.rules.length === 0 && (
        <div className="mt-3"><EmptyState title={t("standing.none")} hint={t("standing.noneHint")} /></div>
      )}

      <ul className="mt-3 space-y-2">
        {(query.data?.rules ?? []).map((rule) => (
          <li key={rule.rule_id} className="flex items-start justify-between gap-3 rounded-2xl bg-surface px-4 py-3">
            <span className="text-sm" lang="en">{rule.said}</span>
            {isOwner && <Button size="sm" variant="ghost" icon={<Trash2 className="size-4" aria-hidden />}
              aria-label={t("standing.remove", { rule: rule.said })}
              loading={remove.isPending && remove.variables === rule.rule_id}
              onClick={() => rule.rule_id != null && remove.mutate(rule.rule_id)}>
              {t("standing.off")}
            </Button>}
          </li>
        ))}
      </ul>

      {!isOwner && <Banner tone="info" className="mt-4">{t("standing.ownerOnly")}</Banner>}
      {isOwner && <form onSubmit={submit} className="mt-5 border-t border-border pt-4">
        <h3 className="font-bold">{t("standing.add")}</h3>
        <div className="mt-2 grid gap-3 sm:grid-cols-2">
          <label className="block text-sm">
            {t("standing.applies")}
            <select value={match} onChange={(e) => setMatch(e.target.value as RuleMatch)} className={field}>
              {MATCHES.map((m) => <option key={m} value={m}>{t(`standing.match.${m}`)}</option>)}
            </select>
          </label>
          {needsValue && (
            <label className="block text-sm">
              {t(`standing.value.${match}`)}
              <input value={value} onChange={(e) => setValue(e.target.value)} className={field}
                aria-invalid={touched && !!valueError} placeholder={t(`standing.placeholder.${match}`)} />
              {touched && valueError && <span className="mt-1 block text-sm text-danger">{valueError}</span>}
            </label>
          )}
          <label className="block text-sm">
            {t("standing.ceiling")}
            <input value={ceiling} onChange={(e) => setCeiling(e.target.value)} inputMode="decimal"
              className={`tabular ${field}`} aria-invalid={touched && !!ceilingError} placeholder={t("standing.noCeiling")} />
            <span className="mt-1 block text-xs text-ink-soft">{t("standing.ceilingHint")}</span>
            {touched && ceilingError && <span className="mt-1 block text-sm text-danger">{ceilingError}</span>}
          </label>
        </div>
        {add.isError && <div className="mt-3"><ErrorNote error={add.error} /></div>}
        <Button type="submit" className="mt-4" loading={add.isPending}>{t("standing.save")}</Button>
      </form>}

      <p className="mt-4 text-xs text-ink-soft">{t("standing.footnote")}</p>
    </Card>
  );
}
