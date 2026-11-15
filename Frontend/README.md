# Frontend: kitchen-side UI

The restaurant's own screen for the ordering agent. Bilingual (English and Finnish), responsive (desktop, kitchen tablet, phone), one user with every permission. Aimo never uses this UI; it appears only as the counterparty.

## Run it locally

Needs Node 20 or newer and both backend services running.

```bash
# terminal 1 and 2, from Backend/ with the venv active
uvicorn gateway.main:app --reload --port 8001
uvicorn kitchen.main:app --reload --port 8002

# terminal 3
cd Frontend
npm install
npm run dev            # http://localhost:5173
```

**One backend setting is required for ordering.** The gateway only accepts orders from a kitchen whose public key it knows. Add this line to `Backend/.env`, then restart the gateway:

```
KITCHEN_JWKS=http://localhost:8002/.well-known/jwks.json
```

Without it, drafting and confirming work but "Approve and order" is refused with `mandate_invalid`.

The dev server forwards `/api/*` to the kitchen service (8002) and `/gateway/*` to the gateway (8001), so the browser only ever talks to port 5173 and there is nothing to configure. To point at other addresses, copy `.env.example` to `.env.local` and change the two URLs.

| Command | Does |
|---|---|
| `npm run dev` | Dev server with hot reload |
| `npm run build` | Type-check, then build to `dist/` |
| `npm run preview` | Serve the built `dist/` |
| `npm run typecheck` | Type-check only |
| `npm run api:types` | Regenerate `src/api/schema.d.ts` from the running kitchen service |

Run `npm run api:types` after any backend change to a request or response shape. A changed field then fails the type-check instead of failing in the demo.

## Screens

| Address | Screen | Endpoints used |
|---|---|---|
| `/` and `/?basket=<id>` | Order: chat, basket, totals, running low | `POST /api/chat`, `GET`/`PATCH /api/basket/{id}`, `POST …/confirm`, `POST …/approve`, `GET /api/suggestions`, `POST /api/suggestions/{id}/basket`, `POST /api/service` |
| `/approvals` | Baskets with a signed price, waiting | `GET /api/baskets?status=quoted` |
| `/inventory` | Stock, on order, menu usage, runs out | `GET /api/inventory` |
| `/rules` | Mandate in force, issue a new one | `GET`/`POST /api/mandate` |
| `/activity` | Orders, agent steps, "What Aimo sees" | `GET /api/orders`, `GET /api/audit`, `GET /api/orders/{id}/audit` |
| `/demo` | Reset both services (not part of the product, not in the navigation) | `POST /api/demo/reset`, `POST /gateway/demo/reset` |

## Which file does what

```
Frontend/
  index.html              page shell, viewport, favicon
  vite.config.ts          dev server and the /api and /gateway forwarding
  package.json            dependencies and the commands above
  fixtures/               sample_basket.json from the backend team (reference only)
  src/
    main.tsx              entry point: data cache, router, announcer, languages, styles
    App.tsx               the route table (address -> page)
    index.css             design tokens (colours, font, radius), focus ring, reduced motion

    api/
      schema.d.ts         GENERATED from the kitchen service's OpenAPI. Do not edit.
      types.ts            short names for the generated shapes (Basket, BasketLine, ...)
      client.ts           the only file that calls the backend; one function per endpoint;
                          turns failures into ApiError (network / rule / missing / invalid)

    i18n/
      index.ts            language setup, remembers the choice, keeps <html lang> right
      en.json, fi.json    every UI string, plus labels for flags, claims, units, refusals

    lib/
      format.ts           money, numbers, dates, countdowns, unit and code labels, product names
      session.ts          current basket, selected role, chat, kitchen generation

    components/
      ui.tsx              Button, Chip, Banner, Card, Skeleton, EmptyState, ErrorNote, PageTitle
      AppShell.tsx        header, top navigation, phone tab bar, language switch, skip link
      Announcer.tsx       one screen-reader live region; useAnnounce() to speak a change

    features/order/
      useBasket.ts        every basket action (chat, edit, confirm, approve, draft suggestion)
      ChatPanel.tsx       conversation, example requests, input
      BasketLine.tsx      one product line: unit sum, quantity, price, delivery, flags, alternatives
      StatusSteps.tsx     Draft -> Signed price -> Ordered
      ActionPanel.tsx     totals, extra-check notice, signed-price state, the primary button;
                          also MobileActionBar (sticky bar on a phone)
      SuggestionsPanel.tsx  "Running low" cards with the sum behind each
      ServiceDialog.tsx   "What did you cook today?" dialog

    features/approvals/
      useWaitingBaskets.ts  finds baskets with a signed price (nav badge + Approvals page)

    pages/
      OrderPage.tsx       lays out chat, basket and side panel; shows approve outcomes
      ApprovalsPage.tsx   InventoryPage.tsx   RulesPage.tsx   ActivityPage.tsx   DemoPage.tsx

    fixtures/
      dishes.ts           dish ids and names (no endpoint lists them yet)
      productNames.ts     names of the 17 seed products in EN and FI (the API sends null)
```

