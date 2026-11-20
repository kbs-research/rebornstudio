/**
 * 버킷 경로를 직접 입력해 등록하는 엔진. 등록 시트의 저장소 탭이 쓴다.
 *
 * 버킷 안을 나열하는 화면은 두지 않는다. 사용자는 저장소 콘솔에서 확인한 경로를 입력한다.
 * 파일 경로 여러 개는 시트 사이드바의 실행 버튼으로, 폴더 경로는 이 탭 안의 버튼으로 등록한다.
 * 폴더 등록은 대상 수를 미리 셀 수 없는 서버 작업이라 사이드바의 대상 수에 넣지 않는다.
 *
 * 입력 검증은 서버가 한다. 허용 버킷, 연결, 앱 경로, 저장소에 있는지를 등록 전에 확인하고,
 * 영상 규격은 등록 뒤 규격 검증 단계가 확인한다. 화면은 보내기 전에 모양만 맞춘다(`lib/storage_paths`).
 *
 * 등록 메타는 시트의 `meta`를 그대로 받아 두 등록 경로가 같은 값을 보낸다.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FolderInput } from "lucide-react";

import type { EngineAction, IngestMeta } from "./UploadQueue";
import { Button } from "@/shared/ui/Button";
import { Input } from "@/shared/ui/Input";
import { Label } from "@/shared/ui/Label";
import { useToast } from "@/shared/ui/Toast";
import { type ApiError } from "@/shared/api/client";
import { errorText } from "@/shared/api/error_text";
import { FIELD_BASE } from "@/shared/ui/field";
import { cn } from "@/shared/lib/cn";
import { randomUuid } from "@/shared/lib/uuid";
import { pipelineKeys } from "@/features/pipeline";
import { useSelectedProject } from "@/store/project";

import { folderPrefix, missingKeyOf, parseKeyLines } from "../lib/storage_paths";
import {
  ingestKeys,
  registerStorageKeys,
  registerStoragePrefix,
  type StoragePrefixImportResult,
} from "../api";

/** 서버가 한 요청에 받는 키 수. 넘으면 나눠 보낸다. */
const CHUNK = 200;

interface Props {
  /** 등록 메타. 시트의 공통 블록에 있다. */
  meta: IngestMeta;
  /** 서버가 준 영상 확장자. 받기 전이면 예비 목록으로 판정한다. */
  videoExtensions?: string[];
  /**
   * 등록 완료를 알린다. 배치 id, 건수, 영상 id 목록을 넘긴다.
   * 결과 토스트는 페이지가 띄운다. 덧붙일 안내가 있으면 `note`로 넘긴다.
   */
  onDone: (batchId: string, count: number, videoIds: string[], note?: string) => void;
  /** 폴더 등록 작업이 큐에 들어갔음을 알린다. 페이지는 묶음 상세로 이동한다. */
  onJobQueued: (batchId: string) => void;
  /** 주 실행 사양을 시트에 보고한다. 버튼은 시트 사이드바의 실행부가 그린다. */
  onActionChange?: (a: EngineAction) => void;
}

