import { useQuery } from "@tanstack/react-query"
import { Navigate, Outlet, useLocation } from "react-router"
import { api, ApiError } from "@/lib/api"
import { RouteFallback } from "@/components/RouteFallback"

export const meQuery = { queryKey: ["me"], queryFn: api.me }

/** Gate for /app: anyone without a session goes to the login screen. */
export function RequireAuth() {
  const me = useQuery(meQuery)
  const location = useLocation()

  if (me.isPending) return <RouteFallback />
  if (me.error instanceof ApiError && me.error.status === 401) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  if (me.isError) {
    return (
      <RouteFallback>
        <p className="max-w-md text-body-lg">
          The server isn't answering. Start the backend (<code className="font-mono">backend/scripts/dev.sh</code>)
          and reload.
        </p>
      </RouteFallback>
    )
  }
  return <Outlet />
}
