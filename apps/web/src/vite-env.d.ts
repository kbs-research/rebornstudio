/**
 * Vite가 주입하는 전역 타입. `import.meta.env`(`DEV`·`PROD`·`MODE`)와 에셋 import를 선언한다.
 *
 * 이 선언이 없으면 `import.meta.env`가 `TS2339` 오류가 된다.
 * 라우터가 디자인 시스템 카탈로그를 개발 빌드에서만 등록할 때 이 값을 읽는다.
 * 운영 빌드에서는 상수 `false`로 치환되어 그 라우트와 화면 모듈이 번들에서 빠진다.
 *
 * `tsconfig`의 `types` 배열 대신 이 파일로 넣는다.
 * `types` 배열은 나열한 것 외의 전역 타입을 모두 꺼서 `vite.config.ts`의 `vitest/config`·node 타입까지 함께 지정해야 한다.
 */
/// <reference types="vite/client" />
