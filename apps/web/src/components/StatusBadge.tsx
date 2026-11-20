/**
 * status enum 문자열을 색과 글자로 바꿔 보여 주는 배지.
 *
 * WorkItem·Video·Clip 등 도메인 전반의 상태 표현을 통일한다.
 * 페이지마다 STATUS_COLOR를 하드코딩하는 대신 이 컴포넌트를 쓴다.
 *
 * 모양은 `Chip`이 정하고, 이 파일은 상태별 색조만 정한다.
 * 모양 값(패딩·글자·라운드)이 두 곳에 있으면 한쪽만 바뀐다.
 */
import { Chip, type ChipTone } from "@/shared/ui/Chip";
import { RIGHTS_LABEL, VERDICT_LABEL } from "@/lib/vocab";

type StatusConfig = { tone: ChipTone; label: string };

/*
  색조 하나는 뜻 하나다. 같은 이름(`대기`)이 영역마다 다른 색이면 색으로 뜻을 구분할 수 없다.
  neutral은 시작 전(대기), note는 진행 중이거나 다음 단계로 넘어가는 중(진행 중·제출됨·분석 중)이다.
  ok는 끝나서 쓸 수 있는 상태(승인·정상·완료), warn은 사람이 확인할 상태(비식별화 신고)다.
  bad는 실패하거나 거절된 상태(반려·실패·오류), quiet는 닫힌 상태(종료)다.
*/
const STATUS_MAP: Record<string, StatusConfig> = {
  // work_item
  available:   { tone: "neutral",   label: "대기" },
  claimed:     { tone: "note",      label: "진행 중" },
  submitted:   { tone: "note",      label: "제출됨" },
  approved:    { tone: "ok",        label: "승인" },
  rejected:    { tone: "bad",       label: "반려" },
  // 비식별화 신고 제외나 영상 삭제로 닫힌 작업. 사람의 판정(반려)이나 실패와 구분해 조용한 색조를 쓴다.
  // 큐와 집계 모집단에서 빠진다.
  cancelled:   { tone: "quiet",     label: "종료" },
  // 영상 업로드
  pending:     { tone: "neutral",   label: "대기" },
  uploaded:    { tone: "ok",        label: "완료" },
  failed:      { tone: "bad",       label: "실패" },
  // 클립 상태(`rights_status`). 이름은 `lib/vocab`의 `RIGHTS_LABEL`을 쓴다.
  cleared:     { tone: "ok",        label: RIGHTS_LABEL.cleared },
  auto_cleared:    { tone: "ok",        label: RIGHTS_LABEL.auto_cleared },
  cleared_with_edit: { tone: "ok",        label: RIGHTS_LABEL.cleared_with_edit },
  legal_review:    { tone: "warn",      label: RIGHTS_LABEL.legal_review },
  // 검수 판정
  approve:             { tone: "ok",        label: "승인" },
  reject:              { tone: "bad",       label: "반려" },
  // 판정 이름은 `lib/vocab`의 `VERDICT_LABEL`을 쓴다. 이 파일에 따로 적으면 화면마다 이름이 달라진다.
  modify_and_approve:  { tone: "note",      label: VERDICT_LABEL.modify_and_approve },
  // MAIU 분석 단계
  registering: { tone: "note",      label: "등록 중" },
  registered:  { tone: "note",      label: "등록됨" },
  analyzing:   { tone: "note",      label: "분석 중" },
  analyzed:    { tone: "note",      label: "분석 완료" },
  summarizing: { tone: "note",      label: "요약 중" },
  done:        { tone: "ok",        label: "완료" },
  error:       { tone: "bad",       label: "오류" },
  // 멤버
  active:      { tone: "ok",        label: "활성" },
  suspended:   { tone: "bad",       label: "정지" },
};

const FALLBACK: StatusConfig = { tone: "quiet",     label: "" };

/**
 * 영역별 이름표. 같은 서버 값이 영역마다 다른 뜻인 경우에 쓴다.
 * 클립 상태의 `rejected`는 `제외`다. 작업 상태 표로 읽으면 `반려`가 된다.
 */
const DOMAIN_LABEL: Record<StatusDomain, Record<string, string>> = {
  rights: RIGHTS_LABEL,
};

/** 배지 값의 영역. 없으면 `STATUS_MAP` 하나로 읽는다. */
export type StatusDomain = "rights";

interface Props {
  status: string;
  /** 바꿔 쓸 라벨. 없으면 영역 이름표를, 영역 이름표도 없으면 `STATUS_MAP`의 label을 쓴다. */
  label?: string;
  /** 값의 영역. `rights`면 `lib/vocab`의 `RIGHTS_LABEL`로 읽는다. */
  domain?: StatusDomain;
  className?: string;
}

/**
 * 상태 배지를 그린다. 표에 없는 상태는 조용한 색조로 원래 값을 보여 준다.
 * 모양은 점 칩이다. 상태를 색만으로 구분하지 않고 점과 글자로 함께 표시한다.
 */
export function StatusBadge({ status, label, domain, className }: Props) {
  const cfg = STATUS_MAP[status] ?? FALLBACK;
  const named = domain ? DOMAIN_LABEL[domain][status] : undefined;
  return (
    <Chip tone={cfg.tone} variant="dot" className={className}>
      {label ?? named ?? (cfg.label || status)}
    </Chip>
  );
}
