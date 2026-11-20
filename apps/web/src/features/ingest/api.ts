/**
 * 영상 등록·준비(등록 시트·두 등록 엔진·등록 Job·등록 콘솔·영상 상세의 준비 카드)의 서버 호출과 쿼리 키.
 *
 * 화면 코드에는 경로 문자열을 두지 않는다. 서버 경로가 바뀌면 이 파일만 고친다.
 * 주의: 쿼리 키의 배열 값은 다른 화면의 캐시·무효화 키와 같다. 콘텐츠 목록·영상 상세가 같은 배열을
 * 읽고 무효화하므로, 값을 바꾸면 오류 없이 무효화가 다른 키를 가리켜 그 화면이 갱신되지 않는다.
 * 멀티파트 업로드 순서(영상 생성, 멀티파트 시작, 파트 서명, 확정)와 재시도 판단은 `components/UploadQueue.tsx`에 있다.
 * 이 파일의 함수는 요청을 하나씩만 보낸다.
 */
import type { components } from "@reborn/contracts";

import { api, apiFile, type ApiFile } from "@/shared/api/client";

import { dispatchBody } from "./lib/dispatch_body";
import type { PrepStage } from "./lib/vocab";

/** 영상 한 개. */
export type VideoDTO = components["schemas"]["VideoDTO"];
/** 분석 잡 한 건. `work_item_id`에는 영상 id가 들어 있다. */
export type MaiuJobDTO = components["schemas"]["MaiuJobDTO"];
/** 등록 콘솔 상태별 영상 수. */
export type VideoBucketCounts = components["schemas"]["VideoBucketCounts"];
/** 분석 요청 결과. 만든 것·보류·실패·이미 요청됨으로 나뉜다. */
export type MaiuDispatchResult = components["schemas"]["MaiuDispatchResult"];
/** 등록 Job 상세. 목록 행에 없는 수행자와 이슈 분해가 더 있다. */
export type IngestJobDetail = components["schemas"]["IngestJobDetailDTO"];
/** 등록 Job 목록의 한 행. */
export type IngestBatchDTO = components["schemas"]["IngestBatchDTO"];
/** 입력한 오브젝트 키 등록 결과. */
export type StorageRegisterResult = components["schemas"]["StorageRegisterResult"];
/** 폴더째 등록 작업 접수 결과. */
export type StoragePrefixImportResult = components["schemas"]["StoragePrefixImportResult"];
/** 영상 한 개의 저장 파일 현황 한 줄. */

/** 서버가 준 업로드 조건. 시트가 열릴 때 한 번 받아 업로드 엔진에 넘긴다. */
export interface UploadLimits {
  max_bytes: number;
  video_extensions: string[];
}

/** 영상 레코드 생성 응답. */
export interface VideoCreated { video_id: string }

/** 멀티파트 업로드 시작 응답. */
export interface MultipartInit {
  upload_id: string;
  s3_key: string;
  part_size: number;
  max_bytes: number;
  /** 이미 올라간 파트 번호. 이어받기에 쓴다. 새 업로드면 빈 배열이다. */
  uploaded_parts: number[];
}

/** 파트 번호별 presigned URL. */
export interface PartUrls {
  urls: Record<number, string>;
  expires_in: number;
}

/** 확정 응답 중 화면이 쓰는 필드. 중복 표시에 쓴다. */
export interface UploadCompleted {
  metadata?: { duplicate_of?: string; duplicate_title?: string } | null;
}

/** 조건으로 푼 영상 id 목록. 서버 상한에 걸리면 `truncated`다. */
export type VideoSelection = { video_ids: string[]; total: number; truncated: boolean };

/** 조건 선택의 조건. 조건 밖 값은 서버가 무시한다. */
export type VideoSelectCondition = { prep?: PrepStage; blocked_stage?: string };

/** 목록 커서. 앞 페이지 마지막 행의 시각과 id. */
export type Cursor = { at: string; id: string };

/** 프로젝트 id. 아직 고르지 않았으면 비어 있다. */
type Pid = string | null | undefined;