/** 버킷 경로 입력란 두 개(파일 경로·폴더 경로)와 폴더 등록 버튼을 그린다. */
export function StoragePathInput({ meta, videoExtensions, onDone, onJobQueued, onActionChange }: Props) {
  const { selectedProjectId } = useSelectedProject();
  const qc = useQueryClient();
  const { toast } = useToast();
  const [keyText, setKeyText] = useState("");
  const [folder, setFolder] = useState("");
  /** 서버가 저장소에 없다고 알린 키. 입력란 아래에 그대로 보여 주고, 입력을 고치면 지운다. */
  const [missing, setMissing] = useState<string | null>(null);

  const exts = useMemo(
    () => (videoExtensions?.length ? new Set(videoExtensions.map((e) => e.toLowerCase())) : undefined),
    [videoExtensions],
  );
  const parsed = useMemo(() => parseKeyLines(keyText, exts), [keyText, exts]);
  const prefix = folderPrefix(folder);

  /** 등록 뒤 영향을 받는 조회를 무효화한다. */
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ingestKeys.videosAll() });
    qc.invalidateQueries({ queryKey: ingestKeys.videoBucketsAll() });
    qc.invalidateQueries({ queryKey: ingestKeys.batchesAll() });
    qc.invalidateQueries({ queryKey: pipelineKeys.summaryAll() });
  };

  /*
    입력한 키를 등록한다. 서버가 요청당 200건까지 받으므로 나눠서 차례로 보낸다.
    나눠 보내도 한 번의 등록은 한 묶음이라 다음 단계에서 이 묶음만 이어서 처리할 수 있다.
    저장소에 없는 키가 있으면 그 요청은 하나도 등록되지 않는다. 앞 요청에서 등록된 건수는 토스트로 밝힌다.
  */
  const register = useMutation({
    mutationFn: async (keys: string[]) => {
      const batch = randomUuid();
      let registered = 0;
      let duplicates = 0;
      let jobId: string = batch;
      const ids: string[] = [];
      for (let i = 0; i < keys.length; i += CHUNK) {
        try {
          const r = await registerStorageKeys(selectedProjectId, {
            keys: keys.slice(i, i + CHUNK),
            broadcaster: meta.broadcaster || undefined,
            program_name: meta.program_name || undefined,
            transcode_profile: meta.transcode_profile,
            upload_batch_id: batch,
          });
          registered += r.registered;
          duplicates += r.duplicates ?? 0;
          // Job id는 서버가 응답한 값을 쓴다. 계약상 기준은 응답 값이다.
          jobId = r.batch_id;
          ids.push(...(r.video_ids ?? []));
        } catch (e) {
          throw Object.assign(e as object, { registered });
        }
      }
      return { registered, duplicates, batch: jobId, ids };
    },
    onSuccess: (r) => {
      invalidate();
      setKeyText("");
      setMissing(null);
      // 결과는 페이지가 한 번만 알린다. 다시 등록된 건수는 덧붙임으로 넘긴다.
      const note = r.duplicates ? `${r.duplicates}개는 이미 등록됐던 파일입니다.` : undefined;
      if (r.registered > 0) {
        onDone(r.batch, r.registered, r.ids, note);
        return;
      }
      toast("warning", "새로 등록된 영상이 없습니다");
    },
    onError: (e) => {
      const err = e as unknown as (ApiError & { registered?: number }) | null;
      const key = missingKeyOf(err?.detail);
      if (key) setMissing(key);
      if (err?.registered) invalidate();
      const t = errorText(err?.detail, { message: "등록 실패" });
      const sofar = err?.registered ? `앞서 보낸 ${err.registered.toLocaleString("ko-KR")}개는 등록했습니다.` : "";
      toast("error", t.message, [sofar, t.hint].filter(Boolean).join(" ") || undefined);
    },
  });

  /*
    폴더 등록은 서버 비동기 작업이다. 화면은 경로만 넘기고 바로 돌아온다.
    시작 시점에는 건수가 정해지지 않아 표시하지 않는다. 묶음 행이 등록되는 대로 센다.
  */
  const registerPrefix = useMutation<StoragePrefixImportResult, Error | ApiError, string>({
    mutationFn: (p: string) =>
      registerStoragePrefix(selectedProjectId, {
        prefix: p,
        broadcaster: meta.broadcaster || undefined,
        program_name: meta.program_name || undefined,
        transcode_profile: meta.transcode_profile,
        upload_batch_id: randomUuid(),
      }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ingestKeys.batchesAll() });
      setFolder("");
      toast("success", "폴더 등록을 시작했습니다");
      onJobQueued(r.batch_id);
    },
    onError: (e) => {
      // 네트워크 계층의 실제 Error와 `api()`가 던지는 `{ status, detail }` 두 종류가 온다.
      if (e instanceof Error) {
        toast("error", e.message);
        return;
      }
      const { message, hint } = errorText(e.detail, {
        message: "폴더를 등록하지 못했습니다",
        hint: "잠시 후 다시 시도해주세요.",
      });
      toast("error", message, hint);
    },
  });

  /* 주 실행 사양을 시트에 보고한다. 의존성 배열 없이 매 렌더 보고하고, 시트는 값이 같으면 상태를 바꾸지 않는다. */
  useEffect(() => {
    if (!onActionChange) return;
    const n = parsed.keys.length;
    onActionChange({
      count: n,
      label: n > 0 ? `경로 ${n.toLocaleString("ko-KR")}개 등록` : "",
      run: () => register.mutate(parsed.keys),
      busy: register.isPending,
    });
  });

  return (
    <div className="space-y-6">
      <div className="space-y-1.5">
        <Label htmlFor="in-storage-keys">파일 경로</Label>
        <textarea
          id="in-storage-keys"
          value={keyText}
          onChange={(e) => { setKeyText(e.target.value); setMissing(null); }}
          rows={8}
          spellCheck={false}
          placeholder={"news/2026/02/evening-0214.mp4\nnews/2026/02/evening-0215.mp4"}
          aria-invalid={missing ? true : undefined}
          aria-describedby="in-storage-keys-hint"
          className={cn(FIELD_BASE, "px-3 py-2 font-mono text-footnote")}
        />
        <div id="in-storage-keys-hint" className="space-y-0.5 text-caption text-label-3">
          <p>한 줄에 경로 하나씩 입력합니다. 저장소 이름은 빼고 저장소 안의 경로만 씁니다.</p>
          {parsed.skipped.length > 0 && (
            <p className="text-warn-text">
              영상 파일이 아닌 줄 {parsed.skipped.length.toLocaleString("ko-KR")}개는 등록하지 않습니다.
            </p>
          )}
          {missing && (
            <p className="break-all text-bad-text">
              저장소에 없는 경로: <span className="font-mono">{missing}</span>
            </p>
          )}
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="in-storage-folder">폴더 경로</Label>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (prefix && !registerPrefix.isPending) registerPrefix.mutate(prefix);
          }}
        >
          <Input
            id="in-storage-folder"
            value={folder}
            onChange={(e) => setFolder(e.target.value)}
            spellCheck={false}
            placeholder="news/2026/02/"
            aria-describedby="in-storage-folder-hint"
            className="font-mono text-footnote"
          />
          <Button type="submit" variant="secondary" disabled={!prefix || registerPrefix.isPending} className="shrink-0">
            <FolderInput size={14} className="mr-1" aria-hidden="true" />
            폴더 전체 등록
          </Button>
        </form>
        <p id="in-storage-folder-hint" className="text-caption text-label-3">
          폴더 아래의 영상을 모두 등록합니다. 창을 닫아도 계속 진행됩니다.
        </p>
      </div>

      {/* 결과와 덧붙임을 두 줄로 나눈다. */}
      <div className="text-caption text-label-3">
        <p>파일을 옮기지 않고 저장소 경로를 연결합니다.</p>
        <p className="mt-0.5">규격 검증과 변환을 거쳐 분석 요청 대기로 들어갑니다.</p>
      </div>
    </div>
  );
}
