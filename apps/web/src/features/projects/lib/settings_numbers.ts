/**
 * 설정 화면 숫자 입력란의 규격과 검사. 입력란의 `min`/`max`, 저장 전 검사,
 * 서버 422를 가리키는 문구가 이 파일 한 곳의 정의에서 나온다.
 *
 * 규격을 입력란마다 JSX 리터럴(`min={10}`)로 흩어 두면 정수인지를 아는 곳이 없다.
 * 그러면 소수는 범위 문구로 틀리게 안내되고, 비울 수 없는 값을 비우면 저장된 것처럼 보인다.
 * 두 경우 모두 이 파일의 규격으로 저장 전에 막는다.
 *
 * 범위의 기준은 계약(`docs/api/openapi.json`)이다. 이 파일은 그 사본이고
 * `settings_numbers.test.ts`가 둘을 대조한다.
 */

/** 숫자 입력란 하나의 규격. `max`가 `null`이면 상한이 없다. */
export interface NumberSpec {
  min: number;
  max: number | null;
  /** 계약 타입이 `integer`이면 true. 소수를 넣으면 서버가 422로 거절한다. */
  int: boolean;
  /**
   * 비울 수 있으면 true. 비울 수 없는 입력란(DB가 NULL을 받지 않는 필드)을 비우면 저장 전에 막는다.
   * 서버는 그 `null`을 422로 돌려보낸다(`org/project.py`의 `_reject_bad_update`).
   */
  clearable: boolean;
}

/** 폼 필드 id별 규격. 키는 폼 필드 id이고 계약(`ProjectUpdateRequest`)의 속성 이름과 같다. */
export const NUMBER_SPECS = {
  // 목표 시간은 비울 수 있다("목표 없음").
  target_hours: { min: 0, max: null, int: false, clearable: true },
} as const satisfies Record<string, NumberSpec>;

export type NumberField = keyof typeof NUMBER_SPECS;

/**
 * 비울 수 없는 입력란을 비웠을 때의 문구.
 * 범위 문구로 대신하면 무엇을 넣어야 하는지만 알리고 비웠다는 사실은 알리지 않는다.
 */
export const REQUIRED_TEXT = "값을 입력해주세요.";

/**
 * 입력란 옆에 보여 줄 범위 문구. 저장 전 검사와 서버 422가 같은 문장을 쓴다.
 *
 * 정수 입력란은 항상 "정수"를 문구에 넣는다.
 * 서버 422는 범위를 벗어난 경우와 소수인 경우를 같은 필드로 가리킨다.
 * 그래서 어느 경우든 이 문장 하나가 맞아야 한다. "1~50 사이로"만 쓰면 5.5를 넣었을 때 틀린 안내가 된다.
 */
export function boundsText(spec: NumberSpec): string {
  if (spec.max == null)
    return spec.int ? `${spec.min} 이상의 정수로 입력해주세요.` : `${spec.min} 이상으로 입력해주세요.`;
  return spec.int
    ? `${spec.min}~${spec.max} 사이의 정수로 입력해주세요.`
    : `${spec.min}~${spec.max} 사이로 입력해주세요.`;
}

/**
 * 저장 전에 검사한다. 걸리면 입력란 옆에 보일 문구를, 통과하면 `null`을 돌려준다.
 *
 * Args:
 *   raw: 입력란의 문자열 값 그대로(비었으면 `""`).
 *   spec: 그 입력란의 규격.
 */
export function checkNumber(raw: string, spec: NumberSpec): string | null {
  const t = raw.trim();
  if (t === "") return spec.clearable ? null : REQUIRED_TEXT;
  const n = Number(t);
  const bad =
    !Number.isFinite(n)
    || (spec.int && !Number.isInteger(n))
    || n < spec.min
    || (spec.max != null && n > spec.max);
  return bad ? boundsText(spec) : null;
}
