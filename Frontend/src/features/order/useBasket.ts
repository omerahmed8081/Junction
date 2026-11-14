// Everything that reads or changes a basket. Each action:
//   1. calls the kitchen service
//   2. puts the basket it returns into the query cache (the server's figures, never ours)
//   3. records the agent's steps for the Activity page
//   4. announces the result for screen readers
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import type { Basket, ChatResponse, LineEdit, OrderResponse } from "../../api/types";
import { useAnnounce } from "../../components/Announcer";
import { money } from "../../lib/format";
import { addChatMessage, clearChat, forgetBasket, logSecurityEvent, logSteps, rememberBasket, setCurrentBasket } from "../../lib/session";

export function useBasketQuery(id: string | null) {
  return useQuery({ queryKey: ["basket", id], queryFn: () => api.basket(id!), enabled: !!id, retry: false });
}

/**
 * `chatTarget` is the basket the chat adds to: the open basket while it is a
 * draft, otherwise null so the next message starts a new order.
 */
export function useBasketActions(basketId: string | null, onBasket?: (basket: Basket) => void,
                                 chatTarget: string | null = basketId, onDeleted?: () => void) {
  const client = useQueryClient();
  const announce = useAnnounce();
  const { t } = useTranslation();

  const accept = (basket: Basket | null | undefined, steps: ChatResponse["steps"]) => {
    logSteps(steps, basket?.basket_id ?? basketId);
    if (!basket) return;
    rememberBasket(basket.basket_id);
    client.setQueryData(["basket", basket.basket_id], basket);
    announce(t("basket.updated", { net: money(basket.net_total) }));
    onBasket?.(basket);
  };
  const afterOrderChange = () => {
    void client.invalidateQueries({ queryKey: ["suggestions"] });
    void client.invalidateQueries({ queryKey: ["inventory"] });
    void client.invalidateQueries({ queryKey: ["orders"] });
  };

  const chat = useMutation({
    mutationFn: (message: string) => api.chat(message, chatTarget),
    onMutate: (message) => addChatMessage({ role: "you", text: message }),
    onSuccess: (r) => { addChatMessage({ role: "agent", text: r.reply, steps: r.steps }); accept(r.basket, r.steps); },
  });
  const edit = useMutation({
    mutationFn: (v: { edits: LineEdit[]; requestedDate?: string }) => api.editBasket(basketId!, v.edits, v.requestedDate),
    onSuccess: (r) => accept(r.basket, r.steps),
  });
  const confirm = useMutation({
    mutationFn: () => api.confirm(basketId!),
    // The signed basket goes on to approval; the conversation for it is over,
    // so the chat starts clean and the next message begins a new order.
    onSuccess: (r) => { accept(r.basket, r.steps); clearChat(); },
  });
  const remove = useMutation({
    mutationFn: () => api.deleteBasket(basketId!),
    onSuccess: () => {
      forgetBasket(basketId!);
      setCurrentBasket(null);
      clearChat();
      client.removeQueries({ queryKey: ["basket", basketId] });
      announce(t("basket.deleted"));
      onDeleted?.();
    },
  });
  const approve = useMutation({
    mutationFn: () => api.approve(basketId!),
    onSuccess: (r: OrderResponse) => {
      accept(r.basket, r.steps);
      if (r.refusal?.security_event) logSecurityEvent(`${r.refusal.reason}: ${r.refusal.detail}`, basketId);
      afterOrderChange();
    },
  });
  const draftSuggestion = useMutation({
    mutationFn: (productId: string) => api.draftSuggestion(productId),
    onSuccess: (r) => { accept(r.basket, r.steps); afterOrderChange(); },
  });

  return { chat, edit, confirm, approve, draftSuggestion, remove };
}
