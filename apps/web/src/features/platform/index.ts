/**
 * 플랫폼 기능의 공개 진입점. 다른 곳은 이 파일로만 플랫폼 부품을 가져온다.
 *
 * 프로젝트 설정의 저장소·분석 연결 카드가 연동 진단 목록을 같은 모양으로 그린다.
 * 라우트(`routes.tsx`)는 내보내지 않는다. 라우터가 `routes.tsx`를 직접 가져간다.
 */
export { DiagnosticsList, diagnosticsSummary } from "./components/DiagnosticsList";
