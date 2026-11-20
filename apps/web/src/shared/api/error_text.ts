/**
 * 서버 에러 코드를 사용자가 읽는 문구로 바꾸는 사전.
 *
 * 서버는 실패를 `{"detail": "..."}`로 보내는데, `project_not_found` 같은 기계 코드와
 * `셋업 토큰이 올바르지 않습니다` 같은 한국어 문장이 같은 필드로 온다.
 * 값을 그대로 띄우면 라벨러가 `concurrency_limit_reached`를 토스트로 보게 된다.
 * 요청 검증 실패(422)는 `detail`이 배열이다. `api()`가 이 배열을 `validation_error`로 바꾸고
 * 필드별 사유를 `ApiError.fields`에 따로 싣는다.
 *
 * 돌려주는 값은 결과(`message`)와 다음 할 일(`hint`) 두 조각이다.
 * `toast(type, message, hint)`의 굵은 제목과 작은 본문에 그대로 들어가므로 좁은 폭에서 줄이 바뀌어도 의미 단위가 유지된다.
 *
 * 사전에 없는 코드는 영문 snake_case 원문 대신 일반 문구를 띄운다.
 * 원문은 `console.warn`으로 개발자에게만 남긴다. 이 로그는 사전이 낡았다는 유일한 신호라서 지우지 않는다.
 */

/** 화면에 표시할 실패 안내. 굵은 결과(message)와 작은 안내(hint)로 나뉜다. */
export interface ErrorText {
  message: string;
  hint?: string;
}

