/**
 * 로컬 파일을 대량으로 올리는 업로드 엔진. 등록 시트의 '내 컴퓨터' 탭이 쓴다.
 *
 * 한 번에 수십 건을 올리는 대량 등록을 전제로 한다. 드롭존, 큐, presign 업로드, 확정 순서로 동작한다.
 * 설정과 메타는 등록 시트에 있고 버킷 경로 등록과 함께 쓴다.
 *
 * 제목을 비운 채 올리면 파일 이름에서 확장자를 뗀 값으로 등록한다. 버킷 경로 등록도 같은 규칙이다.
 * 방송사·방영일은 파일 이름에서 파싱하지 않는다. 명명 규칙이 일정하지 않아 틀린 메타가 알림 없이 들어간다.
 *
 * 파일 길이는 브라우저가 직접 읽는다(loadedmetadata). 그래서 시트의 `총 길이`와 등록 직후 목록이
 * 분석을 기다리지 않고 길이를 보여 준다. 읽지 못한 파일은 합계에서 빠지고, 빠졌다고 표시한다.
 *
 * 파일은 받기 전에 거른다. 영상 판정은 확장자로 하고(`videoFiles`), 크기는 서버가 알려 준 상한(`upload-limits`)으로 거른다.
 * 받은 뒤에 거르면 MIME이 빈 MXF가 알림 없이 버려지고, 크기 초과가 영상 레코드를 만든 뒤에 걸려 고아 레코드가 남는다.
 * 토스트는 몇 초 뒤 사라지므로 거른 파일과 큐에 이미 있어 건너뛴 파일은 드롭존 아래 줄에 종류별로 세어 남긴다.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type DragEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronDown, CircleCheck, CircleStop, Copy, RotateCw, Trash2, Upload, X } from "lucide-react";

import { Button } from "@/shared/ui/Button";
import { Input } from "@/shared/ui/Input";
import { useToast } from "@/shared/ui/Toast";
import { type ApiError } from "@/shared/api/client";
import { cn } from "@/shared/lib/cn";
import { errorText } from "@/shared/api/error_text";
import { fmtBytes } from "@/shared/lib/fmtBytes";
import { fmtTimecode } from "@/shared/lib/fmtSecs";
import { sha256 } from "@/shared/lib/sha256";
import { randomUuid } from "@/shared/lib/uuid";
import { pipelineKeys } from "@/features/pipeline";

import { isVideoName, videoAccept, VIDEO_EXTS } from "../lib/videoFiles";
import {
  completeUpload,
  createVideo,
  deleteVideo,
  ingestKeys,
  type MultipartInit,
  signUploadParts,
  startMultipartUpload,
  type UploadLimits,
} from "../api";

/** 등록 공통 설정. 시트에 있고 두 경로(파일·버킷)가 함께 쓴다. */
export interface IngestMeta {
  broadcaster: string;
  transcode_profile: "auto" | "always" | "never";
  program_name: string;
}

/**
 * 엔진이 시트에 보고하는 주 실행 사양. 버튼은 시트의 사이드바가 그린다.
 *
 * 결정에 필요한 값(대상 수·길이)과 결정 버튼을 한 곳에 두려고 시트로 올려 보낸다.
 * 값을 보는 곳과 버튼을 누르는 곳이 다르면 사용자가 위아래를 오가야 한다.
 */
export interface EngineAction {
  /** 이 버튼이 처리할 건수. 0이면 사이드바가 버튼을 비활성으로 그린다. */
  count: number;
  /** 버튼 문구. 엔진마다 목적어가 다르다("선택 12건 등록" / "8건 등록 시작"). */
  label: string;
  /** 실행 함수. */
  run: () => void;
  busy: boolean;
}

/** 시트의 확인 줄에 쓰는 값. 대상 수와, 길이를 읽은 파일의 길이 합이다. */
export interface UploadSummary {
  ready: number;
  /** 길이를 읽어낸 파일들의 길이 합(초). */
  durationKnown: number;
  /** 길이를 읽지 못한 파일 수. 이 파일들은 길이 합계에서 빠진다. */
  durationUnknown: number;
}

/**
 * 업로드 진행 요약. 시트를 접었을 때 페이지가 그리는 진행 줄에 쓴다.
 * 바이트 합은 큐에 이미 있다. 진행 요약이 없으면 남은 양을 알 수 없다.
 */
export interface UploadProgress {
  /** 이번 실행에서 끝난 건수(성공·실패 모두). */
  done: number;
  /** 이번 실행의 대상 수. */
  total: number;
  /** 바이트 기준 전체 진행률(0~1). */
  ratio: number;
}

/** 동시 업로드 수. 회선을 독점하지 않으면서 대량 업로드를 처리하는 절충값이다. */
const CONCURRENCY = 3;
/**
 * 길이를 동시에 읽는 파일 수.
 * 파일마다 `<video>`를 만들어 메타데이터를 읽는다.
 * 수백 건을 한꺼번에 끌어다 놓으면 그만큼 `<video>`를 동시에 만들어 메모리와 디코더를 한꺼번에 차지한다.
 * 그래서 업로드와 같은 방식으로 몇 개씩만 읽는다.
 */
