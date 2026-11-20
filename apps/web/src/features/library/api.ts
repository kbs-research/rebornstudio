/**
 * 콘텐츠(영상·클립 카탈로그)의 서버 호출과 쿼리 키.
 *
 * 화면과 부품은 경로 문자열을 직접 두지 않고 이 파일의 함수를 부른다. 서버 경로가 바뀌면 이 파일만 고친다.
 * 영상 상세가 부르는 분석 재요청도 영상 한 건을 다루는 동작이라 이 파일에 둔다.
 * 주의: 영상 한 건(`video`)·재생 주소·영상 목록(`videos`)·클립 조회(`clip`) 키는 등록·작업 단계·워크벤치·
 * 콘텐츠 화면이 같은 배열로 읽고 무효화한다. 키 값을 바꾸면 무효화가 다른 화면의 캐시에 닿지 않아 그 화면에 이전 값이 남는다.
 */
import type { components } from "@reborn/contracts";

import type { RetryResult } from "@/features/ingest";

import {
  api,
  type ClipCountDTO,
  type ClipDTO,
  type VideoBucketCounts,
  type VideoDTO,
} from "@/shared/api/client";

/** 재생 주소 응답. presigned URL이라 한 시간 뒤 만료된다. */
export type VideoSourceDTO = components["schemas"]["VideoSourceURLResponse"];
/** 영상 한 건의 준비 타임라인. */
export type PrepTimelineDTO = components["schemas"]["PrepTimelineDTO"];

/** 영상 목록의 필터 조건. 빈 값은 요청에 싣지 않는다. */
export type ContentVideoFilter = { q: string; prep: string | null };
/** 영상 목록의 커서. 마지막 행의 만든 시각과 ID다. */
export type ContentVideoCursor = { at: string; id: string };
/** 클립 목록의 필터 조건. 빈 값은 요청에 싣지 않는다. */
export type ProjectClipFilter = { q: string };

/**
 * 콘텐츠의 쿼리 키 팩토리.
 *
 * `…All`은 키의 앞부분이라 무효화에 쓴다. 그 아래 조건별 목록을 모두 다시 받는다.
 */
export const libraryKeys = {
  /** 영상 한 건. 영상 상세·클립 보기·플레이어가 같은 캐시를 쓴다. */
  video: (videoId: string | undefined) => ["video", videoId] as const,
  /** 영상 재생 주소. 영상 한 건 키 아래에 둔다. */
  videoSource: (videoId: string | undefined) => ["video", videoId, "source-url"] as const,
  /** 영상 목록 전부. 등록 화면의 버킷별 목록도 이 앞부분 아래에 있다. */
  videosAll: () => ["videos"] as const,
  /** 콘텐츠 영상 탭의 목록. 조건이 키에 든다. */
  contentVideos: (pid: string | undefined, f: ContentVideoFilter) =>
    ["videos", pid, "content", f.q, f.prep] as const,
  /** 콘텐츠 영상 탭의 위치별 수. 끝의 `undefined`도 키 값이다. 등록 화면의 `["video-buckets", pid]`와 캐시가 다르다. */
  contentBuckets: (pid: string | undefined) => ["video-buckets", pid, undefined] as const,
  /** 클립 목록 전부. */
  clipsAll: () => ["clips"] as const,
  /** 콘텐츠 클립 탭의 목록. 조건이 키에 든다. */
  projectClips: (pid: string | null, f: ProjectClipFilter) => ["clips", pid, f.q] as const,
  /** 콘텐츠 클립 탭의 분모. 목록과 같은 조건이 키에 든다. 목록 키 아래에 두어 함께 무효화된다. */
  projectClipCount: (pid: string | null, f: ProjectClipFilter) =>
    ["clips", pid, f.q, "count"] as const,
  /** 클립 한 건 조회 전부. 영상 메타가 바뀌면 클립이 물려받은 값도 바뀐다. */
  clipAll: () => ["clip"] as const,
  /** 클립 한 건. 워크벤치·검수·대기열 행이 같은 캐시를 쓴다. */
  clip: (pid: string | null | undefined, clipId: string | null | undefined) => ["clip", pid, clipId] as const,
  /** 영상 한 건의 클립 목록. */
  videoClips: (pid: string | null | undefined, videoId: string | undefined) =>
    ["video-clips", pid, videoId] as const,
  /** 영상 한 건의 준비 타임라인. 영상 상세의 분석본 정리도 이 키를 무효화한다. */
  prepTimeline: (videoId: string | undefined) => ["prep-timeline", videoId] as const,
};

/** 영상 한 건을 받는다. */
export const fetchVideo = (videoId: string | undefined) => api<VideoDTO>(`/videos/${videoId}`);

/** 클립 한 건을 받는다. 장면 요약·키워드·화자·구간·영상 메타가 함께 온다. */
export const fetchClip = (pid: string | null | undefined, clipId: string | null | undefined) =>
  api<ClipDTO>(`/projects/${pid}/clips/${clipId}`);

/** 영상 재생 주소를 받는다. 서명에 시각이 들어가 부를 때마다 다른 주소를 받는다. */
export const fetchVideoSource = (videoId: string) => api<VideoSourceDTO>(`/videos/${videoId}/source-url`);

/** 영상 한 건의 클립 목록을 시작 시각 순으로 받는다. */
export const fetchVideoClips = (pid: string | null | undefined, videoId: string | undefined) =>
  api<ClipDTO[]>(`/projects/${pid}/videos/${videoId}/clips`);

/** 영상 한 건의 준비 타임라인을 받는다. */
export const fetchPrepTimeline = (videoId: string | undefined) =>
  api<PrepTimelineDTO>(`/videos/${videoId}/prep-timeline`);

