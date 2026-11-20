/** 앱 전역 TanStack Query 클라이언트와 실패 알림 규칙. */
import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

import { errorText, isRetryable } from "@/shared/api/error_text";

/* 요청 실패를 전역 onError에서 토스트로 알린다.
   전역 처리가 없으면 제출·초안 저장이 429·5xx·타임아웃으로 실패해도 화면에 표시가 없어 사용자는 성공한 줄 안다.
   문구는 `errorText()`로 바꾼다. `e.detail`을 그대로 실으면 영문 코드가 한국어 토스트에 뜬다.
   토스트는 React 훅(useToast)이라 React 밖인 이 모듈에서는 부를 수 없다.
   그래서 창 이벤트로 넘기고 ToastProvider가 받는다. */

/**
 * 셸이 따로 알리는 실패이면 true. 셸이 한 번 알리므로 요청마다 토스트를 다시 띄우지 않는다.
 * 401은 `api()`가 `reborn:session-expired`로 만료 모달을 띄운다.
 * 403 `account_not_active`(정지된 계정의 남은 세션)는 `사용할 수 없는 계정` 패널이 알린다.
 * 403 `privacy_consent_required`(동의 기록 없음)는 동의 화면이 알린다.
 */
export function shellHandles(e: { status?: number; detail?: string } | undefined): boolean {
  return (
    e?.status === 401 ||
    (e?.status === 403 && (e?.detail === "account_not_active" || e?.detail === "privacy_consent_required"))
  );
}

/** 실패를 창 이벤트(`reborn:toast`)로 보내 토스트로 띄운다. */
function toastError(detail: string | undefined) {
  const { message, hint } = errorText(detail);
  window.dispatchEvent(
    new CustomEvent("reborn:toast", { detail: { type: "error", message, detail: hint } }),
  );
}
/** 앱 전역 QueryClient. 실패 알림과 재시도 규칙을 여기서 정한다. */
export const queryClient = new QueryClient({
  /*
    뮤테이션: 자체 onError가 있으면 그 onError가 처리한다(중복 토스트 방지).
    `meta.handled`를 켠 뮤테이션은 `isError`를 읽어 화면에 직접 그린다(대기열 카드의 `다음 작업` 실패 배너).
  */
  mutationCache: new MutationCache({
    onError: (error, _vars, _ctx, mutation) => {
      if (mutation.options.onError || mutation.meta?.handled) return;
      const e = error as { status?: number; detail?: string };
      if (shellHandles(e)) return;
      toastError(e?.detail);
    },
  }),
  /*
    쿼리: 처음 받아오다 실패해 화면에 아무것도 없는 경우만 알린다.
    알리지 않으면 목록 GET이 503으로 실패해도 빈 목록이 그려져 사용자는 자기 작업이 사라진 줄 안다.
  */
  queryCache: new QueryCache({
    onError: (error, query) => {
      const e = error as { status?: number; detail?: string };
      if (shellHandles(e)) return; // 셸의 만료 모달·계정 패널·동의 화면이 안내한다.
      if (query.meta?.handled) return; // 화면이 직접 그린다.
      // 이전 값이 화면에 보이고 있으므로 백그라운드 갱신(30초마다) 실패는 알리지 않는다.
      if (query.state.data !== undefined) return;
      toastError(e?.detail);
    },
  }),
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      /* 재시도 여부를 상태 코드로 정한다.
         일시 오류만 다시 보낸다. 영구 실패(404·403)는 다시 보내도 결과가 같다. */
      retry: (failureCount, error) => {
        // 화면의 "다시 시도" 안내와 같은 함수로 판정한다.
        if (!isRetryable((error as { status?: number })?.status)) return false;
        return failureCount < 3;
      },
      retryDelay: (i) => Math.min(1000 * 2 ** i, 8000),
      refetchOnWindowFocus: false,
    },
  },
});