/** 코드를 그대로 비교하는 사전. 동적 코드(`already_pending` 등)는 아래 PREFIX가 받는다. */
const EXACT: Record<string, ErrorText> = {
  // ── 입력 검증 ──
  // 서버 코드 대신 `api()`가 붙이는 이름이다. Pydantic 422의 배열 detail을 이 코드로 바꾸고
  // 필드별 사유는 `ApiError.fields`에 따로 싣는다. 폼은 필드 옆에 `fields`를 그린다.
  validation_error: {
    message: "입력값이 올바르지 않습니다",
    hint: "값을 확인한 뒤 다시 시도해주세요.",
  },
  /*
    고칠 입력란이 없는 422. 버튼만 있는 요청이 서버 규격과 맞지 않을 때처럼 폼이 없는 화면에서도 422가 온다.
    누른 사람은 입력란이 없어 고칠 곳을 찾을 수 없다.
    그래서 `api()`가 필드 사유를 싣지 못한 422는 이 코드로 바꾸고, 사용자의 입력 잘못이 아닌 요청 규격 문제로 안내한다.
  */
  validation_error_no_field: {
    message: "요청이 서버 규격과 맞지 않습니다",
    hint: "새로고침한 뒤 다시 시도하고, 그래도 안 되면 관리자에게 알려주세요.",
  },
  // 응답을 받지 못한 실패(`api_error.networkError`). 연결 끊김과 서버 중단을 가를 수 없어 둘 다 안내한다.
  network_error: { message: "서버에 연결하지 못했습니다", hint: "네트워크를 확인한 뒤 다시 시도해주세요." },
  // ── 인증·세션 ──
  unauthenticated: { message: "로그인이 필요합니다" },
  invalid_token: { message: "로그인이 만료되었습니다", hint: "다시 로그인해주세요." },
  token_superseded: {
    message: "다른 곳에서 비밀번호가 바뀌었습니다",
    hint: "보안을 위해 이 세션을 끊었습니다. 다시 로그인해주세요.",
  },
  invalid_refresh_token: { message: "로그인이 만료되었습니다", hint: "다시 로그인해주세요." },
  // refresh·logout이 다른 출처에서 왔을 때만 난다. 이 서비스 주소로 연 화면에서는 나지 않는다.
  origin_not_allowed: {
    message: "허용되지 않은 주소에서 보낸 요청입니다",
    hint: "이 서비스 주소로 접속해 다시 시도해주세요.",
  },
  invalid_credentials: {
    message: "이메일 또는 비밀번호가 맞지 않습니다",
    hint: "다시 확인해주세요.",
  },
  invalid_current_password: { message: "현재 비밀번호가 맞지 않습니다" },
  invalid_or_expired_token: {
    // 이 400은 재설정 확정에서만 나고, 새 링크는 최고관리자만 만들 수 있다.
    // 그래서 `요청해주세요`만 쓰지 않고 요청할 대상을 함께 쓴다.
    message: "링크가 만료되었거나 이미 사용했습니다",
    hint: "최고관리자에게 새 링크를 요청해주세요.",
  },
  // 재설정 링크는 1회용이라 같은 링크로 두 번 바꿀 수 없다. 바뀐 비밀번호로 로그인하면 된다.
  reset_link_used: {
    message: "이미 비밀번호를 바꾼 링크입니다",
    hint: "바꾼 비밀번호로 로그인하거나 최고관리자에게 새 링크를 요청해주세요.",
  },
  too_many_attempts: {
    message: "시도가 너무 잦습니다",
    hint: "잠시 후 다시 시도해주세요.",
  },
  // 맞는 비밀번호로 로그인한 정지 계정에만 온다. 비밀번호가 틀리면 invalid_credentials가 온다.
  account_suspended: {
    message: "정지된 계정입니다",
    hint: "최고관리자에게 문의해주세요.",
  },
  // 남은 세션의 계정이 정지됐거나 더 이상 쓸 수 없는 경우다. 서버가 상태를 보내지 않으므로 문구에 상태를 단정하지 않는다.
  account_not_active: {
    message: "사용할 수 없는 계정입니다",
    hint: "최고관리자에게 문의해주세요.",
  },
  insufficient_permission: { message: "권한이 없습니다" },
  // 동의 화면의 동의 누락(422)과 동의 전 업무 요청(403)이 같은 코드다.
  privacy_consent_required: {
    message: "개인정보 수집·이용 동의가 필요합니다",
    hint: "동의한 뒤에 이용할 수 있습니다.",
  },
  privacy_consent_withdrawal_unavailable: {
    message: "필수 동의만 따로 철회할 수 없습니다",
    hint: "계정 삭제를 최고관리자에게 요청해주세요.",
  },
  // ── 소속·권한 ──
  // 멤버는 관리자가 초대하고 참여 요청 화면은 없으므로, 문구가 관리자 초대를 안내한다.
  not_a_member: { message: "이 프로젝트의 멤버가 아닙니다", hint: "멤버는 프로젝트 관리자가 초대합니다." },
  not_authorized: { message: "권한이 없습니다" },
  not_super_admin: { message: "최고관리자만 할 수 있습니다" }, // 역할 호칭은 role_label을 따른다.
  not_project_admin: { message: "프로젝트 관리자만 할 수 있습니다" },
  not_annotator: { message: "이 프로젝트에서 라벨링을 맡고 있지 않습니다" },
  // 완료 목록·완료 상세·영상 상세는 라벨러에게 닫힌 화면이다.
  // 라우터 가드가 먼저 막으므로 이 문구는 API를 직접 부른 경우에만 나온다.
  not_work_result_viewer: {
    message: "라벨러에게는 열리지 않는 화면입니다",
    hint: "맡은 작업은 내 작업에서 엽니다.",
  },
  not_reviewer: { message: "이 프로젝트에서 검수를 맡고 있지 않습니다" },
  // ── 대상 없음 ──
  project_not_found: { message: "프로젝트를 찾을 수 없습니다" },
  video_not_found: { message: "영상을 찾을 수 없습니다" },
  clip_not_found: { message: "클립을 찾을 수 없습니다" },
  work_item_not_found: { message: "작업을 찾을 수 없습니다" },
  user_not_found: { message: "계정을 찾을 수 없습니다" },
  batch_not_found: { message: "등록 묶음을 찾을 수 없습니다" },
  // 프로젝트 설정이 연결을 이름으로 가리킨다. 편집 창은 이름을 막으므로 API를 직접 부를 때 난다.
  connection_name_immutable: { message: "연결 이름은 바꿀 수 없습니다", hint: "새 이름이 필요하면 연결을 새로 만듭니다." },

  export_not_found: { message: "내보내기 작업을 찾을 수 없습니다" },
  /*
    내보내기 실행 사유. HTTP 오류 대신 `ExportDTO.error` 필드에 담긴 코드다.
    boto3 영문 메시지와 `[일시]` 표식이 이력 행에 그대로 찍히지 않도록 원문 대신 코드로 받는다.
    안내는 실패로 확정된 행을 기준으로 쓴다. 재시도 대기 중인 행은 패널이 시도 횟수로 바꿔 표시한다.
  */
  export_upload_retrying: { message: "업로드 일시 실패", hint: "자동으로 다시 시도합니다." },
  export_upload_failed: {
    message: "업로드 실패",
    hint: "스토리지 연결을 확인한 뒤 다시 생성해주세요.",
  },
  export_build_failed: {
    message: "패키지 생성 실패",
    hint: "다시 생성해도 같으면 최고관리자에게 문의해주세요.",
  },
  export_integrity_failed: {
    message: "패키지 무결성 검사 실패",
    hint: "색인과 파일이 어긋나 올리지 않았습니다. 최고관리자에게 문의해주세요.",
  },
  export_worker_lost: {
    message: "처리 중단",
    hint: "처리 서버가 여러 번 멈췄습니다. 최고관리자에게 문의해주세요.",
  },
  export_failed: { message: "생성 실패", hint: "다시 생성해주세요." },
  not_found: { message: "대상을 찾을 수 없습니다" },
  // ── 작업 흐름 ──
  no_work_available: { message: "지금 받을 수 있는 작업이 없습니다", hint: "잠시 후 다시 눌러주세요." },
  no_review_available: { message: "지금 받을 수 있는 검수가 없습니다", hint: "잠시 후 다시 눌러주세요." },
  /*
    서버는 `claimed_by`가 호출한 사용자가 아니면(super_admin 제외) 이 코드를 내므로 아무도 보유하지 않은 작업도 해당한다.
    그래서 `not_your_review_claim`과 같은 이유로 문구를 `보유 중이 아니다`로 쓴다.
    화면은 보유하지 않은 항목을 읽기 전용으로 그리므로(`useLabelWorkbench`의 `canEdit`) 이 오류는
    보는 사이에 보유가 풀린 경우(다른 탭의 신고, 프로젝트 나가기·멤버 제거)에 난다.
  */
  not_your_claim: {
    // 화면에서는 `쥐다`·`점유` 대신 `보유`라고 쓴다.
    message: "보유 중인 작업이 아닙니다",
    hint: "보유가 풀렸을 수 있습니다. 새로고침해주세요.",
  },
  /*
    문구는 `내가 맡은 것이 아니다` 대신 `보유 중이 아니다`로 쓴다.
    서버는 `review_claimed_by`가 호출한 사용자가 아니면 이 코드를 내므로 아무도 보유하지 않은 건도 해당한다.
    `내가 맡은 것이 아니다`라고 쓰면 사용자는 "다른 사람의 것"으로 읽는다.
    화면은 보유하지 않은 건을 읽기 전용으로 그리므로(`review.tsx`) 이 토스트는 보는 사이에 보유가 풀린 경우(다른 탭의 신고)에만 뜬다.
  */
  not_your_review_claim: {
    message: "보유 중인 검수가 아닙니다",
    hint: "보유가 풀렸을 수 있습니다. 새로고침해주세요.",
  },
  // 비식별화 신고 처리. 다른 관리자가 먼저 처리했거나 제외 사유를 비운 경우다.
  not_reported: { message: "이미 처리된 신고입니다", hint: "목록을 새로고침해주세요." },
  exclude_note_required: { message: "제외 사유가 필요합니다", hint: "사유를 적은 뒤 다시 제외해주세요." },
  // 캡션이 프로젝트 기준(`caption_min_tokens`)에 못 미친 제출. 서버도 화면과 같은 기준으로 막는다.
  // 워크벤치가 같은 기준으로 버튼을 잠그므로, 이 오류는 화면을 연 뒤 서버의 기준이 바뀐 경우에만 난다.
  // 워크벤치 제출 조건이 `캡션 50토큰 이상 · 현재 3`을 이미 보여 주므로 문구에 수치를 넣지 않는다.
  // 수정 후 승인과 보류 캡션 수정도 같은 코드로 거절된다. 검수 화면과 품질검수 화면은 자기 입력을 가리키는 안내로 바꿔 보여 준다.
  caption_too_short: {
    message: "캡션이 기준보다 짧습니다",
    hint: "기준이 바뀌었을 수 있습니다. 옆의 제출 조건에 맞춰 캡션을 채운 뒤 다시 제출해주세요.",
  },
  cannot_review_own_work: { message: "직접 라벨링한 항목은 검수할 수 없습니다" },
  modified_label_data_required: { message: "수정할 라벨 내용이 필요합니다" },
  // 수정 후 승인의 기준 라벨(검수 화면이 보여 준 제출본). 이전 버전의 화면이 열려 있으면 이 값을 싣지 않는다.
  base_label_id_required: {
    message: "수정본의 기준 캡션을 알 수 없습니다",
    hint: "화면을 새로고침한 뒤 다시 수정해주세요.",
  },
  // 수정본이 다른 제출본을 기준으로 만들어진 경우. 검수 화면은 수정 내용을 비우고 캡션을 다시 받는다.
  review_base_mismatch: {
    message: "수정본이 이 작업의 캡션과 맞지 않습니다",
    hint: "수정 내용을 비웠습니다. 캡션을 다시 확인한 뒤 판정해주세요.",
  },
  project_not_active: { message: "진행 중인 프로젝트가 아닙니다" },
  // 일시정지는 새 작업 배정만 막는다. 보유한 작업은 다음 작업으로 다시 열고 제출할 수 있다.
  // 막힌 곳에서 이 사실을 함께 알려야 작업자가 자기 작업을 잃었다고 오해하지 않는다.
  project_paused: {
    message: "일시정지된 프로젝트입니다",
    hint: "새 작업 배정만 멈춥니다. 보유 중인 작업은 이어서 제출할 수 있습니다.",
  },
  no_clip: { message: "클립 단위 작업이 아닙니다" },
  // 서버는 없는 작업과 승인 전 작업에 같은 404를 돌려준다. 그래서 문구가 두 경우를 모두 안내한다.
  not_completed: {
    message: "완료 작업을 찾을 수 없습니다",
    hint: "없는 작업이거나 검수 승인이 끝나지 않은 작업입니다.",
  },
  // 승인됐지만 캡션 품질검수가 보류해 완료 목록에서 빠진 작업. 보류가 풀리면 다시 들어온다.
  qc_held: {
    message: "품질검수에서 보류된 작업입니다",
    hint: "보류가 풀리면 완료 목록에 다시 나타납니다.",
  },
  // ── 영상·클립 ──
  already_uploaded: { message: "이미 업로드가 끝난 영상입니다" },
  video_not_uploaded: { message: "아직 업로드가 끝나지 않았습니다" },
  object_not_uploaded: { message: "업로드된 파일을 찾을 수 없습니다", hint: "업로드를 다시 시도해주세요." },
  size_mismatch: { message: "업로드된 파일 크기가 맞지 않습니다", hint: "업로드를 다시 시도해주세요." },
  invalid_source_uri: { message: "재생할 수 있는 영상이 없습니다" },
  // 영상 행을 만들기 전에 막는다. 행을 먼저 만들면 끝낼 수 없는 업로드가 남는다.
  video_file_empty: { message: "빈 파일은 올릴 수 없습니다" },
  video_file_too_large: { message: "업로드 용량 상한을 넘는 파일입니다" },
  video_too_long: { message: "영상 길이 상한을 넘는 파일입니다" },
  not_resettable: { message: "지금은 다시 시도할 수 없는 상태입니다" },
  // 등록 묶음의 메타 일괄 입력(`media.meta_sheet`·`media.meta_import`). 파일을 고쳐 다시 올리게 안내한다.
  meta_import_file_too_large: { message: "파일이 너무 큽니다", hint: "행을 나눠 파일 여러 개로 올립니다." },
  meta_import_unsupported_file: {
    message: "읽을 수 없는 파일 형식입니다",
    hint: "양식을 .xlsx로 저장하거나 CSV(UTF-8)로 저장해 올립니다.",
  },
  meta_import_unreadable: { message: "파일을 읽지 못했습니다", hint: "파일이 손상되지 않았는지 확인한 뒤 다시 올립니다." },
  meta_import_no_header: {
    message: "머리글 행을 찾지 못했습니다",
    hint: "첫 행에 영상 ID·파일·제목 중 하나 이상의 머리글이 있어야 합니다.",
  },
  meta_import_duplicate_column: { message: "같은 머리글의 열이 둘 이상입니다", hint: "열 하나만 남기고 다시 올립니다." },
  meta_import_too_many_rows: { message: "행이 너무 많습니다", hint: "행을 나눠 파일 여러 개로 올립니다." },
  meta_import_video_not_in_batch: {
    message: "묶음에 없는 영상이 있어 반영하지 않았습니다",
    hint: "그사이 삭제된 영상이 있을 수 있습니다. 파일을 다시 올려 미리 보기를 새로 받습니다.",
  },
  // 보유가 풀려 대기열로 돌아간 작업에 늦게 제출·저장한 라벨러에게 보여 준다.
  // 다른 탭의 신고나 프로젝트 나가기로 보유가 풀린 경우다.
  work_item_is_available: {
    message: "보유가 풀려 대기열로 돌아간 작업입니다",
    hint: "이 화면의 편집은 저장되지 않았습니다.",
  },
  // 품질검수 보류 해제·다시 검사. 이미 풀렸거나 처음부터 보류가 아닌 건이다.
  // 목록이 오래된 상태일 때 난다.
  qc_not_held: { message: "보류 중인 작업이 아닙니다", hint: "목록을 새로고침해주세요." },
  // 보류분 캡션 고쳐 다시 승인(`caption_qc.hold.revise`). 승인본에 없는 문장이나 바뀐 것 없는 요청은 받지 않는다.
  sentence_not_found: { message: "승인본에 없는 문장입니다", hint: "목록을 새로고침해주세요." },
  caption_unchanged: {
    message: "고친 문장이 없습니다",
    hint: "캡션이 맞다면 다시 검사하거나 사유를 적어 보류를 해제합니다.",
  },
  clip_overlaps: { message: "다른 클립과 구간이 겹칩니다" },
  out_of_range: { message: "영상 길이를 벗어난 구간입니다" },
  deident_not_retryable: {
    message: "다시 가릴 수 있는 영상이 아닙니다",
    hint: "완료된 영상에 다시 실행하면 이미 가린 것을 또 가립니다. 비식별화를 쓰지 않는 프로젝트에서도 실행하지 않습니다.",
  },
  // 처리 중이거나 일시 오류 뒤 자동 재시도를 기다리는 영상이다. 실패로 확정된 뒤에만 다시 넣는다.
  // 준비 타임라인은 이 상태를 `대기`로 그려 버튼을 두지 않으므로 화면이 오래된 상태일 때만 난다.
  deident_in_progress: {
    message: "비식별화가 진행 중입니다",
    hint: "실패로 멈춘 뒤에 다시 시도할 수 있습니다.",
  },
  // 규격 검증 전이거나 변환 중이라 비식별화 단계가 아직 열리지 않은 영상이다.
  // 준비 타임라인은 이 상태를 `대기`로 그려 버튼을 두지 않으므로 화면이 오래된 상태일 때만 난다.
  deident_waiting: {
    message: "앞 단계가 끝나지 않은 영상입니다",
    hint: "규격 검증과 변환이 끝나면 비식별화가 자동으로 시작됩니다.",
  },
  // 비식별화 건너뛰기. 비식별화를 켠 프로젝트에서 건너뛰면 가리지 않은 원본이 분석으로 나간다.
  // 화면은 그런 프로젝트에 건너뛰기를 두지 않으므로 설정이 바뀐 뒤 오래된 화면에서만 난다.
  deident_enabled: {
    message: "비식별화를 사용하는 프로젝트입니다",
    hint: "가리지 않은 원본이 분석으로 나가지 않도록 건너뛰지 않습니다. 비식별화 재시도로 다시 실행합니다.",
  },
  // 비식별화 건너뛰기. 실패로 확정된 영상만 받는다. 목록이 오래된 상태일 때 난다.
  deident_not_failed: { message: "비식별화 실패 상태가 아닙니다", hint: "목록을 새로고침해주세요." },
  // 원본 보관이 해제된 건이다. 다시 넣어도 다운로드에서 실패하고, 화면에는 "비식별화 실패"로 보인다.
  deident_source_gone: {
    message: "다시 읽을 원본이 없습니다",
    hint: "원본 보관을 해제한 영상은 얼굴을 다시 가릴 수 없습니다.",
  },
  // 변환 재시도. 변환 중이거나 이미 끝난 영상이다. 목록이 오래된 상태일 때 난다.
  transcode_not_failed: { message: "지금 변환 실패 상태가 아닙니다", hint: "목록을 새로고침해주세요." },
  // 장면 분할 재시도. 클립이 이미 있으면 다시 자를 때 같은 구간에 클립이 두 개씩 생긴다.
  scene_split_has_clips: {
    message: "이미 장면을 나눈 영상입니다",
    hint: "다시 나누면 같은 구간에 클립이 중복으로 생깁니다.",
  },
  scene_split_not_failed: { message: "지금 장면 분할 실패 상태가 아닙니다", hint: "목록을 새로고침해주세요." },
  scene_split_not_ready: {
    message: "준비가 끝나지 않아 장면을 나눌 수 없습니다",
    hint: "규격 검증·변환·비식별화가 끝난 뒤에 다시 시도해주세요.",
  },
  thumbnail_not_failed: { message: "다시 만들 썸네일이 없습니다", hint: "재시도 중인 썸네일은 곧 다시 만들어집니다." },
  // 사람이 넣는 구간의 하한은 프로젝트의 클립 길이 기본값을 따른다(서버 `clip_min_seconds`).
  clip_too_short: {
    message: "구간이 최소 클립 길이보다 짧습니다",
    hint: "최소 클립 길이 이상으로 정합니다.",
  },
  batch_id_conflict: {
    message: "이미 접수된 가져오기입니다",
    hint: "목록에서 진행 상황을 확인해주세요.",
  },
  // ── 프로젝트·멤버 ──
  slug_already_exists: { message: "이미 쓰이는 주소입니다", hint: "다른 주소를 입력해주세요." },
  // 프로젝트와 연결이 속하는 기본 조직 행이 없는 경우. 마이그레이션을 끝까지 적용하지 않은 설치에서만 난다.
  default_org_missing: { message: "설치가 끝나지 않았습니다", hint: "서버 관리자에게 데이터베이스 업그레이드를 요청해주세요." },
  name_exists: { message: "이미 쓰이는 이름입니다", hint: "다른 이름을 입력해주세요." },
  already_member: { message: "이미 이 프로젝트의 멤버입니다" },
  last_admin_cannot_leave: {
    message: "마지막 관리자는 나갈 수 없습니다",
    hint: "다른 멤버에게 관리자 역할을 넘긴 뒤에 나갑니다.",
  },
  cannot_change_own_role: { message: "자기 역할은 바꿀 수 없습니다" },
  cannot_suspend_self: { message: "자기 계정은 정지할 수 없습니다" },
  membership_not_found: { message: "멤버십을 찾을 수 없습니다" },
  active_membership_not_found: { message: "지금 참여 중인 멤버가 아닙니다" },
  // 재설정 링크 발급. 정지 계정은 링크를 써도 로그인할 수 없다.
  user_not_active: {
    message: "정지된 계정입니다",
    hint: "계정 상태는 최고관리자가 바꿉니다.",
  },
  // 초대. 없는 계정과 정지된 계정을 같은 응답으로 묶어 이메일 대입으로 계정 유무를 알아낼 수 없게 한다.
  invitee_unavailable: {
    message: "초대할 수 있는 계정이 아닙니다",
    hint: "등록되어 있고 정지되지 않은 계정만 초대할 수 있습니다.",
  },
  // ── 설정·연결 ──
  // 프로젝트별 분석 대상(연결·워크스페이스·분석 프로젝트). 고르는 규칙은 서버 `project_stack`에 있다.
  analysis_stack_unset: {
    message: "분석 대상을 저장하지 않아 영상을 등록할 수 없습니다",
    hint: "프로젝트 설정의 분석 연동에서 분석 대상을 저장하면 풀립니다.",
  },
  analysis_stack_locked: {
    message: "영상이 등록된 프로젝트라 분석 대상을 바꿀 수 없습니다",
    hint: "분석 결과가 두 분석 프로젝트에 나뉘지 않게 첫 영상 등록부터 고정합니다.",
  },
  analysis_stack_unsupported: { message: "이 프로젝트의 분석 방식은 분석 대상을 쓰지 않습니다" },
  analysis_stack_incomplete: { message: "분석 연결·워크스페이스·분석 프로젝트를 함께 골라주세요" },
  maiu_bucket_mismatch: {
    message: "워크스페이스에 연결된 저장소가 이 프로젝트의 저장소와 다릅니다",
    hint: "저장소 대상과 같은 저장소를 연결한 워크스페이스를 고릅니다.",
  },
  maiu_project_not_found: { message: "워크스페이스에 그 분석 프로젝트가 없습니다", hint: "목록에 있는 분석 프로젝트를 고릅니다." },
  maiu_target_not_found: { message: "워크스페이스나 분석 프로젝트를 찾지 못했습니다", hint: "목록을 다시 불러와 고릅니다." },
  maiu_access_denied: { message: "분석 서비스가 접근을 거부했습니다", hint: "분석 연결의 키와 권한을 확인합니다." },
  maiu_unreachable: { message: "분석 서비스를 지금 확인하지 못했습니다", hint: "잠시 후 다시 저장해주세요." },
  // 프로젝트별 저장소 대상(저장소 연결·버킷). 고르는 규칙은 서버 `project_storage`에 있다.
  storage_stack_unset: {
    message: "저장소 대상을 저장하지 않아 영상을 등록할 수 없습니다",
    hint: "프로젝트 설정의 분석 연동에서 저장소 대상을 저장하면 풀립니다.",
  },
  storage_stack_locked: {
    message: "영상이 등록된 프로젝트라 저장소 대상을 바꿀 수 없습니다",
    hint: "원본과 산출물이 두 저장소에 나뉘지 않게 첫 영상 등록부터 고정합니다.",
  },
  storage_stack_unsupported: { message: "이 프로젝트의 분석 방식은 NCP 저장소를 쓰지 않습니다" },
  storage_stack_incomplete: { message: "저장소 연결과 저장소를 함께 골라주세요" },
  storage_bucket_invalid: {
    message: "저장소 이름 형식이 맞지 않습니다",
    hint: "소문자·숫자·점·하이픈으로 된 3~63자 이름을 씁니다.",
  },
  storage_bucket_not_found: { message: "저장소를 찾지 못했습니다", hint: "저장소 이름과 저장소 연결의 주소를 확인합니다." },
  // 등록 시트는 프로젝트 저장소 하나만 탐색·등록한다. 다른 저장소의 원본은 프로젝트 연결 키로 읽지 못할 수 있다.
  storage_bucket_not_project: {
    message: "이 프로젝트의 저장소가 아닙니다",
    hint: "프로젝트 설정의 저장소 대상에 있는 저장소에서 등록합니다.",
  },
  // 저장소 연결이 허용한 저장소(연결 설정의 저장소와 허용 저장소 목록)만 고르고 쓴다(`project_storage.require_allowed`).
  storage_bucket_not_allowed: {
    message: "이 저장소 연결이 허용하지 않은 저장소입니다",
    hint: "최고관리자가 자격증명 연결의 허용 저장소에 추가하면 고를 수 있습니다.",
  },
  storage_access_denied: { message: "저장소가 접근을 거부했습니다", hint: "저장소 연결의 키에 이 저장소 권한이 있는지 확인합니다." },
  storage_unreachable: { message: "저장소를 지금 확인하지 못했습니다", hint: "잠시 후 다시 저장해주세요." },
  // 프로젝트가 이름으로 가리키는 연결(캡션 품질검수 모델)은 지울 수 없다.
  // 지우면 그 프로젝트의 검수가 나중에 알림 없이 멈춘다.
  connection_in_use: {
    message: "프로젝트가 쓰는 연결이라 삭제할 수 없습니다",
    hint: "프로젝트 설정에서 다른 연결로 바꾼 뒤에 삭제합니다.",
  },
  // 프로젝트가 쓰는 저장소를 연결의 허용 범위에서 빼면 그 프로젝트의 등록·탐색이 멈춘다(`project_storage.users_losing_bucket`).
  // 영상이 있는 프로젝트는 저장소 대상을 바꿀 수 없다(`storage_stack_locked`).
  allowed_bucket_in_use: {
    message: "이 저장소를 쓰는 프로젝트가 있습니다",
    hint: "프로젝트 설정의 분석 연동에서 저장소 대상을 먼저 바꿉니다. 영상이 있는 프로젝트는 바꿀 수 없어 이 저장소를 허용 저장소에 남깁니다.",
  },
  // 고른 저장소·분석 연결을 쓸 수 없을 때. 영상 등록·분석 요청이 시작하기 전에 막힌다.
  // 다시 시도해도 풀리지 않으므로 고칠 곳(분석 연동·자격증명 연결)을 안내한다.
  storage_not_configured: {
    message: "저장소 연결을 쓸 수 없어 처리할 수 없습니다",
    hint: "분석 연동에서 저장소 연결을 고르고, 고른 연결은 자격증명 연결에서 점검합니다.",
  },
  analysis_not_configured: {
    message: "분석 연결을 쓸 수 없어 분석을 요청할 수 없습니다",
    hint: "분석 연동에서 분석 연결을 고르고, 고른 연결의 점검 상태를 자격증명 연결에서 확인합니다.",
  },
  connection_not_verified: {
    message: "점검이 정상인 연결만 고를 수 있습니다",
    hint: "자격증명 연결에서 그 연결을 고쳐 저장하면 다시 점검합니다. 정상이 되면 다시 고릅니다.",
  },
  connection_provider_mismatch: {
    message: "고를 수 없는 서비스의 연결입니다",
    hint: "저장소에는 NCP Object Storage 연결을, 분석에는 Media Intelligence 연결을 고릅니다.",
  },
  // 프로젝트 설정은 등록된 연결 이름만 저장한다. 설정 화면은 목록에서만 고르게 한다.
  connection_not_found: {
    message: "등록되지 않은 연결입니다",
    hint: "목록에 있는 연결을 고르거나 최고관리자에게 등록을 요청해주세요.",
  },
  // 연결 주소 검사(`stacks.connections.address_problem`). 서버가 요청을 보내는 주소라 내부 호스트를 막는다.
  connection_url_bad_scheme: {
    message: "http나 https 주소만 쓸 수 있습니다",
    hint: "https://로 시작하는 전체 주소를 입력해주세요.",
  },
  connection_url_userinfo: {
    message: "주소에 사용자 이름이나 비밀번호를 넣을 수 없습니다",
    hint: "자격증명은 액세스 키와 비밀 키 입력란에 넣습니다.",
  },
  connection_url_blocked: {
    message: "서버 내부를 가리키는 주소는 쓸 수 없습니다",
    hint: "사설 IP나 점이 있는 호스트 이름을 씁니다. 꼭 필요한 내부 호스트는 서버 설정 CONNECTION_HOST_ALLOWLIST에 적습니다.",
  },
  // 요청이 나가는 곳(주소·엔드포인트·리전)을 바꾸면 저장된 키가 새 주소로 나가므로 키를 함께 받는다(`connections.secret_required`).
  secret_required_for_address_change: {
    message: "주소를 바꾸려면 키를 다시 입력해야 합니다",
    hint: "저장된 키가 새 주소로 나가지 않도록 주소와 키를 함께 저장합니다.",
  },
  // 키 없는 Bedrock 연결은 서버의 기본 AWS 자격증명을 쓰게 되므로 저장하지 않는다(`connections._check_bedrock_keys`).
  bedrock_keys_required: {
    message: "액세스 키와 비밀 키를 모두 입력해야 합니다",
    hint: "Bedrock 연결은 키 없이 저장하지 않습니다.",
  },
  connection_allowed_buckets_invalid: {
    message: "허용 저장소 이름 형식이 맞지 않습니다",
    hint: "소문자·숫자·점·하이픈으로 된 3~63자 이름을 쉼표로 나눠 씁니다.",
  },
  // 품질검수 모델 연결은 모델을 부를 수 있는 서비스만 받는다(`caption_qc.adapters.adapter_keys_for`).
  connection_not_for_caption_qc: {
    message: "품질검수에 쓸 수 없는 연결입니다",
    hint: "목록에 있는 모델 연결을 골라주세요.",
  },
  unknown_analysis_preset: { message: "알 수 없는 분석 방식입니다" },
  analysis_preset_unavailable: { message: "지금은 쓸 수 없는 분석 방식입니다" },
  // 서버는 입력한 폴더 경로의 `peek["files"]`가 비었을 때 이 오류를 낸다.
  // 경로를 잘못 입력해도 같은 오류가 나므로 경로 확인을 안내한다.
  empty_prefix: {
    message: "이 폴더에는 파일이 없습니다",
    hint: "폴더 경로를 확인해주세요.",
  },
  // 앱이 만든 경로(변환본·업로드 원본 등)는 원본으로 등록하지 않는다.
  system_prefix: {
    message: "앱이 만든 파일은 등록할 수 없습니다",
    hint: "원본 영상이 있는 경로를 입력해주세요.",
  },
  // 같은 이메일의 계정이 이미 있는데 계정을 만들려 한 경우(services/auth/admin.py).
  email_in_use: { message: "이미 있는 이메일입니다", hint: "다른 이메일을 입력해주세요." },
  invalid_role: { message: "알 수 없는 역할입니다" },
  invalid_status: { message: "알 수 없는 상태입니다" },
  invalid_month: { message: "잘못된 월 형식입니다" },
  // 클립 목록의 다음 페이지 요청에 커서 네 값 중 일부만 왔을 때 서버가 낸다(services/media/clip.py).
  // 화면(ClipsPanel)은 네 값을 함께 보내므로 화면과 서버의 버전이 어긋났을 때만 난다.
  incomplete_cursor: { message: "목록을 이어서 불러오지 못했습니다", hint: "목록을 새로고침해주세요." },
  // ── 서버·외부 ──
  db_unavailable: { message: "서버가 응답하지 않습니다", hint: "잠시 후 다시 시도해주세요." },
  redis_unavailable: { message: "서버가 응답하지 않습니다", hint: "잠시 후 다시 시도해주세요." },
  storage_unavailable: { message: "스토리지를 조회하지 못했습니다", hint: "잠시 후 다시 시도해주세요." },
};

