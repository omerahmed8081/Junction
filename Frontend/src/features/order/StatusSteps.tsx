// Draft → Signed price → Ordered, as an ordered list. The current step is
// marked with aria-current and a filled style, so it never relies on colour alone.
import { clsx } from "clsx";
import { Check } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Basket } from "../../api/types";

const ORDER = ["draft", "quoted", "ordered"] as const;

export function StatusSteps({ status }: { status: Basket["status"] }) {
  const { t } = useTranslation();
  const at = status === "ordered" ? 2 : status === "quoted" || status === "pending_approval" || status === "approved" ? 1 : 0;
  return (
    <ol aria-label={t("status.progress")} className="flex text-xs font-semibold">
      {ORDER.map((step, i) => (
        <li key={step} aria-current={i === at ? "step" : undefined}
          className={clsx("flex items-center gap-1 border px-2.5 py-1.5 first:rounded-l-lg last:rounded-r-lg -ml-px first:ml-0",
            i === at ? "border-primary bg-primary text-on-primary" : i < at ? "border-border-strong bg-sunken text-ink" : "border-border-strong bg-surface text-ink-soft")}>
          {i < at && <Check className="size-3.5" aria-hidden />}
          {t(`status.${step}`)}
        </li>
      ))}
    </ol>
  );
}
