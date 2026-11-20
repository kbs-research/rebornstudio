/**
 * 감사 기록의 이벤트 종류(`event_type`)마다 화면 이름과 심각도를 정한다.
 *
 * 사용자 활동 이력이 이 파일을 쓴다.
 * 이벤트 종류마다 한 줄씩 적는다.
 * 이름 정규식으로 심각도를 추측하면 `caption_qc.hold_released`(품질검수 보류 해제)가 위험으로 칠해진다.
 * 도메인 사전과 동작 사전을 조합해 이름을 만들면 같은 동작 낱말이 도메인마다 다른 뜻일 때 이름이 틀린다.
 *
 * 서버가 내는 종류가 모두 이 표에 있는지는 `scripts/ci/check-event-labels.py`와 `.mjs`가 확인한다.
 * 표에 없는 종류는 도메인 이름과 원문 동작으로 표시한다. 감추면 빠진 종류를 찾을 수 없다.
 */

/** 이벤트 심각도. */
export type EventTone = "bad" | "warn" | "ok" | "default";

/** 이벤트 한 종류의 화면 정의 */
interface EventDef {
  /** 활동 열에 쓰는 이름. 명사구로 쓴다. */
  label: string;
  /** 심각도. 적지 않으면 `default`(일반)다. */
  tone?: EventTone;
}

const bad = "bad", warn = "warn", ok = "ok";

/**
 * 이벤트 종류별 화면 정의.
 *
 * 심각도 기준은 네 가지다.
 * 위험(`bad`)은 반려·폐기·실패처럼 작업이나 처리가 잘못된 기록이다.
 * 주의(`warn`)는 자동 반납·신고·로그인 실패처럼 사람이 확인해야 하는 기록이다.
 * 정상(`ok`)은 승인·해제·완료처럼 풀리거나 끝난 기록이다.
 * 일반(`default`)은 그 밖의 일상 기록이다.
 * `품질검수 보류 해제`는 해제라서 정상이다. 그래서 이름의 낱말로 심각도를 추측하지 않는다.
 *
 * 서버 코드에는 없지만 DB에 남아 있을 수 있는 이전 종류도 적는다(`system.platform_defaults_retired`,
 * 자동 반납 `work.reclaimed`·`review.reclaimed`, 한도 변경 `member.concurrent_limit_changed`).
 * 그래야 이미 남은 행이 원문 대신 화면 이름으로 보인다.
 */
