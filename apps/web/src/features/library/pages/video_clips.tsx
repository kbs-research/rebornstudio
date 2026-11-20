/**
 * 클립 뷰어(`/p/:slug/videos/:videoId/clips`). 영상이 클립으로 어떻게 나뉘었는지 보는 화면이다.
 *
 * 판정 기능 없이 나뉜 구간과 작업 결과만 보여 준다. 왼쪽 무대에서 클립 구간만 재생하고,
 * 오른쪽 목록에 각 클립의 작업 진행 상태를 둔다. 승인된 클립은 완료 상세로 연결한다.
 *
 * 라벨러도 클립이 나뉜 방식을 봐야 하므로 모든 멤버에게 열려 있다.
 * 영상 상세와 완료 상세는 라벨러에게 막혀 있어서, 나가는 링크 두 개(돌아가기, `라벨링 결과 보기`)만 역할로 나눈다.
 * 열 수 없는 화면의 링크는 그리지 않는다.
 */
import { useQuery } from "@tanstack/react-query";
import type { MediaPlayerInstance } from "@vidstack/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Keyboard } from "lucide-react";

import { fetchVideo, fetchVideoClips, libraryKeys } from "../api";
import { VideoPlayer } from "../components/VideoPlayer";
import { IdChip } from "@/shared/ui/IdChip";
import { Chip } from "@/shared/ui/Chip";
import { ErrorState } from "@/shared/ui/ErrorState";
import { EmptyState } from "@/shared/ui/EmptyState";
import { WorkbenchSkeleton } from "@/shared/ui/PageSkeleton";
import { ShortcutsModal } from "@/shared/ui/ShortcutsModal";
import { Thumb } from "@/shared/ui/Thumb";
import {
  WorkbenchHeader,
  useVideoShortcuts,
  VIDEO_SHORTCUTS,
  useWorkbenchSurface,
} from "@/features/workbench";
import { ClipSummaryText } from "../components/ClipSummaryText";
import { cn } from "@/shared/lib/cn";
import { clipRangeFormatter } from "../lib/clip_range";
import { stageEmpty } from "../lib/video_stage";
import { clipStatusView } from "../lib/clip_status";
import { hasProjectRole, WORK_RESULT_VIEWER_ROLES } from "@/lib/roles";
import { useAuth } from "@/store/auth";
import { useSelectedProject } from "@/store/project";
import { useProjects } from "@/features/projects";

