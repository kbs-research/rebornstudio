/**
 * audit 기능의 공개 진입점. 이벤트 종류의 화면 이름·심각도와 감사 시각 형식을 내보낸다.
 *
 * 사용자 활동 이력과 비밀번호 재설정이 같은 이름과 시각 형식을 쓰게 한다.
 */
export { eventTone, fmtEventTime, humanizeEvent, type EventTone } from "./lib/event_label";