### Where to change common things

| To change | Edit |
|---|---|
| A word on screen | `src/i18n/en.json` and `fi.json` (same key in both) |
| The label for a new flag, claim, unit or refusal code | the `flag`, `claim`, `unit`, `unitOne`, `refusal` blocks in both JSON files. An unknown code falls back to readable text, never a blank. |
| A colour or the font | the `@theme` block in `src/index.css` |
| How money or dates look | `src/lib/format.ts` |
| A backend address | `.env.local` (dev) |
| A new endpoint | add a function in `src/api/client.ts`, run `npm run api:types` |
| The role sent on approve | the role selected in the header; the backend enforces its permissions |

## How it behaves

- **No sign-in yet.** The header lets the person at the screen choose chef, manager or owner; the backend enforces what that role may do. This is a demo role switch, not user authentication.
- **The server's numbers, always.** The UI never adds up totals. After every action it shows the basket the backend returned.
- **The basket id is in the address**, so refresh, Back and a copied link all work.
- **Approve has three answers**, all shown as a banner above the basket: ordered, adjusted (the basket was repaired, confirm again), refused (with the reason; a failed signature is shown as a security stop and logged in Activity).
- **Signed prices expire.** The countdown is live; once it passes, the button becomes "Confirm price again".

## Languages

Fully bilingual: navigation, buttons, statuses, flags, claims, units, money (`€1,010.00` / `1 010,00 €`), dates (`Mon 16 Nov` / `ma 16.11.`).

English only, because the backend produces them: product names in baskets, the agent's replies and reasons, policy reasons, dish names. They carry `lang="en"` so a Finnish screen reader pronounces them correctly. The Finnish strings are a first draft and need a native check.

## Accessibility

Built to WCAG 2.2 AA:

- Skip link, landmarks (`header`, `nav`, `main`, `aside`), one `h1` per page.
- Everything works by keyboard; a visible 3 px focus ring is never removed.
- Dialogs trap focus, close on Escape and return focus to the button that opened them.
- Touch targets are at least 44 px.
- Warnings use an icon, words and a dashed outline, not colour alone.
- Basket changes are announced through a live region; errors and order outcomes use `role="alert"`.
- Form fields have visible labels; errors sit under the field and are linked to it.
- `prefers-reduced-motion` is respected; text zooms to 200% and the layout holds at 320 px wide.

Not yet done: a pass with a real screen reader, and an automated axe run.

## Workarounds for endpoints that do not exist yet

| Screen part | Missing in the backend | What the frontend does |
|---|---|---|
| "What did you cook today?" | a list of dishes | `src/fixtures/dishes.ts`, copied from `Data/seed/recipes.json` |
| Product names in Inventory and Running low | `name` is `null` | `src/fixtures/productNames.ts` |
| Finnish product names in baskets | no `name_fi` field | shows the English name |
| Product groups on the mandate form | category list on the kitchen service | not offered; a new mandate allows all groups |
| Tamper and scenario switch | no endpoints | not on the demo page |

Each fixture file says at the top what replaces it.

The approvals list and activity trace now come from the backend, so they are shared across devices and survive a page reload. The order flow and both services have backend tests; this frontend has a production build/type-check but no automated browser test suite yet.

## Stack

Vite 8, React 19, TypeScript 5, Tailwind CSS 4, React Router 7, TanStack Query 5, react-i18next, Radix Dialog, Lucide icons, Inter (bundled, no network font request).

Changes from the stack proposed earlier: components are hand-written on Radix Dialog rather than installed through the shadcn/ui CLI (same primitives, no generator step); the two small forms use plain React state instead of React Hook Form and Zod; MSW and unit tests are not included. TypeScript is pinned to 5.x because the type generator does not run on TypeScript 7.
