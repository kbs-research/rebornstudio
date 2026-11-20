/**
 * 콘텐츠 기능의 공개 진입점. 다른 기능은 이 파일로만 영상 부품과 키를 가져온다.
 *
 * 영상 플레이어는 검수·완료 상세·라벨링 워크벤치가, 키 팩토리는 같은 영상·클립 캐시를 무효화하는 화면이 쓴다.
 * 화면(`pages/`)과 라우트(`routes.tsx`)는 라우터만 쓰므로 내보내지 않는다.
 * 주의: 이 파일이 영상 플레이어를 내보내므로 이 파일을 거치는 순수 로직 테스트도 `@vidstack/react`를 불러온다.
 * `vite.config.ts`의 `test.server.deps.inline`이 이 패키지를 변환한다.
 */
export { fetchClip, fetchVideo, libraryKeys } from "./api";
export { VideoPlayer } from "./components/VideoPlayer";
export { clipRangeFormatter } from "./lib/clip_range";
export { clipSummary, SPEECH_SOURCE_LABEL } from "./lib/clip_summary";
