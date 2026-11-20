/**
 * 완료 클립 뷰. 라벨링 워크벤치를 읽기 전용으로 옮긴 화면이다.
 *
 * 상세 페이지(`CompletedDetailPage`)와 완료 워크벤치(`completed_workbench`)가 함께 쓴다.
 * 클립을 내보내도 되는지 판단하려면 캡션 전문과 이력을 함께 봐야 해서 `LabelWorkbench`의 구조를 그대로 쓴다.
 * 왼쪽에 무대(영상), 오른쪽에 탭 레일을 두고 편집 장치만 뺀다.
 *
 * 클립이 패키지에 실리지 않는 이유(`blockers`)는 `report.json`에서 찾게 하지 않고 이 화면의 머리에 둔다.
 * `blockers`는 서버가 납품 패키지와 같은 검증으로 만들고, 항목 이름(`캡션`, `비식별화 신고`, `타임코드`)은 서버 사전이 정한다.
 */
import { useMemo, useRef, useState, type ReactNode } from "react";
import type { MediaPlayerInstance } from "@vidstack/react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight } from "lucide-react";

import { VideoPlayer } from "@/features/library";
import { ErrorState } from "@/shared/ui/ErrorState";
import { PageHeader } from "@/shared/ui/PageHeader";
import { buttonClasses } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { IdChip } from "@/shared/ui/IdChip";
import { PageSkeleton } from "@/shared/ui/PageSkeleton";
import { SectionHeader } from "@/shared/ui/SectionHeader";
import { Skeleton } from "@/shared/ui/Skeleton";
import { Tabs, TabCount, type TabItem } from "@/shared/ui/Tabs";
import {
  liveSentences,
  toSentences,
  type CaptionSentence,
  useVideoShortcuts,
} from "@/features/workbench";
import { type ApiError } from "@/shared/api/client";
import { cn } from "@/shared/lib/cn";
import { errorText } from "@/shared/api/error_text";
import { withJosa } from "@/shared/lib/josa";
import { airedDate, VIDEO_META_FIELDS, VIDEO_META_NAME } from "@/lib/video_meta";
import { VERDICT_LABEL } from "@/lib/vocab";
import { useSelectedProject } from "@/store/project";
import { splitBlocker } from "@/lib/blocker";
import { fmtClipRange } from "@/shared/lib/fmtSecs";
import { fmtDateTime } from "@/shared/lib/time";

import { fetchCompletedDetail, pipelineKeys } from "../api";

/**
 * 라벨과 값 한 줄을 그린다. `ReadOnlyLabel`에는 클립 정보 행이 없어서 이 파일에 둔다.
 *
 * 좁은 폭에서는 라벨과 값을 위아래로 쌓는다.
 * 라벨을 `w-24`로 고정하면 375px 폭에서 값 영역이 23px까지 줄어 한 글자씩 줄바꿈된다.
 */
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 py-1 text-caption sm:flex-row sm:gap-2">
      <span className="text-label-3 sm:w-24 sm:shrink-0">{label}</span>
      <span className="min-w-0 flex-1 text-label-2">{children}</span>
    </div>
  );
}

/**
 * 완료 클립 하나를 읽기 전용으로 그린다. 상세 페이지와 완료 워크벤치가 함께 쓴다.
 *
 * 같은 내용을 그리는 화면이 둘이라 컴포넌트 하나로 둔다. 복사본을 두면 두 화면이 서로 달라진다.
 *
 * @param backTo 목록으로 돌아가는 절대 경로. `null`이면 링크를 그리지 않는다.
 *   워크벤치는 목록이 같은 화면에 있어 `null`을 넘긴다. 상대 경로 `..`는 없는 라우트로 풀려 오류 화면이 뜬다.
 */
