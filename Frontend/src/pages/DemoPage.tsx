// /demo: not part of the product. One button that resets both services and
// everything this browser remembers, to run before each rehearsal.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { Banner, Button, Card, ErrorNote, PageTitle } from "../components/ui";
import { clearChat, clearSteps, forgetAllBaskets } from "../lib/session";

export function DemoPage() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const reset = useMutation({
    mutationFn: async () => { await api.resetKitchen(); await api.resetGateway(); },
    onSuccess: () => { forgetAllBaskets(); clearSteps(); clearChat(); client.clear(); },
  });
  return (
    <>
      <PageTitle>{t("demo.title")}</PageTitle>
      <Card className="max-w-xl space-y-3 p-6">
        <p className="text-sm text-ink-soft">{t("demo.note")}</p>
        <Button variant="primary" loading={reset.isPending} icon={<RotateCcw className="size-4" aria-hidden />} onClick={() => reset.mutate()}>
          {reset.isPending ? t("demo.resetting") : t("demo.reset")}
        </Button>
        {reset.isSuccess && <Banner tone="ok" alert>{t("demo.done")}</Banner>}
        {reset.isError && <ErrorNote error={reset.error} />}
      </Card>
    </>
  );
}
