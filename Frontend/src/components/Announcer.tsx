// One polite live region for the whole app. Call announce("…") after anything
// changes that a screen-reader user would otherwise have to go looking for.
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

const Ctx = createContext<(message: string) => void>(() => {});
export const useAnnounce = () => useContext(Ctx);

export function AnnouncerProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState("");
  const timer = useRef<number | undefined>(undefined);
  const announce = useCallback((text: string) => {
    setMessage("");                                   // clear first so a repeated message is read again
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMessage(text), 60);
  }, []);
  return (
    <Ctx.Provider value={announce}>
      {children}
      <div aria-live="polite" role="status" className="sr-only">{message}</div>
    </Ctx.Provider>
  );
}