export function CompletedClipView({
  workItemId,
  backTo,
  variant = "page",
  sidebar,
  empty,
  videoHref,
  qcHref,
}: {
  /** 보여 줄 완료 작업. 워크벤치 얼개에서는 아무것도 고르지 않으면 `null`이고, 그때 무대는 `empty`를 그린다. */
  workItemId: string | null;
  backTo?: string | null;
  /** 워크벤치 얼개에서 고른 클립이 없을 때 무대에 그릴 것(빈 상태). */
  empty?: ReactNode;
  /**
   * 워크벤치 얼개의 완료 목록. xl 폭에서는 왼쪽 열, lg 폭에서는 레일 위쪽에 들어간다.
   * 없으면 레일은 참고 자료만 보여 준다.
   */
  sidebar?: ReactNode;
  /**
   * 레이아웃 종류. `"stage"`는 워크벤치(무대가 화면을 채운다), `"page"`는 앱 셸 안의 문서다.
   * 자세한 이유는 아래 렌더 앞의 주석에 있다. 기본값은 `"page"`다.
   */
  variant?: "page" | "stage";
  /**
   * 영상 상세 주소. 납품 불가 사유가 영상 단위 값(방영일 등)이면 고칠 곳으로 버튼을 둔다.
   * 영상 상세를 고칠 수 있는 역할(관리자)에게만 넘긴다.
   */
  videoHref?: string | null;
  /**
   * 품질검수 보류 목록 주소. 고른 클립이 보류 중이라 열리지 않을 때 그곳으로 가는 링크를 둔다.
   * 보류 목록을 열 수 있는 역할(관리자)에게만 넘긴다.
   */
  qcHref?: string | null;
}) {
  const { selectedProjectId } = useSelectedProject();
  const [tab, setTab] = useState("caption");

  const q = useQuery({
    queryKey: pipelineKeys.completedDetail(selectedProjectId, workItemId),
    queryFn: () => fetchCompletedDetail(selectedProjectId, workItemId),
    enabled: !!selectedProjectId && !!workItemId,
    /* 실패는 본문이 사유와 함께 표시하므로 전역 토스트를 띄우지 않는다. */
    meta: { handled: true },
    /* 워크벤치 얼개에서는 다음 클립을 불러오는 동안 앞 클립을 흐리게 둔다. 골격으로 바꾸면
       바깥 구조가 달라져 레일 위 목록이 다시 마운트된다(아래 `stageShell`). */
    placeholderData: (prev, prevQuery) =>
      variant === "stage" && prevQuery?.queryKey[1] === selectedProjectId ? prev : undefined,
  });
  /* 문장 목록은 저장소 공통 규칙(`toSentences`)으로 만든다. 배열이 있으면 배열을 쓰고, 없으면 문자열을 문장으로 나눈다.
     `brief_sentences`만 보면 문자열뿐인 이전 형식의 payload가 캡션 탭에서 "캡션 없음"(배지 `캡션 0`)으로 나온다.
     납품 검토 화면에서 있는 캡션을 없다고 표시하면 검토자가 그 클립을 납품 불가로 잘못 판단한다. */
  const sentences = useMemo(
    () =>
      liveSentences(
        toSentences(
          // 계약 타입은 `list[dict]`라 느슨하다. 모양은 `toSentences`가 공통 규칙으로 맞춘다.
          q.data?.caption.brief_sentences as CaptionSentence[] | undefined,
          q.data?.caption.brief,
        ),
      ),
    [q.data],
  );

  // 훅은 조기 반환보다 위에서 부른다.
  const playerRef = useRef<MediaPlayerInstance>(null);
  /* 워크벤치 얼개에서는 다른 워크벤치와 같은 재생 키(`PLAYBACK_SHORTCUTS`)를 쓴다.
     문서 얼개는 앱 셸 안이라 전역 키를 걸지 않는다.
     플레이어 내장 키(J = 10초 뒤로, K = 재생 토글)를 쓰면 워크벤치마다 키 뜻이 달라진다. */
  useVideoShortcuts(playerRef, { enabled: variant === "stage" });

  /* 로딩 골격은 실제로 그릴 화면의 모양을 따른다.
     `stage`는 영역을 채우는 블록 하나로 그린다. 무대와 레일을 따로 그리면 실제 화면이 올 때 모양이 두 번 바뀐다.
     인자 없는 `PageSkeleton`은 빈 영역만 그리므로 `stage`는 `Skeleton`을 직접 조립하고 바깥에 `role="status"`를 둔다.
     `Skeleton`은 `aria-hidden`이라 혼자 두면 스크린리더가 아무것도 읽지 않는다. 문서 레이아웃은 `panels={2}`로 그린다. */
  /* 워크벤치 얼개는 어느 상태에서나 같은 바깥 구조를 그리고, 상태마다 무대와 레일의 내용만 바꾼다.
     레일 위쪽 목록(`sidebar`)에 검색 입력이 있어서, 바깥 구조가 바뀌면 목록이 다시 마운트되고 입력의 포커스가 빠진다.
     넓은 폭(xl, 1280 이상)은 목록 · 무대 · 레일 세 열, lg 폭은 목록이 레일 위쪽에 들어간 두 열, 좁은 폭은 한 열이다.
     목록은 한 번만 마운트하고 격자로 위치만 옮긴다. 두 곳에 그리면 검색 입력이 둘이 된다.
     고른 클립이 없으면 좁은 폭에서는 목록만, 넓은 폭에서는 무대 영역에 `empty`를 그린다.
     무대 배경(`bg-ws-stage`)은 양 테마 모두 어두워서 영상이 있을 때만 쓴다. */
  const stageShell = (stage: ReactNode, rail: ReactNode, busy = false) => (
    <div
      className={cn(
        "flex min-h-0 flex-1 flex-col overflow-y-auto lg:grid lg:overflow-hidden",
        sidebar
          ? "lg:grid-cols-[minmax(0,1fr)_416px] lg:grid-rows-[minmax(0,1.2fr)_minmax(0,1fr)] xl:grid-cols-[360px_minmax(0,1fr)_416px] xl:grid-rows-1"
          : "lg:grid-cols-[minmax(0,1fr)_416px]",
      )}
    >
      {/* 목록을 받으면 xl에서는 왼쪽 열, lg에서는 레일 위쪽 열에 둔다.
          고른 클립이 있으면 좁은 폭에서는 목록을 그리지 않는다. 그 폭에서는 라우트가 목록을 첫 화면으로 보여 준다. */}
      {sidebar && (
        <div
          className={cn(
            "min-h-0 flex-col border-separator bg-canvas-2 lg:col-start-2 lg:row-start-1 lg:flex lg:border-b lg:border-l xl:col-start-1 xl:border-b-0 xl:border-l-0 xl:border-r",
            workItemId ? "hidden" : "flex flex-1",
          )}
        >
          {sidebar}
        </div>
      )}
      <div
        className={cn(
          "relative min-h-[40vh] min-w-0 flex-col lg:col-start-1 lg:row-start-1 lg:flex lg:min-h-0",
          sidebar ? "lg:row-span-2 xl:col-start-2 xl:row-span-1" : "",
          workItemId ? "flex" : "hidden",
          "bg-canvas",
          busy && "opacity-60",
        )}
        aria-busy={busy || undefined}
      >
        {stage}
      </div>
      <aside
        className={cn(
          "flex w-full min-w-0 flex-col border-separator bg-canvas-2 lg:col-start-2 lg:overflow-hidden lg:border-l",
          sidebar ? "lg:row-start-2 xl:col-start-3 xl:row-start-1" : "lg:row-start-1",
          !workItemId && "hidden lg:flex",
        )}
      >
        {rail}
      </aside>
    </div>
  );

  if (variant === "stage" && !workItemId) {
    return stageShell(<div className="flex flex-1 items-center justify-center p-6">{empty}</div>, null);
  }
  /* `isPending`으로 판정한다. v5의 `isLoading`은 fetch가 시작되기 전 첫 렌더에서 거짓이라 그 한 프레임에
     아래 오류 분기(`!q.data`)로 떨어진다(라벨링 워크벤치가 같은 이유로 `isPending`을 쓴다). */
  if (q.isPending)
    return variant === "stage" ? (
      stageShell(
        <div className="flex min-h-0 flex-1 flex-col p-2" role="status" aria-label="불러오는 중">
          <Skeleton className="min-h-0 flex-1" />
        </div>,
        null,
      )
    ) : (
      <PageSkeleton panels={2} />
    );
  /* 원인이 확인된 실패는 원인을 알린다.
     404 `not_completed`(반려·재투입으로 승인 집합에서 빠진 건), 404 `qc_held`(캡션 품질검수가 보류한 건), 403(멤버 아님)은 다시 눌러도 결과가 같다.
     그래서 4xx는 사전 문구와 완료 목록 링크를 보여 주고, 보류 건은 보류 목록을 열 수 있는 사람에게 그곳으로 가는 링크를 준다.
     재시도는 일시 오류(5xx, 연결 실패)에만 준다. */
  if (q.isError || !q.data) {
    const err = q.error as unknown as ApiError | null;
    const lasting = !!err?.status && err.status < 500;
    const t = errorText(err?.detail, { message: "클립 조회 실패", hint: "잠시 후 다시 시도해주세요." });
    const failed = (
      <div className="flex flex-col items-center">
        <ErrorState
          message={t.message}
          description={t.hint}
          onRetry={lasting ? undefined : () => void q.refetch()}
        />
        {/* 두 링크는 가로로 띄워 둔다. 세로로 붙이면 한 링크의 두 줄처럼 읽힌다. */}
        <div className="flex flex-wrap items-center justify-center gap-x-5 gap-y-2">
          {lasting && backTo && (
            <Link to={backTo} className="inline-flex items-center gap-1 text-caption text-accent hover:underline">
              <ArrowLeft size={14} aria-hidden="true" /> 완료 클립 목록
            </Link>
          )}
          {err?.detail === "qc_held" && qcHref && (
            <Link to={qcHref} className="inline-flex items-center gap-1 text-caption text-accent hover:underline">
              품질검수 보류 목록
              <ArrowRight size={14} aria-hidden="true" />
            </Link>
          )}
        </div>
      </div>
    );
    return variant === "stage"
      ? stageShell(<div className="flex flex-1 items-center justify-center p-6">{failed}</div>, null)
      : failed;
  }
  /** 다음 클립을 불러오는 동안 보이는 앞 클립. 흐리게 두고 불러오는 중이라고 알린다. */
  const busy = q.isPlaceholderData;

  const d = q.data;
  const r = d.row;
  /* 영상 메타는 클립 카드 한 곳에서만 보여 준다.
     방송사, 프로그램명, 장르, 방영일은 영상에서 물려받는 값이고 납품 메타에도 실린다(서버 `video_meta`).
     라벨러가 클립마다 넣는 `meta`는 납품에 쓰이지 않아 그리지 않는다. 그리면 실리지 않는 값을 실리는 값처럼 보여 준다. */
  const vm = d.video_meta;

  /* 탭 이름은 패널 제목과 같게 쓴다.
     수가 무엇을 세는지 스크린리더에도 알린다(`개`, `건`).
     이력 수는 제출과 판정의 합이라 툴팁이 둘을 나눠 보여 준다. */
  const tabs: TabItem[] = [
    {
      key: "caption",
      label: <>문장 <TabCount count={sentences.length} /><span className="sr-only">개</span></>,
    },
    { key: "info", label: "정보" },
    {
      key: "history",
      label: <>이력 <TabCount count={d.revisions.length + d.reviews.length} /><span className="sr-only">건</span></>,
      title: `제출 ${d.revisions.length}건 · 판정 ${d.reviews.length}건`,
    },
  ];


  /* 레이아웃은 두 가지이고, 화면 조각(무대·레일·사유 카드)은 한 번만 만들어 배치만 다르게 한다.
     `variant="stage"`는 완료 워크벤치의 셸이다. 무대가 열을 채우고 오른쪽에 고정 폭 레일이 붙는다.
     워크벤치 안에서 문서처럼 그리면 16:9 상자가 넓은 빈 공간 가운데 뜨고 머리 띠가 둘이 된다.
     `variant="page"`는 앱 셸(사이드바, 브레드크럼, 여백) 안의 문서 레이아웃이다.
     완료 상세는 좁은 폭에서도 쓰고 북마크로 여는 화면이라 화면 전체를 채우는 무대를 쓰지 않는다. */
  const title = r.video_title ?? r.program_name ?? "완료 클립";
  const range =
    r.start_seconds != null && r.end_seconds != null
      ? fmtClipRange(r.start_seconds, r.end_seconds)
      : null;

  /** 워크벤치 레일 머리의 상태 한 줄. 점과 이름, 원본 구간과 길이를 잇는다. */
  const statusLine = (
    <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-footnote">
      <span className="flex items-center gap-1.5 font-medium text-label">
        <span
          className={cn("size-2 rounded-capsule", r.deliverable ? "bg-ok" : "bg-bad")}
          aria-hidden="true"
        />
        {r.deliverable ? "납품 대상" : "납품 불가"}
      </span>
      {range && (
        <span className="font-mono text-micro text-label-3">
          원본 {range}
          {r.start_seconds != null && r.end_seconds != null && ` · ${(r.end_seconds - r.start_seconds).toFixed(1)}초`}
        </span>
      )}
    </p>
  );

  /** 납품 불가 사유 중 영상 단위 값(방송사·프로그램명·장르·방영일). 클립이 아니라 영상에서 고친다. */
  const videoMetaNames = new Set(Object.values(VIDEO_META_NAME));
  const videoLevelFields = r.blockers
    .map((b) => splitBlocker(b).field)
    .filter((f): f is string => !!f && videoMetaNames.has(f));
  /* 납품 불가 사유 카드. 내보내지 않는 이유를 사용자가 화면에서 찾아다니지 않게 한다.
     막힌 항목이 없으면 값이 `false`라 아무것도 그리지 않는다. 두 레이아웃이 각자 알맞은 위치에 놓는다. */
  const blockerCard = !r.deliverable && r.blockers.length > 0 && (
    <Card className="border-bad/40 bg-bad-subtle px-3 py-2">
      {/* 카드 제목은 이름이고, 결과 문장은 아래 작은 줄이다. */}
      <p className="text-caption font-semibold text-bad-text">납품 불가 사유</p>
      <p className="text-micro text-label-2">지금 상태로는 납품 패키지에 실리지 않습니다.</p>
      {/* 서버는 사유를 화면 이름으로 만든다(`캡션: 10자 이상 필요`). 이 카드는 문자열을 막힌 항목과 이유로 나누기만 한다(`lib/blocker`).
          이름 사전은 서버 `export_package.FIELD_LABEL`에 있고, 받는 쪽 시스템이 쓰는 스키마 키는 `report.json`의 `fields`가 따로 싣는다.
          항목 이름은 사람이 읽는 말이라 고정폭(`<code>`)으로 그리지 않는다. 스키마 키(`caption`, `rights_status`)로는 검수자가 화면의 항목을 찾을 수 없다. */}
      <ul className="mt-1.5 space-y-1 text-footnote text-label-2">
        {r.blockers.map((b) => {
          const { field, reason } = splitBlocker(b);
          return (
            <li key={b} className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
              {field && (
                <span className="rounded-control bg-bad-muted px-1 text-micro font-medium text-bad-text">
                  {field}
                </span>
              )}
              <span className="min-w-0">{reason}</span>
            </li>
          );
        })}
      </ul>
      {/* 사유가 영상 단위 값이면 고칠 곳을 알리고, 고칠 수 있는 사람에게 영상 상세로 가는 버튼을 둔다. */}
      {videoLevelFields.length > 0 && (
        <div className="mt-2 space-y-2">
          <p className="text-footnote text-label-2">
            {withJosa(videoLevelFields.join("·"), "은", "는")} 영상 단위 값이라 영상 상세에서 넣습니다.
          </p>
          {videoHref && (
            <Link to={videoHref} className={buttonClasses("secondary", "sm")}>
              영상 상세
            </Link>
          )}
        </div>
      )}
    </Card>
  );

  const player = (
    <VideoPlayer
      hideTitle
      /* 클립마다 새 플레이어를 만든다. 워크벤치는 클립이 바뀌어도 이 부품을 다시 마운트하지 않기 때문이다. */
      key={`${r.video_id}:${r.clip_id}`}
      videoId={r.video_id}
      playerRef={playerRef}
      startSeconds={r.start_seconds ?? undefined}
      endSeconds={r.end_seconds ?? undefined}
      disableBuiltinKeys={variant === "stage"}
    />
  );

  /* 패널은 캡션·정보·이력 세 개이고, 어디에 둘지는 레이아웃이 정한다.
     워크벤치 레이아웃은 라벨링 워크벤치처럼 세 패널을 레일 탭에 두고 캡션을 첫 탭으로 둔다.
     문서 레이아웃은 캡션을 영상 바로 아래에 두어 대조하기 쉽게 하고, 정보와 이력은 곁 열에 쌓는다. */
  /*
    제목은 문서 레이아웃에서만 단다. 워크벤치 레일은 탭 이름(`문장`)이 이미 제목이라 카드 머리에 다시 쓰지 않는다.
  */
  const captionPanel = (titled: boolean) => (
    <Card className={cn("px-3", titled ? "py-2" : "py-3")}>
      {titled && <SectionHeader bare title="문장" />}
      {sentences.length === 0 ? (
        <p className="text-caption text-label-3">캡션 없음</p>
      ) : (
        <ol className="space-y-1.5">
          {sentences.map((s, i) => (
            <li key={s.id} className="flex gap-2 px-1.5 py-1 text-footnote leading-relaxed">
              <span className="mt-0.5 flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-capsule bg-fill-2 text-nano tabular-nums text-label-3">
                {i + 1}
              </span>
              <span className="min-w-0 flex-1 text-label">{s.text}</span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );

  const infoPanel = (
    <>
      <Card className="px-3 py-2">
        <SectionHeader bare title="클립" />
        {/* 설계 어휘(축·분석본)를 쓰지 않는다. 단위도 한국어로 쓴다. */}
        {d.source_start_offset !== 0 && (
          <Field label="시작 시각 보정">
            {/* 서버 값은 소수 여섯째 자리까지 온다. 영상 시각은 밀리초까지만 의미가 있어 셋째 자리로 반올림한다. */}
            <span className="tabular-nums">{d.source_start_offset > 0 ? "+" : ""}{Number(d.source_start_offset.toFixed(3))}초</span>
            <span className="ml-1 text-label-3">분석용 사본과 원본 영상의 시작 차이를 반영했습니다</span>
          </Field>
        )}
        {/* 영상에서 물려받는 메타 네 항목. 납품 메타에 실리는 값과 같다.
            빈 항목도 그린다. `—`는 빈 값으로 나간다는 뜻이고, 감추면 검토자가 값이 채워진 것으로 오해한다. */}
        {VIDEO_META_FIELDS.map((k) => {
          const val = k === "aired_at" ? airedDate(vm?.aired_at) : (vm?.[k] ?? null);
          return (
            <Field key={k} label={VIDEO_META_NAME[k]}>
              {val || <span className="text-label-4">—</span>}
            </Field>
          );
        })}
        {/* 납품 패키지의 `clip_id`·`source_video_id`와 같은 값이다. 받은 JSON과 이 화면을 대조할 때 쓴다.
            클립이 없는 영상 단위 작업은 납품이 작업 ID를 `clip_id`로 싣는다(`export_package.build_metadata`). */}
        <Field label="클립 ID"><IdChip id={r.clip_id ?? r.work_item_id} label="클립 ID" /></Field>
        <Field label="영상 ID"><IdChip id={r.video_id} label="영상 ID" /></Field>
        {/* 체크섬은 검수자에게 생소한 말이라 하는 일로 부른다. 값이 없으면 `없음`이다. */}
        <Field label="원본 파일 확인값">
          {/* 64자 해시 전체는 레일을 두 줄로 채우고 눈으로 대조하는 데 쓰이지도 않는다.
              앞부분만 보여 주고 전체는 복사와 툴팁으로 제공한다(`IdChip`). */}
          {d.source_checksum
            ? <IdChip id={d.source_checksum} label="원본 파일 확인값" chars={19} />
            : <span className="text-label-3">없음</span>}
        </Field>
      </Card>
    </>
  );

  /*
    이력은 제출과 판정을 섞어 하나의 시간순 목록으로 그린다.
    제출을 모두 그린 다음 검수를 모두 그리면 제출과 반려가 오가는 건에서 시각이 중간에 되돌아가고,
    반려가 어느 제출의 것인지 짝지을 수 없다.
    사유는 별도 줄로 내린다. 364px 레일에서 한 줄에 이어 붙이면 `truncate`가 사유를 거의 다 자른다.
  */
  /* 정렬은 시각 값으로 비교한다. 문자열 비교는 표기 형식이 같을 때만 맞다.
     서버의 일부 경로가 `+09:00` 오프셋으로 내보내면 같은 순간이 `1…`과 `0…`처럼 다른 문자열이 된다.
     그러면 순서가 뒤집혀 이력에서 시각이 되돌아간다.
     해석할 수 없는 값은 끝으로 보낸다. 앞으로 보내면 정상인 줄들의 순서까지 바뀐다. */
  const at = (iso: string) => {
    const t = Date.parse(iso);
    return Number.isNaN(t) ? Number.MAX_SAFE_INTEGER : t;
  };
  const timeline = [
    /* 처리한 사람까지 보여 준다. 이름은 서버가 싣고, 계정이 삭제됐으면 이름 없이 그린다.
       이름이 없으면 승인한 사람을 확인하려고 감사 로그로 가야 한다. */
    ...d.revisions.map((v) => ({
      at: v.submitted_at,
      key: `rev-${v.label_id}`,
      // 제출 차수는 검수 화면과 같은 말(`N차 제출`)로 쓴다.
      what: `${v.revision}차 제출`,
      who: v.submitted_by_name ?? null,
      tone: "text-label-2",
      /* 첫 제출에 AI 초안이 보관돼 있으면 초안 대비 교정한 글자 수(수정률에 들어가는 수)를 쓴다.
         `diff_chars_count`는 직전 제출과의 길이 차이라서 첫 제출에서는 payload 전체 길이가 된다.
         그 값을 쓰면 바로 위 AI 초안 줄과 다른 수가 뜬다. */
      note:
        v.edit_distance != null
          ? `AI 초안 대비 ${v.edit_distance}자 교정`
          : v.diff_chars_count > 0 ? `${v.diff_chars_count}자 교정` : null,
    })),
    ...d.reviews.map((v, i) => ({
      at: v.reviewed_at,
      key: `verdict-${i}`,
      what: VERDICT_LABEL[v.verdict] ?? v.verdict,
      who: v.reviewer_name ?? null,
      tone: v.verdict === "reject" ? "text-bad-text" : "text-ok-text",
      /* 사유 칩(서버의 코드·이름)과 보충 문장을 표시용으로 한 줄에 잇는다.
         이 문자열은 표시에만 쓰고 다시 나누지 않는다. 사용하지 않게 된 사유도 이름으로 온다. */
      note: [...(v.reasons ?? []).map((x) => x.label), (v.comment ?? "").trim()]
        .filter(Boolean).join(" · ") || null,
    })),
  ].sort((a, b) => at(a.at) - at(b.at));

  /* 이력의 첫 항목은 AI 초안이다. 서버가 작업의 첫 제출 때 보관한 초안이 첫 제출(`revisions[0]`)에 붙어 온다.
     이 항목에는 시각이 없으므로 시간순 목록에 섞지 않고 맨 앞에 따로 둔다. 임의의 시각으로 정렬하면 이력이 사실과 다르게 표시된다.
     보관되지 않은 건(보관 기능 도입 전 제출, 초안이 없던 클립)은 없다고 쓴다. */
  const aiDraft = d.revisions[0]?.ai_draft ? d.revisions[0] : null;

  /* 같은 날짜가 이어지면 날짜를 되풀이하지 않는다. 좁은 레일에서 날짜가 사유를 밀어내기 때문이다.
     표기는 `shared/lib/time.fmtDateTime`(`9월 23일 18:42`)이고, 같은 날의 다음 줄은 끝의 시각만 쓴다.
     그래서 화면마다 시각 서식이 같다. */
  const dayOf = (iso: string) => new Date(iso).toDateString();
  const stampOf = (iso: string, withDay: boolean) => {
    const full = fmtDateTime(iso);
    return withDay ? full : full.slice(full.lastIndexOf(" ") + 1);
  };

  /* 이력은 세로 시간선 하나로 AI 초안, 제출, 반려, 승인 같은 항목을 시간순으로 잇는다.
     반려는 빨강, 승인은 초록 점이고 나머지는 회색이다. 같은 날은 시각만 쓴다. */
  const dotOf = (tone: string) =>
    tone === "text-bad-text" ? "bg-bad" : tone === "text-ok-text" ? "bg-ok" : "bg-label-3";
  const historyPanel = (
    <Card className="px-4 py-3">
      {/* 초안 저장은 이력에 없다. 제출과 판정만 적힌다(수는 머리 오른쪽). */}
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h3 className="text-headline font-semibold text-label">이력</h3>
        <span className="text-footnote text-label-3">
          제출 {d.revisions.length}건 · 판정 {d.reviews.length}건
        </span>
      </div>
      {timeline.length === 0 && !aiDraft ? (
        <p className="text-footnote text-label-3">기록 없음</p>
      ) : (
        <ol>
          {aiDraft ? (
            <li className="relative pb-4 pl-5">
              {timeline.length > 0 && <span className="absolute bottom-0 left-[4px] top-3 w-px bg-separator" aria-hidden="true" />}
              <span className="absolute left-0 top-1.5 size-[9px] rounded-capsule bg-label-4" aria-hidden="true" />
              <p className="text-footnote font-semibold text-label">AI 초안</p>
              <p className="text-footnote text-label-3">{aiDraft.draft_chars}자</p>
              {/* 원문은 세 줄까지만 보여 줘 좁은 곁 열에서 이력을 밀어내지 않게 한다. 전문은 툴팁으로 본다. */}
              <p className="line-clamp-3 text-micro text-label-3" title={aiDraft.ai_draft ?? undefined}>
                {aiDraft.ai_draft}
              </p>
            </li>
          ) : (
            <li className="relative pb-4 pl-5">
              {timeline.length > 0 && <span className="absolute bottom-0 left-[4px] top-3 w-px bg-separator" aria-hidden="true" />}
              <span className="absolute left-0 top-1.5 size-[9px] rounded-capsule bg-label-4" aria-hidden="true" />
              <p className="text-footnote text-label-3">AI 초안 기록 없음</p>
            </li>
          )}
          {timeline.map((e, i) => (
            <li key={e.key} className="relative pb-4 pl-5 last:pb-0">
              {i < timeline.length - 1 && (
                <span className="absolute bottom-0 left-[4px] top-3 w-px bg-separator" aria-hidden="true" />
              )}
              <span className={cn("absolute left-0 top-1.5 size-[9px] rounded-capsule", dotOf(e.tone))} aria-hidden="true" />
              <p className="text-footnote">
                <span className="font-semibold text-label">{e.what}</span>
                {e.who && <span className="text-label-3"> · {e.who}</span>}
              </p>
              {e.note && <p className="text-footnote text-label-2">{e.note}</p>}
              <p className="font-mono text-micro tabular-nums text-label-3">
                {stampOf(e.at, i === 0 || dayOf(timeline[i - 1].at) !== dayOf(e.at))}
              </p>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
  /** 레일이 그리는 탭. 워크벤치도 캡션을 레일의 첫 탭(`문장 N`)에 둔다. */
  const railTabs: TabItem[] = tabs;
  /* 목록에 없는 탭이 선택돼 있으면 아무것도 그려지지 않으므로 첫 탭으로 되돌린다. */
  const activeTab = railTabs.some((t) => t.key === tab) ? tab : railTabs[0].key;

  const railBody = (
    <>
      <Tabs tabs={railTabs} value={activeTab} onChange={setTab} variant="underline" className="mb-2" />

      <div className="min-h-0 flex-1 space-y-3 lg:overflow-y-auto">
        {activeTab === "caption" && captionPanel(false)}
        {activeTab === "info" && infoPanel}
        {activeTab === "history" && historyPanel}
      </div>
    </>
  );


  /* ── 워크벤치 레이아웃: 무대가 영역을 채우고 오른쪽에 레일이 붙는다 ──────────── */
  if (variant === "stage") {
    return stageShell(
      <div className="flex min-h-0 flex-1 flex-col gap-4 p-4">
        {/* 영상. 제목·구간은 레일 머리에 있으므로 무대 위 표찰을 두지 않는다.
            남는 세로 공간은 영상 카드가 쓰고 영상은 그 안에서 16:9로 맞춘다. */}
        <div className="flex min-h-[30vh] flex-1 items-center justify-center overflow-hidden rounded-card bg-ws-stage">
          <div className="relative mx-auto flex aspect-video h-full max-h-full max-w-full items-center justify-center">
            {player}
          </div>
        </div>
      </div>,
      /* 레일 머리에 클립 정보(제목·상태)를, 그 아래 판단 근거(납품 불가 사유)를 둔다. */
      <div className={cn("flex min-h-0 flex-1 flex-col", busy && "opacity-60")} aria-busy={busy}>
        <div className="shrink-0 space-y-3 border-b border-separator px-5 py-4">
          <div>
            <p className="truncate text-headline font-bold text-label">{title}</p>
            {statusLine}
          </div>
          {blockerCard}
        </div>
        <div className="flex min-h-0 flex-1 flex-col px-5 pb-4 pt-2">{railBody}</div>
      </div>,
      busy,
    );
  }

  /* ── 문서 레이아웃: 앱 셸 안의 완료 상세 ─────────────────────────────────── */
  /*
    제목·납품 상태, 영상과 캡션, 곁 열의 사유·정보·이력 순서다.
    곁 열은 탭으로 나누지 않고 카드로 쌓는다. 납품 불가 사유를 곁 열 맨 위에 둔다.
  */
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* 목록으로 돌아가는 링크. 경로는 절대 경로로 쓴다.
          상대 경로 `..`는 `/p/:slug/completed`로 풀리는데, 그런 라우트가 없어 오류 화면이 뜬다. */}
      {backTo && (
        <Link
          to={backTo}
          className="mb-3 inline-flex items-center gap-1 self-start text-footnote text-label-2 transition-colors hover:text-label"
        >
          <ArrowLeft size={14} aria-hidden="true" /> 완료 클립 목록
        </Link>
      )}

      {/* 제목 옆에 납품 상태를 둔다. 구간은 원본 영상 기준이라 앞에 `원본`이라고 쓴다. */}
      <PageHeader
        title={title}
        subtitle={[
          range ? `원본 ${range}` : "영상 단위 작업",
          r.start_seconds != null && r.end_seconds != null ? `${(r.end_seconds - r.start_seconds).toFixed(1)}초` : null,
          r.work_item_id.slice(0, 8),
        ].filter(Boolean).join(" · ")}
        actions={
          <span className="flex items-center gap-1.5 text-footnote font-semibold">
            <span className={cn("size-2 rounded-capsule", r.deliverable ? "bg-ok" : "bg-bad")} aria-hidden="true" />
            <span className={r.deliverable ? "text-ok-text" : "text-bad-text"}>
              {r.deliverable ? "납품 대상" : "납품 불가"}
            </span>
          </span>
        }
      />

      <div className="flex min-h-0 flex-1 flex-col gap-5 lg:flex-row lg:items-start">
        <div className="flex min-w-0 flex-col gap-4 lg:flex-1">
          {/* 무대는 16:9로 두고 화면 높이로 제한한다. 제한하지 않으면 세로가 긴 화면에서 무대가 캡션을 밀어낸다. */}
          <div className="aspect-video max-h-[60vh] overflow-hidden rounded-card bg-ws-stage">
            {player}
          </div>
          {captionPanel(true)}
        </div>

        <div className="flex w-full min-w-0 flex-col gap-4 lg:w-[380px] lg:shrink-0">
          {blockerCard}
          {infoPanel}
          {historyPanel}
        </div>
      </div>
    </div>
  );
}
