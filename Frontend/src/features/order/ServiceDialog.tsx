// "What did you cook today?": one number per dish. The backend turns dishes
// into ingredients used, re-runs the running-low check, and may draft a basket.
// Built on Radix Dialog: focus is trapped, Escape closes, focus returns to the opener.
import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import { Banner, Button, ErrorNote } from "../../components/ui";
import { DISHES } from "../../fixtures/dishes";

type Props = { open: boolean; onOpenChange: (open: boolean) => void; onBasket: (basketId: string) => void };

export function ServiceDialog({ open, onOpenChange, onBasket }: Props) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [values, setValues] = useState<Record<string, string>>({});
  const [touched, setTouched] = useState(false);
  const refs = useRef<Record<string, HTMLInputElement | null>>({});

  const parsed = Object.fromEntries(DISHES.map((d) => [d.id, values[d.id]?.trim() ? Number(values[d.id].replace(",", ".")) : 0]));
  const invalid = (id: string) => !Number.isFinite(parsed[id]) || parsed[id] < 0;
  const total = DISHES.reduce((sum, d) => sum + (invalid(d.id) ? 0 : parsed[d.id]), 0);

  const log = useMutation({
    mutationFn: () => api.logService(Object.fromEntries(Object.entries(parsed).filter(([, n]) => n > 0))),
    onSuccess: (r) => {
      if (r.basket) client.setQueryData(["basket", r.basket.basket_id], r.basket);
      void client.invalidateQueries({ queryKey: ["suggestions"] });
      void client.invalidateQueries({ queryKey: ["inventory"] });
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    const firstBad = DISHES.find((d) => invalid(d.id));
    if (firstBad) { refs.current[firstBad.id]?.focus(); return; }     // go to the first field that needs fixing
    if (total <= 0) { refs.current[DISHES[0].id]?.focus(); return; }
    log.mutate();
  };
  const close = (next: boolean) => { if (!next) { log.reset(); setValues({}); setTouched(false); } onOpenChange(next); };

  return (
    <Dialog.Root open={open} onOpenChange={close}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/45" />
        <Dialog.Content className="fixed inset-x-0 bottom-0 z-50 max-h-[92dvh] overflow-y-auto rounded-t-[1.75rem] bg-surface p-6 shadow-2xl sm:inset-auto sm:left-1/2 sm:top-1/2 sm:w-[min(36rem,92vw)] sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-[1.75rem]">
          <div className="flex items-start justify-between gap-3">
            <div>
              <Dialog.Title className="text-2xl font-extrabold tracking-[-0.03em]">{t("service.title")}</Dialog.Title>
              <Dialog.Description className="mt-1 text-sm text-ink-soft">{t("service.hint")}</Dialog.Description>
            </div>
            <Dialog.Close asChild><Button variant="ghost" aria-label={t("service.cancel")} className="min-w-11 px-0"><X className="size-5" aria-hidden /></Button></Dialog.Close>
          </div>

          {log.data ? (
            <div className="mt-4 space-y-3">
              <Banner tone="ok"><span lang="en">{log.data.reply}</span></Banner>
              <div className="flex flex-wrap justify-end gap-2">
                {log.data.basket && <Button variant="primary" onClick={() => { const id = log.data!.basket!.basket_id; close(false); onBasket(id); }}>{t("service.openBasket")}</Button>}
                <Button onClick={() => close(false)}>{t("service.cancel")}</Button>
              </div>
            </div>
          ) : (
            <form onSubmit={submit} noValidate className="mt-4">
              <ul className="divide-y divide-border">
                {DISHES.map((d) => {
                  const bad = touched && invalid(d.id);
                  return (
                    <li key={d.id} className="flex items-center justify-between gap-3 py-2.5">
                      <label htmlFor={`dish-${d.id}`} className="min-w-0 flex-1 text-sm" lang="en">{d.name}</label>
                      <div className="text-right">
                        <input id={`dish-${d.id}`} ref={(el) => { refs.current[d.id] = el; }} inputMode="numeric" placeholder="0"
                          value={values[d.id] ?? ""} onChange={(e) => setValues((v) => ({ ...v, [d.id]: e.target.value }))}
                          aria-label={t("service.portionsFor", { dish: d.name })} aria-invalid={bad || undefined} aria-describedby={bad ? `dish-${d.id}-err` : undefined}
                          className={`tabular min-h-11 w-24 rounded-full border bg-surface px-2 text-center text-base ${bad ? "border-2 border-danger" : "border-border-strong"}`} />
                        {bad && <p id={`dish-${d.id}-err`} className="mt-1 text-xs font-semibold text-danger">{t("service.invalid")}</p>}
                      </div>
                    </li>
                  );
                })}
              </ul>
              {touched && total <= 0 && !DISHES.some((d) => invalid(d.id)) && <p role="alert" className="mt-2 text-sm font-semibold text-danger">{t("service.nothing")}</p>}
              {log.isError && <div className="mt-3"><ErrorNote error={log.error} /></div>}
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                <p className="tabular text-sm text-ink-soft" aria-live="polite">{t("service.total", { count: total })}</p>
                <div className="flex gap-2">
                  <Dialog.Close asChild><Button>{t("service.cancel")}</Button></Dialog.Close>
                  <Button type="submit" variant="primary" loading={log.isPending}>{log.isPending ? t("service.logging") : t("service.log")}</Button>
                </div>
              </div>
            </form>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
