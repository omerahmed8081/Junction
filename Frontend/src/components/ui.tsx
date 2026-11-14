// Small building blocks used on every screen: button, chip, banner, card,
// skeleton, empty and error states. Colours come from the tokens in index.css.
import { clsx } from "clsx";
import { LoaderCircle, TriangleAlert, CircleCheck, Info, ShieldAlert } from "lucide-react";
import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "../api/client";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "accent" | "secondary" | "ghost";
  size?: "md" | "sm" | "lg";
  loading?: boolean;
  icon?: ReactNode;
};

export function Button({ variant = "secondary", size = "md", loading, icon, className, children, disabled, ...rest }: ButtonProps) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={clsx(
        "inline-flex items-center justify-center gap-2 rounded-full font-semibold tracking-tight transition-colors duration-150 cursor-pointer select-none",
        "disabled:cursor-not-allowed disabled:opacity-55",
        size === "sm" && "min-h-9 px-4 text-sm",
        size === "md" && "min-h-11 px-5 text-sm",       // 44 px: the minimum touch target
        size === "lg" && "min-h-14 px-7 text-base",
        variant === "primary" && "bg-primary text-on-primary hover:bg-primary-hover",
        variant === "accent" && "bg-accent text-ink hover:bg-accent-hover",
        variant === "secondary" && "border border-border bg-surface text-ink hover:bg-sunken",
        variant === "ghost" && "text-ink hover:bg-sunken",
        className,
      )}
    >
      {loading ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
}

type Tone = "neutral" | "warn" | "ok" | "info" | "danger";

export function Chip({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 rounded-full border px-3 py-0.5 text-xs font-semibold leading-5",
        tone === "neutral" && "border-transparent bg-sunken text-ink",
        tone === "warn" && "border-warn-border border-dashed bg-warn-soft text-warn",     // dashed + icon + words, not colour alone
        tone === "ok" && "border-transparent bg-ok-soft text-ok",
        tone === "info" && "border-transparent bg-info-soft text-info",
        tone === "danger" && "border-transparent bg-danger-soft text-danger",
        className,
      )}
    >
      {tone === "warn" && <TriangleAlert className="size-3.5 shrink-0" aria-hidden />}
      {children}
    </span>
  );
}

const bannerIcon = { neutral: Info, info: Info, ok: CircleCheck, warn: TriangleAlert, danger: ShieldAlert } as const;

export function Banner({ tone = "info", title, children, actions, alert, className }: {
  tone?: Tone; title?: ReactNode; children?: ReactNode; actions?: ReactNode; alert?: boolean; className?: string;
}) {
  const Icon = bannerIcon[tone];
  return (
    <div
      role={alert ? "alert" : undefined}
      className={clsx(
        "flex gap-3 rounded-2xl border p-4 text-sm",
        tone === "info" && "border-transparent bg-info-soft",
        tone === "neutral" && "border-border bg-surface",
        tone === "ok" && "border-transparent bg-ok-soft",
        tone === "warn" && "border-dashed border-warn-border bg-warn-soft",
        tone === "danger" && "border-danger/40 bg-danger-soft",
        className,
      )}
    >
      <Icon className={clsx("mt-0.5 size-5 shrink-0", tone === "danger" ? "text-danger" : tone === "warn" ? "text-warn" : tone === "ok" ? "text-ok" : "text-info")} aria-hidden />
      <div className="min-w-0 flex-1">
        {title && <p className="font-semibold text-ink">{title}</p>}
        {children && <div className="text-ink">{children}</div>}
        {actions && <div className="mt-2 flex flex-wrap gap-2">{actions}</div>}
      </div>
    </div>
  );
}

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div {...rest} className={clsx("tta-card rounded-(--radius-card) bg-card", className)} />;
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  const widths = ["w-2/3", "w-11/12", "w-1/2", "w-3/4"];
  return (
    <div className="space-y-2.5" aria-hidden>
      {Array.from({ length: lines }, (_, i) => <div key={i} className={clsx("h-3 animate-pulse rounded-full bg-sunken", widths[i % widths.length])} />)}
    </div>
  );
}

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="tta-card rounded-(--radius-card) bg-card px-6 py-12 text-center">
      <p className="text-2xl font-extrabold leading-tight tracking-[-0.03em] text-balance sm:text-3xl">{title}</p>
      {hint && <p className="mx-auto mt-2 max-w-md text-sm text-ink-soft">{hint}</p>}
      {action && <div className="mt-5 flex justify-center">{action}</div>}
    </div>
  );
}

/** Says what happened, what is safe, and what to do. Used for failed loads and failed actions. */
export function ErrorNote({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useTranslation();
  const api = error instanceof ApiError ? error : null;
  const network = !api || api.kind === "network";
  const upstream = api?.kind === "upstream";
  return (
    <Banner
      alert
      tone={network ? "danger" : "warn"}
      title={network ? t("error.network") : upstream ? t("error.upstream") : api?.kind === "rule" ? t("error.rule") : t("error.server")}
      actions={onRetry && <Button size="sm" onClick={onRetry}>{t("action.retry")}</Button>}
    >
      {network ? t("error.networkHint") : upstream ? (
        <>
          {t("error.upstreamHint")}
          {/* The backend's own words, kept for whoever is debugging. */}
          {api?.message && <details className="mt-1 text-xs text-ink-soft"><summary className="cursor-pointer">{t("error.details")}</summary><span lang="en" className="break-all">{api.message}</span></details>}
        </>
      ) : <span lang="en">{api?.message}</span>}
    </Banner>
  );
}

export function PageTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <h1 className="text-3xl font-extrabold leading-none tracking-[-0.04em] sm:text-4xl">{children}</h1>
      {aside}
    </div>
  );
}
