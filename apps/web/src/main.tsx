/** 웹 앱 진입점. 쿼리 클라이언트·토스트·라우터를 연결해 루트 요소에 렌더한다. */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";

import { AppProviders } from "@/app/providers";
import { router } from "@/app/router";
import { restoreSession } from "@/shared/api/auth";
import "@/store/theme"; // 부수 효과: 렌더 전에 저장된 테마를 적용한다.

/* 글꼴은 자체 호스팅한다. 그래서 외부 CDN에 접근할 수 없는 폐쇄망 배포에서도 같은 글꼴로 그린다.
   굵기는 타입 스케일이 쓰는 것만 싣는다. 한글은 unicode-range 조각으로 나뉘어 화면에 나온 글자의 조각만 받는다. */
import "@fontsource/ibm-plex-sans-kr/400.css";
import "@fontsource/ibm-plex-sans-kr/500.css";
import "@fontsource/ibm-plex-sans-kr/600.css";
import "@fontsource/ibm-plex-sans-kr/700.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "@fontsource/jetbrains-mono/600.css";
import "@/tokens.css";
import "@/styles/globals.css";

/* access 토큰은 메모리에만 있으므로 새로고침과 새 탭은 렌더 전에 refresh 쿠키로 세션을 복구한다.
   복구 결과를 보고 셸이 로그인 화면으로 보낼지 정하므로 렌더를 복구 뒤로 미룬다. */
void restoreSession().finally(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <AppProviders>
        <RouterProvider router={router} />
      </AppProviders>
    </StrictMode>,
  );
});
