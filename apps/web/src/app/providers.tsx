/** 앱 전역 공급자(쿼리 클라이언트·토스트). 진입점과 라우터 사이에 둔다. */
import { QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { ToastProvider } from "@/shared/ui/Toast";

import { queryClient } from "./queryClient";

/** 쿼리 클라이언트와 토스트를 자식 트리에 공급한다. 토스트는 쿼리 전역 오류 알림이 쓰므로 안쪽에 둔다. */
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>
  );
}