export const EVENTS: Record<string, EventDef> = {
  // 작업(라벨링)
  // 다음 작업으로 새로 받은 기록이다. 보유한 작업을 다시 연 것은 남기지 않는다.
  "work.claimed": { label: "작업 배정" },
  // 비식별화 신고로 보유가 풀린 기록이다(`cause`). 이전 버전이 남긴 반납 기록도 같은 종류다.
  "work.released": { label: "작업 보유 해제" },
  "work.reclaimed": { label: "작업 자동 반납", tone: warn },
  "work.submitted": { label: "작업 제출" },
  // 비식별화 신고 제외·영상 삭제로 닫힌 작업.
  "work.closed": { label: "작업 종료" },

  // 검수
  "review.claimed": { label: "검수 배정" },
  "review.released": { label: "검수 보유 해제" },
  "review.reclaimed": { label: "검수 자동 반납", tone: warn },
  "review.approve": { label: "검수 승인", tone: ok },
  "review.modify_and_approve": { label: "검수 수정 후 승인", tone: ok },
  "review.reject": { label: "검수 반려", tone: bad },


  // 캡션 품질검수
  // 활동 열은 좁아서 기능 이름을 `품질검수`로 줄인다. `품질검수 검사 통과`처럼 낱말을 겹치지 않는다.
  "caption_qc.pass": { label: "품질검수 통과", tone: ok },
  "caption_qc.needs_review": { label: "품질검수 확인 필요", tone: warn },
  "caption_qc.reject_suggested": { label: "품질검수 반려 추천", tone: bad },
  // 관리자가 사유를 적어 보류를 푼 기록과, 사전을 고친 뒤 검사만 다시 실행한 기록. 버튼 이름을 쓴다.
  "caption_qc.hold_released": { label: "품질검수 보류 해제", tone: ok },
  "caption_qc.rechecked": { label: "품질검수 다시 검사" },
  // 관리자가 보류된 승인본의 캡션을 고쳐 다시 승인한 기록. 승인 검사가 다시 돌아 게이트를 정한다.
  "caption_qc.hold_revised": { label: "품질검수 캡션 고쳐 다시 승인" },
  // 워커가 실행 기록을 남기지 못한 검사. 같은 건을 몇 번 다시 시도했는지 이 이벤트로 센다.
  "caption_qc.failed": { label: "품질검수 실행 실패", tone: warn },

  // 클립
  "clip.created_manually": { label: "클립 직접 생성" },
  // 비식별화가 부족하다고 알린 기록은 `비식별화 미흡 신고`라고 부른다.
  "clip.deident_miss_reported": { label: "클립 비식별화 미흡 신고", tone: bad },
  // 라벨링 단계 `비식별화 신고` 카드의 두 버튼. 재개는 작업을 대기열로 되돌리고 제외는 클립을 납품에서 뺀다.
  "clip.deident_report_resumed": { label: "비식별화 신고 재개", tone: ok },
  "clip.deident_report_excluded": { label: "비식별화 신고 제외", tone: warn },

  // 영상
  "video.uploaded": { label: "영상 업로드" },
  "video.updated": { label: "영상 수정" },
  "video.deleted": { label: "영상 삭제", tone: warn },
  "video.bulk_registered": { label: "영상 일괄 등록" },
  "video.registered": { label: "영상 저장소 등록" },
  "video.duplicate_detected": { label: "영상 중복 감지", tone: warn },
  "video.probe_rejected": { label: "영상 규격 위반", tone: bad },
  // 단계 이름은 `규격 검증`이다(`features/ingest/lib/vocab.ts`).
  "video.probe_error": { label: "영상 규격 검증 실패", tone: bad },
  "video.probe_reset": { label: "영상 규격 재검사" },
  "video.analysis_done": { label: "영상 분석 완료", tone: ok },
  "video.transcode_retried": { label: "영상 변환 재시도" },
  "video.deident_retried": { label: "영상 비식별화 실행" },
  // 비식별화를 끈 프로젝트의 대기 영상을 배치가 넘긴 기록이다(`deident.skip_disabled`). 수행자가 없다.
  "video.deident_skipped": { label: "영상 비식별화 건너뜀" },
  // 비식별화를 끈 프로젝트에서 실패한 영상을 관리자가 넘긴 기록이다(`deident.skip_failed`).
  // 가리지 않은 원본으로 다음 단계가 진행되므로 주의 색으로 둔다.
  "video.deident_skip_manual": { label: "영상 비식별화 수동 건너뜀", tone: warn },
  "video.scene_split_retried": { label: "영상 장면 분할 재시도" },
  "video.thumbnails_retried": { label: "영상 썸네일 재생성" },

  // 분석(분석 서비스 연동)
  // 서비스 이름을 단계 이름으로 쓰지 않는다. 분석 스택은 프로젝트마다 다르다.
  "maiu.provisioned": { label: "분석 연동 준비 완료", tone: ok },
  "maiu.dispatched": { label: "분석 요청 시작" },
  "maiu.requested": { label: "분석 요청" },
  "maiu.dispatch_source_missing": { label: "분석 원본 없음", tone: bad },
  "maiu.retried": { label: "분석 재시도" },
  // 장면 요약만 다시 요청한 기록이다. 분석 재시도와 비용과 결과가 달라 이름을 나눈다.
  "maiu.summary_retried": { label: "장면 요약 재요청" },
  // 사람이 분석 재요청이나 요약 재요청을 누를 때마다 남는 묶음 기록이다(`maiu.service.retry_failed`).
  // 종류 이름의 failed는 대상 영상의 상태라 실패 색을 쓰지 않는다. 성공한 재요청도 같은 종류로 남는다.
  "maiu.retry_failed": { label: "분석 재요청" },
  "maiu.stage.register_triggered": { label: "분석 등록 요청" },
  "maiu.stage.registered": { label: "분석 등록 완료", tone: ok },
  // 등록이 끝난 뒤 시스템이 분석을 넘긴 기록이다. 사람의 요청(`maiu.requested`)과 구분한다.
  // 같은 이름을 쓰면 활동 기록에 "분석 요청"이 두 번 찍혀 두 번 요청한 것처럼 읽힌다.
  "maiu.stage.analyze_triggered": { label: "분석 시작" },
  "maiu.stage.analyzed": { label: "분석 완료", tone: ok },
  "maiu.stage.summarize_requested": { label: "장면 요약 요청" },
  "maiu.stage.summarized": { label: "장면 요약 완료", tone: ok },
  "maiu.stage.summarize_failed": { label: "장면 요약 실패", tone: bad },
  "maiu.stage.summarize_request_failed": { label: "장면 요약 요청 실패", tone: bad },
  "maiu.stage.transient_error": { label: "분석 일시 오류", tone: warn },
  "maiu.stage.deferred": { label: "분석 재시도 보류", tone: warn },
  "maiu.stage.retry_deferred": { label: "분석 재시도 대기" },
  "maiu.stage.swept_stale": { label: "분석 장기 대기 작업 회수", tone: warn },
  // `f"maiu.stage.{stage}_failed"`가 내는 종류. `_STAGE_BY_CALLER`의 여섯 단계와 폴백이다.
  "maiu.stage.register_failed": { label: "분석 등록 실패", tone: bad },
  "maiu.stage.poll_registration_failed": { label: "분석 등록 상태 조회 실패", tone: bad },
  "maiu.stage.analyze_failed": { label: "분석 실패", tone: bad },
  "maiu.stage.poll_analyze_failed": { label: "분석 상태 조회 실패", tone: bad },
  "maiu.stage.summary_failed": { label: "장면 요약 실패", tone: bad },
  "maiu.stage.poll_summary_failed": { label: "장면 요약 상태 조회 실패", tone: bad },
  "maiu.stage.unknown_failed": { label: "분석 단계 실패", tone: bad },

  // 분석본
  "media.analysis_purged": { label: "분석본 삭제" },
  "media.analysis_purged_bulk": { label: "분석본 일괄 삭제" },

  // 등록 묶음·저장소
  "ingest_batch.updated": { label: "등록 묶음 수정" },
  // 묶음 화면의 메타 일괄 입력으로 영상 메타를 바꾼 기록이다. 영상마다 `video.updated`도 함께 남는다.
  "ingest_batch.meta_imported": { label: "등록 묶음 메타 일괄 입력" },
  // 등록 화면의 토스트와 같은 `폴더 등록`으로 부른다. 구어 `폴더째`는 쓰지 않는다.
  "storage.prefix_import_queued": { label: "폴더 등록 시작" },
  "storage.prefix_import_done": { label: "폴더 등록 완료", tone: ok },
  "storage.prefix_import_empty": { label: "폴더 등록 대상 없음", tone: warn },
  "storage.prefix_import_failed": { label: "폴더 등록 실패", tone: bad },

  // 내보내기
  // `추가`는 패키지를 만드는 기록이고 `다운로드`는 받아 가는 기록이다. 두 이름을 합치면 누가 데이터를 가져갔는지 구분되지 않는다.
  // 이름은 내보내기 화면의 버튼과 같은 `내보내기 추가`다.
  "export.created": { label: "내보내기 추가" },
  "export.downloaded": { label: "내보내기 다운로드" },


  // 멤버
  "member.invited": { label: "멤버 초대" },
  "member.removed": { label: "멤버 제거" },
  "member.left": { label: "멤버 탈퇴" },
  "member.role_changed": { label: "멤버 역할 변경" },
  // 더 생기지 않는 종류다. 감사 로그에 남은 행을 화면 이름으로 보이려고 둔다.
  "member.concurrent_limit_changed": { label: "멤버 동시 보유 한도 변경" },

  // 프로젝트
  "project.created": { label: "프로젝트 생성" },
  "project.updated": { label: "프로젝트 수정" },
  // 프로젝트 분석 대상의 주기 점검이 정상과 실패 사이를 오갈 때 남는다(`project_stack.record`).
  "project.analysis_check_changed": { label: "분석 대상 점검 상태 변경" },
  "project.storage_check_changed": { label: "저장소 대상 점검 상태 변경" },
  // 삭제한 프로젝트는 서버가 `project_id` 없이 남긴다. 그래서 플랫폼 감사 로그에만 보인다.
  "project.deleted": { label: "프로젝트 삭제", tone: warn },

  // 사용자·인증
  "user.created": { label: "계정 생성" },
  "user.role_changed": { label: "사용자 역할 변경" },
  "user.status_changed": { label: "사용자 상태 변경" },
  "user.password_changed": { label: "비밀번호 변경" },
  // 관리자가 1회용 링크를 만든 기록. 본인이 비밀번호를 바꾼 `비밀번호 재설정`과 다른 일이다.
  "user.password_reset_issued": { label: "비밀번호 재설정 링크 발급" },
  "user.password_reset": { label: "비밀번호 재설정" },
  // 동의·계정 화면이 부르는 이름은 `필수 동의`·`선택 동의`다.
  "user.privacy_consented": { label: "수집·이용 동의" },
  // 철회하면 서버가 선택 정보를 지운다. 지운 필드 이름이 `purged`에 남는다.
  "user.optional_privacy_consent_withdrawn": { label: "선택 동의 철회" },
  // 인증 종류는 `인증` 접두어 없이 부른다. `인증 로그인 실패`는 같은 낱말이 겹친다.
  "auth.login": { label: "로그인" },
  "auth.login_failed": { label: "로그인 실패", tone: warn },
  // 이메일 단위 실패 임계에 닿아 서버가 그 계정의 로그인을 일정 시간 막은 기록이다. 맞는 비밀번호도 막힌다.
  "auth.login_locked": { label: "로그인 잠금", tone: warn },
  "auth.logout": { label: "로그아웃" },
  "auth.refreshed": { label: "세션 갱신" },
  // 폐기된 refresh 토큰이 유예 시간을 넘겨 다시 와서 서버가 그 로그인의 세션을 모두 끊은 기록이다.
  "auth.refresh_reused": { label: "세션 재사용 차단", tone: warn },

  // 연결·시스템
  // 연결 화면과 같은 이름 `연결`로 부른다.
  "connection.created": { label: "연결 추가" },
  "connection.updated": { label: "연결 수정" },
  "connection.deleted": { label: "연결 삭제" },
  "connection.health_changed": { label: "연결 상태 변경" },
  "system.audit_purged": { label: "감사 로그 정리" },
  // 운영 명령이 연결 시크릿을 새 암호화 키로 다시 암호화한 기록이다. 건수만 남긴다.
  "system.credentials_rekeyed": { label: "자격증명 키 교체" },
  "system.integration_changed": { label: "연동 상태 변경" },
  // 시스템 기본 저장소·분석 연결을 없애면서 연결을 고르지 않은 설정에 그 연결을 지정한 마이그레이션 기록이다.
  "system.platform_defaults_retired": { label: "기본 연결 정리" },
};

