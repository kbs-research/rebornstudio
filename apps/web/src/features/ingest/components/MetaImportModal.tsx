/**
 * 등록 묶음의 영상 메타 일괄 입력 창.
 *
 * 여러 영상을 한 번에 등록하면 영상 상세를 하나씩 열어 메타를 넣어야 한다. 이 창은 세 단계로 묶음 전체를 채운다.
 *   1. 양식 내려받기: 묶음의 모든 영상과 지금 값이 든 .xlsx
 *   2. 파일 올리기: 채운 양식(.xlsx 또는 CSV)
 *   3. 미리 보기와 반영: 서버가 행마다 판정한 결과를 보고, 변경 행만 반영한다
 *
 * 판정(영상 찾기·값 검사)은 서버가 한다. 반영도 서버가 다시 검사하므로 미리 보기 뒤 값이 바뀌어도 규칙을 벗어난 값은 저장되지 않는다.
 */
import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Download, FileSpreadsheet, Upload } from "lucide-react";

import { errorText, type ErrorText } from "@/shared/api/error_text";
import { type ApiError } from "@/shared/api/client";
import { saveBlob } from "@/shared/lib/download";
import { Button } from "@/shared/ui/Button";
import { Chip } from "@/shared/ui/Chip";
import { DataTable, type DataColumn } from "@/shared/ui/DataTable";
import { EmptyState } from "@/shared/ui/EmptyState";
import { failToast } from "@/shared/ui/failToast";
import { Modal } from "@/shared/ui/Modal";
import { useToast } from "@/shared/ui/Toast";
import {
  applyMetaImport,
  downloadMetaTemplate,
  ingestKeys,
  previewMetaImport,
  type MetaImportPreview,
  type MetaImportRow,
} from "../api";
import {
  META_FIELD_NAME,
  META_IMPORT_ACCEPT,
  META_IMPORT_MAX_BYTES,
  META_IMPORT_MAX_ROWS,
  META_ROW_STATUS,
  META_STATUS_ORDER,
  metaApplyItems,
  metaSkippedRows,
  metaValueText,
} from "../lib/meta_import";

/** 오류를 굵은 결과와 작은 안내로 나눈다. 사전에 없는 실패는 일반 문구로 둔다. */
function failText(e: unknown, fallback: string): ErrorText {
  return errorText((e as Partial<ApiError> | null)?.detail, { message: fallback, hint: "잠시 후 다시 시도해주세요." });
}

/** 미리 보기 표의 열. 행 번호는 스프레드시트에 보이는 번호와 같다. */
const columns: DataColumn<MetaImportRow>[] = [
  { label: "행", kind: "number", width: "4rem", value: (r) => r.row },
  {
    label: "영상",
    kind: "title",
    shrink: true,
    value: (r) => r.title ?? r.file ?? null,
    cell: (r) => (
      <span className="flex min-w-0 flex-col">
        <span className="truncate text-label" title={r.title ?? undefined}>{r.title ?? "제목 없음"}</span>
        {r.file && <span className="truncate text-caption text-label-3" title={r.file}>{r.file}</span>}
      </span>
    ),
  },
  {
    label: "판정",
    kind: "status",
    width: "7rem",
    value: (r) => META_ROW_STATUS[r.status].label,
    cell: (r) => (
      <Chip variant="dot" tone={META_ROW_STATUS[r.status].tone}>{META_ROW_STATUS[r.status].label}</Chip>
    ),
  },
  {
    label: "바뀌는 값",
    sortable: false,
    searchText: (r) => (r.changes ?? []).map((c) => `${META_FIELD_NAME[c.field]} ${c.after}`).join(" "),
    cell: (r) =>
      (r.changes ?? []).length === 0 ? (
        <span className="text-label-4">없음</span>
      ) : (
        <ul className="flex flex-col gap-0.5">
          {(r.changes ?? []).map((c) => (
            <li key={c.field} className="text-footnote">
              <span className="text-label-3">{META_FIELD_NAME[c.field]}</span>{" "}
              <span className="text-label-3">{metaValueText(c.field, c.before)}</span>
              <span className="text-label-4" aria-hidden="true"> → </span>
              <span className="sr-only">에서 </span>
              <span className="font-medium text-label">{metaValueText(c.field, c.after)}</span>
            </li>
          ))}
        </ul>
      ),
  },
  {
    label: "오류",
    sortable: false,
    searchText: (r) => (r.errors ?? []).map((e) => e.message).join(" "),
    cell: (r) =>
      (r.errors ?? []).length === 0 ? (
        <span className="text-label-4">없음</span>
      ) : (
        // 오류 문장은 줄을 바꾼다. 한 줄로 두면 표가 창보다 넓어져 영상 칸이 몇 글자로 줄어든다.
        <ul className="flex min-w-48 flex-col gap-0.5 whitespace-normal">
          {(r.errors ?? []).map((e, i) => (
            <li key={i} className="text-footnote text-bad-text">
              {/* 문장이 이미 칸 이름으로 시작하면(예: 방영일은 1920년부터) 칸 이름을 다시 붙이지 않는다. */}
              {e.field && !e.message.startsWith(META_FIELD_NAME[e.field]) ? `${META_FIELD_NAME[e.field]}: ` : ""}
              {e.message}
            </li>
          ))}
        </ul>
      ),
  },
];

