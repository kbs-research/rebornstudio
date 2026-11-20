/**
 * 저장소 대상 카드(설정 `분석 연동`). 이 프로젝트의 원본과 산출물을 둘 저장소 연결·버킷을 고르고 점검한다.
 *
 * NCP 저장소를 쓰는 분석 방식(분석 서비스·샷 검출)은 이 값을 저장해야 영상을 등록한다.
 * 미리 채워 두는 값은 없다. 자격증명 연결에 등록한 NCP 저장소 연결 중에서 고른다.
 * 저장소는 그 연결이 허용한 저장소만 보인다(`allowed_buckets`). 범위는 최고관리자가 자격증명 연결에서 정한다.
 * 고를 연결이 없으면 자격증명 연결 화면으로 가는 길을 같은 카드에 둔다. 첫 영상이 등록되면 바꿀 수 없다.
 * 고르는 규칙·잠금·점검은 서버(`services/stacks/project_storage`)가 정하고 화면은 그 결과를 그린다.
 * 분석 대상 카드와 같은 위치·같은 모양으로 잠긴 이유와 점검 결과를 같은 카드에 둔다.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Lock, TriangleAlert } from "lucide-react";

import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { ErrorState } from "@/shared/ui/ErrorState";
import { Input } from "@/shared/ui/Input";
import { Select } from "@/shared/ui/Select";
import { SettingCard, SettingRow } from "@/shared/ui/SettingRow";
import { Skeleton } from "@/shared/ui/Skeleton";
import { useToast } from "@/shared/ui/Toast";
import { failToast } from "@/shared/ui/failToast";
import { cn } from "@/shared/lib/cn";
import { DiagnosticsList, diagnosticsSummary } from "@/features/platform";
import type { DiagnosticsReport } from "@/shared/api/client";

import {
  checkStorageStack,
  fetchStorageChoices,
  projectKeys,
  saveStorageStack,
  type StorageChoicesDTO,
} from "../api";
import { lockTitle } from "../lib/stack_target";
import { NoConnectionNotice } from "./NoConnectionNotice";
import { TargetNotice } from "./TargetNotice";

/** 고르는 값 둘. 빈 문자열은 아직 고르지 않은 값이다. */
interface StorageForm {
  connectionId: string;
  bucket: string;
}

const EMPTY: StorageForm = { connectionId: "", bucket: "" };

/** 서버 선택 값을 폼 값으로 바꾼다. 연결이나 버킷이 비었으면 기록이 없다고 본다. */
function formOf(v: StorageChoicesDTO["selected"] | null | undefined): StorageForm | null {
  if (!v?.bucket || !v.storage_connection_id) return null;
  return { connectionId: v.storage_connection_id, bucket: v.bucket };
}

/** 두 폼 값이 같으면 참. */
function sameForm(a: StorageForm, b: StorageForm | null): boolean {
  return !!b && a.connectionId === b.connectionId && a.bucket === b.bucket;
}

/** 연결 선택지 한 줄의 이름. 고를 수 없는 연결은 이유를 붙인다. */
function connectionLabel(c: NonNullable<StorageChoicesDTO["connections"]>[number]): string {
  const base = c.name;
  if (c.selectable) return base;
  if (!c.has_secret) return `${base} · 자격증명 없음`;
  return c.last_status === "fail" ? `${base} · 점검 실패` : `${base} · 점검 전`;
}

/**
 * 저장소 대상 카드를 그린다.
 * `canManageConnections`는 그 화면을 열 수 있는 사람(최고관리자)이면 true다.
 */
