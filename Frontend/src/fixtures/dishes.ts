// The kitchen service has no endpoint that lists dishes yet, but POST /api/service
// is keyed by dish id. These ids and names are copied from Backend/Data/seed/recipes.json.
// Replace this file with a query once the backend exposes the list.
export const DISHES = [
  { id: "d1", name: "Creamy salmon soup (lohikeitto)" },
  { id: "d2", name: "Meatballs in cream sauce with mashed potatoes" },
  { id: "d3", name: "Creamy chicken tagliatelle" },
  { id: "d4", name: "Creamy mushroom soup" },
  { id: "d5", name: "Oven pancake with strawberry jam and whipped cream (pannukakku)" },
] as const;