/**
 * 접두사로 판정하는 동적 코드. 서버가 상태를 코드에 붙여 보내는 경우다
 * (`f"work_item_is_{item.status}"` 등).
 *
 * 사용자에게는 코드에 붙은 상태값이 필요 없으므로 할 일만 알린다.
 * 앞에서부터 맞춰 보므로 겹치는 접두사가 생기면 긴 것을 먼저 둔다.
 */
const PREFIX: [string, ErrorText][] = [
  ["work_item_is_", { message: "이미 처리된 작업입니다", hint: "목록을 새로고침해주세요." }],
  ["not_submitted_", { message: "제출 상태가 아닙니다", hint: "다른 검수자가 이미 처리했을 수 있습니다. 목록을 새로고침해주세요." }],
  ["export_status_", { message: "지금은 내보낼 수 없는 상태입니다" }],
  ["rate_limit_exceeded:", { message: "요청이 너무 잦습니다", hint: "잠시 후 다시 시도해주세요." }],
  // 입력한 영상 키가 저장소에 없다. 서버는 코드 뒤에 첫 번째 없는 키를 붙이고 아무것도 등록하지 않는다.
  // 등록 화면은 그 키를 따로 꺼내 보여 준다.
  ["storage_key_not_found:", { message: "저장소에 없는 경로가 있습니다", hint: "경로를 확인한 뒤 다시 등록해주세요." }],
  ["already_", { message: "이미 처리되었습니다", hint: "목록을 새로고침해주세요." }],
];

