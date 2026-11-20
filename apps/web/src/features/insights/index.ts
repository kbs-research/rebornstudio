/**
 * 통계·개요 기능의 공개 진입점. 다른 곳은 이 파일로만 통계 조회·지표 사전·표기를 가져온다.
 *
 * 사람 서랍과 멤버 표는 통계 사람 표와 같은 조회·지표 이름·날짜 표기·반려율·수정률 표기를 쓴다.
 * 라우트(`routes.tsx`)와 화면은 내보내지 않는다. 라우터만 쓰고, 이 파일에 두면 표기 하나를 가져오는 곳도 화면 전체를 함께 불러온다.
 * 통계 사람 표가 사람 서랍(`features/people`)을 열기 때문에 이 파일이 화면을 내보내면 import가 순환한다.
 */
export { fetchLabelerPerformance, insightsKeys } from "./api";
export { fmtDay, fmtTime } from "./lib/dates";
export { EDIT_RATE_OVER_NOTE, editRateOver, editRateView } from "./lib/edit_rate";
export { doesLabeling } from "./lib/labeling_roles";
export { METRICS } from "./lib/metrics";
export { approvalView, rejectionView, splitUnit } from "./lib/rates";
