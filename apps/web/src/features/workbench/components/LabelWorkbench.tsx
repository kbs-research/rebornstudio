/**
 * 라벨링 전체 화면 워크벤치 뷰. `useLabelWorkbench` 훅의 값을 받아 흑연색 레이아웃으로 그리기만 한다.
 *
 * 머리 줄 한 줄, 왼쪽 무대(영상), 오른쪽 416px 레일(검수 워크벤치·완료 보기와 같은 폭)과 레일 바닥의 `제출 전 확인`이다.
 */
import { useCallback, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import {
  BookOpen, Check, Eye, Info, Keyboard, Lock, ShieldAlert,
} from "lucide-react";

import { VideoPlayer } from "@/features/library";
import { AccessDenied } from "@/shell/AccessDenied";
import { Card } from "@/shared/ui/Card";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { WorkbenchSkeleton } from "@/shared/ui/PageSkeleton";
import { StatusBadge } from "@/components/StatusBadge";
import { WorkbenchHeader, type WbAction, type WbStat } from "./WorkbenchHeader";
import { GuidePanel } from "./GuidePanel";
import { SentenceEditor } from "./SentenceEditor";
import { ReportDeidentModal } from "./ReportDeidentModal";
import { WorkbenchConfirm, type ConfirmRow } from "./WorkbenchConfirm";
import { REASON_CHIP } from "./chip";
import { useLabelWorkbench } from "./useLabelWorkbench";
import type { ApiError } from "@/shared/api/client";
import { errorText } from "@/shared/api/error_text";
import { type CaptionSentence, liveSentences } from "../lib/caption";
import { fmtClipRange } from "@/shared/lib/fmtSecs";
import { HOLD_LOST } from "../lib/hold_lost";
import { readOnlyReason } from "../lib/home";
import { withJosa } from "@/shared/lib/josa";
import { isRework } from "../lib/rework";
import { fmtDateTime } from "@/shared/lib/time";
import { useWorkbenchSurface } from "../store";
import {
  CAPTION_FONT_SIZE, CAPTION_LENGTH_UNIT, WbSaveStatus, WbSection, WbShortcutsModal, WbVideoMeta,
} from "./label_parts";

/** 편집할 수 없는 건의 머리 줄 상태 이름. 사유는 읽기 전용 띠가 문장으로 설명한다. */
const READ_ONLY_STATE: Record<string, string> = {
  submitted: "제출됨 · 검수 대기",
  approved: "승인됨",
  cancelled: "종료됨",
  claimed: "다른 사람 보유 중",
  available: "보유자 없음",
};

/**
 * 라벨링 워크벤치 화면을 그린다. 주소의 작업이 바뀌면 화면 전체를 새로 그린다.
 *
 * 같은 주소 틀(`work/:id/label`)에서 `id`만 바뀌면 React는 같은 컴포넌트를 다시 쓴다. 그러면 앞 작업의
 * 캡션·선택·서랍 상태가 다음 작업으로 따라온다. 그래서 `id`를 key로 걸어 새로 마운트한다.
 */
export function LabelWorkbench() {
  const { id } = useParams<{ id: string }>();
  return <LabelWorkbenchView key={id} />;
}

/** 한 작업의 라벨링 워크벤치를 그린다. */
function LabelWorkbenchView() {
  // 테마와 관계없이 흑연색 면으로 그린다. 워크벤치 루트 밖에 그려지는 요소도 흑연색이 되도록 문서 루트에도 범위를 단다.
  useWorkbenchSurface();

  const wb = useLabelWorkbench();
  const {
    id, item, labels, reviews, clip, playerRef,
    caption, setCaption,
    saveState, savedAt, reportOpen, setReportOpen, openReport, reportAtMs, helpOpen, setHelpOpen,
    seedSource, maiuSummary, captionSourceFailed,
    canEdit, locked, holdLost, manages, home,
    briefTokens, briefOk, canSubmit, captionMinTokens,
    saveDraft, edited, draftMut, submit, reportMut,
    submitConfirmOpen, setSubmitConfirmOpen, requestSubmit, confirmSubmit,
    project,
  } = wb;

  const [guideOpen, setGuideOpen] = useState(false);
  const toggleGuide = useCallback(() => setGuideOpen((o) => !o), []);

  /* 재작업이면 사유와 지적 문장을 레일 첫 카드로 보여 라벨러가 고칠 곳을 바로 알게 한다.
     재작업 판정은 마지막 판정과 누적 반려 횟수를 함께 봐야 해서 `lib/rework` 한 곳에 둔다. */
  const latestReview =
    [...(reviews.data ?? [])].sort((a, b) => a.reviewed_at.localeCompare(b.reviewed_at)).at(-1) ?? null;
  const rework = isRework({
    latestVerdict: latestReview?.verdict,
    rejectionCount: item.data?.rejection_count,
  });
  const latestReject = rework ? latestReview : null;
  // 반려 상세. 지적 문장·사유 칩(서버의 코드·이름)·보충 문장이 따로 온다.
  const rejectInfo = {
    reason: latestReject?.comment ?? "",
    reasons: latestReject?.reasons ?? [],
    flagged: latestReject?.flagged_sentences ?? [],
  };

  /* 로딩·오류 상태도 흑연색 면 안에서 그려 흰 화면이 깜빡이지 않게 한다.
     로딩은 `isPending`으로 판단한다. TanStack Query v5의 `isLoading`은 fetch가 시작되기 전 첫 렌더에서 false라
     그 한 프레임 동안 아래 오류 분기로 떨어진다. */
  if (item.isPending) return <WorkbenchSkeleton />;
  /* 403은 권한 문제라서 일반 오류와 문구를 따로 둔다. `work/:id/label` 라우트에는 역할 게이트가 없고,
     남의 작업인지는 작업의 점유자로 정해지므로 서버가 403과 사유로 막는다.
     검수 역할이 없는 사람이 남의 작업을 열면 서버는 `not_reviewer`를 돌려주지만, 그 사람은 검수를 하려던 것이 아니므로
     화면은 실제 이유(남의 작업)를 표시한다. 이동할 곳은 `내 작업`이고 머리 줄은 본 화면과 같은 뒤로 가기를 둔다. */
  if (item.isError || !item.data) {
    const err = item.error as unknown as ApiError | null;
    const denied = err?.status === 403;
    const why = errorText(err?.detail, { message: "이 작업을 볼 권한이 없습니다" });
    const mineOnly = err?.detail === "not_reviewer" || err?.detail === "not_your_claim";
    return (
      <div data-surface="workbench" className="fixed inset-0 z-workbench flex flex-col bg-canvas text-label">
        <WorkbenchHeader badge="라벨링" />
        <div className="flex min-h-0 flex-1 items-center justify-center">
          {/* 보던 작업의 보유가 끝났으면 권한 문제 대신 보유가 끝났다는 사실을 표시하고 라벨링 큐로 보낸다. */}
          {holdLost ? (
            <EmptyState
              icon={<Lock size={18} aria-hidden="true" />}
              message={HOLD_LOST.message}
              description={HOLD_LOST.hint}
              action={
                <Link to={home.to} className="rounded-control px-2 py-1 text-footnote font-medium text-label hover:bg-fill">
                  {home.label}
                </Link>
              }
            />
          ) : denied ? (
            <AccessDenied
              reason="not-holder"
              message={mineOnly ? "라벨링 작업은 보유한 사람만 엽니다." : why.message}
              hint={mineOnly ? null : (why.hint ?? null)}
              to={{ to: "/my/work", label: "내 작업" }}
            />
          ) : (
            // 없는 작업(404)은 다시 해도 같다. 실패 객체를 넘기면 재시도 대신 사유를 보여 준다.
            <ErrorState message={err?.status === 404 ? "작업 없음" : "작업 조회 실패"} onRetry={() => void item.refetch()} error={err ?? undefined} />
          )}
        </div>
      </div>
    );
  }

  // 삭제하지 않은 문장. 캡션 절 머리의 문장 수와 지적 문장 대조에 쓴다.
  const liveBrief = liveSentences(caption.brief);
  const liveBriefCount = liveBrief.length;

  /* 반려 때 지적된 문장 중 아직 그대로인 수. 그대로 제출하면 다시 반려되므로 재작업의 주요 근거다. */
  const flaggedRemaining = rejectInfo.flagged.filter(
    (f) => f && liveBrief.some((x) => x.text.trim().startsWith(f)),
  ).length;

  /* 제출 확인 창의 근거 표다. 이름과 값이 같은 것을 가리키게 한 줄에 한 절씩 쓴다.
     캡션은 설정과 같은 이름(`캡션 길이`)으로 쓴다. */
  const submitRows: ConfirmRow[] = [
    { name: "문장", value: `${liveBriefCount}개` },
    { name: "캡션 길이", value: `${briefTokens} / 최소 ${captionMinTokens}` },
    ...(rework ? [{ name: "반려", value: `${item.data.rejection_count}회` }] : []),
  ];

  /* 머리 줄 가운데 묶음. 재작업이면 반려 횟수를 둔다. 보유에는 끝나는 시각이 없어 남은 시간을 쓰지 않는다. */
  const stats: WbStat[] =
    canEdit && rework ? [{ key: "rework", label: "반려", value: `${item.data.rejection_count}회` }] : [];

  /* 머리 줄 상태 슬롯. 편집하는 사람에게는 저장 상태, 읽기 전용이면 작업 상태다. */
  const headerStatus: ReactNode = !canEdit ? (
    <StatusBadge
      status={item.data.status}
      label={
        item.data.status === "claimed" && !item.data.claimed_by
          ? READ_ONLY_STATE.available
          : (READ_ONLY_STATE[item.data.status] ?? "읽기 전용")
      }
    />
  ) : (
    <WbSaveStatus state={saveState} at={savedAt} onRetry={saveDraft} />
  );

  /* 머리 줄 도구. 비식별화 미흡 신고는 검수와 같은 버튼 하나로 둔다.
     신고는 서버에서 `_load_working_claim`을 거쳐 보유한 사람만 낼 수 있다.
     그래서 잠긴 동안에는 그리지 않는다. 단축키는 아이콘으로 접는다. 반납 버튼은 없다. */
  const tools: WbAction[] = [
    { key: "guide", icon: <BookOpen size={16} aria-hidden="true" />, label: "가이드", onClick: toggleGuide, active: guideOpen },
    ...(!locked
      ? [{ key: "report", icon: <ShieldAlert size={16} aria-hidden="true" />, label: "비식별화 신고", onClick: openReport }]
      : []),
    { key: "shortcuts", icon: <Keyboard size={16} aria-hidden="true" />, label: "단축키", onClick: () => setHelpOpen(true), compact: true },
  ];

  const clipRange = clip.data ? fmtClipRange(clip.data.start_seconds, clip.data.end_seconds) : null;
  const clipLen = clip.data ? `${(clip.data.end_seconds - clip.data.start_seconds).toFixed(1)}초` : null;
  const canWork = canEdit;

  return (
    <div data-surface="workbench" className="fixed inset-0 z-workbench flex flex-col bg-canvas text-label">

      <WorkbenchHeader
        badge="라벨링"
        backTo={home.to}
        backLabel={home.label}
        /* 보고 있는 대상은 영상 제목으로 알린다. 구간·길이·해시는 둘째 줄에 작게 둔다. */
        title={clip.data?.video_title ?? undefined}
        subtitle={[clipRange ? `클립 ${clipRange}` : null, clipLen, id?.slice(0, 8)].filter(Boolean).join(" · ")}
        stats={stats}
        status={headerStatus}
        actions={tools}
        /* 편집할 수 없는 건이면 저장·제출을 그리지 않는다.
           비활성 강조 버튼은 왜 눌리지 않는지 의문만 남긴다. 캡션이 모자라면 비활성 모양으로 둔다.
           이유는 레일 바닥의 `제출 전 확인`에 표시한다. */
        secondary={canWork ? { label: "임시저장", onClick: saveDraft, disabled: draftMut.isPending } : undefined}
        primary={canWork ? {
          label: submit.isPending ? "제출 중…" : "제출",
          // 바로 보내지 않는다. 확인 창이 근거를 보여 준 뒤 보낸다.
          onClick: requestSubmit,
          disabled: !canSubmit || submit.isPending,
          tone: "submit",
          keys: ["⌘", "↵"],
        } : undefined}
      />

      {/*
        읽기 전용 띠. 굵은 이름과 작은 사유, 오른쪽에 이동할 곳을 둔다.
        다음 작업은 서버가 정하므로 "이 작업을 받으라"는 경로는 없다. 그래서 이동할 곳은 그 사람의 라벨링 큐다.
        작업자는 `내 작업`, 관리자는 작업 단계의 라벨링 화면이다(`lib/home`).
      */}
      {!canEdit && (
        <div className="flex shrink-0 items-center gap-3 border-b border-separator bg-canvas-2 px-5 py-3">
          <Eye size={16} className="shrink-0 text-label-3" aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <p className="text-footnote font-semibold text-label">읽기 전용</p>
            <p className="text-footnote text-label-2">{readOnlyReason(item.data, manages)}</p>
          </div>
          <Link to={home.to} className="shrink-0 rounded-control px-2 py-1 text-footnote font-medium text-label hover:bg-fill">
            {home.label}
          </Link>
        </div>
      )}

      {/* ── 본문 ── */}
      <div className="relative flex min-h-0 flex-1 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">

        {/* 왼쪽: 무대 */}
        <div className="flex min-w-0 flex-col gap-4 p-4 lg:flex-1">

          {/* 영상. 제목·구간 칩은 올리지 않는다. 머리 줄에 이미 있다. */}
          <div className="flex min-h-[40vh] flex-1 items-center justify-center overflow-hidden rounded-card bg-ws-stage lg:min-h-0">
            <div className="relative mx-auto flex aspect-video h-full max-h-full max-w-full items-center justify-center">
              <VideoPlayer
                hideTitle
                videoId={item.data.video_id}
                playerRef={playerRef}
                startSeconds={clip.data?.start_seconds}
                endSeconds={clip.data?.end_seconds}
                rangePending={!!item.data.clip_id && clip.isPending}
              />
            </div>
          </div>
        </div>

        {/* 오른쪽: 라벨 레일 */}
        <aside className="flex w-full flex-col border-l border-separator bg-canvas-2 lg:w-[416px] lg:shrink-0">

          {/* 레일 내용. lg 이상에서는 레일 안에서 스크롤하고, 그보다 좁으면 페이지가 스크롤된다. */}
          <div className="min-h-0 flex-1 pb-5 lg:overflow-y-auto">
            {/* 반려 카드. 재작업이면 반려 횟수·사유·보충 설명을 레일 첫 카드에 모아 보여 준다.
                사유는 라벨러가 고칠 곳을 알 수 있는 유일한 근거라 자르지 않는다.
                한 줄 flex 안에서 `truncate`로 두면 좁은 폭에서 긴 사유가 대부분 잘린다.
                사유 칩은 서버가 코드와 이름으로 준다. */}
            {latestReject && (
              <Card className="mx-5 mt-5 px-4 py-3">
                <div className="flex items-baseline gap-2">
                  <span className="text-body font-bold text-label">반려 {item.data.rejection_count}회</span>
                  <time dateTime={latestReject.reviewed_at} className="text-footnote text-label-3">
                    {fmtDateTime(latestReject.reviewed_at)}
                  </time>
                  {rejectInfo.flagged.length > 0 && (
                    <span className="ml-auto shrink-0 text-footnote text-label-2">지적 문장 {rejectInfo.flagged.length}개</span>
                  )}
                </div>
                {rejectInfo.reasons.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {rejectInfo.reasons.map((r) => (
                      <span key={r.code} className={REASON_CHIP}>
                        {r.label}
                      </span>
                    ))}
                  </div>
                )}
                {rejectInfo.reason && (
                  <p className="mt-2 border-l-2 border-separator-strong pl-3 text-footnote text-label">{rejectInfo.reason}</p>
                )}
              </Card>
            )}

            {/* 제출 이력을 받지 못하면 빈 캡션이 "저장된 것 없음"으로 읽히므로 실패로 알린다. */}
            {captionSourceFailed && (
              <div className="mx-5 mt-5">
                <ErrorState
                  message="제출 이력 조회 실패"
                  description={locked ? undefined : "캡션을 새로 쓰기 전에 다시 시도합니다."}
                  onRetry={() => void labels.refetch()}
                />
              </div>
            )}

            {/* 캡션을 AI 초안으로 채웠으면 그 사실을 한 번 알린다. 결과와 할 일은 두 줄로 나눈다. */}
            {seedSource && !locked && (
              <div className="mx-5 mt-5 flex items-start gap-2 rounded-card border border-separator px-3 py-2.5 text-footnote">
                <Info size={14} className="mt-0.5 shrink-0 text-label-3" aria-hidden="true" />
                <p>
                  <span className="block text-label">캡션을 AI 초안으로 채웠습니다.</span>
                  <span className="block text-label-3">영상과 다른 곳만 고칩니다.</span>
                </p>
              </div>
            )}

            <fieldset disabled={locked} className="m-0 min-w-0 border-0 p-0">
              {/* 캡션(문장 카드 단위, 한국어). 이 화면에서 하는 일은 캡션 교정이라 레일 맨 위에 둔다.
                  기준 길이는 `제출 전 확인`이 프로젝트 설정 값으로 보여 준다.
                  할 일 설명 문장은 두지 않는다. 할 일은 작업 가이드 서랍에 있다. */}
              <WbSection
                title="캡션"
                aside={<span>문장 {liveBriefCount}개</span>}
              >
                <SentenceEditor
                  value={caption.brief}
                  onChange={edited((v: CaptionSentence[]) =>
                    setCaption((c) => ({ ...c, brief: v })))}
                  fontSize={CAPTION_FONT_SIZE}
                  flaggedTexts={rejectInfo.flagged}
                  baseline={maiuSummary}
                  readOnly={locked}
                />
              </WbSection>

            </fieldset>

            {/* 영상 메타. 방송사·프로그램명·장르·방영일은 영상 단위 값이라 관리자가 영상 상세에서 넣고 클립이 물려받는다.
                그래서 읽기 전용으로 두고 입력란 대신 누가 채우는지를 적는다.
                납품과 자동 검수가 이 값을 쓰고 반려 칩(`메타 누락`)도 이 값을 가리키므로 라벨러가 값을 볼 수 있어야 한다. */}
            {clip.data?.video_meta && <WbVideoMeta meta={clip.data.video_meta} />}
          </div>

          {/*
            제출 전 확인. 레일 바닥에 고정한다. 막는 조건은 호박색 점, 선택 항목은 회색 점,
            채운 조건은 확인 표시로 모양을 구분한다. 제출 조건의 수는 이 영역에서만 보인다.
            마지막 줄은 제출 뒤 돌아갈 화면이다.
            편집할 수 있는 사람에게만 보인다. 읽기 전용으로 보는 사람은 제출할 수 없으므로 제출 조건이 필요 없다.
          */}
          {canWork && (
            <div className="shrink-0 border-t border-separator px-5 py-4">
              <h4 className="mb-2 text-caption font-semibold text-label-2">제출 전 확인</h4>
              <ul className="space-y-1.5 text-footnote">
                <li className="flex items-center gap-2" title={CAPTION_LENGTH_UNIT}>
                  {briefOk ? (
                    <Check size={14} className="shrink-0 text-ok" aria-hidden="true" />
                  ) : (
                    <span className="mx-[3px] size-2 shrink-0 rounded-capsule bg-amber" aria-hidden="true" />
                  )}
                  <span className={briefOk ? "text-label-2" : "font-semibold text-label"}>
                    캡션 길이 {briefTokens} / 최소 {captionMinTokens}
                  </span>
                </li>
                {rework && flaggedRemaining > 0 && (
                  <li className="flex items-center gap-2">
                    <span className="mx-[3px] size-2 shrink-0 rounded-capsule bg-amber" aria-hidden="true" />
                    <span className="font-semibold text-label">지적 문장 {flaggedRemaining}개 아직 그대로</span>
                  </li>
                )}
              </ul>
              <p className="mt-2.5 text-footnote text-label-3">
                제출하면 {withJosa(home.label, "으로", "로")} 돌아갑니다.
              </p>
            </div>
          )}
        </aside>

        {/* 작업 가이드 서랍. 배경막 없이 레일 위에 겹쳐 영상은 계속 보인다. */}
        <GuidePanel open={guideOpen} onClose={() => setGuideOpen(false)} project={project.data} />
      </div>

      {/* 모달·서랍 */}
      <ReportDeidentModal
        open={reportOpen}
        onClose={() => setReportOpen(false)}
        onSubmit={(reason) => reportMut.mutate(reason)}
        pending={reportMut.isPending}
        presentation="drawer"
        scope="label"
        positionMs={reportAtMs}
        clipName={clip.data?.video_title}
        clipRange={clipRange}
        error={reportMut.error ? errorText(reportMut.error.detail, { message: "신고 실패", hint: "잠시 후 다시 시도해주세요." }) : null}
      />
      <WbShortcutsModal open={helpOpen} onClose={() => setHelpOpen(false)} />
      {/* 제출 확인. 되돌릴 수 없어서 근거를 같은 화면에 두고 한 번 더 확인한다.
          맨 아래에 제출 뒤 돌아갈 화면을 둔다. 그래서 누르기 전에 이동할 곳을 알 수 있다. */}
      <WorkbenchConfirm
        open={submitConfirmOpen}
        onClose={() => setSubmitConfirmOpen(false)}
        onConfirm={confirmSubmit}
        title="작업 제출"
        result={rework ? "제출하면 다시 검수로 넘어갑니다." : "제출하면 검수로 넘어갑니다."}
        rows={submitRows}
        warn={
          rework && flaggedRemaining > 0
            ? `반려 때 지적된 문장 ${flaggedRemaining}개가 아직 그대로입니다.`
            : undefined
        }
        note="제출한 뒤에는 반려되기 전까지 고칠 수 없습니다."
        next={
          <span className="flex items-baseline gap-2">
            <span className="shrink-0 text-label-3">제출 뒤</span>
            <span className="text-label">{home.label}</span>
          </span>
        }
        confirmLabel="제출"
        pendingLabel="제출 중…"
        pending={submit.isPending}
        tone="submit"
      />
    </div>
  );
}