/** 사전에 없는 코드에 쓰는 일반 문구. 영문 원문 대신 띄운다. */
const FALLBACK: ErrorText = {
  message: "처리하지 못했습니다",
  hint: "잠시 후 다시 시도해주세요.",
};

/** 한글이 들어 있으면 true. 서버가 사람이 읽는 문장으로 보낸 detail이다. */
const HAS_HANGUL = /[가-힣]/;

/**
 * 서버 `detail`을 화면 문구로 바꾼다.
 *
 * @param detail `ApiError.detail`. 없으면 일반 문구를 쓴다. 문자열이 아닌 값도 예외 없이 받는다.
 * @param fallback 이 화면에 더 맞는 기본 문구(없으면 "처리하지 못했습니다").
 * @returns 굵은 `message`와 작은 `hint`. 영문 코드는 담지 않는다.
 */
export function errorText(detail: unknown, fallback?: ErrorText): ErrorText {
  /* `api()`를 거치지 않은 fetch나 이전 캐시가 Pydantic 배열·객체를 그대로 넘기면 아래 `startsWith`에서 TypeError가 난다.
     onError 안의 예외는 TanStack이 삼키므로 토스트가 통째로 사라진다.
     그래서 형식이 틀려도 안내가 나가도록 문자열이 아닌 값을 먼저 거른다. */
  if (typeof detail !== "string") {
    return Array.isArray(detail) ? EXACT.validation_error : (fallback ?? FALLBACK);
  }
  if (!detail) return fallback ?? FALLBACK;
  const hit = EXACT[detail];
  if (hit) return hit;
  for (const [p, text] of PREFIX) if (detail.startsWith(p)) return text;
  // 서버가 보낸 한국어 문장은 그대로 쓴다. 서버 문장에 화면보다 구체적인 맥락이 담겨 있다.
  if (HAS_HANGUL.test(detail)) return { message: detail };
  // 사전에 없는 코드는 화면 대신 콘솔에만 남긴다. 이 로그가 사전이 낡았다는 유일한 신호다.
  // `RouteError`가 라우팅 오류 원문을 콘솔로만 넘기는 것과 같은 방식이다.
  console.warn("[errorText] 사전에 없는 코드:", detail);
  return fallback ?? FALLBACK;
}

