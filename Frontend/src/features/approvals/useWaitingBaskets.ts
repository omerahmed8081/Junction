// Baskets with a signed price, waiting to be approved and placed. The kitchen
// lists them for every device, so a basket the chef confirms on the kitchen
// tablet shows up on the manager's phone. Split by who is looking:
//   * mine   - this role may approve it
//   * others - it needs someone more senior
// Shared by the nav badge and the Approvals page.
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { canApprove, useRole } from "../../lib/session";

export function useWaitingBaskets() {
  const role = useRole();
  const query = useQuery({ queryKey: ["baskets", "quoted"], queryFn: () => api.baskets("quoted"), refetchInterval: 15_000 });
  const all = query.data ?? [];
  return {
    role,
    baskets: all.filter((b) => canApprove(role, b.policy?.level)),
    others: all.filter((b) => !canApprove(role, b.policy?.level)),
    loading: query.isLoading,
    error: query.error,
  };
}