/**
 * 도메인(이벤트 종류의 앞부분)별 이름. 표에 없는 종류의 이름 앞부분이 된다.
 *
 * `maiu`는 `분석`이다. 서비스 이름을 도메인 이름으로 쓰지 않는다.
 */
const DOMAIN_KO: Record<string, string> = {
  work: "작업", review: "검수", caption_qc: "품질검수",
  clip: "클립", video: "영상", maiu: "분석", media: "분석본",
  ingest_batch: "등록 묶음", storage: "저장소", export: "내보내기",
  member: "멤버", project: "프로젝트", user: "사용자",
  auth: "인증", connection: "연결", system: "시스템",
};

/** 이벤트 종류의 도메인(첫 점 앞부분)을 돌려준다. */
function domainOf(eventType: string): string {
  const dot = eventType.indexOf(".");
  return dot < 0 ? eventType : eventType.slice(0, dot);
}

/** 이벤트 종류의 이름을 돌려준다. 표에 없으면 도메인 이름과 원문 동작으로 표시한다. */
export function humanizeEvent(eventType: string): string {
  const def = EVENTS[eventType];
  if (def) return def.label;
  const domain = domainOf(eventType);
  const d = DOMAIN_KO[domain];
  return d && domain !== eventType ? `${d} ${eventType.slice(domain.length + 1)}` : eventType;
}

/** 이벤트 심각도를 돌려준다. 표에 적은 값이고, 적지 않은 종류는 일반이다. */
export function eventTone(eventType: string): EventTone {
  return EVENTS[eventType]?.tone ?? "default";
}

// 시각

/**
 * 감사 기록의 시각을 `2026-09-23 19:12:14` 형식으로 쓴다. `withMs`면 밀리초까지 붙인다.
 *
 * 감사 로그와 서버 로그는 줄을 맞춰 대조하는 장부라 표와 상세가 같은 형식을 쓴다.
 * 품질검수의 실행 이력·보류 목록도 검사 기록이라 같은 형식을 쓴다.
 * 시각은 보는 사람의 시간대로 쓴다.
 */
export function fmtEventTime(iso: string, withMs = false): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  const base =
    `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  return withMs ? `${base}.${p(d.getMilliseconds(), 3)}` : base;
}