/**
 * Pydantic 검증 오류 한 건(`{type, msg, ctx}`)을 필드 옆에 그릴 한국어 한 줄로 바꾼다.
 *
 * `api()`가 422를 받을 때 부른다. Pydantic의 `msg`는 영어(`String should have at least 12
 * characters`)라서 그대로 싣지 않는다.
 * 흔한 `type`은 뜻을 옮기고, 직접 만든 검증기의 한국어 사유는 그대로 쓴다. 나머지는 일반 문구로 둔다.
 */
export function validationItemText(item: unknown): string {
  const it = (item ?? {}) as { type?: unknown; msg?: unknown; ctx?: unknown };
  const ctx = (typeof it.ctx === "object" && it.ctx !== null ? it.ctx : {}) as Record<string, unknown>;
  const num = (k: string): string | null => {
    const v = ctx[k];
    return typeof v === "number" || typeof v === "string" ? String(v) : null;
  };
  const withN = (k: string, text: (n: string) => string, bare: string) => {
    const n = num(k);
    return n === null ? bare : text(n);
  };
  switch (it.type) {
    case "missing":
      return "필수 항목입니다";
    case "string_too_short":
      return withN("min_length", (n) => `${n}자 이상이어야 합니다`, "너무 짧습니다");
    case "string_too_long":
      return withN("max_length", (n) => `${n}자 이하여야 합니다`, "너무 깁니다");
    case "too_short":
      return withN("min_length", (n) => `${n}개 이상이어야 합니다`, "항목이 모자랍니다");
    case "too_long":
      return withN("max_length", (n) => `${n}개 이하여야 합니다`, "항목이 너무 많습니다");
    case "greater_than_equal":
      return withN("ge", (n) => `${n} 이상이어야 합니다`, "값이 너무 작습니다");
    case "greater_than":
      return withN("gt", (n) => `${n}보다 커야 합니다`, "값이 너무 작습니다");
    case "less_than_equal":
      return withN("le", (n) => `${n} 이하여야 합니다`, "값이 너무 큽니다");
    case "less_than":
      return withN("lt", (n) => `${n}보다 작아야 합니다`, "값이 너무 큽니다");
    case "int_parsing":
    case "int_type":
    case "int_from_float":
    case "float_parsing":
    case "float_type":
    case "decimal_parsing":
      return "숫자를 입력해야 합니다";
    case "date_parsing":
    case "date_type":
    case "date_from_datetime_parsing":
    case "datetime_parsing":
    case "datetime_type":
    case "datetime_from_date_parsing":
      return "날짜 형식이 올바르지 않습니다";
    case "enum":
    case "literal_error":
      return "허용되지 않는 값입니다";
    case "string_pattern_mismatch":
    case "uuid_parsing":
      return "형식이 올바르지 않습니다";
    case "json_invalid":
      return "요청 형식이 올바르지 않습니다";
  }
  const msg = typeof it.msg === "string" ? it.msg : "";
  // 직접 만든 검증기(`raise ValueError("…")`)의 한국어 사유는 맥락을 담고 있다. 접두어만 뗀다.
  const own = msg.replace(/^(Value error|Assertion failed),\s*/, "");
  if (HAS_HANGUL.test(own)) return own;
  if (/email/i.test(msg)) return "이메일 형식이 올바르지 않습니다";
  return "값이 올바르지 않습니다";
}

