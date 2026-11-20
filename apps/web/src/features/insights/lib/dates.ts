/**
 * 통계·멤버 화면의 날짜와 시각 표기. 앱 전체 규칙(`shared/lib/time`)을 그대로 쓴다.
 *
 * 날짜는 `9월 23일`, 시각은 `17:53`이다. 올해가 아닌 날짜는 앞에 연도를 붙인다.
 * 시각은 24시간제로 쓰고 `오전`·`오후`를 쓰지 않는다. 같은 기록이 감사 로그(`18:30:00`)와 통계에서
 * 다른 꼴로 보이면 같은 일인지 대조할 수 없다.
 */
import { fmtDate, fmtTime as fmtClock } from "@/shared/lib/time";

/** 날짜 하나를 쓴다. `9월 23일`, 올해가 아니면 `2025년 9월 23일`이다. */
export function fmtDay(iso: string, now: Date = new Date()): string {
  return fmtDate(iso, now);
}

/** 시각(`09:02`)만 쓴다. 같은 날의 기록을 나란히 놓는 표에 쓴다. */
export function fmtTime(iso: string): string {
  return fmtClock(iso);
}
