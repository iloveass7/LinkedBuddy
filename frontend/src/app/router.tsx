import type { ComponentType } from "react"
import { createBrowserRouter, Navigate } from "react-router"
import { RequireAuth } from "@/components/RequireAuth"
import { RouteError } from "@/components/RouteError"
import { RouteFallback } from "@/components/RouteFallback"

const page = (load: () => Promise<{ default: ComponentType }>) => async () => ({
  Component: (await load()).default,
})

export const router = createBrowserRouter([
  {
    HydrateFallback: RouteFallback,
    ErrorBoundary: RouteError,
    children: [
      { path: "/", lazy: page(() => import("@/pages/Landing")) },
      { path: "/login", lazy: page(() => import("@/pages/Login")) },
      {
        path: "/app",
        Component: RequireAuth,
        children: [
          {
            lazy: page(() => import("@/components/AppShell")),
            children: [
              { index: true, element: <Navigate to="/app/inbox" replace /> },
              { path: "inbox", lazy: page(() => import("@/pages/Inbox")) },
              { path: "review", lazy: page(() => import("@/pages/Review")) },
              { path: "review/:eventId", lazy: page(() => import("@/pages/Review")) },
              { path: "archive", lazy: page(() => import("@/pages/Archive")) },
              { path: "settings", lazy: page(() => import("@/pages/Settings")) },
            ],
          },
        ],
      },
      { path: "*", lazy: page(() => import("@/pages/NotFound")) },
    ],
  },
])
