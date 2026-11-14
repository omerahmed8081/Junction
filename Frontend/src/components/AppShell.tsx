// The frame around every page: skip link, header, navigation (top bar on
// tablet and desktop, bottom tabs on a phone), language switch, <main>.
import { clsx } from "clsx";
import { Activity, ChefHat, ClipboardCheck, Package, ShoppingBasket, SlidersHorizontal } from "lucide-react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet } from "react-router-dom";
import { useWaitingBaskets } from "../features/approvals/useWaitingBaskets";

const ITEMS = [
  { to: "/", key: "order", icon: ShoppingBasket },
  { to: "/approvals", key: "approvals", icon: ClipboardCheck },
  { to: "/inventory", key: "inventory", icon: Package },
  { to: "/rules", key: "rules", icon: SlidersHorizontal },
  { to: "/activity", key: "activity", icon: Activity },
] as const;

function LanguageSwitch() {
  const { t, i18n } = useTranslation();
  return (
    <div role="group" aria-label={t("app.language")} className="inline-flex overflow-hidden rounded-lg border border-border-strong">
      {(["en", "fi"] as const).map((lng) => (
        <button
          key={lng}
          type="button"
          lang={lng}
          aria-pressed={i18n.language === lng}
          onClick={() => void i18n.changeLanguage(lng)}
          className={clsx("min-h-11 min-w-11 cursor-pointer px-3 text-sm font-semibold uppercase",
            i18n.language === lng ? "bg-primary text-on-primary" : "bg-surface text-ink hover:bg-sunken")}
        >
          <span aria-hidden>{lng}</span>
          <span className="sr-only">{lng === "en" ? "English" : "Suomi"}</span>
        </button>
      ))}
    </div>
  );
}

function Badge({ count, label }: { count: number; label: string }) {
  if (!count) return null;
  return (
    <span className="ml-1 inline-flex min-w-5 items-center justify-center rounded-full bg-primary px-1.5 text-xs font-bold text-on-primary">
      <span aria-hidden>{count}</span><span className="sr-only">, {label}</span>
    </span>
  );
}

export function AppShell() {
  const { t } = useTranslation();
  const waiting = useWaitingBaskets().baskets.length;
  return (
    <div className="flex min-h-dvh flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-4 focus:py-2 focus:font-semibold">
        {t("app.skip")}
      </a>
      <header className="sticky top-0 z-30 border-b border-border bg-surface">
        <div className="mx-auto flex max-w-[1500px] items-center gap-4 px-4 py-2">
          <div className="flex items-center gap-2 font-bold">
            <ChefHat className="size-6 text-primary" aria-hidden />
            <span className="hidden sm:inline">{t("app.name")}</span>
          </div>
          <nav aria-label={t("nav.main")} className="hidden md:block">
            <ul className="flex gap-1">
              {ITEMS.map(({ to, key }) => (
                <li key={key}>
                  <NavLink to={to} end={to === "/"} className={({ isActive }) => clsx(
                    "flex min-h-11 items-center rounded-lg px-3 text-sm font-semibold",
                    isActive ? "bg-primary-soft text-primary underline decoration-2 underline-offset-8" : "text-ink-soft hover:bg-sunken hover:text-ink")}>
                    {t(`nav.${key}`)}
                    {key === "approvals" && <Badge count={waiting} label={t("approvals.title")} />}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
          <div className="ml-auto"><LanguageSwitch /></div>
        </div>
      </header>

      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-[1500px] flex-1 px-4 py-5 pb-28 md:pb-8">
        <Outlet />
      </main>

      {/* Phone: five destinations, icon + label, always visible. */}
      <nav aria-label={t("nav.main")} className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-surface pb-[env(safe-area-inset-bottom)] md:hidden">
        <ul className="grid grid-cols-5">
          {ITEMS.map(({ to, key, icon: Icon }) => (
            <li key={key}>
              <NavLink to={to} end={to === "/"} className={({ isActive }) => clsx(
                "relative flex min-h-14 flex-col items-center justify-center gap-0.5 px-1 text-[0.6875rem] font-semibold leading-tight",
                isActive ? "text-primary" : "text-ink-soft")}>
                {({ isActive }) => (<>
                  {isActive && <span className="absolute inset-x-3 top-0 h-1 rounded-b bg-primary" aria-hidden />}
                  <Icon className="size-5" aria-hidden />
                  <span className="text-center">{t(`nav.${key}`)}{key === "approvals" && <Badge count={waiting} label={t("approvals.title")} />}</span>
                </>)}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}
