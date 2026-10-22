import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MotionConfig } from "motion/react"
import { RouterProvider } from "react-router"
import "./index.css"
import { router } from "@/app/router"
import { SmoothScroll } from "@/components/SmoothScroll"
import { Toaster } from "@/components/ui/toast"
import { ApiError } from "@/lib/api"

const queryClient = new QueryClient({
  queryCache: new QueryCache({
    // An expired session mid-use sends the owner back to log in.
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401 && router.state.location.pathname.startsWith("/app")) {
        queryClient.clear()
        void router.navigate("/login", { replace: true })
      }
    },
  }),
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      refetchOnWindowFocus: false,
    },
  },
})

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <MotionConfig reducedMotion="user">
        <SmoothScroll />
        <RouterProvider router={router} />
        <Toaster />
      </MotionConfig>
    </QueryClientProvider>
  </StrictMode>,
)
