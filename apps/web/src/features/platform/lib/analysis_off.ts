/**
 * 분석 연동이 꺼진 서버의 점검 안내를 한 문구로 맞춘다.
 *
 * 같은 상태를 서버의 여러 점검(연동 진단, 분석 대상 점검, 연결 점검)이 각자 다른 문장으로 알린다.
 * 어느 화면은 환경 변수 이름을, 어느 화면은 없는 역할 이름을 써서 무엇을 누가 켜는지 읽히지 않는다.
 * 그래서 화면에 그리기 전에 이 상태의 측정값과 안내를 한 문구로 바꾼다.
 * 이 상태의 측정값은 모두 `분석 연동이 꺼져`로 시작하므로 그 머리로 가린다.
 * 참고: 서버 문장(`services/maiu/client.py`의 `FAKE_MODE_HINT`, `provisioning.py`, `ops/diagnostics.py`)이 이 문구와 같아지면 이 변환은 원문을 그대로 돌려준다.
 */

/** 분석 연동이 꺼진 서버의 측정값 머리. */
const OFF_PREFIX = "분석 연동이 꺼져";

/** 분석 연동이 꺼졌을 때의 안내. 서버 설정 이름과 켤 수 있는 사람을 함께 쓴다. */
export const ANALYSIS_OFF_HINT = "실제 분석을 쓰려면 최고관리자가 서버 설정 MAIU_ENABLED를 켜야 합니다.";

/** 점검 결과 한 줄의 측정값과 안내. */
interface CheckText {
  detail?: string | null;
  hint?: string | null;
}

/** 분석 연동이 꺼진 상태의 점검 결과이면 true. */
export function isAnalysisOff(c: CheckText): boolean {
  return !!c.detail && c.detail.startsWith(OFF_PREFIX);
}

/**
 * 점검 결과의 측정값과 안내를 화면 문구로 바꾼다. 분석 연동이 꺼진 상태가 아니면 그대로 돌려준다.
 * 측정값 끝의 환경 변수 괄호는 안내가 같은 이름을 쓰므로 뺀다.
 */
export function analysisOffText<T extends CheckText>(c: T): T {
  if (!isAnalysisOff(c)) return c;
  return { ...c, detail: c.detail!.replace(/\s*\(MAIU_ENABLED=\w+\)$/, ""), hint: ANALYSIS_OFF_HINT };
}