/**
 * 다시 보내면 결과가 달라질 수 있는 실패이면 true.
 * 일시 오류(408·425·429·5xx)와 응답을 받지 못한 경우만 true다.
 *
 * `queryClient`의 자동 재시도와 화면의 "다시 시도" 안내가 같은 판정을 쓴다.
 * 403·404·409는 반복해서 보내도 결과가 같으므로 "다시 시도"를 붙이지 않는다.
 */
export function isRetryable(status: number | undefined): boolean {
  if (status === undefined || status === 0) return true; // 응답을 받지 못한 경우(네트워크 끊김·타임아웃)
  if (status >= 400 && status < 500) return status === 408 || status === 425 || status === 429;
  return true;
}

/**
 * 실패 객체 전체를 받아 화면 문구로 바꾼다.
 * onError에서 `e`를 버리고 고정 힌트를 다는 대신 이 함수를 쓴다.
 *
 * `errorText` 결과에 상태 코드로만 알 수 있는 안내를 더한다. 사전에 힌트가 있는 코드는 그대로 둔다.
 * 422 검증 실패는 첫 필드 사유(`ApiError.fields`)를, 403은 "권한을 확인해주세요."를 붙인다.
 * 원인은 아는 경우에만 쓴다.
 * 일시 오류(429·5xx·네트워크)에는 "잠시 후 다시 시도해주세요."를 붙인다.
 * 그 밖의 영구 실패(404·409 등)는 다시 눌러도 결과가 같으므로 힌트를 붙이지 않는다.
 *
 * @param e `api()`가 던진 `ApiError`(또는 네트워크 예외).
 * @param fallbackMessage 사전에 없는 코드일 때 굵게 쓸 결과(예: "승인 실패").
 */