/**
 * 영상 등록·준비 화면이 쓰는 쿼리 키 팩토리.
 *
 * `…All`은 무효화에 쓰는 앞부분이다. 영상 목록 앞부분(`["videos"]`)과 상태별 수(`["video-buckets"]`)는
 * 콘텐츠 목록도 같은 앞부분으로 캐시하므로 등록·조치 뒤 두 화면이 함께 다시 받는다.
 */
export const ingestKeys = {
  /** 업로드 크기 상한과 받는 확장자. */
  uploadLimits: (pid: Pid) => ["upload-limits", pid] as const,
  batchesAll: () => ["ingest-batches"] as const,
  /** 등록 Job 목록. */
  batches: (pid: Pid) => ["ingest-batches", pid] as const,
  jobAll: () => ["ingest-job"] as const,
  /** 등록 Job 상세. */
  job: (batch: string | null) => ["ingest-job", batch] as const,
  videoBucketsAll: () => ["video-buckets"] as const,
  /** 등록 콘솔 상태별 영상 수. */
  videoBuckets: (pid: Pid) => ["video-buckets", pid] as const,
  videosAll: () => ["videos"] as const,
  /** 한 프로젝트의 영상 목록 전부. 업로드 뒤 무효화에 쓴다. */
  projectVideos: (pid: Pid) => ["videos", pid] as const,
  batchVideosAll: () => ["videos-batch"] as const,
  /** 한 프로젝트의 Job별 영상 목록 전부. 업로드 뒤 무효화에 쓴다. */
  projectBatchVideos: (pid: Pid) => ["videos-batch", pid] as const,
  /** Job 한 개의 영상 목록. */
  batchVideos: (pid: Pid, batch: string | null) => ["videos-batch", pid, batch] as const,
  prepVideosAll: () => ["videos-prep"] as const,
  /** 준비 단계 하나의 영상 목록. */
  prepVideos: (pid: Pid, batch: string | null, stage: PrepStage | undefined) =>
    ["videos-prep", pid, batch, stage] as const,
  maiuJobsAll: () => ["maiu-jobs"] as const,
  /** 분석 잡 목록. */
  maiuJobs: (pid: Pid) => ["maiu-jobs", pid] as const,
  /** 분석 단가. 영상 등록 화면의 예상 비용이 쓴다. */
  maiuCostStats: (pid: Pid) => ["maiu-cost-stats", pid] as const,
  /** 영상 한 개의 준비 타임라인. 영상 상세가 조회하고 분석본 정리가 무효화한다. */
  prepTimeline: (videoId: string | undefined) => ["prep-timeline", videoId] as const,
};

/** 값이 있는 파라미터만 붙인 쿼리 경로를 만든다. 붙이는 순서는 `p`의 키 순서다. */
function withQuery(path: string, p: Record<string, string | number | undefined>): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(p)) if (v != null) qs.set(k, String(v));
  const s = qs.toString();
  return s ? `${path}?${s}` : path;
}

/** 버킷 오브젝트 등록 요청 본문. 등록 메타의 빈 값은 싣지 않는다. */
export type StorageRegisterBody = {
  bucket?: string;
  broadcaster?: string;
  program_name?: string;
  transcode_profile: "auto" | "always" | "never";
  upload_batch_id: string;
};

/**
 * 입력한 오브젝트 키를 영상으로 등록한다. 서버는 요청당 200건까지 받으므로 나눠 보내는 것은 부르는 쪽이 한다.
 * 저장소에 없는 영상 키가 있으면 서버가 아무것도 등록하지 않고 `storage_key_not_found:<키>`로 거절한다.
 */
