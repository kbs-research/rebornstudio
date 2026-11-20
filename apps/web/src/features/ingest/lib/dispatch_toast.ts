/**
 * 분석 요청 결과를 토스트 한 건으로 만든다. 서버가 준 수(`MaiuDispatchResult`)로만 문구를 정한다.
 *
 * 분모를 밝힌다. 12건 중 4건만 요청됐으면 나머지 8건이 요청되지 않은 사유를 함께 알린다.
 * 결과마다 다음 할 일이 다르다.
 * - deferred: 분석 서비스의 일시 오류로 보류됐다. 서버가 잠시 후 같은 영상을 다시 요청한다
 *   (`redispatch_deferred`). 다시 누르라고 하면 같은 영상을 두 번 요청하게 된다.
 * - failed: 그 영상 고유의 문제다. 영상은 요청 대기에 남고 사유는 그 행이 보여 준다.
 *   영상이 문제 상태로 옮겨지지 않으므로 문제 목록으로 안내하면 그 영상을 찾을 수 없다.
 * - skipped: 이미 요청됐다.
 * - held: 요청 게이트에서 보류됐다. 사유(규격 검증 전·준비 중 등)는 서버가 준다.
 *
 * 이 판정만 따로 테스트하려고 컴포넌트 밖에 둔다.
 */
import { holdReasonText } from "./vocab";

/** 이 판정에 필요한 응답 필드. */
export interface DispatchCounts {
  created: unknown[];
  deferred: number;
  failed: number;
  skipped: number;
  held: number;
  held_reasons?: Record<string, number>;
}

/** 토스트 한 건. `useToast`의 `(type, message, detail?)` 인자에 그대로 넘긴다. */
export interface DispatchToast {
  tone: "success" | "warning";
  title: string;
  detail?: string;
}

/** 보류 안내. 서버가 다시 요청하므로 다시 누르라고 하지 않는다. */
export const DEFERRED_HINT = "분석 서비스가 일시적으로 요청을 받지 못했습니다. 잠시 후 자동으로 다시 요청합니다.";
/** 실패 안내. 실패한 영상은 요청 대기에 남고 행이 사유를 보여 준다. */
export const FAILED_HINT = "사유는 요청 대기 목록의 영상 행에 있습니다.";

/** 결과를 토스트 한 건으로 바꾼다. 영상 수라서 단위는 `개`다. */
export function dispatchToast(r: DispatchCounts): DispatchToast {
  const n = r.created.length;
  const rest = [
    r.deferred > 0 ? `${r.deferred}개 보류` : "",
    r.failed > 0 ? `${r.failed}개 실패` : "",
    r.skipped > 0 ? `${r.skipped}개는 이미 요청됨` : "",
    r.held > 0 ? `${r.held}개 요청하지 않음` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const held = holdReasonText(r.held_reasons);
  const detail = r.deferred > 0
    ? DEFERRED_HINT
    : r.failed > 0
      ? FAILED_HINT
      : held
        ? `사유 · ${held}`
        : undefined;
  if (n > 0 && rest) return { tone: "warning", title: `영상 ${n}개 분석 요청 · ${rest}`, detail };
  if (n > 0) return { tone: "success", title: `영상 ${n}개 분석 요청`, detail: "진행 상황은 분석 중 목록에 보입니다." };
  if (rest) return { tone: "warning", title: rest, detail };
  return { tone: "warning", title: "요청 가능한 대기 영상이 없습니다" };
}