const DURATION_CONCURRENCY = 3;


type RowStatus = "ready" | "uploading" | "done" | "error";

interface Row {
  id: string;
  file: File;
  /** 사용자가 입력한 제목. 비어 있으면 파일 이름으로 등록한다. */
  title: string;
  status: RowStatus;
  /** 업로드 진행률(0~1, XHR progress). */
  progress: number;
  /**
   * 길이(초).
   * 브라우저가 메타데이터에서 읽는다. null은 읽기 실패(합계 제외), undefined는 읽는 중이다.
   */
  duration?: number | null;
  /** 실패 결과(굵은 줄). 오류 사전이 바꾼 문구다. */
  error?: string;
  /** 실패 안내(작은 줄). 다음에 할 일을 알려 준다. */
  hint?: string;
  /** 이 실패를 `재시도`로 되살릴 수 있으면 true. false면 행은 제거만 권한다. */
  retryable?: boolean;
  /** 생성된 영상 id. 재시도할 때 다시 써서 중복 레코드를 만들지 않는다. */
  videoId?: string;
  /** 같은 내용의 기존 영상 id. 서버가 확정 때 내용 해시로 찾는다. */
  duplicateOf?: string;
  duplicateTitle?: string;
}

/** 파일을 받지 않은 이유. 받지 않은 파일 줄에 종류별로 세고, 펼치면 파일명이 나온다. */
type RejectKind = "not_video" | "empty" | "too_big" | "in_queue";

interface Rejected {
  kind: RejectKind;
  name: string;
}

/** 사용자가 `중단`을 눌러 멈춘 업로드. 실패와 다른 문구로 표시한다. */
class UploadAborted extends Error {
  constructor() {
    super("업로드 중단");
  }
}

/**
 * 이 파일로는 다시 시도해도 결과가 같은 실패.
 *
 * 크기 상한 같은 조건은 재시도로 풀리지 않는다.
 * 그 행에 `재시도`를 그리면 효과 없는 다음 단계를 안내하게 되므로 행에 그릴 버튼을 이 타입으로 나눈다.
 */
class UploadRejected extends Error {}

/**
 * 받지 않은 파일 줄의 종류 순서. 흔한 종류부터 둔다.
 * 순서가 고정되어야 사용자가 같은 종류를 같은 위치에서 찾는다.
 */
const REJECT_ORDER: RejectKind[] = ["not_video", "empty", "too_big", "in_queue"];

/**
 * 받지 않은 이유의 이름을 만든다. 이름으로 쓰이므로 명사구로 쓴다.
 * 상한은 서버가 알려 준 값을 그대로 쓴다.
 * 아직 받지 못했으면 숫자 없이 `크기 초과`라고만 쓴다.
 */
function rejectLabel(kind: RejectKind, cap: number | null): string {
  if (kind === "not_video") return "영상 아님";
  if (kind === "empty") return "빈 파일";
  if (kind === "in_queue") return "목록에 이미 있음";
  return cap != null ? `최대 ${fmtBytes(cap)} 초과` : "크기 초과";
}

/** 파일명에서 확장자를 뗀 값을 돌려준다. 제목을 비운 채 올린 파일의 제목이다. */
function titleFromFile(name: string): string {
  return name.replace(/\.[^.]+$/, "").slice(0, 255);
}

/**
 * 실패를 행에 표시할 문구로 바꾼다. `ApiError`는 `Error`의 인스턴스가 아니므로 따로 처리한다.
 *
 * `api()`는 `{ status, detail }`을 던지고, 오류 사전이 그 코드를 문구로 바꾼다.
 * 이 파일의 코드가 던진 `Error`는 이미 사용자용 문구다.
 * `Error`의 `message`만 읽으면 `ApiError`는 항상 `실패` 한 마디가 된다.
 * 그러면 사전에 있는 `already_uploaded`·`size_mismatch`·`object_not_uploaded` 문구를 쓸 수 없다.
 */
function failureOf(e: unknown): { error: string; hint?: string; retryable: boolean } {
  if (e instanceof UploadAborted) {
    return { error: "업로드 중단", hint: "다시 시도하면 올라간 부분부터 이어서 올립니다.", retryable: true };
  }
  // 파일 자체가 조건을 충족하지 못한 경우. 다음 할 일은 제거다.
  if (e instanceof UploadRejected) {
    return { error: e.message, hint: "목록에서 제거하고 조건에 맞는 파일로 다시 등록해주세요.", retryable: false };
  }
  if (e instanceof Error) return { error: e.message, retryable: true };
  const t = errorText((e as ApiError | null)?.detail, { message: "업로드 실패" });
  return { error: t.message, hint: t.hint, retryable: true };
}

/**
 * 파일 길이를 읽는다. 메타데이터만 불러오므로 파일 전체를 읽지 않는다.
 * 브라우저가 지원하지 않는 코덱이면 실패할 수 있고, 그때 null을 돌려준다.
 * 그 파일은 합계에서 빠지고, 빠졌다고 표시된다.
 */
