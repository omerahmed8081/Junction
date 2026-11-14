// Language setup. Two dictionaries, the choice remembered in localStorage,
// and <html lang> kept in step so screen readers pronounce the page correctly.
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./en.json";
import fi from "./fi.json";

export type Lang = "en" | "fi";
const KEY = "tta.lang";

function initial(): Lang {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "en" || saved === "fi") return saved;
  } catch { /* private mode: fall through */ }
  return navigator.language?.toLowerCase().startsWith("fi") ? "fi" : "en";
}

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, fi: { translation: fi } },
  lng: initial(),
  fallbackLng: "en",
  interpolation: { escapeValue: false },
  returnNull: false,
});

document.documentElement.lang = i18n.language;
i18n.on("languageChanged", (lng) => {
  document.documentElement.lang = lng;
  try { localStorage.setItem(KEY, lng); } catch { /* ignore */ }
});

export default i18n;
