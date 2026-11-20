/**
 * people feature의 공개 진입점. 다른 feature와 화면은 이 파일로만 가져간다.
 *
 * 사람 서랍은 멤버 화면 밖에서도 통계 사람 표가 연다.
 */
export { listMembers, peopleKeys } from "./api";
export { PersonDrawer } from "./components/PersonDrawer";
