/**
 * 보류된 분석 요청의 행 안내.
 *
 * 짧은 사유(`label`)와 다음 재요청 시각(`retry_by`)은 서버가 정한다. 화면은 사유 문장을 해석해
 * 종류를 다시 판정하지 않고, 시각을 "몇 분 안에"로 바꿔 쓰기만 한다. 전체 사유는 마우스를 올리면 보인다.
 */
import type { components } from "@reborn/contracts";

type DispatchDeferred = components["schemas"]["DispatchDeferred"];

/** 요청 대기 행에 붙일 안내 한 줄과 마우스를 올리면 보일 전체 사유. */
export interface DeferredNote {
  text: string;
  title?: string;
}

/**
 * 보류된 분석 요청의 행 안내를 만든다. 보류가 아니면 `undefined`다.
 *
 * 다음 재요청 시각은 늦어도 재요청이 시작되는 시각이라 "N분 안에"로 쓴다.
 * 시각이 없거나(이전 형식의 보류 표시) 이미 지났으면(다시 보낼 영상이 밀려 있음) 임의의 시각 대신 "잠시 후"로 쓴다.
 *
 * @param d 영상 응답의 `dispatch_deferred`
 * @param now 지금 시각
 */
export function deferredNote(d: DispatchDeferred | null | undefined, now: Date): DeferredNote | undefined {
  if (!d) return undefined;
  const left = d.retry_by ? (Date.parse(d.retry_by) - now.getTime()) / 1000 : NaN;
  const when = left > 0 ? `${Math.max(1, Math.ceil(left / 60))}분 안에` : "잠시 후";
  return {
    text: `보류됨 · ${d.label} · ${when} 자동으로 다시 요청합니다`,
    title: d.reason ?? undefined,
  };
}
