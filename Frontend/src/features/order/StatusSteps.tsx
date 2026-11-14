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
    <ol aria-label={t("status.progress")} className="flex gap-0.5 rounded-full bg-card p-1 text-xs font-semibold">
      {ORDER.map((step, i) => (
        <li key={step} aria-current={i === at ? "step" : undefined}
          className={clsx("flex items-center gap-1 rounded-full px-3 py-1.5",
            i === at ? "bg-primary text-on-primary" : i < at ? "text-ink" : "text-ink-soft")}>
          {i < at && <Check className="size-3.5" aria-hidden />}
          {t(`status.${step}`)}
        </li>
      ))}
    </ol>
  );
}