export function StorageTargetCard({
  projectId, canManageConnections,
}: { projectId: string; canManageConnections: boolean }) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [form, setForm] = useState<StorageForm>(EMPTY);
  const [ready, setReady] = useState(false);
  const [report, setReport] = useState<DiagnosticsReport | null>(null);

  /* 저장된 선택·잠금·연결 목록은 연결을 고르지 않은 조회로 받는다. 폼의 처음 값을 정한다. */
  const base = useQuery({
    queryKey: projectKeys.storageChoices(projectId),
    queryFn: () => fetchStorageChoices(projectId),
  });
  /* 고른 연결의 버킷 목록. 고를 때마다 다시 받고 받는 동안 이전 목록을 둔다. */
  const browse = useQuery({
    queryKey: projectKeys.storageChoices(projectId, form.connectionId),
    queryFn: () => fetchStorageChoices(projectId, form.connectionId),
    enabled: ready && !!base.data?.supported && !!form.connectionId,
    placeholderData: keepPreviousData,
  });

  const saved = formOf(base.data?.selected);
  /* 처음 한 번만 채운다. 저장된 선택이 없으면 비워 둔다. 채워 줄 기본값은 없다. */
  useEffect(() => {
    if (ready || !base.data) return;
    setForm(formOf(base.data.selected) ?? EMPTY);
    setReady(true);
  }, [base.data, ready]);

  const save = useMutation({
    mutationFn: () =>
      saveStorageStack(projectId, {
        storage_connection_id: form.connectionId,
        bucket: form.bucket.trim() || null,
      }),
    onSuccess: () => {
      toast("success", "저장소 대상을 저장했습니다", "분석을 쓰면 분석 대상도 이 저장소에 맞춰 저장합니다.");
      setReport(null);
      void qc.invalidateQueries({ queryKey: projectKeys.storageChoicesAll(projectId) });
      void qc.invalidateQueries({ queryKey: projectKeys.analysisChoicesAll(projectId) });
      void qc.invalidateQueries({ queryKey: projectKeys.analysisProvisioning(projectId) });
      void qc.invalidateQueries({ queryKey: projectKeys.list() });
      void qc.invalidateQueries({ queryKey: projectKeys.detail(projectId) });
    },
    onError: (e) => failToast(toast, e, "저장 실패"),
  });
  const check = useMutation({
    mutationFn: () => checkStorageStack(projectId),
    onSuccess: setReport,
    onError: (e) => failToast(toast, e, "점검 실패"),
  });

  if (base.isLoading) {
    return (
      <Card as="section" aria-label="저장소 대상" className="space-y-3 px-5 py-4">
        <Skeleton className="h-5 w-1/3" />
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
      </Card>
    );
  }
  if (!base.data) {
    return (
      <Card as="section" aria-label="저장소 대상" className="px-5 py-4">
        <ErrorState inline message="저장소 대상 조회 실패" onRetry={() => void base.refetch()} />
      </Card>
    );
  }

  const data = base.data;
  if (!data.supported) {
    return (
      <Card as="section" aria-label="저장소 대상" className="px-5 py-4">
        <h3 className="text-callout font-semibold text-label">저장소 대상</h3>
        <p className="mt-0.5 text-footnote text-label-2">이 프로젝트의 분석 방식은 NCP 저장소를 쓰지 않습니다.</p>
      </Card>
    );
  }

  const list = form.connectionId ? (browse.data ?? data) : data;
  const locked = data.locked;
  const dirty = !sameForm(form, saved);
  const conns = data.connections ?? [];
  const picked = conns.find((c) => c.id === form.connectionId);
  /*
    서버는 그 연결이 허용한 저장소만 목록에 싣는다. 목록을 읽지 못했으면 연결의 허용 범위에서 고르게 한다.
    직접 입력을 열어 두면 허용 범위 밖 이름을 적고 저장해야 거절을 보게 된다.
  */
  const buckets = list.buckets ?? (picked ? (picked.allowed_buckets ?? []) : null);
  const noneAllowed = !!picked && !locked && buckets !== null && buckets.length === 0 && !browse.isFetching;
  const bucket = form.bucket.trim();

  function pickConnection(value: string) {
    setReport(null);
    // 연결을 바꾸면 그 연결 설정의 버킷으로 다시 채운다. 다른 연결의 버킷이 남으면 권한이 없는 버킷을 고르게 된다.
    const conn = conns.find((c) => c.id === value);
    setForm({ connectionId: value, bucket: conn?.bucket ?? "" });
  }

  function pickBucket(value: string) {
    setReport(null);
    setForm((f) => ({ ...f, bucket: value }));
  }

  return (
    <SettingCard aria-label="저장소 대상">
      <div className="px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
          <div className="min-w-0">
            <h3 className="text-callout font-semibold text-label">저장소 대상</h3>
            <p className="mt-0.5 text-footnote text-label-2">이 프로젝트의 원본과 산출물을 둘 연결과 저장소입니다.</p>
          </div>
          <span
            className={cn(
              "inline-flex shrink-0 items-center gap-1.5 text-footnote font-semibold",
              locked ? "text-label-2" : saved ? "text-ok-text" : "text-warn-text",
            )}
          >
            {locked ? <Lock size={13} aria-hidden="true" /> : (
              <span aria-hidden="true" className={cn("size-2 rounded-capsule", saved ? "bg-ok" : "bg-warn")} />
            )}
            {locked ? "고정됨" : saved ? "저장됨" : "저장 전"}
          </span>
        </div>

        {/* 잠긴 이유와 저장 전에 막힌 것을 선택 목록 위에 둔다. 결과(굵게)와 이유(작게)를 나눈다. */}
        {locked && !saved ? (
          <TargetNotice tone="warn" title="영상이 있는데 저장소 대상이 기록되지 않았습니다">
            기존 영상이 있는 저장소를 최고관리자가 기록해야 영상 등록이 풀립니다.
          </TargetNotice>
        ) : locked ? (
          <TargetNotice tone="neutral" title={lockTitle(data.video_count, data.deleted_video_count, "저장소 대상")}>
            원본과 산출물이 두 저장소에 나뉘지 않게 첫 영상 등록부터 고정합니다.
          </TargetNotice>
        ) : !conns.length ? (
          <NoConnectionNotice kind="storage" canManage={canManageConnections} />
        ) : !saved && data.required ? (
          <TargetNotice tone="warn" title="저장소 대상을 저장하기 전에는 영상을 등록할 수 없습니다">
            연결과 저장소를 고른 뒤 저장합니다.
          </TargetNotice>
        ) : null}
      </div>

      <SettingRow
        label="저장소 연결"
        htmlFor="storage-target-connection"
        description="자격증명 연결에 등록한 NCP 저장소 연결입니다."
        control={
          <div className="w-72 shrink-0">
            <Select
              id="storage-target-connection"
              value={form.connectionId}
              disabled={locked}
              onChange={(e) => pickConnection(e.target.value)}
            >
              <option value="" disabled>{conns.length ? "고르기" : "등록한 연결 없음"}</option>
              {conns.map((c) => (
                <option key={c.id} value={c.id} disabled={!c.selectable && c.id !== form.connectionId}>
                  {connectionLabel(c)}
                </option>
              ))}
            </Select>
          </div>
        }
      />
      <SettingRow
        label="저장소"
        htmlFor="storage-target-bucket"
        description="이 연결이 허용한 저장소입니다. 분석을 쓰면 분석 워크스페이스에 연결된 저장소와 같아야 합니다."
        control={
          <div className="w-72 shrink-0">
            {buckets ? (
              <Select
                id="storage-target-bucket"
                value={form.bucket}
                disabled={locked}
                onChange={(e) => pickBucket(e.target.value)}
              >
                <option value="">{browse.isFetching && !buckets.length ? "불러오는 중…" : "고르기"}</option>
                {buckets.map((b) => (
                  <option key={b} value={b}>{b}</option>
                ))}
                {form.bucket && !buckets.includes(form.bucket) && (
                  <option value={form.bucket}>{form.bucket}</option>
                )}
              </Select>
            ) : (
              <Input
                id="storage-target-bucket"
                value={form.bucket}
                disabled={locked}
                placeholder="저장소 이름"
                autoComplete="off"
                onChange={(e) => pickBucket(e.target.value)}
              />
            )}
          </div>
        }
      />

      <div className="px-5 py-4">
        {noneAllowed && (
          <div role="status" className="mb-3 flex gap-1.5 text-footnote text-warn-text">
            <TriangleAlert size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
            <div className="min-w-0">
              <p className="font-semibold">이 연결에서 고를 수 있는 저장소가 없습니다</p>
              <p className="text-caption text-label-2">최고관리자가 자격증명 연결의 허용 저장소에 추가하면 고를 수 있습니다.</p>
            </div>
          </div>
        )}
        {list.error && (
          <p role="status" className="mb-3 flex gap-1.5 text-footnote text-bad-text">
            <TriangleAlert size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
            {list.error}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <p className="min-w-0 flex-1 text-footnote text-label-2">
            {locked ? "영상 등록도 같은 점검을 거칩니다." : "저장하면 저장소 접근과 쓰기·업로드 권한을 실제로 점검한 뒤 기록합니다."}
          </p>
          <Button size="sm" variant="secondary" onClick={() => check.mutate()} disabled={check.isPending}>
            {check.isPending ? "점검 중…" : "점검"}
          </Button>
          {!locked && (
            <Button
              size="sm"
              onClick={() => save.mutate()}
              disabled={save.isPending || !form.connectionId || !bucket || !dirty}
            >
              {save.isPending ? "저장 중…" : "저장"}
            </Button>
          )}
        </div>
      </div>

      {report && (
        <div className="py-3">
          <h4 className="px-5 text-footnote font-semibold text-label">점검 결과 · {diagnosticsSummary(report)}</h4>
          <DiagnosticsList report={report} />
        </div>
      )}
    </SettingCard>
  );
}