function readDuration(file: File): Promise<number | null> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const v = document.createElement("video");
    v.preload = "metadata";
    let settled = false;
    const finish = (d: number | null) => {
      if (settled) return;
      settled = true;
      URL.revokeObjectURL(url);
      v.removeAttribute("src");
      resolve(d);
    };
    v.onloadedmetadata = () => finish(Number.isFinite(v.duration) ? v.duration : null);
    v.onerror = () => finish(null);
    setTimeout(() => finish(null), 10_000); // 10초 안에 끝나지 않으면 읽기를 그만둔다. 등록은 막지 않는다
    v.src = url;
  });
}

/** 파트 하나를 PUT한다. 진행률 이벤트가 필요해서 fetch 대신 XHR을 쓴다. */
function putPart(
  url: string, blob: Blob, onProgress: (loaded: number) => void, signal: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(new UploadAborted());
    const xhr = new XMLHttpRequest();
    const onAbort = () => xhr.abort();
    signal.addEventListener("abort", onAbort, { once: true });
    xhr.open("PUT", url);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded); };
    xhr.onload = () => {
      signal.removeEventListener("abort", onAbort);
      if (xhr.status >= 200 && xhr.status < 300) return resolve();
      // 흔한 원인은 서명 만료(403)다. 재시도하면 서명을 다시 받는다는 것까지 알린다.
      reject(new Error(`저장소가 업로드를 거부했습니다 (${xhr.status}).`));
    };
    xhr.onerror = () => {
      signal.removeEventListener("abort", onAbort);
      reject(new Error("네트워크 오류"));
    };
    xhr.onabort = () => reject(new UploadAborted());
    xhr.send(blob);
  });
}

