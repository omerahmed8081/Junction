// The frame around every page: skip link, header, navigation (top bar on
// tablet and desktop, bottom tabs on a phone), language switch, <main>.
import { clsx } from "clsx";
import { Activity, ChefHat, ClipboardCheck, Package, ShoppingBasket, SlidersHorizontal } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { NavLink, Outlet } from "react-router-dom";
import { useWaitingBaskets } from "../features/approvals/useWaitingBaskets";
import { ROLES, setRole, useRole, type Role } from "../lib/session";

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
    <div role="group" aria-label={t("app.language")} className="inline-flex gap-0.5 rounded-full bg-card p-1">
      {(["en", "fi"] as const).map((lng) => (
        <button
          key={lng}
          type="button"
          lang={lng}
          aria-pressed={i18n.language === lng}
          onClick={() => void i18n.changeLanguage(lng)}
          className={clsx("min-h-11 min-w-11 cursor-pointer rounded-full px-3 text-sm font-semibold uppercase transition-colors",
            i18n.language === lng ? "bg-primary text-on-primary" : "text-ink hover:bg-sunken")}
        >
          <span aria-hidden>{lng}</span>
          <span className="sr-only">{lng === "en" ? "English" : "Suomi"}</span>
        </button>
      ))}
    </div>
  );
}

/** Who is using the screen. No sign-in yet, so it is a choice this browser remembers. */
function RoleSwitch() {
  const { t } = useTranslation();
  const role = useRole();
  return (
    <label className="flex items-center gap-2 text-sm font-semibold" title={t("role.hint")}>
      <span className="hidden lg:inline">{t("role.label")}</span>
      <span className="sr-only lg:hidden">{t("role.label")}</span>
      <select value={role} onChange={(e) => setRole(e.target.value as Role)} aria-describedby="role-hint"
        className="min-h-11 cursor-pointer rounded-full border border-border-strong bg-surface px-4 text-sm font-semibold">
        {ROLES.map((r) => <option key={r} value={r}>{t(`role.${r}`)}</option>)}
      </select>
      <span id="role-hint" className="sr-only">{t("role.hint")}</span>
    </label>
  );
}

function Badge({ count, label }: { count: number; label: string }) {
  if (!count) return null;
  return (
    <span className="ml-1 inline-flex min-w-5 items-center justify-center rounded-full bg-accent px-1.5 text-xs font-bold text-ink">
      <span aria-hidden>{count}</span><span className="sr-only">, {label}</span>
    </span>
  );
}

export function AppShell() {
  const { t } = useTranslation();
  // The kitchen names itself, so a second tenant needs no frontend change.
  const name = useQuery({ queryKey: ["kitchen"], queryFn: api.kitchen, staleTime: Infinity });
  const waiting = useWaitingBaskets().baskets.length;
  return (
    <div className="flex min-h-dvh flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-4 focus:py-2 focus:font-semibold">
        {t("app.skip")}
      </a>
      <header className="sticky top-0 z-30 bg-surface/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] items-center gap-6 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-2 text-xl font-extrabold tracking-[-0.04em]">
            <ChefHat className="size-7" aria-hidden />
            <span className="hidden sm:inline">{name.data?.name || t("app.name")}</span>
          </div>
          <nav aria-label={t("nav.main")} className="hidden md:block">
            <ul className="flex gap-1">
              {ITEMS.map(({ to, key }) => (
                <li key={key}>
                  <NavLink to={to} end={to === "/"} className={({ isActive }) => clsx(
                    "flex min-h-11 items-center rounded-full px-4 text-[0.9375rem] font-semibold tracking-tight transition-colors",
                    isActive ? "bg-primary text-on-primary" : "text-ink hover:bg-card")}>
                    {t(`nav.${key}`)}
                    {key === "approvals" && <Badge count={waiting} label={t("approvals.title")} />}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
          <div className="ml-auto flex items-center gap-3"><RoleSwitch /><LanguageSwitch /></div>
        </div>
      </header>

      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-[1500px] flex-1 px-4 py-6 pb-28 sm:px-6 md:pb-10">
        <Outlet />
      </main>

      {/* Phone: five destinations, icon + label, always visible. */}
      <nav aria-label={t("nav.main")} className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-surface pb-[env(safe-area-inset-bottom)] md:hidden">
        <ul className="grid grid-cols-5">
          {ITEMS.map(({ to, key, icon: Icon }) => (
            <li key={key}>
              <NavLink to={to} end={to === "/"} className={({ isActive }) => clsx(
                "relative flex min-h-14 flex-col items-center justify-center gap-0.5 px-1 text-[0.6875rem] font-semibold leading-tight",
                isActive ? "text-ink" : "text-ink-soft")}>
                {({ isActive }) => (<>
                  {isActive && <span className="absolute inset-x-3 top-0 h-1 rounded-b-full bg-primary" aria-hidden />}
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