export function registerStorageKeys(
  pid: string | null,
  body: StorageRegisterBody & { keys: string[] },
): Promise<StorageRegisterResult> {
  return api<StorageRegisterResult>(`/projects/${pid}/storage/register`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** 폴더째 등록 작업을 서버에 맡긴다. 등록은 서버가 비동기로 하고 응답은 접수 결과다. */
export function registerStoragePrefix(
  pid: string | null,
  body: StorageRegisterBody & { prefix: string },
): Promise<StoragePrefixImportResult> {
  return api<StoragePrefixImportResult>(`/projects/${pid}/storage/register-prefix`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** 업로드 크기 상한과 받는 확장자를 받는다. */
export function fetchUploadLimits(pid: string): Promise<UploadLimits> {
  return api<UploadLimits>(`/projects/${pid}/videos/upload-limits`);
}

/** 업로드할 영상 레코드의 생성 요청 본문. */
export type VideoCreateBody = {
  title: string;
  transcode_profile: "auto" | "always" | "never";
  broadcaster?: string;
  program_name?: string;
  upload_batch_id: string;
  file_size_bytes: number;
  duration_seconds?: number;
  /** 고른 파일의 원래 이름. 서버가 경로를 떼고 저장하며 납품 패키지의 `sources.csv`에 실린다. */
  original_filename?: string;
};

/** 업로드할 영상 레코드를 만든다. */
export function createVideo(pid: string, body: VideoCreateBody): Promise<VideoCreated> {
  return api<VideoCreated>(`/projects/${pid}/videos`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** 멀티파트 업로드를 시작한다. 이어받기면 이미 올라간 파트 번호가 온다. */
export function startMultipartUpload(videoId: string): Promise<MultipartInit> {
  return api<MultipartInit>(`/videos/${videoId}/upload/multipart`, {
    method: "POST",
  });
}

/** 파트 번호 목록의 presigned URL을 받는다. */
export function signUploadParts(videoId: string, body: { upload_id: string; part_numbers: number[] }): Promise<PartUrls> {
  return api<PartUrls>(`/videos/${videoId}/upload/parts`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** 업로드를 확정한다. 서버가 크기와 내용 해시를 대조한다. */
export function completeUpload(
  videoId: string,
  body: { upload_id: string; expected_size_bytes: number; content_hash: string },
): Promise<UploadCompleted> {
  return api<UploadCompleted>(`/videos/${videoId}/upload/complete`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** 영상 레코드를 지운다. 완료되지 않은 멀티파트도 서버가 함께 정리한다. */
export function deleteVideo(videoId: string): Promise<unknown> {
  return api(`/videos/${videoId}`, { method: "DELETE" });
}

/** 등록 Job 이름을 바꾼다. */
export function renameIngestBatch(batchId: string, label: string): Promise<IngestBatchDTO> {
  return api<IngestBatchDTO>(`/ingest-batches/${batchId}`, {
    method: "PATCH",
    body: JSON.stringify({ label }),
  });
}

/** 메타 일괄 입력 미리 보기 결과. 행마다 찾은 영상, 바뀌는 값, 오류가 있다. */
export type MetaImportPreview = components["schemas"]["BulkMetaPreview"];
/** 미리 보기의 행 하나. */
export type MetaImportRow = components["schemas"]["BulkMetaPreviewRow"];
/** 반영 요청의 영상 하나. */
export type MetaImportItem = components["schemas"]["BulkMetaApplyItem"];
/** 메타 일괄 입력 반영 결과. */
export type MetaImportResult = components["schemas"]["BulkMetaApplyResult"];

/** 묶음의 모든 영상과 지금 메타를 채운 양식(.xlsx)을 받는다. 파일 이름은 서버가 정한다. */
export function downloadMetaTemplate(batchId: string): Promise<ApiFile> {
  return apiFile(`/ingest-batches/${batchId}/meta-template`);
}

/** 채운 양식을 올려 미리 보기를 받는다. 서버는 아무것도 저장하지 않는다. */
export function previewMetaImport(batchId: string, file: File): Promise<MetaImportPreview> {
  const form = new FormData();
  form.append("file", file);
  return api<MetaImportPreview>(`/ingest-batches/${batchId}/meta-import/preview`, {
    method: "POST",
    body: form,
  });
}

/** 미리 보기에서 확인한 변경을 반영한다. 서버가 값을 다시 검사하고 하나라도 실패하면 아무것도 바꾸지 않는다. */
export function applyMetaImport(batchId: string, items: MetaImportItem[]): Promise<MetaImportResult> {
  return api<MetaImportResult>(`/ingest-batches/${batchId}/meta-import/apply`, {
    method: "POST",
    body: JSON.stringify({ items }),
  });
}

/** 등록 Job 목록 한 페이지를 받는다. `cursor`가 있으면 그보다 앞선 Job이다. */
export function fetchIngestBatches(pid: string | null, limit: number, cursor: Cursor | undefined): Promise<IngestBatchDTO[]> {
  return api<IngestBatchDTO[]>(
    `/projects/${pid}/ingest-batches?limit=${limit}` +
      (cursor ? `&before=${encodeURIComponent(cursor.at)}&before_id=${cursor.id}` : ""),
  );
}

/** 등록 Job 상세를 받는다. */
export function fetchIngestJob(batch: string | null): Promise<IngestJobDetail> {
  return api<IngestJobDetail>(`/ingest-batches/${batch}`);
}

/** 등록 콘솔 상태별 영상 수를 받는다. */
export function fetchVideoBuckets(pid: string | null): Promise<VideoBucketCounts> {
  return api<VideoBucketCounts>(`/projects/${pid}/videos/buckets`);
}

/** 영상 목록을 받는다. 빈 값은 쿼리에서 빠지고 나머지는 `params`의 순서대로 붙는다. */
export function fetchProjectVideos(
  pid: string | null,
  params: Record<string, string | number | undefined>,
): Promise<VideoDTO[]> {
  return api<VideoDTO[]>(withQuery(`/projects/${pid}/videos`, params));
}

/** 분석 잡 목록을 받는다. 관리자만 받을 수 있다. */
export function fetchMaiuJobs(pid: string | null): Promise<MaiuJobDTO[]> {
  return api<MaiuJobDTO[]>(`/projects/${pid}/maiu/jobs`);
}

/** 분석 단가를 받는다. 관리자만 받을 수 있다. */
export function fetchMaiuCostStats(pid: string | null): Promise<{ cost_per_hour_krw: number }> {
  return api<{ cost_per_hour_krw: number }>(`/projects/${pid}/maiu/cost-stats`);
}

/** 고른 영상의 분석을 요청한다. 과금이 일어난다. */
export function dispatchAnalysis(pid: string | null, videoIds: readonly string[]): Promise<MaiuDispatchResult> {
  return api<MaiuDispatchResult>(`/projects/${pid}/maiu/dispatch`, {
    method: "POST",
    body: JSON.stringify(dispatchBody(videoIds)),
  });
}

/**
 * 재요청 결과(`maiu/retry`). 영상마다 서버가 처리 방식을 정한다.
 * `requeued`는 분석 재요청, `restarted`는 등록부터 다시 시작, `resummarized`는 장면 요약만 다시 요청한 수다.
 * `resummarize_failed`는 요약 요청을 분석 서비스가 받지 않은 수이고, 사유는 영상의 준비 진행에 남는다.
 */
export type RetryResult = {
  requeued: number;
  restarted: number;
  resummarized: number;
  resummarize_failed: number;
  skipped: number;
};

/**
 * 분석에서 실패한 잡을 다시 요청한다. 등록된 자산이 없으면 서버가 등록부터 한다.
 * 장면 요약을 받지 못한 영상은 서버가 요약만 다시 요청한다.
 */
export function retryAnalysis(pid: string | null, videoIds: string[]): Promise<RetryResult> {
  return api<RetryResult>(`/projects/${pid}/maiu/retry`, {
    method: "POST", body: JSON.stringify({ video_ids: videoIds }),
  });
}

/** 영상 한 개의 규격 검사를 되돌려 다시 검사하게 한다. */
export function resetProbe(pid: string | null, videoId: string): Promise<unknown> {
  return api(`/projects/${pid}/videos/${videoId}/probe/reset`, { method: "POST" });
}

/** 영상 한 개의 멈춘 변환을 다시 시도한다. */
export function retryTranscode(pid: string | null, videoId: string): Promise<unknown> {
  return api(`/projects/${pid}/videos/${videoId}/transcode/retry`, { method: "POST" });
}

/** 조건에 맞는 영상 전체의 id를 받는다. `batch`가 있으면 그 Job 안에서만 찾는다. */
export function selectVideos(
  pid: string | null,
  body: VideoSelectCondition & { batch?: string },
): Promise<VideoSelection> {
  return api<VideoSelection>(`/projects/${pid}/videos/select`, { method: "POST", body: JSON.stringify(body) });
}