/** 클립 목록과 고른 클립의 재생 화면을 그린다. 구간은 분석이 나눈 그대로 보여 준다. */
export function VideoClipsPage() {
  const { slug, videoId } = useParams<{ slug: string; videoId: string }>();
  const { selectedProjectId: pid } = useSelectedProject();
  const playerRef = useRef<MediaPlayerInstance>(null);
  const [selected, setSelected] = useState(0);
  // 워크벤치와 같은 흑연색 면이다. 포털로 그리는 모달까지 흑연색이 되도록 문서 루트에도 속성을 붙인다.
  useWorkbenchSurface();
  const [helpOpen, setHelpOpen] = useState(false);

  /* 두 조회의 실패는 이 화면이 제자리에 표시하므로 전역 토스트를 끈다. */
  const video = useQuery({
    queryKey: libraryKeys.video(videoId),
    queryFn: () => fetchVideo(videoId),
    enabled: !!videoId,
    meta: { handled: true },
  });
  const clips = useQuery({
    queryKey: libraryKeys.videoClips(pid, videoId),
    queryFn: () => fetchVideoClips(pid, videoId),
    enabled: !!pid && !!videoId,
    meta: { handled: true },
  });
  /* 영상 상세를 열 수 있으면 참. 서버의 완료 결과 조회 권한과 같은 `WORK_RESULT_VIEWER_ROLES`로 판정한다.
     영상 상세는 `GET /videos/{id}`가 이 목록으로 막으므로, 라우트 접근 목록으로 판정하면 눌러도 403을 받는 링크가 생긴다. */
  const me = useAuth((s) => s.user);
  const projects = useProjects({ enabled: !!me });
  const project = projects.data?.find((p) => p.project_id === pid);
  const canOpenVideoDetail = hasProjectRole(me, project, WORK_RESULT_VIEWER_ROLES);

  const rows = useMemo(() => clips.data ?? [], [clips.data]);
  const cur = rows[selected];

  /** 작업 티켓이 생긴 클립 수. 헤더에 전체 클립 수와 함께 표시해 분모를 밝힌다. */
  const worked = rows.filter((c) => c.work_status).length;
  /** 목록 구간 서식. 가장 늦은 끝으로 형식을 한 번 정한다. */
  const fmtRange = clipRangeFormatter(Math.max(0, ...rows.map((c) => c.end_seconds)));

  /*
    재생 키는 워크벤치 표준(`features/workbench/lib/shortcuts.ts`)을 그대로 쓴다.
    클립 사이 이동은 목록의 행을 눌러서 한다.
  */
  const toggleHelp = useCallback(() => setHelpOpen((o) => !o), []);
  useVideoShortcuts(playerRef, { onToggleHelp: toggleHelp, maxRate: 4 });

  /* 고른 행이 화면 밖이면 따라 스크롤한다. `nearest`라 이미 보이면 움직이지 않는다. */
  useEffect(() => {
    document
      .querySelector('[data-clip-row][aria-current="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  /* 본체가 `fixed inset-0 z-workbench` 오버레이라서 로딩 골격도 같은 층에 그려 데이터가 올 때 화면이 바뀌지 않게 한다. */
  if (video.isLoading || clips.isLoading) return <WorkbenchSkeleton />;
  // 둘 중 하나라도 실패하면 빈 목록 대신 오류를 그린다. 아래 빈 상태는 클립이 없다는 뜻이다.
  // 다시 해도 같은 실패(403·404)에는 `다시 시도`를 주지 않는다(`ErrorState`의 `error`).
  if (video.isError || clips.isError)
    return (
      <ErrorState
        message="클립 조회 실패"
        error={video.error ?? clips.error}
        onRetry={() => {
          void video.refetch();
          void clips.refetch();
        }}
      />
    );

  return (
    /*
      라벨링 워크벤치와 같은 전체 화면 오버레이(`fixed inset-0 z-workbench`)다.
      앱 셸 안에 그리면 셸 머리와 워크벤치 헤더가 겹쳐 머리가 둘이 된다.
    */
    <div data-surface="workbench" className="fixed inset-0 z-workbench flex flex-col bg-canvas text-label">
      <WorkbenchHeader
        backTo={canOpenVideoDetail ? `/p/${slug}/videos/${videoId}` : "/my/work"}
        backLabel={canOpenVideoDetail ? "영상 상세" : "내 작업"}
        badge="클립"
        title={video.data?.title ?? undefined}
        /*
          가운데 묶음은 다른 워크벤치와 같은 슬롯(`stats`)에 지금 위치와 작업이 생긴 클립 수를 둔다.
          단위는 클립이 `개`, 작업이 `건`이다.
        */
        stats={
          rows.length > 0
            ? [
                { key: "pos", label: "클립", value: `${selected + 1} / ${rows.length}`, title: "고른 클립의 순번" },
                { key: "work", label: "작업", value: `${worked}건`, title: "작업이 생긴 클립 수" },
              ]
            : undefined
        }
        actions={[
          {
            key: "shortcuts",
            icon: <Keyboard size={14} />,
            label: "단축키",
            onClick: () => setHelpOpen(true),
          },
        ]}
      />

      <ShortcutsModal
        open={helpOpen}
        onClose={() => setHelpOpen(false)}
        groups={[{ title: "재생", items: VIDEO_SHORTCUTS }]}
      />

      {rows.length === 0 ? (
        /*
          클립이 없는 이유는 영상 상세의 빈 상태와 같은 규칙(`video_stage.stageEmpty`)으로 쓴다.
          이 화면에는 준비 진행이 없으므로 결과 한 줄만 싣고, 다음 할 일은 영상 상세에서 안내한다.
        */
        <EmptyState
          message="클립 없음"
          description={(video.data && stageEmpty(video.data, false)?.text) || undefined}
        />
      ) : (
        <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
          {/*
            무대. 고른 클립 구간만 재생한다. `key`로 다시 마운트해 새 구간이 적용되게 한다.
          */}
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <div className="flex min-h-0 flex-1 items-center justify-center bg-ws-stage p-2">
              {cur && (
                <div className="aspect-video max-h-full w-full max-w-full overflow-hidden rounded-card">
                  {/*
                    재생기 내장 키를 끈다. 내장 키(`j` 10초 뒤로, `k` 재생·일시정지)가 표준 재생 키(`useVideoShortcuts`)와
                    겹쳐 재생기를 클릭한 직후에는 한 번 누르면 두 번 움직인다.
                  */}
                  <VideoPlayer
                    key={cur.clip_id}
                    videoId={cur.video_id}
                    playerRef={playerRef}
                    startSeconds={cur.start_seconds}
                    endSeconds={cur.end_seconds}
                    autoPlay
                    maxPlaybackRate={4}
                    disableBuiltinKeys
                    hideTitle
                  />
                </div>
              )}
            </div>
          </div>

          {/* 목록. 클립을 시간 순서대로 세로로 쌓는다.
              1024px 미만에서는 무대와 목록이 둘 다 `flex-1`로 높이를 나누고 각자 스크롤한다.
              1024px 이상에서는 `lg:w-[26rem] lg:flex-none`으로 다른 워크벤치 레일과 같은 폭의 고정 레일이 된다.
              `shrink-0`을 쓰면 1024px 미만에서 목록이 콘텐츠 높이를 그대로 차지해 무대 높이가 0이 된다. */}
          <ol className="min-h-0 w-full flex-1 overflow-y-auto border-t border-separator bg-panel lg:w-[26rem] lg:flex-none lg:border-l lg:border-t-0">
            {rows.map((c, i) => {
              const w = clipStatusView(c);
              return (
                /*
                  행의 배경(선택·hover)은 `li`에 두어 행 버튼 밖의 링크도 같은 행으로 보이게 한다.
                  행 버튼 안에 링크를 넣으면 HTML 규칙에 어긋나고 스크린리더가 겹친 컨트롤을 제대로 읽지 못한다.
                */
                /* 고른 행은 호박색 왼쪽 줄과 옅은 호박색 배경이다. 이 화면의 호박색은 고른 행과 재생 위치에만 쓴다. */
                <li
                  key={c.clip_id}
                  className={cn(
                    "relative border-b border-separator transition-colors",
                    i === selected ? "bg-amber-soft" : "hover:bg-fill-3",
                  )}
                >
                  {i === selected && (
                    <span className="absolute inset-y-0 left-0 w-[3px] bg-amber" aria-hidden="true" />
                  )}
                  <button
                    type="button"
                    data-clip-row
                    onClick={() => setSelected(i)}
                    aria-current={i === selected ? "true" : undefined}
                    className="flex w-full gap-3 px-4 py-2.5 text-left"
                  >
                    <Thumb
                      seed={c.clip_id}
                      src={c.scene_thumbnail_url ?? undefined}
                      className="h-10 w-16 shrink-0"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-caption font-semibold tabular-nums text-label-3">
                          {i + 1}
                        </span>
                        <span className="font-mono text-caption tabular-nums text-label-2">
                          {fmtRange(c.start_seconds, c.end_seconds)}
                        </span>
                        {/*
                          작업 상태가 있으면 작업 상태를, 없으면 클립 상태를 표시한다(`clipStatusView`).
                          둘 다 해당하지 않으면 칩을 그리지 않는다.
                        */}
                        {w && <Chip variant="dot" tone={w.tone} className="ml-auto">{w.text}</Chip>}
                      </div>
                      <p className="mt-0.5 line-clamp-2 text-caption text-label-2">
                        <ClipSummaryText clip={c} />
                      </p>
                    </div>
                  </button>
                  {/* 행 아래 줄. 클립 ID는 납품 패키지의 `clip_id`와 같은 값이고, 행 버튼 안에 버튼을 둘 수 없어 이 줄에 둔다.
                      승인된 클립은 완료 상세로 연결한다. 최종 캡션·구간·이력·납품 가능 여부는 완료 상세에 있다.
                      완료 상세를 열 수 있는 사람에게만 링크를 그린다(`canOpenVideoDetail`, 돌아가기 링크와 같은 판정).
                      썸네일 폭만큼 들여 글자 열에 맞춘다. */}
                  <div className="-mt-1 flex items-center gap-3 pb-2.5 pl-23">
                    <IdChip id={c.clip_id} label="클립 ID" tabIndex={i === selected ? 0 : -1} className="-ml-1" />
                    {canOpenVideoDetail && c.work_status === "approved" && c.work_item_id && (
                      <Link
                        to={`/p/${slug}/completed/${c.work_item_id}`}
                        /* 행 안의 링크와 ID 칩은 고른 행에서만 탭으로 이동한다. 행마다 탭 정지가 셋이면
                           클립이 많은 영상에서 탭 정지 대부분이 이 목록이 된다. */
                        tabIndex={i === selected ? 0 : -1}
                        className="inline-block text-caption font-medium text-accent hover:underline"
                      >
                        라벨링 결과 보기 →
                      </Link>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        </div>
      )}
    </div>
  );
}
