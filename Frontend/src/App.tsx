// The route table. Every screen has its own address, so links and Back work.
import { Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { ActivityPage } from "./pages/ActivityPage";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { DemoPage } from "./pages/DemoPage";
import { InventoryPage } from "./pages/InventoryPage";
import { OrderPage } from "./pages/OrderPage";
import { RulesPage } from "./pages/RulesPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<OrderPage />} />
        <Route path="approvals" element={<ApprovalsPage />} />
        <Route path="inventory" element={<InventoryPage />} />
        <Route path="rules" element={<RulesPage />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="demo" element={<DemoPage />} />
        <Route path="*" element={<OrderPage />} />
      </Route>
    </Routes>
  );
}