/** 영상 메타 중 바뀐 필드만 보낸다. `null`은 그 값을 지운다. 이 영상의 클립이 같은 값을 물려받는다. */
export const updateVideoMeta = (videoId: string | undefined, body: Record<string, string | null>) =>
  api<VideoDTO>(`/videos/${videoId}`, { method: "PATCH", body: JSON.stringify(body) });

/** 영상을 지운다. */
export const deleteVideo = (videoId: string | undefined) => api<void>(`/videos/${videoId}`, { method: "DELETE" });

/** 멈춘 규격 검사를 대기로 되돌린다. 다음 배치가 다시 검사한다. */
export const resetVideoProbe = (pid: string, videoId: string | undefined) =>
  api<VideoDTO>(`/projects/${pid}/videos/${videoId}/probe/reset`, { method: "POST" });

/** 재시도 상한에 걸려 멈춘 변환을 다시 요청한다. */
export const retryVideoTranscode = (pid: string, videoId: string | undefined) =>
  api<void>(`/projects/${pid}/videos/${videoId}/transcode/retry`, { method: "POST" });

/** 대표 이미지·클립 썸네일의 실패 표시를 지운다. 다음 주기의 워커가 다시 만든다. */
export const retryVideoThumbnails = (pid: string, videoId: string | undefined) =>
  api<void>(`/projects/${pid}/videos/${videoId}/thumbnails/retry`, { method: "POST" });

/** 영상 한 건의 분석본을 정리한다. 정리할 수 없으면 서버가 409와 사유 문장을 돌려준다. */
export const purgeVideoAnalysis = (videoId: string | undefined) =>
  api<{ freed_bytes: number }>(`/videos/${videoId}/artifacts/analysis`, { method: "DELETE" });

/** 실패한 장면 분할을 다시 실행할 수 있게 한다. 클립이 이미 있으면 서버가 409로 막는다. */
export const retryVideoSceneSplit = (pid: string, videoId: string | undefined) =>
  api<void>(`/projects/${pid}/videos/${videoId}/scene-split/retry`, { method: "POST" });

/** 실패한 비식별화를 다시 요청한다. 완료된 비식별화는 서버가 409로 막는다. */
export const retryVideoDeident = (pid: string, videoId: string | undefined) =>
  api<void>(`/projects/${pid}/videos/${videoId}/deident/retry`, { method: "POST" });

/**
 * 비식별화를 끈 프로젝트에서 실패로 확정된 비식별화를 건너뛰고 재생본 단계로 넘긴다.
 * 켠 프로젝트와 실패가 아닌 영상은 서버가 409로 막는다.
 */
export const skipVideoDeident = (pid: string, videoId: string | undefined) =>
  api<void>(`/projects/${pid}/videos/${videoId}/deident/skip`, { method: "POST" });

/**
 * 영상 한 건의 실패한 분석을 다시 요청한다. 자산이 있으면 재등록 없이 분석만 다시 건다.
 * 장면 요약을 받지 못한 영상은 서버가 요약만 다시 요청한다(`resummarized`).
 */
export const retryVideoAnalysis = (pid: string, videoId: string | undefined) =>
  api<RetryResult>(`/projects/${pid}/maiu/retry`, {
    method: "POST",
    body: JSON.stringify({ video_ids: [videoId] }),
  });

/** 사람이 정한 구간으로 클립 하나를 만든다. 관리자만 받는다. */
export const addManualClip = (pid: string, videoId: string, startSeconds: number, endSeconds: number) =>
  api<ClipDTO>(`/projects/${pid}/videos/${videoId}/clips`, {
    method: "POST",
    body: JSON.stringify({ start_seconds: startSeconds, end_seconds: endSeconds }),
  });

/** 콘텐츠 영상 탭의 한 페이지를 받는다. 검색·필터는 서버가 적용한다. */
export function fetchContentVideos(
  pid: string | undefined,
  limit: number,
  f: ContentVideoFilter,
  cursor: ContentVideoCursor | undefined,
): Promise<VideoDTO[]> {
  const sp = new URLSearchParams({ limit: String(limit) });
  if (f.q) sp.set("q", f.q);
  if (f.prep) sp.set("prep", f.prep);
  if (cursor) {
    sp.set("before", cursor.at);
    sp.set("before_id", cursor.id);
  }
  return api<VideoDTO[]>(`/projects/${pid}/videos?${sp}`);
}

/** 영상 위치별 수를 받는다. 문제 영상 줄과 `전체` 수가 이 응답을 쓴다. */
export const fetchVideoBuckets = (pid: string | undefined) =>
  api<VideoBucketCounts>(`/projects/${pid}/videos/buckets`);

/** 콘텐츠 클립 탭의 분모를 받는다. 목록과 같은 조건으로 서버가 센다. */
export function fetchProjectClipCount(pid: string | null, f: ProjectClipFilter): Promise<ClipCountDTO> {
  const sp = new URLSearchParams();
  if (f.q) sp.set("q", f.q);
  return api<ClipCountDTO>(`/projects/${pid}/clips/count?${sp}`);
}

/** 콘텐츠 클립 탭의 한 페이지를 받는다. 검색은 서버가 적용하고, 커서의 키는 그대로 싣는다. */
export function fetchProjectClips(
  pid: string | null,
  limit: number,
  f: ProjectClipFilter,
  cursor: Record<string, string> | undefined,
): Promise<ClipDTO[]> {
  const sp = new URLSearchParams({ limit: String(limit) });
  if (f.q) sp.set("q", f.q);
  if (cursor) for (const [k, v] of Object.entries(cursor)) sp.set(k, v);
  return api<ClipDTO[]>(`/projects/${pid}/clips?${sp}`);
}
