import "@/styles/index.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "@/app/App";
import { bootstrapUi } from "@/stores/uiStore";

bootstrapUi();

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root container #root not found in document");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
