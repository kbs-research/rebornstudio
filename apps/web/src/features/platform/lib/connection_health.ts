/**
 * 플랫폼 개요의 자격증명 연결 한 줄을 만든다. 문구와 색을 같이 정한다.
 *
 * 상태(실패·주의·점검 전)는 쓰는 곳이 있는 연결만 센다. 서버가 그 기준으로 센 값을 준다
 * (`ConnectionHealthSummary`). 쓰는 곳이 없는 연결은 수만 쓰고 색을 쓰지 않는다.
 * 연동 점검과 세는 대상이 달라 개요에서 줄을 따로 둔다.
 * 연동 점검은 플랫폼 환경(DB·Redis·워커·TLS)과 등록한 저장소·분석 연결의 수를 보고, 이 줄은 등록한 연결의 사용 여부까지 본다.
 * 이 줄이 없으면 연결 화면에 실패 행이 있어도 개요가 모두 정상으로 표시될 수 있다.
 * 쓰지 않는 테스트용 연결의 실패로는 경보를 내지 않는다.
 */
import type { components } from "@reborn/contracts";

export type ConnectionHealth = components["schemas"]["ConnectionHealthSummary"];

/** 줄의 색. `bad`는 쓰는 연결이 실패했을 때만 쓴다. */
export type ConnectionHealthTone = "bad" | "warn" | "muted";

/**
 * 개요 한 줄의 문구와 색을 돌려준다.
 *
 * 문제 수는 사용 중인 연결의 일부라서 괄호 안에 쓴다.
 * 0인 상태는 쓰지 않는다. 늘 붙어 있으면 신호가 약해진다.
 */
export function connectionHealthLine(h: ConnectionHealth): { text: string; tone: ConnectionHealthTone } {
  if (h.total === 0) return { text: "연결 없음", tone: "muted" };
  const unused = h.unused ?? 0;
  const inUse = h.total - unused;
  const problems = [
    h.fail > 0 && `실패 ${h.fail}개`,
    h.warn > 0 && `주의 ${h.warn}개`,
    h.unchecked > 0 && `점검 전 ${h.unchecked}개`,
  ].filter(Boolean) as string[];
  const parts: string[] = [];
  if (inUse > 0) {
    parts.push(problems.length === 0
      ? `사용 중 ${inUse}개 정상`
      : `사용 중 ${inUse}개(${problems.join(", ")})`);
  }
  if (unused > 0) parts.push(`사용처 없음 ${unused}개`);
  const tone: ConnectionHealthTone = h.fail > 0 ? "bad" : h.warn > 0 ? "warn" : "muted";
  return { text: parts.join(" · "), tone };
}