/** 등록 묶음의 영상 메타 일괄 입력 창을 그린다. */
export function MetaImportModal({
  open, onClose, batchId, videoCount,
}: {
  open: boolean;
  onClose: () => void;
  batchId: string;
  /** 묶음에서 삭제되지 않은 영상 수. 양식에 실리는 행 수다. */
  videoCount: number;
}) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [preview, setPreview] = useState<MetaImportPreview | null>(null);
  const [fileError, setFileError] = useState<ErrorText | null>(null);

  const template = useMutation({
    mutationFn: () => downloadMetaTemplate(batchId),
    onSuccess: (f) => saveBlob(f.blob, f.filename ?? "meta.xlsx"),
    onError: (e) => failToast(toast, e, "양식 내려받기 실패"),
  });

  const check = useMutation({
    mutationFn: (file: File) => previewMetaImport(batchId, file),
    onMutate: () => {
      setPreview(null);
      setFileError(null);
    },
    onSuccess: (p) => setPreview(p),
    // 파일 문제는 고쳐서 다시 올려야 하므로 토스트로 띄우고 사라지게 두지 않고 창 안에 남긴다.
    onError: (e) => setFileError(failText(e, "미리 보기 실패")),
  });

  const items = preview ? metaApplyItems(preview) : [];
  const skipped = preview ? metaSkippedRows(preview) : 0;

  const apply = useMutation({
    mutationFn: () => applyMetaImport(batchId, items),
    onSuccess: (res) => {
      void qc.invalidateQueries({ queryKey: ingestKeys.batchVideosAll() });
      void qc.invalidateQueries({ queryKey: ingestKeys.videosAll() });
      void qc.invalidateQueries({ queryKey: ingestKeys.jobAll() });
      toast(
        "success",
        `영상 ${res.updated.toLocaleString("ko-KR")}개의 메타를 반영했습니다`,
        res.unchanged > 0
          ? `저장된 값과 같은 영상 ${res.unchanged.toLocaleString("ko-KR")}개는 바꾸지 않았습니다.`
          : undefined,
      );
      close();
    },
    onError: (e) => failToast(toast, e, "메타 반영 실패"),
  });

  /** 창을 닫고 고른 파일과 미리 보기를 비운다. 다시 열면 처음부터 시작한다. */
  function close() {
    setPreview(null);
    setFileName(null);
    setFileError(null);
    onClose();
  }

  /** 고른 파일을 서버에 올려 미리 보기를 받는다. 크기 상한을 넘으면 올리지 않고 바로 알린다. */
  function pick(file: File | undefined) {
    if (!file) return;
    setFileName(file.name);
    if (file.size > META_IMPORT_MAX_BYTES) {
      setPreview(null);
      setFileError(errorText("meta_import_file_too_large"));
      return;
    }
    check.mutate(file);
  }

  const busy = template.isPending || check.isPending || apply.isPending;
  const counts = preview?.counts;

  return (
    <Modal
      open={open}
      onClose={busy ? () => {} : close}
      title="메타 일괄 입력"
      icon={<FileSpreadsheet size={16} aria-hidden="true" />}
      tone="neutral"
      size="lg"
      footer={
        <>
          {preview && skipped > 0 && (
            <span className="mr-auto text-footnote text-label-3">
              오류·일치 없음 {skipped.toLocaleString("ko-KR")}행은 반영하지 않습니다
            </span>
          )}
          <Button variant="secondary" onClick={close} disabled={apply.isPending}>닫기</Button>
          <Button onClick={() => apply.mutate()} disabled={items.length === 0 || busy}>
            {/* 미리 보기 전에는 반영할 수가 없으므로 수를 붙이지 않는다. */}
            {apply.isPending ? "반영 중…" : preview ? `영상 ${items.length.toLocaleString("ko-KR")}개에 반영` : "반영"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-5">
        <section className="flex flex-col gap-2">
          <h3 className="text-callout font-semibold text-label">1. 양식 내려받기</h3>
          <p className="text-footnote text-label-3">
            이 묶음의 영상 {videoCount.toLocaleString("ko-KR")}개와 지금 메타가 들어 있습니다.
            영상 ID와 파일 열은 영상을 찾는 데 쓰므로 고치지 않습니다.
          </p>
          <span>
            <Button size="sm" variant="secondary" onClick={() => template.mutate()} disabled={busy}>
              <Download size={14} aria-hidden="true" />
              {template.isPending ? "만드는 중…" : "양식 내려받기"}
            </Button>
          </span>
        </section>

        <section className="flex flex-col gap-2">
          <h3 className="text-callout font-semibold text-label">2. 파일 올리기</h3>
          <p className="text-footnote text-label-3">
            빈 셀은 바꾸지 않습니다. 값을 지우려면 영상 상세에서 메타를 편집합니다.
          </p>
          <span className="flex flex-wrap items-center gap-2">
            <input
              ref={inputRef}
              type="file"
              accept={META_IMPORT_ACCEPT}
              className="sr-only"
              aria-label="채운 양식 파일"
              onChange={(e) => {
                pick(e.target.files?.[0]);
                e.target.value = "";
              }}
            />
            <Button size="sm" variant="secondary" onClick={() => inputRef.current?.click()} disabled={busy}>
              <Upload size={14} aria-hidden="true" />
              {check.isPending ? "확인 중…" : fileName ? "다른 파일 선택" : "파일 선택"}
            </Button>
            {fileName && <span className="min-w-0 truncate text-footnote text-label-2" title={fileName}>{fileName}</span>}
          </span>
          <p className="text-caption text-label-3">
            .xlsx 또는 CSV(UTF-8) · 최대 {Math.round(META_IMPORT_MAX_BYTES / 1024 / 1024)} MB ·{" "}
            {META_IMPORT_MAX_ROWS.toLocaleString("ko-KR")}행
          </p>
          {fileError && (
            <p role="alert" className="flex flex-col gap-0.5 text-footnote">
              <b className="font-semibold text-bad-text">{fileError.message}</b>
              {fileError.hint && <span className="text-label-3">{fileError.hint}</span>}
            </p>
          )}
        </section>

        {preview && counts && (
          <section className="flex flex-col gap-3">
            <h3 className="text-callout font-semibold text-label">3. 미리 보기</h3>
            {/* 분모를 밝힌다. 파일에 없는 영상은 바뀌지 않는다. */}
            <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-footnote tabular-nums text-label-2">
              {META_STATUS_ORDER.filter((s) => s === "changed" || (counts[s] ?? 0) > 0).map((s) => (
                <span key={s}>
                  {META_ROW_STATUS[s].label} <b className="font-semibold text-label">{(counts[s] ?? 0).toLocaleString("ko-KR")}</b>
                </span>
              ))}
              <span className="text-label-3">
                묶음 영상 {preview.batch_video_count.toLocaleString("ko-KR")}개 중 {preview.matched_video_count.toLocaleString("ko-KR")}개 일치
              </span>
            </p>
            {(preview.ignored_columns ?? []).length > 0 && (
              <p className="text-caption text-label-3">읽지 않은 열: {(preview.ignored_columns ?? []).join(", ")}</p>
            )}
            <DataTable<MetaImportRow>
              columns={columns}
              rows={preview.rows}
              rowKey={(r) => String(r.row)}
              density="compact"
              unit="행"
              search="영상·값·오류 검색"
              empty={<EmptyState dense message="데이터 행 없음" description="머리글 아래에 값이 든 행이 없습니다." />}
            />
          </section>
        )}
      </div>
    </Modal>
  );
}
