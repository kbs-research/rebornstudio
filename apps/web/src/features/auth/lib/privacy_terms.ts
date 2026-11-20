/**
 * 개인정보 수집·이용 고지의 수집 항목 표와 동의 문구. 안내 화면(`/privacy`)·동의 화면·최초 설정 화면이 같은 값을 쓴다.
 *
 * 동의 화면은 동의를 받기 전에 이 표의 항목과 기간을 같이 보여 준다. 두 화면이 각자 적으면 동의한 조건과 고지가 달라진다.
 * 수집 항목은 필수와 자동 수집 둘이다. 선택 항목(전화번호·생년월일·주소)은 받지 않는다.
 */
import type { ChipTone } from "@/shared/ui/Chip";

/** 수집 항목 표의 한 행. 구분·수집 항목·이용 목적·보유 기간. */
export interface CollectedRow {
  kind: string;
  items: string;
  purpose: string;
  period: string;
}

/** 수집 항목 표. */
export const COLLECTED: CollectedRow[] = [
  {
    kind: "필수",
    items: "이메일, 표시 이름, 비밀번호(암호화 저장)",
    purpose: "회원 식별·로그인, 작업 이력 관리",
    period: "이용 종료 후 5년",
  },
  {
    kind: "자동 수집",
    /*
      서버가 실제로 모으는 것을 모두 적는다.
      로그인·세션 갱신·로그아웃 때 접속 IP와 User-Agent를 감사 로그에 남긴다(`services/audit.py`의 `origin`).
      User-Agent에는 브라우저와 운영체제가 들어 있어 "브라우저·운영체제 정보"로 적는다.
      주의: 서버가 모으는 항목을 늘리면 이 목록도 같이 고친다. 고치지 않으면 고지 없이 수집하는 상태가 된다.
    */
    items: "로그인 일시, 접속 IP 주소, 브라우저·운영체제 정보, 서비스 이용 기록(작업·검수 활동)",
    purpose: "보안 감사, 품질·출퇴근 등 운영 통계",
    /*
      이 기간은 이용자 고지이면서 감사 로그 보존 기본값의 근거다.
      서버도 같은 기간만 남긴다(`services/ops/audit_retention.py`의 `DEFAULT_RETENTION_DAYS = 1095`).
      주의: 두 값은 함께 고치고 고지 개정을 먼저 한다. 두 값이 다르면 동의한 조건과 실제 보존 기간이 달라진다.
      법적 고지라서 서버 값을 받아 그리지 않는다. 서버 설정을 따라 바뀌면 사용자에게 알리지 않고 동의 조건이 바뀐다.
    */
    period: "수집일로부터 3년",
  },
];

/**
 * 보유 기간의 기준인 이용 종료의 정의. 계정 상태 값(`approval_status`)의 정지(`suspended`)에 맞춘다.
 * 계정을 지우는 기능은 없어서 계정 삭제 요청도 계정 정지로 처리한다.
 * 5년이 지난 정보는 자동으로 지우지 않는다.
 */
export const USE_END_NOTE = "이용 종료는 계정 정지(계정 삭제 요청 처리 포함)를 말합니다.";

/** 필수 동의 체크 문구. 최초 설정·동의 화면이 같은 문구를 쓴다. */
export const REQUIRED_CONSENT_LABEL = "[필수] 개인정보 수집·이용에 동의합니다";

/** 구분 배지의 색. 자동 수집만 입력하지 않아도 모이는 항목이라 색으로 구분하고, 나머지는 중립이다. */
export const KIND_TONE: Record<string, ChipTone> = { 필수: "neutral", "자동 수집": "note" };