export function apiErrorText(e: unknown, fallbackMessage?: string): ErrorText {
  const err = (typeof e === "object" && e !== null ? e : {}) as {
    status?: unknown;
    detail?: unknown;
    fields?: unknown;
  };
  const status = typeof err.status === "number" ? err.status : undefined;
  const base = errorText(err.detail, { message: fallbackMessage ?? FALLBACK.message });
  const fields = Array.isArray(err.fields) ? (err.fields as { message?: unknown }[]) : [];
  const first = fields.find((f) => typeof f?.message === "string")?.message as string | undefined;
  if (err.detail === "validation_error" && first) {
    const more = fields.length > 1 ? ` 외 ${fields.length - 1}건` : "";
    return { message: base.message, hint: `${first}${more}.` };
  }
  if (base.hint) return base;
  if (status === 403) return { message: base.message, hint: "권한을 확인해주세요." };
  if (isRetryable(status)) return { message: base.message, hint: "잠시 후 다시 시도해주세요." };
  return base;
}

/** 서버가 알려 준 대기 초를 "N분 후" 안내로 바꾼다. 없으면 기본 안내를 쓴다. */
function waitHint(retryAfter: unknown, fallback: string): string {
  if (typeof retryAfter !== "number" || retryAfter <= 0) return fallback;
  return `${Math.max(1, Math.ceil(retryAfter / 60))}분 후 다시 시도해주세요.`;
}

