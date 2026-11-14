// Which baskets have a signed price and are waiting for approval.
// The backend cannot list baskets, so this asks for each basket this browser
// started and keeps the ones whose status is "quoted". Shared by the nav badge
// and the Approvals page.
import { useQueries } from "@tanstack/react-query";
import { useEffect } from "react";
import { ApiError, api } from "../../api/client";
import type { Basket } from "../../api/types";
import { forgetBasket, useKnownBaskets } from "../../lib/session";

export function useWaitingBaskets() {
  const ids = useKnownBaskets();
  const results = useQueries({
    queries: ids.map((id) => ({
      queryKey: ["basket", id],
      queryFn: () => api.basket(id),
      retry: false,
      staleTime: 15_000,
    })),
  });

  // A basket that no longer exists (demo reset, restart without state) is dropped from the list.
  useEffect(() => {
    results.forEach((r, i) => {
      if (r.error instanceof ApiError && r.error.kind === "missing") forgetBasket(ids[i]);
    });
  });

  const baskets = results.map((r) => r.data).filter((b): b is Basket => !!b && b.status === "quoted");
  return { baskets, loading: results.some((r) => r.isLoading), error: results.find((r) => r.error && !(r.error instanceof ApiError && r.error.kind === "missing"))?.error };
}
