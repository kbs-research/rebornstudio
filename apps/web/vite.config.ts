/// <reference types="vitest/config" />
import path from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig, transformWithEsbuild, type Plugin } from "vite";

/**
 * `@vidstack/react`의 JSX를 rollup보다 먼저 JS로 변환하는 플러그인을 만든다.
 *
 * 이 패키지는 dev/prod 두 번들 모두에 트랜스파일되지 않은 JSX를 그대로 담아 배포한다.
 * vite dev는 esbuild 의존성 최적화가 JSX를 처리하지만, prod 빌드(rollup)는 node_modules의
 * raw JSX를 파싱하지 못해 실패한다(`@vidstack/react/prod/vidstack.js`의 `return <MediaPlayerBridge/>`).
 * 그래서 이 패키지의 `.js`만 esbuild(jsx 로더)로 먼저 변환해 rollup이 순수 JS만 받게 한다.
 * 변환 범위는 이 패키지로 한정하고, `prod/vidstack.js` 외에 `prod/chunks/`·`prod/player/` 서브엔트리도 같은 필터로 포함한다.
 * `@vidstack/react` 2.x가 미리 트랜스파일한 번들을 배포하면 이 플러그인을 지울 수 있다.
 */
function vidstackJsx(): Plugin {
  return {
    name: "vidstack-jsx",
    enforce: "pre",
    // 객체 형태 훅으로 쓰고 반환 타입만 명시적으로 느슨하게 둔다.
    // rollup 4.x는 마이너 버전마다 transform 훅 타입이 좁아진다(함수 축약과 Promise<null>을 받지 않는다).
    // 그래서 호스트와 다른 rollup 버전을 받는 빌드에서도 컴파일되게 한다.
    transform: {
      async handler(code, id): Promise<unknown> {
        const file = id.split("?")[0];
        if (!file.includes("/@vidstack/react/") || !file.endsWith(".js")) return null;
        return transformWithEsbuild(code, id, { loader: "jsx", jsx: "automatic" });
      },
    } as Plugin["transform"],
  };
}

const SRC = path.resolve(__dirname, "src");

/** Vite 빌드·개발 서버·vitest 설정. */
export default defineConfig({
  plugins: [vidstackJsx(), react()],
  resolve: {
    alias: [{ find: "@", replacement: SRC }],
  },
  // 테스트는 순수 로직만 대상으로 한다(아래 include). 화면 렌더 테스트는 두지 않는다.
  // 대상은 표기·집계·조사·오류 문구처럼 틀려도 `tsc`가 잡아내지 못하고 화면만 틀리는 코드다.
  // 날짜 계산(`features/insights/lib/dates.ts` 등)이 하루 밀리지 않는지 같은 조건에서 확인하도록 시간대를 KST로 고정한다.
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
    env: { TZ: "Asia/Seoul" },
    // `@vidstack/react`는 테스트에서도 vite 변환(위 `vidstackJsx`)을 거치게 한다.
    // vitest는 node_modules를 변환 없이 Node에 바로 넘기는데, 이 패키지는 `.js`에 JSX를 그대로 담아
    // Node가 `SyntaxError: Unexpected token '<'`로 멈춘다. 기능 폴더의 `index.ts`가 화면 부품도 내보내므로,
    // 사전 하나를 `index.ts`로 가져오는 순수 로직 테스트도 영상 플레이어까지 불러온다.
    server: { deps: { inline: ["@vidstack/react"] } },
  },
  server: {
    port: 3000,
    // 컨테이너에서 실행할 때는 0.0.0.0으로 열어야 호스트에서 접속할 수 있다(compose web 서비스).
    host: true,
    // 도커 볼륨 마운트에서는 파일 이벤트가 오지 않는 경우가 있어 폴링으로 HMR을 유지한다.
    watch: { usePolling: !!process.env.VITE_DOCKER },
    proxy: {
      // FastAPI api-gateway를 `/api` 접두사로 호출한다(CORS 회피, 프로덕션과 같은 경로).
      // target은 실행 위치에 따라 다르다. 호스트면 localhost, compose 안이면 서비스 이름(api)이다.
      // localhost로 고정하면 컨테이너 안에서 자기 자신을 가리켜 프록시가 동작하지 않는다.
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/@vidstack/")) return "vidstack";
          if (
            id.includes("node_modules/react/") ||
            id.includes("node_modules/react-dom/") ||
            id.includes("node_modules/scheduler/")
          )
            return "react-vendor";
          if (id.includes("node_modules/@tanstack/")) return "query";
          if (id.includes("node_modules/lucide-react/")) return "icons";
        },
      },
    },
  },
});