/**
 * 로그인 실패를 원인별로 안내한다. 로그인 화면과 세션 만료 모달이 함께 쓴다.
 *
 * 429는 연속 실패 잠금(`too_many_attempts`, (이메일, 주소) 쌍과 이메일 단위, 서버가 `Retry-After`로 남은 초를 준다)과
 * 주소별 실패 한도(`rate_limit_exceeded:auth:login`, 1분 창) 두 가지다.
 * 주소별 한도는 같은 공인 IP를 쓰는 사무실 전체의 실패를 함께 센다.
 * 둘을 같은 "시도 횟수 초과"로 표시하면 비밀번호를 맞게 입력한 사람도 자기 잘못으로 읽는다.
 * 401·429 외의 오류(500·502·422)를 "네트워크를 확인해주세요"로 표시하면
 * 서버 장애를 네트워크 문제로 오해한다.
 */
export function loginErrorText(e: unknown): ErrorText {
  const err = (typeof e === "object" && e !== null ? e : {}) as {
    status?: unknown;
    detail?: unknown;
    retryAfter?: unknown;
  };
  if (err.status === 429 && err.detail === "too_many_attempts") {
    return { message: "로그인이 잠겼습니다", hint: waitHint(err.retryAfter, "잠시 후 다시 시도해주세요.") };
  }
  if (err.status === 429) {
    return {
      message: "같은 네트워크의 로그인 실패가 많아 잠시 막혔습니다",
      hint: waitHint(err.retryAfter, "1분 후 다시 시도해주세요."),
    };
  }
  if (err.status === 401) return EXACT.invalid_credentials;
  return apiErrorText(e, "로그인하지 못했습니다");
}