/** 바이트 배열을 16진수 문자열로 바꾼다. */
const hex = (b: Uint8Array) =>
  [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");

/**
 * 멀티파트 업로드를 한다. 3GB 파일이 90%에서 끊겨도 끊긴 파트부터 이어서 올린다.
 *
 * 서명은 필요한 만큼만 그때그때 받는다.
 * 640개를 한 번에 받으면 느린 회선에서는 뒤쪽 파트를 올릴 때 서명이 이미 만료되어 있다.
 *
 * 내용 해시를 함께 만든다. 파트마다 sha256을 구하고, 그 값들을 이어 붙여 다시 sha256을 구한다.
 * 멀티파트 ETag는 파트 크기에 따라 달라져 내용 대조에 쓸 수 없다(중복 감지용, 표시 전용).
 */
async function uploadMultipart(
  videoId: string,
  file: File,
  init: MultipartInit,
  onProgress: (ratio: number) => void,
  signal: AbortSignal,
): Promise<string> {
  const total = Math.ceil(file.size / init.part_size);
  const done = new Set(init.uploaded_parts ?? []);
  // 이어받기로 건너뛴 파트도 진행률에 센다. 세지 않으면 90%에서 이어받은 업로드가 0%로 보인다.
  const sent = new Map<number, number>();
  done.forEach((n) => sent.set(n, Math.min(init.part_size, file.size - (n - 1) * init.part_size)));
  /** 보낸 바이트 합으로 진행률(0~1)을 계산해 알린다. */
  const report = () =>
    onProgress(Math.min(1, [...sent.values()].reduce((a, b) => a + b, 0) / file.size));
  report();

  const digests: string[] = [];
  const SIGN_BATCH = 16;
  for (let start = 1; start <= total; start += SIGN_BATCH) {
    if (signal.aborted) throw new UploadAborted();
    const nums: number[] = [];
    for (let n = start; n < start + SIGN_BATCH && n <= total; n++) nums.push(n);
    const need = nums.filter((n) => !done.has(n));
    let urls: Record<number, string> = {};
    if (need.length) {
      const r = await signUploadParts(videoId, { upload_id: init.upload_id, part_numbers: need });
      urls = r.urls;
    }
    // 건너뛴 파트도 파트 해시를 구한다. 이어받기에서도 내용 해시에 모든 파트가 들어가야 한다.
    for (const n of nums) {
      if (signal.aborted) throw new UploadAborted();
      const blob = file.slice((n - 1) * init.part_size, n * init.part_size);
      const buf = await blob.arrayBuffer();
      digests[n - 1] = hex(await sha256(buf));
      if (done.has(n)) continue;
      await putPart(urls[n], blob, (loaded) => { sent.set(n, loaded); report(); }, signal);
      sent.set(n, blob.size);
      report();
    }
  }
  const root = await sha256(new TextEncoder().encode(digests.join("")));
  return `${hex(root)}-${total}`;
}


interface Props {
  projectId: string;
  /** 등록 메타. 시트의 공통 블록에 있다. 등록은 막히지 않으므로 항상 값이 있다. */
  meta: IngestMeta;
  /**
   * 크기 상한과 확장자. 파일을 받기 전에 거르는 데 쓴다.
   * 아직 오지 않았으면 `undefined`이고, 그때는 예비 목록으로 판정한다.
   */
  limits?: UploadLimits;
  /** 등록 완료를 알린다. 배치 id, 성공 건수, 영상 id 목록을 넘긴다. */
  onDone: (batchId: string, count: number, videoIds: string[]) => void;
  /** 진행 중인 업로드 수를 페이지에 알린다. 페이지는 이 값으로 접힌 진행 줄을 보여 준다. */
  onBusyChange: (uploading: number) => void;
  /** 진행 요약을 알린다. 접힌 진행 줄이 `N / M건 · P%`를 표시한다. 끝나면 null이다. */
  onProgress?: (p: UploadProgress | null) => void;
  /** 확인 줄에 쓸 값을 알린다. 대상 수, 읽은 길이의 합, 길이를 읽지 못한 수다. */
  onSummary?: (s: UploadSummary) => void;
  /** 주 실행 사양을 시트에 보고한다. 버튼은 시트 사이드바가 그린다. */
  onActionChange?: (a: EngineAction) => void;
}

/** 로컬 파일 업로드 큐를 그린다. 드롭존, 받지 않은 파일 줄, 행 목록, 실행 줄을 둔다. */
export function UploadQueue({
  projectId, meta, limits, onDone, onBusyChange, onProgress, onSummary, onActionChange,
}: Props) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { slug } = useParams<{ slug: string }>();
  const inputRef = useRef<HTMLInputElement>(null);

  const [rows, setRows] = useState<Row[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [batchId, setBatchId] = useState<string | null>(null);
  /** 행마다 두는 중단 컨트롤러. XHR과 다음 단계를 함께 중단한다. */
  const aborts = useRef(new Map<string, AbortController>());
  /** 이번 실행의 대상. 접힌 진행 줄의 분모다. */
  const runIds = useRef<Set<string>>(new Set());
  /**
   * 서버가 알려 준 업로드 상한. 한 번 받으면 드롭할 때 거른다.
   * 영상 레코드와 멀티파트를 만든 뒤에 비교하면 큰 파일이 `업로드 미완료` 고아 레코드를 남긴다.
   * 시트가 미리 조회한 값(`limits`)이 있으면 첫 파일부터 쓰고, 없으면 첫 멀티파트 응답에서 얻는다.
   */
  const maxBytes = useRef<number | null>(null);
  if (limits) maxBytes.current = limits.max_bytes;
  /**
   * 받는 확장자 목록.
   * 서버가 준 목록을 기준으로 쓰고, 없으면 예비 목록(`videoFiles`)을 쓴다.
   */
  const exts = useMemo(
    () => (limits ? new Set(limits.video_extensions.map((e) => e.toLowerCase())) : VIDEO_EXTS),
    [limits],
  );

  /**
   * 받지 않은 파일 목록. 드롭존 아래 한 줄로 남긴다.
   * 여러 번 끌어다 놓아도 목록을 쌓아, 앞서 거른 파일의 이유가 지워지지 않게 한다.
   */
  const [rejected, setRejected] = useState<Rejected[]>([]);
  const [rejectOpen, setRejectOpen] = useState(false);

  const counts = useMemo(() => {
    const c = { ready: 0, uploading: 0, done: 0, error: 0 };
    for (const r of rows) c[r.status]++;
    return c;
  }, [rows]);
  const busy = counts.uploading > 0;

  // 확인 줄 값을 시트로 보낸다. 길이는 비동기로 채워지므로 rows가 바뀔 때마다 다시 계산한다.
  useEffect(() => {
    if (!onSummary) return;
    const pending = rows.filter((r) => r.status === "ready" || r.status === "error");
    onSummary({
      ready: pending.length,
      durationKnown: pending.reduce((s, r) => s + (r.duration ?? 0), 0),
      durationUnknown: pending.filter((r) => r.duration === null || r.duration === undefined).length,
    });
  }, [rows, onSummary]);

  // 진행 요약을 보낸다. 이번 실행 대상만 센다. 큐에 남은 이전 행은 분모에 넣지 않는다.
  useEffect(() => {
    if (!onProgress) return;
    if (!busy) { onProgress(null); return; }
    const targets = rows.filter((r) => runIds.current.has(r.id));
    const bytes = targets.reduce((s, r) => s + r.file.size, 0);
    const sent = targets.reduce(
      (s, r) => s + r.file.size * (r.status === "done" ? 1 : r.progress), 0);
    onProgress({
      done: targets.filter((r) => r.status === "done" || r.status === "error").length,
      total: targets.length,
      ratio: bytes > 0 ? sent / bytes : 0,
    });
  }, [rows, busy, onProgress]);

  /** 행 하나의 필드를 바꾼다. */
  const patch = (id: string, next: Partial<Row>) =>
    setRows((cur) => cur.map((r) => (r.id === id ? { ...r, ...next } : r)));

  /**
   * 길이를 몇 개씩만 읽는다.
   * 그래서 수백 건을 끌어다 놓아도 `<video>`가 한꺼번에 만들어지지 않는다.
   */
  function readDurations(fresh: Row[]) {
    const queue = [...fresh];
    const worker = async () => {
      for (let r = queue.shift(); r; r = queue.shift()) {
        const d = await readDuration(r.file);
        patch(r.id, { duration: d });
      }
    };
    for (let i = 0; i < Math.min(DURATION_CONCURRENCY, queue.length); i++) void worker();
  }

  /**
   * 끌어다 놓은 파일을 큐에 넣는다. 거른 파일은 지우지 않고 아래 줄에 남긴다.
   *
   * 영상인지(확장자), 빈 파일인지, 상한을 넘는지, 큐에 이미 있는지를 판정한다.
   * 네 판정 모두 레코드를 만들기 전에 하므로 고아 레코드가 남지 않는다.
   */
  function addFiles(files: FileList | File[]) {
    const all = [...files];
    const cap = maxBytes.current;
    const out: Rejected[] = [];
    const picked: File[] = [];
    // 같은 파일(이름과 크기)이 이미 큐에 있으면 건너뛴다. 그래서 두 번 끌어다 놓아도 중복 등록되지 않는다.
    // 건너뛴 파일도 받지 않은 파일 줄에 남겨 알린다.
    const seen = new Set(rows.map((r) => `${r.file.name}:${r.file.size}`));
    for (const f of all) {
      const key = `${f.name}:${f.size}`;
      if (!isVideoName(f.name, exts)) out.push({ kind: "not_video", name: f.name });
      // 0바이트 파일은 업로드 확정이 항상 거절하므로 레코드를 만들기 전에 거른다.
      else if (f.size === 0) out.push({ kind: "empty", name: f.name });
      else if (cap != null && f.size > cap) out.push({ kind: "too_big", name: f.name });
      else if (seen.has(key)) out.push({ kind: "in_queue", name: f.name });
      else { seen.add(key); picked.push(f); }
    }
    if (out.length) {
      setRejected((cur) => [...cur, ...out]);
      setRejectOpen(false);
    }
    if (picked.length === 0) return;
    const fresh: Row[] = picked.map((f, i) => ({
      id: `${Date.now()}-${i}-${f.name}`,
      file: f,
      title: "",
      status: "ready" as const,
      progress: 0,
    }));
    setRows((cur) => [...cur, ...fresh]);
    // 끌어다 놓자마자 행이 보이도록 길이는 나중에 채운다.
    readDurations(fresh);
  }

  /**
   * 등록을 그만둔 영상 레코드를 지운다.
   * 업로드가 끝나지 않은 레코드가 남으면 등록 콘솔 이슈에 `업로드 미완료`로 계속 남는다.
   * 서버 삭제는 완료되지 않은 멀티파트도 함께 정리한다.
   * 삭제가 실패해도 큐 정리는 계속한다. 남은 레코드는 영상 상세에서 지울 수 있다.
   */
  async function discardVideo(videoId: string) {
    try {
      await deleteVideo(videoId);
      void qc.invalidateQueries({ queryKey: ingestKeys.videoBuckets(projectId) });
    } catch {
      /* 이미 지워졌거나 권한이 바뀐 경우. 큐에서는 그대로 뺀다 */
    }
  }

  /**
   * 한 건을 등록한다. 영상 생성, presign, 업로드, 확정 순서다.
   * 실패는 행에 남겨 재시도 대상으로 둔다.
   * 성공하면 영상 id를, 실패하면 null을 돌려주고 호출자가 집계한다.
   * 호출자가 상태에서 결과를 다시 읽으면 비동기 갱신과 어긋난다.
   */
  async function uploadRow(row: Row, batch: string, m: IngestMeta): Promise<string | null> {
    const ctl = new AbortController();
    aborts.current.set(row.id, ctl);
    patch(row.id, {
      status: "uploading", progress: 0, error: undefined, hint: undefined, retryable: undefined,
    });
    try {
      // 받은 상한을 넘으면 레코드를 만들기 전에 멈춘다.
      if (maxBytes.current != null && row.file.size > maxBytes.current) {
        throw new UploadRejected(`최대 ${fmtBytes(maxBytes.current)} 초과`);
      }
      // 재시도면 앞서 만든 영상을 그대로 쓴다. 매번 새로 만들면 실패한 수만큼 upload=pending
      // 고아 레코드가 쌓이고, 대량 등록에서는 그 수가 특히 많아진다.
      let videoId = row.videoId;
      if (!videoId) {
        const created = await createVideo(projectId, {
          title: row.title.trim() || titleFromFile(row.file.name),
          transcode_profile: m.transcode_profile,
          broadcaster: m.broadcaster || undefined,
          program_name: m.program_name || undefined,
          upload_batch_id: batch,
          file_size_bytes: row.file.size,
          // 브라우저가 읽은 길이. 그래서 등록 직후 목록이 분석을 기다리지 않고 길이를 보여 준다.
          duration_seconds: row.duration ?? undefined,
          // 제목은 고칠 수 있고 업로드 키에는 파일 이름이 남지 않는다. 납품 패키지가 원본 파일을 가리키려면 이름을 따로 보낸다.
          original_filename: row.file.name,
        });
        videoId = created.video_id;
        patch(row.id, { videoId });
      }
      if (ctl.signal.aborted) throw new UploadAborted();
      const init = await startMultipartUpload(videoId);
      maxBytes.current = init.max_bytes;
      if (row.file.size > init.max_bytes) {
        // 상한을 받기 전인 첫 파일은 레코드가 만들어진다. 고아 레코드를 남기지 않도록 바로 지운다.
        await discardVideo(videoId);
        patch(row.id, { videoId: undefined });
        throw new UploadRejected(`최대 ${fmtBytes(init.max_bytes)} 초과`);
      }
      const contentHash = await uploadMultipart(videoId, row.file, init,
        (p) => patch(row.id, { progress: p }), ctl.signal);
      if (ctl.signal.aborted) throw new UploadAborted();
      const done = await completeUpload(videoId, {
        upload_id: init.upload_id,
        expected_size_bytes: row.file.size,
        content_hash: contentHash,
      });
      patch(row.id, {
        status: "done", progress: 1,
        duplicateOf: done.metadata?.duplicate_of ?? undefined,
        duplicateTitle: done.metadata?.duplicate_title ?? undefined,
      });
      return videoId;
    } catch (e) {
      patch(row.id, { status: "error", ...failureOf(e) });
      return null;
    } finally {
      aborts.current.delete(row.id);
    }
  }

  /**
   * 큐를 실행한다. 동시에 CONCURRENCY개씩 처리하고, 성공과 실패는 이 함수에서 집계해 돌려준다.
   * setState 업데이터 안에서 토스트를 부르면 렌더 중 다른 컴포넌트를 갱신하게 되어 React가 경고한다.
   */
  const run = useMutation({
    mutationFn: async (targets: Row[]) => {
      const batch = batchId ?? randomUuid();
      if (!batchId) setBatchId(batch);
      runIds.current = new Set(targets.map((t) => t.id));
      onBusyChange(targets.length);
      const queue = [...targets];
      const ids: string[] = [];
      let failed = 0;
      const workers = Array.from({ length: Math.min(CONCURRENCY, queue.length) }, async () => {
        for (let next = queue.shift(); next; next = queue.shift()) {
          const id = await uploadRow(next, batch, meta);
          if (id) ids.push(id);
          else failed++;
        }
      });
      await Promise.all(workers);
      return { batch, total: targets.length, failed, ids };
    },
    onSuccess: ({ batch, total, failed, ids }) => {
      onBusyChange(0);
      void qc.invalidateQueries({ queryKey: ingestKeys.projectVideos(projectId) });
      void qc.invalidateQueries({ queryKey: ingestKeys.projectBatchVideos(projectId) });
      void qc.invalidateQueries({ queryKey: ingestKeys.videoBuckets(projectId) });
      void qc.invalidateQueries({ queryKey: ingestKeys.batches(projectId) });
      void qc.invalidateQueries({ queryKey: pipelineKeys.summary(projectId) });
      // 일부가 실패하면 완료 처리하지 않는다. 그래서 일부만 성공한 Job이 알림 없이 넘어가지 않는다.
      // 실패한 행은 목록에 남아 행마다 사유와 다시 시도를 보여 준다. 토스트는 결과만 쓴다.
      if (failed > 0) toast("warning", `파일 ${total}개 중 ${failed}개 실패`);
      else onDone(batch, total, ids);
    },
    onError: () => onBusyChange(0),
  });

  /** 대기 중인 행을 모두 올린다. */
  const startAll = () => run.mutate(rows.filter((r) => r.status === "ready"));
  /**
   * 실패 재시도 대상. 되살릴 수 있는 실패만 센다.
   * 상한 초과처럼 이 파일로는 결과가 같은 실패까지 담으면 눌러도 그 수만큼 다시 실패한다.
   */
  const retryable = rows.filter((r) => r.status === "error" && r.retryable !== false);
  /** 되살릴 수 있는 실패 행만 다시 올린다. */
  const retryFailed = () => run.mutate(retryable);

  /** 행을 큐에서 뺀다. 업로드가 끝나지 않은 영상 레코드가 있으면 서버에서도 지운다. */
  const removeRow = (r: Row) => {
    if (r.videoId && r.status !== "done") void discardVideo(r.videoId);
    setRows((cur) => {
      const next = cur.filter((x) => x.id !== r.id);
      if (next.length === 0) setBatchId(null);
      return next;
    });
  };

  /*
    주 실행 사양을 시트에 보고한다.
    올릴 파일이 있으면 등록이, 없고 실패만 남았으면 재시도가 주 실행이다.
    의존성 배열 없이 매 렌더 보고한다. 시트는 건수·문구·busy가 같으면 상태를 바꾸지 않아 렌더가 반복되지 않는다.
    run 클로저는 항상 최신 값을 읽고 이전 rows를 참조하지 않는다.
  */
  useEffect(() => {
    if (!onActionChange) return;
    if (counts.ready > 0)
      onActionChange({ count: counts.ready, label: `영상 ${counts.ready.toLocaleString("ko-KR")}개 등록`, run: startAll, busy });
    else if (retryable.length > 0)
      onActionChange({ count: retryable.length, label: `실패 재시도 ${retryable.length}개`, run: retryFailed, busy });
    else onActionChange({ count: 0, label: "", run: () => {}, busy });
  });

  return (
    <div className="space-y-3">
      {/*
        드롭존. 여러 파일을 한 번에 고를 수 있다.
        영역을 눌러도 열리지만, 키보드와 스크린리더를 위해 보이는 버튼을 따로 둔다.
        onClick만 있는 영역과 숨긴 입력만으로는 키보드로 파일 선택 창을 열 수 없다.
      */}
      <div
        onDragOver={(e: DragEvent) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e: DragEvent) => { e.preventDefault(); setDragOver(false); addFiles(e.dataTransfer.files); }}
        onClick={() => inputRef.current?.click()}
        /* 끌어 놓는 영역은 한 줄 높이다. 시트를 연 사용자는 주로 아래의 업로드 목록을 보므로 그 목록을 위로 올린다. */
        className={cn(
          "flex cursor-pointer items-center gap-3.5 rounded-card border border-dashed px-4 py-3.5 transition-colors",
          dragOver ? "border-focus bg-amber-soft" : "border-separator-strong bg-canvas-2 hover:border-label-3",
        )}
      >
        <span className="flex size-10 shrink-0 items-center justify-center rounded-capsule border border-separator bg-panel text-label-2">
          <Upload size={16} aria-hidden="true" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-callout font-semibold text-label">영상 파일을 끌어 놓기</span>
          {/* 상한은 서버가 알려 준 값(`upload-limits`)을 쓴다. 화면에 고정하면 설정이 바뀐 뒤로 틀린 값이 된다.
              아직 오지 않았으면 상한을 생략한다. `video/*` 같은 MIME 표기는 쓰지 않는다. */}
          {limits && (
            <span className="block text-caption text-label-3">파일당 최대 {fmtBytes(limits.max_bytes)}</span>
          )}
        </span>
        {/* 버튼 클릭이 영역의 클릭으로 번지면 선택 창이 두 번 열린다. */}
        <Button
          variant="secondary"
          onClick={(e) => { e.stopPropagation(); inputRef.current?.click(); }}
        >
          파일 선택
        </Button>
        <input
          ref={inputRef}
          type="file"
          accept={videoAccept(limits?.video_extensions)}
          multiple
          className="hidden"
          onChange={(e) => { addFiles(e.target.files ?? []); e.target.value = ""; }}
        />
      </div>

      {/* ── 받지 않은 파일 ──
          드롭존 바로 아래에 두어, 방금 끌어다 놓은 위치에서 사용자가 일부만 들어온 이유를 바로 알게 한다.
          종류별 수를 세고, 펼치면 파일명이 나온다. 수는 추정하지 않는다. 이 목록이 큐에 들어가지 못한 파일 전부다. */}
      {rejected.length > 0 && (
        <div className="rounded-card border border-warn/30 bg-warn-muted/40">
          <div className="flex items-center gap-2 px-3 py-2">
            <button
              type="button"
              onClick={() => setRejectOpen((o) => !o)}
              aria-expanded={rejectOpen}
              className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
            >
              <ChevronDown
                size={12}
                className={cn("shrink-0 text-warn-text transition-transform", !rejectOpen && "-rotate-90")}
                aria-hidden="true"
              />
              <span className="text-caption font-medium text-warn-text">
                받지 않은 파일 {rejected.length.toLocaleString("ko-KR")}개
              </span>
              <span className="truncate text-caption text-label-3">
                {REJECT_ORDER
                  .map((k) => {
                    const n = rejected.filter((r) => r.kind === k).length;
                    return n > 0 ? `${rejectLabel(k, maxBytes.current)} ${n}` : null;
                  })
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </button>
            <button
              type="button"
              aria-label="받지 않은 파일 목록 지우기"
              title="받지 않은 파일 목록 지우기"
              className="shrink-0 rounded-control p-1 text-label-4 transition-colors hover:bg-fill hover:text-label-2"
              onClick={() => { setRejected([]); setRejectOpen(false); }}
            >
              <X size={14} aria-hidden="true" />
            </button>
          </div>
          {rejectOpen && (
            <ul className="max-h-40 space-y-0.5 overflow-y-auto border-t border-warn/20 px-3 py-2">
              {rejected.map((r, i) => (
                <li key={`${r.kind}-${r.name}-${i}`} className="flex items-baseline gap-2 text-caption">
                  <span className="shrink-0 text-label-3">{rejectLabel(r.kind, maxBytes.current)}</span>
                  <span className="min-w-0 flex-1 truncate font-mono text-label-3">{r.name}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {rows.length > 0 && (
        <div className="overflow-hidden rounded-card border border-separator">
          <table className="w-full text-callout">
            <thead className="border-b border-separator text-caption font-medium text-label-3">
              <tr>
                <th className="px-3 py-2 text-left font-medium">제목</th>
                <th className="w-20 px-3 py-2 text-left font-medium">크기</th>
                <th className="w-16 px-3 py-2 text-right font-medium">길이</th>
                <th className="w-56 px-3 py-2 text-left font-medium">상태</th>
                <th className="w-9" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-separator/60 last:border-0">
                  <td className="px-3 py-1.5">
                    <Input
                      value={r.title}
                      placeholder={titleFromFile(r.file.name)}
                      aria-label={`${r.file.name} 제목`}
                      disabled={r.status !== "ready" && r.status !== "error"}
                      onChange={(e) => patch(r.id, { title: e.target.value })}
                      className="h-7"
                    />
                    <p className="mt-0.5 truncate font-mono text-micro text-label-3">{r.file.name}</p>
                  </td>
                  <td className="px-3 py-1.5 tabular-nums text-label-2">{fmtBytes(r.file.size)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-label-2">
                    {/* null은 이 브라우저가 코덱을 지원하지 않아 길이를 읽지 못한 경우다.
                        합계에서 빠진다는 표시다. */}
                    {r.duration === undefined ? "…" : r.duration === null ? "—" : fmtTimecode(r.duration)}
                  </td>
                  <td className="px-3 py-1.5">
                    {r.status === "ready" && <span className="text-label-3">대기</span>}
                    {r.status === "uploading" && (
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 flex-1 overflow-hidden rounded-capsule bg-fill">
                          {/*
                            전이 시간은 200ms로, 폴링 막대(500ms)보다 짧게 둔다.
                            `xhr.upload.onprogress`가 초당 수십 번 갱신하므로 전이가 길면 막대가 실제 진행보다 뒤처져 보인다.
                          */}
                          <div
                            className="h-full rounded-capsule bg-accent transition-[width] duration-200"
                            style={{ width: `${Math.round(r.progress * 100)}%` }}
                          />
                        </div>
                        <span className="tabular-nums text-label-3">{Math.round(r.progress * 100)}%</span>
                      </div>
                    )}
                    {r.status === "done" && (
                      <span className="flex flex-col leading-tight">
                        <span className="inline-flex items-center gap-1 text-ok-text">
                          <CircleCheck size={14} aria-hidden="true" /> 완료
                        </span>
                        {/*
                          중복을 알린다. 서버는 확정 때 내용 해시로 같은 파일을 찾아 `duplicate_of`를 남긴다.
                          정책을 달리해 다시 올리는 경우가 있어서 막지는 않는다.
                        */}
                        {r.duplicateOf && (
                          <span className="inline-flex items-center gap-1 text-caption text-warn-text">
                            <Copy size={12} aria-hidden="true" /> 중복 ·
                            <Link
                              to={`/p/${slug}/videos/${r.duplicateOf}`}
                              className="underline-offset-2 hover:underline"
                              title={r.duplicateTitle}
                            >
                              기존 영상 보기
                            </Link>
                          </span>
                        )}
                      </span>
                    )}
                    {r.status === "error" && (
                      /*
                        사유와 그 사유에 맞는 다음 행동을 함께 보여 준다.
                        이 행으로 다시 시도할 수 있으면 `재시도`를, 아니면 제거를 권한다.
                        큐 전체의 재시도만 두면 되살릴 수 없는 행도 다시 시도되어 같은 실패를 되풀이한다.
                      */
                      <span className="flex flex-col items-start leading-tight">
                        <span className="inline-flex items-center gap-1 text-bad-text">
                          <X size={14} aria-hidden="true" /> {r.error}
                        </span>
                        {r.hint && <span className="text-caption text-label-3">{r.hint}</span>}
                        {r.retryable !== false && !busy && (
                          <button
                            type="button"
                            onClick={() => run.mutate([r])}
                            className="mt-1 inline-flex items-center gap-1 rounded-control border border-separator px-1.5 py-0.5 text-caption font-medium text-label-2 transition-colors hover:bg-fill"
                          >
                            <RotateCw size={12} aria-hidden="true" /> 재시도
                          </button>
                        )}
                      </span>
                    )}
                  </td>
                  <td className="px-2 py-1.5">
                    {r.status === "uploading" ? (
                      /* 업로드 중에도 멈출 수 있다. 탭을 닫지 않고 멈추는 방법이다. */
                      <button
                        type="button"
                        aria-label="업로드 중단"
                        title="업로드 중단"
                        className="rounded-control p-1 text-label-3 hover:bg-fill hover:text-bad-text"
                        onClick={() => aborts.current.get(r.id)?.abort()}
                      >
                        <CircleStop size={14} aria-hidden="true" />
                      </button>
                    ) : (
                      <button
                        type="button"
                        aria-label="목록에서 제거"
                        title="목록에서 제거"
                        className="rounded-control p-1 text-label-3 hover:bg-fill hover:text-bad-text"
                        onClick={() => removeRow(r)}
                      >
                        <Trash2 size={14} aria-hidden="true" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 실행 줄.
          진행·실패 수와, 실패 행 가까이에 둘 복구 버튼 하나를 둔다. */}
      {rows.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="flex items-center gap-2 text-caption tabular-nums text-label-3">
            {counts.done > 0 && <span className="text-ok-text">완료 {counts.done}</span>}
            {counts.error > 0 && <span className="text-bad-text">실패 {counts.error}</span>}
            {busy && <span className="text-accent">업로드 중 {counts.uploading}</span>}
          </span>
          {/* 주 실행 버튼은 사이드바가 그린다. 이 줄에는 실패 행 바로 옆에 둘 복구 버튼 하나만 둔다.
              올릴 파일이 하나도 없으면 재시도가 주 실행이 되어 사이드바로 올라가므로 이 줄에서는 숨긴다. */}
          {retryable.length > 0 && counts.ready > 0 && !busy && (
            <Button className="ml-auto" variant="secondary" size="sm" onClick={retryFailed}>
              <RotateCw size={14} aria-hidden="true" /> 실패 재시도 {retryable.length}개
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
