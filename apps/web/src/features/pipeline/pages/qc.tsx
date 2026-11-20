/**
 * 캡션 품질검수 화면(`/p/:slug/qc`). 관리자가 처리할 보류 작업 목록이다.
 *
 * Export Gate는 `qc_status='held'`인 건을 완료 목록에서 뺀다.
 * 행에 지적 문장을 바로 실어 다시 검사할지 풀지를 그 문장으로 정하게 한다.
 * 표로 고르고, 행을 누르면 오른쪽 드로어에서 그 한 건의 전체 내용을 읽는다.
 *
 * 정렬은 두지 않는다. `더 보기`로 잇는 페이지 목록이라 불러온 50행만 정렬하면
 * "지적이 가장 많은 건"이 전체가 아닌 그 50건 안에서 고른 결과가 된다.
 *
 * 이 화면은 판정하지 않고 근거만 보여 준다. 품질검수는 반려하지 않고 보류만 한다.
 * 보류는 관리자가 푼다. 캡션이 틀렸으면 고쳐 다시 승인하고(승인 검사가 다시 돈다),
 * 사전·설정이 틀렸으면 고친 뒤 다시 검사하고, 오탐이면 사유를 남기고 해제한다.
 *
 * 프로젝트는 주소의 slug로 정한다. 선택된 프로젝트로 조회하면 전환기 동기화가 늦을 때
 * 다른 프로젝트의 보류가 이 프로젝트 이름 아래 보인다.
 */
import { useInfiniteQuery, useMutation } from "@tanstack/react-query";
import { ArrowRight, ChevronDown, RefreshCw, TriangleAlert } from "lucide-react";
import { Fragment, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";

import { Button, buttonClasses } from "@/shared/ui/Button";
import { Chip } from "@/shared/ui/Chip";
import { type DataColumn } from "@/shared/ui/DataTable";
import { Drawer } from "@/shared/ui/Drawer";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { IdChip } from "@/shared/ui/IdChip";
import { Label } from "@/shared/ui/Label";
import { Modal } from "@/shared/ui/Modal";
import { PageHeader } from "@/shared/ui/PageHeader";
import { TableCard } from "@/shared/ui/TableCard";
import { useToast } from "@/shared/ui/Toast";
import { FIELD_BASE } from "@/shared/ui/field";
import { cn } from "@/shared/lib/cn";
import { type ApiError } from "@/shared/api/client";
import { errorText } from "@/shared/api/error_text";
import { fmtDateTime, fmtFullDateTime } from "@/shared/lib/time";
import { fmtClipRange } from "@/shared/lib/fmtSecs";
import { useProjects } from "@/features/projects";
import type { components } from "@reborn/contracts";

import {
  fetchQcHeld,
  pipelineKeys,
  recheckQcHold,
  releaseQcHold,
  reviseQcHold,
} from "../api";
import { leadFinding } from "../lib/lead_finding";

/** 한 번에 불러오는 행 수. 더 보려면 `더 보기`가 다음 묶음을 잇는다(전수는 서버가 센다). */
const PAGE = 50;
type QcStageDTO = components["schemas"]["QcStage"];
type QcHeldRowDTO = components["schemas"]["QcHeldRow"];
type QcFindingDTO = components["schemas"]["QcFinding"];

/** 판정 3분류의 화면 문구. 서버 값을 그대로 노출하지 않는다. */
const VERDICT_KO: Record<string, string> = {
  pass: "검사 통과",
  needs_review: "확인 필요",
  reject_suggested: "반려 추천",
};

/** 판정 점의 색. 반려 추천이 확인 필요보다 무겁다는 것을 색으로도 보여 준다. */
const VERDICT_DOT: Record<string, string> = {
  pass: "bg-ok",
  needs_review: "bg-warn",
  reject_suggested: "bg-bad",
};

/**
 * 단계 상태 4분류. 서버 `QcStage.status`가 이 밖의 값을 보내면 `skipped`로 본다.
 * `noted`는 검출됐지만 판정에 반영되지 않는 단계다(선택 항목이 비었다는 `info` 지적).
 */
type StageKind = "passed" | "flagged" | "noted" | "skipped";
const STAGE_KINDS: readonly StageKind[] = ["passed", "flagged", "noted", "skipped"];

/**
 * 단계 막대 한 마디의 모양. 통과는 옅은 회색, 검출은 위험색, 참고는 먹색, 미실행은 빈 마디다.
 */
const CELL: Record<StageKind, string> = {
  passed: "bg-label-4",
  flagged: "bg-bad",
  noted: "bg-label-2",
  skipped: "border border-separator-strong bg-panel",
};
const STAGE_KO: Record<StageKind, string> = { passed: "통과", flagged: "검출", noted: "참고", skipped: "미실행" };

/**
 * 단계 상태를 4분류 중 하나로 정한다. 드로어의 막대와 수가 같은 규칙을 쓴다.
 *
 * 판정이 통과인 실행의 `flagged`는 `noted`로 본다.
 * 통과는 판정에 반영되는 지적이 하나도 없다는 뜻이라 그 실행의 검출은 모두 참고 지적이다.
 * 이전 형식으로 저장된 실행에는 선택 항목 알림도 `flagged`로 남아 있어, 이 규칙이 없으면 통과한 실행 옆에 경고색 `검출`이 보인다.
 */
function stageKind(status: string, verdict?: string | null): StageKind {
  if (status === "flagged" && verdict === "pass") return "noted";
  return (STAGE_KINDS as readonly string[]).includes(status) ? (status as StageKind) : "skipped";
}

/** 상태별 단계 수를 센다. `stages`가 없으면 `null`이다. 기록이 없는 경우를 0과 구분한다. */
function countStages(
  stages: QcStageDTO[] | null | undefined,
  verdict?: string | null,
): Record<StageKind, number> | null {
  if (!stages) return null;
  const acc: Record<StageKind, number> = { passed: 0, flagged: 0, noted: 0, skipped: 0 };
  for (const s of stages) acc[stageKind(s.status, verdict)]++;
  return acc;
}

/** 단계 수를 `통과 6 · 검출 1 · 미실행 4` 꼴로 쓴다. 참고가 있는 경우가 드물어 0인 참고는 뺀다. */
function stageCountText(c: Record<StageKind, number>): string {
  return STAGE_KINDS.filter((k) => k !== "noted" || c.noted > 0)
    .map((k) => `${STAGE_KO[k]} ${c[k]}`)
    .join(" · ");
}

/**
 * 검사 단계 막대를 그린다. 검사 순서대로 한 마디씩 둬서 어느 단계에서 걸렸는지 위치로 읽힌다.
 * 마디의 색만으로 구분하지 않도록 수를 접근 이름(`aria-label`)으로 읽어 준다.
 */
function StageCells({ stages, verdict }: { stages: QcStageDTO[]; verdict?: string | null }) {
  const c = countStages(stages, verdict)!;
  return (
    <span role="img" aria-label={stageCountText(c)} className="inline-flex shrink-0 gap-0.5">
      {stages.map((s) => (
        <span key={s.key} className={cn("h-3.5 w-2", CELL[stageKind(s.status, verdict)])} />
      ))}
    </span>
  );
}

/**
 * 드로어의 `검사한 단계` 절. 막대 · 수 · 미실행 사유 · 접은 통과 항목 순서다.
 *
 * 이 검사는 단계가 12개이고 그중 상당수는 조건에 따라 실행되지 않는다.
 * 그래서 `통과`와 `안 함`을 같은 모양으로 그리지 않는다.
 * 미실행 사유는 상주 문구로 보여 주고, 같은 사유로 건너뛴 단계는 한 줄로 묶는다.
 * 통과 항목은 수를 보인 채 접는다. 감추면 몇 개를 확인했는지 알 수 없다.
 * `stages`가 `null`인 실행은 단계 기록이 생기기 전의 실행이라 기록이 없다고 표시한다.
 */
function StageSection({ stages, verdict }: { stages: QcStageDTO[] | null | undefined; verdict?: string | null }) {
  if (stages === null || stages === undefined)
    return (
      <section>
        <h3 className="text-caption font-semibold text-label-2">검사한 단계</h3>
        <p className="mt-1.5 text-footnote text-label-3">단계 기록을 남기기 전에 실행된 검사입니다.</p>
      </section>
    );
  if (stages.length === 0) return null;
  const c = countStages(stages, verdict)!;
  const kindOf = (s: QcStageDTO) => stageKind(s.status, verdict);
  const skipped = stages.filter((s) => kindOf(s) === "skipped");
  const passed = stages.filter((s) => kindOf(s) === "passed");
  // 같은 사유로 건너뛴 단계를 묶는다. 묶지 않으면 규칙에 걸려 AI 넷을 건너뛸 때 같은 문장이 네 번 나온다.
  const byReason = new Map<string, string[]>();
  for (const s of skipped) {
    // 단계 기록 초기 형식의 실행에는 사유가 없어 기록되지 않았다고 쓴다.
    const reason = s.reason ?? "사유가 기록되지 않았습니다";
    byReason.set(reason, [...(byReason.get(reason) ?? []), s.label || s.key]);
  }
  return (
    <section>
      <h3 className="text-caption font-semibold text-label-2">
        검사한 단계 <span className="tabular-nums">{stages.length}</span>
      </h3>
      <div className="mt-2.5">
        <StageCells stages={stages} verdict={verdict} />
      </div>
      <p className="mt-2 text-footnote tabular-nums text-label-2">
        {STAGE_KINDS.filter((k) => k !== "noted" || c.noted > 0).map((k, i) => (
          <Fragment key={k}>
            {i > 0 && <span aria-hidden="true"> · </span>}
            <span className={k === "flagged" && c.flagged > 0 ? "font-semibold text-bad-text" : undefined}>
              {STAGE_KO[k]} {c[k]}
            </span>
          </Fragment>
        ))}
      </p>
      {byReason.size > 0 && (
        <div className="mt-3">
          <h4 className="border-b border-separator pb-1.5 text-caption font-semibold text-label-2">미실행 사유</h4>
          <dl className="mt-2 space-y-2 text-footnote">
            {[...byReason].map(([reason, names]) => (
              <div key={reason}>
                <dt className="font-semibold text-label">{names.join(" · ")}</dt>
                <dd className="text-label-2">{reason}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
      {passed.length > 0 && (
        <details className="group mt-3">
          <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-control text-footnote text-label-2 hover:text-label">
            <ChevronDown size={14} className="transition-transform group-open:rotate-180" aria-hidden="true" />
            통과한 항목 {passed.length}개 보기
          </summary>
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {passed.map((s) => (
              <li key={s.key}>
                <Chip tone="neutral">{s.label || s.key}</Chip>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

/**
 * 지적 항목 칩. 규칙 검사와 AI 검사는 신뢰도가 달라 색을 나눈다. AI는 파랑, 규칙은 회색이다.
 * 참고 지적(`info`)은 테두리만 둔 칩이라 판정에 드는 지적과 구분된다.
 * `sunken`은 흐린 상자(확인 창의 대상 상자) 위에 놓일 때 쓴다. 회색 칩이 상자와 같은 색이라 흰 면으로 바꾼다.
 */
function FindingChip({ f, sunken = false }: { f: QcFindingDTO; sunken?: boolean }) {
  return (
    <Chip
      tone={f.source === "model" ? "note" : "neutral"}
      outlined={f.severity === "info"}
      className={cn("shrink-0", (f.severity === "info" || (sunken && f.source !== "model")) && "bg-panel")}
      title={f.source === "model" ? "AI 검사 지적" : "규칙 검사 지적"}
    >
      {f.check_label || f.check}
    </Chip>
  );
}

/** 지적 한 줄 목록. 칩과 문장을 테두리 상자 안에 한 줄씩 둔다. */
function FindingRows({ findings }: { findings: QcFindingDTO[] }) {
  return (
    <ul className="mt-2 space-y-2">
      {findings.map((f, i) => (
        <li
          key={i}
          className={cn(
            "flex items-baseline gap-2.5 rounded-card border border-separator px-3.5 py-2.5 text-callout",
            f.severity === "info" ? "text-label-2" : "text-label",
          )}
        >
          <FindingChip f={f} />
          <span className="min-w-0 break-words">{f.message}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * 드로어의 지적 절. 판정에 드는 지적과 참고 지적을 따로 묶는다.
 * 참고 머리에 `판정에 들어가지 않음`을 적어 경고와 구분한다.
 * 지적이 없으면 없다고 표시한다. 건너뛴 단계가 있을 수 있으므로 "모두 통과"라고는 쓰지 않는다.
 */
function FindingSections({ findings }: { findings: QcFindingDTO[] | null | undefined }) {
  const all = findings ?? [];
  const blocking = all.filter((f) => f.severity !== "info");
  const info = all.filter((f) => f.severity === "info");
  if (all.length === 0)
    return (
      <section>
        <h3 className="text-caption font-semibold text-label-2">지적 없음</h3>
        <p className="mt-1 text-footnote text-label-2">실행된 검사에서 검출된 항목이 없습니다.</p>
      </section>
    );
  return (
    <>
      {blocking.length > 0 && (
        <section>
          <h3 className="text-caption font-semibold text-label-2">
            지적 <span className="tabular-nums">{blocking.length}</span>
          </h3>
          <FindingRows findings={blocking} />
        </section>
      )}
      {info.length > 0 && (
        <section>
          <div className="flex items-baseline justify-between gap-2">
            <h3 className="text-caption font-semibold text-label-2">
              참고 <span className="tabular-nums">{info.length}</span>
            </h3>
            <span className="text-caption text-label-3">판정에 들어가지 않음</span>
          </div>
          <FindingRows findings={info} />
        </section>
      )}
    </>
  );
}

/** 판정 문구. 점과 굵은 글자로 표시하고 색만으로 구분하지 않는다. */
function Verdict({ value }: { value?: string | null }) {
  if (!value) return <span className="text-label-3">판정 기록 없음</span>;
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap font-semibold text-label">
      <span className={cn("size-1.75 shrink-0 rounded-capsule", VERDICT_DOT[value] ?? "bg-warn")} aria-hidden="true" />
      {VERDICT_KO[value] ?? value}
    </span>
  );
}

/**
 * 검사 방식. 모델 ID(`us.anthropic.…`)는 개발자용 문자열이라 사실 줄에 쓰지 않는다.
 * 관리자에게는 드로어의 `기술 정보`에서 보여 준다.
 */
function methodOf(model?: string | null): string {
  return model ? "AI 검사" : "규칙 검사";
}

/**
 * 검사 시각을 쓴다. 다른 화면과 같은 표기(`10월 13일 13:08`, `shared/lib/time`)를 쓰고, 연도와 초는 툴팁에 둔다.
 * 표기가 화면마다 다르면 같은 시각을 두 형식으로 읽게 된다.
 */
function When({ iso, className }: { iso: string; className?: string }) {
  return (
    <time dateTime={iso} title={fmtFullDateTime(iso)} className={className ?? "tabular-nums"}>
      {fmtDateTime(iso)}
    </time>
  );
}

/** 관리자에게만 보이는 기술 정보. 판정한 모델 ID를 접어 둔다. */
function TechInfo({ model }: { model?: string | null }) {
  if (!model) return null;
  return (
    <details className="border-t border-separator pt-3 text-footnote">
      <summary className="cursor-pointer text-label-2">기술 정보</summary>
      <p className="mt-1.5 text-label-2">
        AI 모델 <span className="font-mono text-caption text-label">{model}</span>
      </p>
    </details>
  );
}

/**
 * 드로어 머리의 사실 줄. 판정 · 방식 · 시각을 한 줄에, 구간과 작업 ID를 다음 줄에 둔다.
 * 제목(영상 제목)은 드로어 머리가 그린다.
 */
function DetailFacts({ verdict, model, at, start, end, workItemId }: {
  verdict?: string | null; model?: string | null; at?: string | null;
  start?: number | null; end?: number | null; workItemId: string;
}) {
  return (
    <div className="space-y-1.5 border-b border-separator pb-4">
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-footnote text-label-2">
        <Verdict value={verdict} />
        <span>{methodOf(model)}</span>
        {at && <When iso={at} className="text-caption tabular-nums" />}
      </p>
      <p className="flex flex-wrap items-center gap-x-2 text-footnote text-label-2">
        {/* 구간이 없으면 영상 단위 실행이라 구간을 쓰지 않는다. */}
        {start != null && end != null && (
          <span>클립 <span className="font-mono tabular-nums">{fmtClipRange(start, end)}</span></span>
        )}
        <IdChip id={workItemId} label="작업 ID" />
      </p>
    </div>
  );
}

/** 드로어 바닥 줄의 이동 링크. 판정 버튼과 섞이지 않게 왼쪽에 둔다. */
function NavLink({ to, children, onClick }: { to: string; children: ReactNode; onClick?: () => void }) {
  return (
    <Link to={to} onClick={onClick} className="inline-flex items-center gap-1 font-semibold text-label hover:underline">
      {children}
      <ArrowRight size={14} aria-hidden="true" />
    </Link>
  );
}

/**
 * 캡션이 보이는데 `캡션 없음`으로 보류된 건이면 true.
 * 문장 단위로 저장되지 않은 이전 형식의 캡션(`caption.brief` 문자열)을 검사가 읽지 못해 생긴 보류다.
 * 지금 검사는 문자열 캡션도 읽으므로 다시 검사하면 풀린다.
 */
function isStaleCaptionHold(row: QcHeldRowDTO): boolean {
  return !!row.caption_brief?.trim() && (row.findings ?? []).some((f) => f.check === "caption_empty");
}

/** `캡션 읽기 오류` 칩. 다시 검사로 지금 조치할 건이라 호박색이다. */
function StaleChip() {
  return <Chip tone="amber" className="shrink-0">캡션 읽기 오류</Chip>;
}

/**
 * 보류 표의 `지적` 열. 판정을 만든 가장 무거운 지적의 칩과 문장을 싣는다(`leadFinding`).
 * 다시 검사할지 풀지는 그 문장을 보고 정하므로 드로어를 열기 전에 보여 준다.
 * 지적이 더 있으면 수를 붙인다. 참고 지적은 수만 적는다.
 *
 * 마지막 다시 검사가 AI 검사 실패로 끝났으면(서버 `recheck_failed_at`) 그 시각을 같은 셀에 쓰고
 * 지적 칩 옆에 `이전 판정`을 붙인다. 실패한 다시 검사는 기록을 남기지 않아 보이는 지적은 그 전 판정의 지적이다.
 * 토스트로만 알리면 바뀐 기준으로는 더는 걸리지 않을 지적이 지금 판정처럼 남는다.
 */
function HeldFindingsCell({ row }: { row: QcHeldRowDTO }) {
  const all = row.findings ?? [];
  const blocking = all.filter((f) => f.severity !== "info");
  const info = all.length - blocking.length;
  const first = leadFinding(all);
  const failedAt = row.recheck_failed_at ?? null;
  const failedLine = failedAt && (
    <p className="flex items-center gap-1 text-caption text-warn-text">
      <TriangleAlert size={12} className="shrink-0" aria-hidden="true" />
      <span>마지막 다시 검사 · AI 검사 실패 · <When iso={failedAt} /></span>
    </p>
  );
  if (!first)
    return (
      <div className="min-w-0 space-y-1">
        <span className="text-footnote text-label-3">지적 기록 없음</span>
        {failedLine}
      </div>
    );
  return (
    <div className="min-w-0 space-y-1">
      <div className="flex flex-wrap items-center gap-1.5">
        <FindingChip f={first} />
        {failedAt && <Chip tone="neutral" className="shrink-0">이전 판정</Chip>}
        {isStaleCaptionHold(row) && <StaleChip />}
        {blocking.length > 1 && <span className="text-caption text-label-3">외 {blocking.length - 1}건</span>}
        {info > 0 && <span className="text-caption text-label-3">참고 {info}</span>}
      </div>
      {/* 표 셀은 줄을 바꾸지 않아서 긴 지적은 표를 문장 길이만큼 넓히고 처리 열을 카드 밖으로 민다. 이 문장만 줄을 바꾸고 두 줄에서 자른다. */}
      <p className="line-clamp-2 whitespace-normal text-footnote text-label-2" title={first.message}>{first.message}</p>
      {failedLine}
    </div>
  );
}

/** 대상 열. 제목과 구간을 두 줄로 둔다. 구간이 없으면 영상 단위라 구간을 쓰지 않는다. */
function TargetCell({ title, start, end, inline = false }: {
  title?: string | null; start?: number | null; end?: number | null; inline?: boolean;
}) {
  return (
    <div className={cn("min-w-0 text-footnote", inline && "flex items-baseline gap-2.5")}>
      <span className="block min-w-0 truncate font-semibold text-label" title={title ?? undefined}>
        {title ?? "영상"}
      </span>
      {start != null && end != null && (
        <span className={cn("shrink-0 font-mono text-caption tabular-nums text-label-3", !inline && "mt-0.5 block")}>
          {fmtClipRange(start, end)}
        </span>
      )}
    </div>
  );
}

/**
 * 확인 창의 대상 상자. 처리할 건을 창에서 보여 준다.
 * 창은 표에서 눌러 열리므로 사용자가 어떤 행을 눌렀는지 놓치기 쉽다.
 */
function TargetBox({ row, children }: { row: QcHeldRowDTO; children?: ReactNode }) {
  return (
    <div className="space-y-1.5 rounded-card bg-canvas-2 px-3.5 py-3">
      <TargetCell title={row.video_title} start={row.start_seconds} end={row.end_seconds} inline />
      {children}
    </div>
  );
}

/** 품질검수가 꺼져 있을 때의 빈 상태. 설정을 여는 버튼을 둔다. */
function QcOffState({ slug }: { slug?: string }) {
  return (
    <EmptyState
      message="캡션 품질검수 사용 안 함"
      action={
        <Link to={`/p/${slug}/settings?tab=qc`} className={buttonClasses("secondary", "sm")}>
          프로젝트 설정 열기
        </Link>
      }
    />
  );
}

/** 캡션 품질검수의 보류 목록과 보류 해제·다시 검사·캡션 고치기 동작을 그린다. */
export function QcPage() {
  const { slug } = useParams<{ slug: string }>();
  const { toast } = useToast();

  /*
    프로젝트는 주소의 slug로 찾는다.
    QC가 꺼져 있는지도 이 조회로 확인한다. 빈 상태를 사실대로 표시하려면 이 값이 필요하다.
  */
  const projects = useProjects();
  const project = projects.data?.find((p) => p.slug === slug);
  const pid = project?.project_id;
  const qcEnabled = project?.caption_qc?.enabled;

  const held = useInfiniteQuery({
    queryKey: pipelineKeys.qcHeld(pid),
    queryFn: ({ pageParam = 0 }) => fetchQcHeld(pid, { limit: PAGE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last, all) => {
      const loaded = all.reduce((n, p) => n + p.items.length, 0);
      return loaded < last.total ? loaded : undefined;
    },
    enabled: !!pid,
  });

  const heldRows = (held.data?.pages ?? []).flatMap((p) => p.items);
  const heldTotal = held.data?.pages[0]?.total;

  // 행을 누르면 오른쪽 드로어를 연다. 표는 고를 때 쓰고, 한 건의 전체 내용만 드로어에 싣는다.
  const [detail, setDetail] = useState<QcHeldRowDTO | null>(null);
  /*
    확인 창은 표의 행 밖(페이지)에 둔다.
    React는 포털 이벤트도 컴포넌트 트리를 따라 전파한다. 그래서 창이 행 안에 있으면 창 안을 누를 때
    행 클릭(드로어 열기)까지 함께 실행된다.
  */
  const [releasing, setReleasing] = useState<QcHeldRowDTO | null>(null);
  const [rechecking, setRechecking] = useState<QcHeldRowDTO | null>(null);
  const [revising, setRevising] = useState<QcHeldRowDTO | null>(null);
  const [releaseReason, setReleaseReason] = useState("");
  /* 고쳐 다시 승인 창의 문장 id별 본문. 창을 열 때 승인본 문장으로 채운다. */
  const [edits, setEdits] = useState<Record<string, string>>({});
  const openRevise = (r: QcHeldRowDTO) => {
    setEdits(Object.fromEntries((r.sentences ?? []).map((x) => [x.id, x.text])));
    setRevising(r);
  };
  /* 바뀐 문장만 보낸다. 서버도 같은 기준으로 바뀐 것이 없으면 거절한다(422 caption_unchanged). */
  const changedEdits = (revising?.sentences ?? [])
    .filter((x) => (edits[x.id] ?? x.text).trim() !== x.text.trim())
    .map((x) => ({ id: x.id, text: (edits[x.id] ?? "").trim() }));
  const blankEdit = changedEdits.some((x) => !x.text);

  /* 세 동작은 호출을 나눠 둔다(`api.ts`). 주소를 조립하면 라우트를 세는 게이트가 읽지 못한다. */
  const holdAct = useMutation({
    mutationFn: ({ kind, row }: { kind: "release" | "recheck" | "revise"; row: QcHeldRowDTO }) =>
      kind === "release"
        ? releaseQcHold(pid, row.work_item_id, releaseReason.trim())
        : kind === "revise"
          ? reviseQcHold(pid, row.work_item_id, changedEdits)
          : recheckQcHold(pid, row.work_item_id),
    onSuccess: (r, { kind }) => {
      setReleasing(null);
      setRechecking(null);
      setRevising(null);
      setReleaseReason("");
      // 처리한 건은 목록에서 빠지므로 드로어의 값은 곧 오래된 값이 된다. 그래서 드로어도 함께 닫는다.
      setDetail(null);
      if (kind === "revise") {
        // 게이트가 `pending`으로 돌아가고 승인 검사가 다시 돈다. 결과가 나오기 전이므로 풀렸다고 표시하지 않는다.
        toast("success", "캡션을 고쳐 다시 승인했습니다", "승인 검사가 끝나면 보류 여부가 정해집니다.");
      } else if (kind === "release") {
        toast("success", "보류를 해제했습니다", "납품 대상에 들어갑니다.");
      } else if (r.model_failed) {
        // 서버는 AI 검사를 부르지 못하면 규칙만으로 풀지 않고 기록도 남기지 않는다(`hold.recheck`).
        toast("warning", "AI 검사를 실행하지 못했습니다", "보류는 그대로입니다. 모델 연결을 확인한 뒤 다시 검사해주세요.");
      } else if (r.qc_status === "held") {
        // 다시 검사의 결과는 통과이거나 다시 보류다.
        toast("info", "다시 검사했습니다", "같은 문제가 남아 보류가 유지됩니다.");
      } else {
        toast("success", "다시 검사에서 통과했습니다", "납품 대상에 들어갑니다.");
      }
      void held.refetch();
    },
    onError: (e, { kind }) => {
      // 사전의 안내는 워크벤치 제출 조건을 가리킨다. 이 창에서는 이 창의 입력을 고치도록 안내한다.
      if (kind === "revise" && (e as Partial<ApiError>).detail === "caption_too_short") {
        toast("error", "캡션이 기준보다 짧습니다", "프로젝트의 캡션 최소 길이를 넘도록 고쳐주세요.");
        return;
      }
      const t = errorText(e);
      toast("error", t.message, t.hint);
    },
  });
  const openRelease = (r: QcHeldRowDTO) => { setReleaseReason(""); setReleasing(r); };

  const heldColumns: DataColumn<QcHeldRowDTO>[] = [
    {
      label: "대상",
      kind: "title",
      width: "13rem",
      sortable: false,
      shrink: true,
      cell: (r) => <TargetCell title={r.video_title} start={r.start_seconds} end={r.end_seconds} />,
    },
    {
      label: "판정",
      kind: "status",
      width: "8.5rem",
      sortable: false,
      // 판정 아래에 검사 방식을 적는다. 규칙 검사와 AI 검사는 지적의 신뢰도가 다르다.
      cell: (r) => (
        <div className="space-y-0.5 text-footnote">
          <Verdict value={r.verdict} />
          <p className="text-caption text-label-2">{methodOf(r.model)}</p>
        </div>
      ),
    },
    {
      label: "지적",
      sortable: false,
      cell: (r) => <HeldFindingsCell row={r} />,
    },
    {
      label: "검사 시각",
      kind: "date",
      width: "8rem",
      sortable: false,
      cell: (r) => (r.checked_at ? <When iso={r.checked_at} className="text-caption tabular-nums text-label-2" /> : <span className="text-caption text-label-3">—</span>),
    },
    /*
      처리 열이다. 이 화면은 관리자만 열고 서버도 같은 권한으로 받는다.
      두 버튼 모두 확인 창을 거친다. 버튼은 행 클릭(드로어)과 겹치는 위치라 전파를 멈춘다.
    */
    {
      label: "처리",
      width: "18.5rem",
      sortable: false,
      cell: (r: QcHeldRowDTO) => (
        <span className="inline-flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
          {(r.sentences ?? []).length > 0 && (
            <Button size="sm" variant="secondary" disabled={holdAct.isPending} onClick={() => openRevise(r)}>
              캡션 고치기
            </Button>
          )}
          <Button size="sm" variant="secondary" disabled={holdAct.isPending} onClick={() => setRechecking(r)}>
            다시 검사
          </Button>
          <Button size="sm" variant="secondary" disabled={holdAct.isPending} onClick={() => openRelease(r)}>
            보류 해제
          </Button>
        </span>
      ),
    },
  ];

  /** `더 보기` 줄. 불러온 수와 전수를 왼쪽에, 버튼을 오른쪽에 둔다. 조회 상한을 숨기지 않는다. */
  const moreFooter = (shown: number, total: number | undefined, hasNext: boolean, loading: boolean, onMore: () => void) =>
    hasNext ? (
      <div className="flex w-full items-center justify-between gap-3">
        <span className="text-footnote tabular-nums text-label-2">
          불러온 {shown.toLocaleString("ko-KR")}건 / 전체 {(total ?? shown).toLocaleString("ko-KR")}건
        </span>
        <Button variant="secondary" size="sm" disabled={loading} onClick={onMore}>
          {loading ? "불러오는 중…" : "더 보기"}
        </Button>
      </div>
    ) : undefined;


  return (
    <div className="space-y-4">
      <PageHeader title="캡션 품질검수" />

      {held.isError && held.data == null ? (
        <ErrorState message="보류 목록 조회 실패" onRetry={() => void held.refetch()} />
      ) : (
        <TableCard<QcHeldRowDTO>
          // 표 머리 한 줄이 보류의 뜻과 분모를 함께 보여 준다.
          title="납품에서 빠진 승인 작업"
          count={heldTotal != null ? `${heldTotal.toLocaleString("ko-KR")}건` : ""}
          columns={heldColumns}
          rows={heldRows}
          rowKey={(r) => r.work_item_id}
          loading={held.isLoading || !pid}
          density="comfortable"
          onRowClick={(r) => setDetail(r)}
          pageSize={0}
          footer={moreFooter(heldRows.length, heldTotal, !!held.hasNextPage, held.isFetchingNextPage, () => void held.fetchNextPage())}
          empty={
            /* QC 꺼짐과 켜짐을 구분한다. 켜져 있어도 "모두 통과했다"고 쓰지 않는다. */
            qcEnabled === false ? (
              <QcOffState slug={slug} />
            ) : (
              <EmptyState message="보류 작업 없음" />
            )
          }
        />
      )}

      {/*
        한 건의 전체 내용. 제목은 대상 제목이다. 서랍의 대상은 열린 행으로 알 수 있다.
        판정 버튼(오른쪽)과 이동 링크(왼쪽)는 바닥 고정 줄에서 나눈다.
        검수 화면 링크는 그 화면을 열 수 있는 관리자에게만 그린다.
      */}
      <Drawer
        open={!!detail}
        onClose={() => setDetail(null)}
        title={detail ? (detail.video_title ?? "영상") : undefined}
        className="max-w-[520px]"
        footer={
          detail && (
            <div className="flex w-full flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-footnote">
                {/* 보류 건은 이미 승인되어 검수 화면이 읽기 전용이다. 링크 이름은 목적지에서 할 수 있는 일로 쓴다. */}
                <NavLink to={`/review/${detail.work_item_id}`}>검수 내용</NavLink>
              </div>
              <div className="flex items-center gap-2">
                {(detail.sentences ?? []).length > 0 && (
                  <Button size="sm" variant="secondary" disabled={holdAct.isPending} onClick={() => openRevise(detail)}>
                    캡션 고치기
                  </Button>
                )}
                <Button size="sm" variant="secondary" disabled={holdAct.isPending} onClick={() => setRechecking(detail)}>
                  다시 검사
                </Button>
                <Button size="sm" variant="secondary" disabled={holdAct.isPending} onClick={() => openRelease(detail)}>
                  보류 해제
                </Button>
              </div>
            </div>
          )
        }
      >
        {detail && (
          <div className="space-y-5">
            <DetailFacts
              verdict={detail.verdict}
              model={detail.model}
              at={detail.checked_at}
              start={detail.start_seconds}
              end={detail.end_seconds}
              workItemId={detail.work_item_id}
            />
            {/* 마지막 다시 검사가 실패했으면 아래 지적과 판정이 그 전 검사 결과임을 먼저 밝힌다. */}
            {detail.recheck_failed_at && (
              <p className="rounded-card border border-amber-line bg-amber-soft px-3.5 py-2.5 text-footnote text-label">
                <b className="font-semibold">마지막 다시 검사는 AI 검사 실패로 끝났습니다.</b>
                <When iso={detail.recheck_failed_at} className="block text-caption tabular-nums text-label-2" />
                <span className="block text-label-2">
                  지적과 판정은 그 전 검사 결과입니다. 모델 연결을 확인한 뒤 다시 검사하면 지금 기준으로 다시 확인합니다.
                </span>
              </p>
            )}
            {isStaleCaptionHold(detail) && (
              <p className="rounded-card border border-amber-line bg-amber-soft px-3.5 py-2.5 text-footnote text-label">
                <b className="font-semibold">캡션을 읽지 못해 보류된 기록입니다.</b>
                <span className="block text-label-2">
                  문장 단위로 저장되지 않은 캡션이라 검사가 비어 있다고 판정했습니다. 다시 검사하면 현재 규칙으로 다시 확인합니다.
                </span>
              </p>
            )}
            <FindingSections findings={detail.findings} />
            {detail.caption_brief && (
              <section>
                <h3 className="text-caption font-semibold text-label-2">캡션</h3>
                <p className="mt-2 rounded-card bg-canvas-2 px-3.5 py-3 text-callout leading-relaxed text-label">
                  {detail.caption_brief}
                </p>
              </section>
            )}
            <StageSection stages={detail.stages} verdict={detail.verdict} />
            <TechInfo model={detail.model} />
          </div>
        )}
      </Drawer>

      {/*
        다시 검사 확인 창. 검사 기록을 남기고, AI 검사가 켜져 있으면 호출 비용이 든다.
        그래서 누르면 바로 실행하지 않고 일어날 일을 먼저 알린다.
      */}
      <Modal
        open={!!rechecking}
        onClose={() => setRechecking(null)}
        title="다시 검사"
        footer={
          rechecking && (
            <>
              <Button variant="secondary" size="sm" onClick={() => setRechecking(null)}>취소</Button>
              <Button
                size="sm"
                disabled={holdAct.isPending}
                onClick={() => void holdAct.mutate({ kind: "recheck", row: rechecking })}
              >
                <RefreshCw size={14} aria-hidden="true" />
                {holdAct.isPending ? "검사 중…" : "검사"}
              </Button>
            </>
          )
        }
      >
        {rechecking && (
          <div className="space-y-3">
            {/* 굵은 결과와 작은 안내로 나눈다. 결과 문장은 본문이고, 창 제목은 Modal의 h2다. */}
            <div className="text-body">
              <b className="block font-semibold text-label">현재 사전과 설정으로 다시 검사합니다.</b>
              <span className="block text-footnote text-label-2">통과하면 보류가 풀리고, 다시 걸리면 보류가 남습니다.</span>
            </div>
            <TargetBox row={rechecking}>
              {(() => {
                const f = leadFinding(rechecking.findings);
                return f ? (
                  <p className="flex flex-wrap items-center gap-2 text-footnote text-label-2">
                    <FindingChip f={f} sunken />
                    <span>{f.message}</span>
                    {isStaleCaptionHold(rechecking) && <StaleChip />}
                  </p>
                ) : null;
              })()}
            </TargetBox>
            <p className="text-footnote text-label-2">
              규칙 검사를 통과하면 AI 검사도 다시 부릅니다. AI 검사를 부르지 못하면 보류를 그대로 둡니다.
            </p>
          </div>
        )}
      </Modal>

      {/*
        캡션 고쳐 다시 승인 창. 관리자가 보류를 푸는 세 방법 중 첫째다. 개인정보처럼 캡션 자체가 틀린 보류를 고친다.
        문장마다 그 문장에 걸린 지적을 위에 두어 무엇을 고칠지 같은 창에서 본다.
        고친 본은 새 개정과 수정 후 승인으로 남고, 승인 검사가 다시 돌아 보류 여부를 정한다.
      */}
      <Modal
        open={!!revising}
        onClose={() => setRevising(null)}
        title="캡션 고쳐 다시 승인"
        footer={
          revising && (
            <>
              <Button variant="secondary" size="sm" onClick={() => setRevising(null)}>취소</Button>
              <Button
                size="sm"
                disabled={holdAct.isPending || changedEdits.length === 0 || blankEdit}
                onClick={() => void holdAct.mutate({ kind: "revise", row: revising })}
              >
                {holdAct.isPending ? "승인 중…" : "다시 승인"}
              </Button>
            </>
          )
        }
      >
        {revising && (
          <div className="space-y-4">
            <div className="text-body">
              <b className="block font-semibold text-label">고친 캡션으로 다시 승인합니다.</b>
              <span className="block text-footnote text-label-2">
                승인 검사가 다시 돌고, 통과하면 납품 대상에 들어갑니다. 고친 내용은 수정 후 승인으로 기록됩니다.
              </span>
            </div>
            <TargetBox row={revising} />
            <ol className="space-y-3">
              {(revising.sentences ?? []).map((x, i) => {
                const hits = (revising.findings ?? []).filter((f) => f.sentence_id === x.id);
                const value = edits[x.id] ?? x.text;
                return (
                  <li key={x.id} className="space-y-1.5">
                    <Label htmlFor={`qc-revise-${x.id}`}>문장 {i + 1}</Label>
                    {hits.length > 0 && <FindingRows findings={hits} />}
                    <textarea
                      id={`qc-revise-${x.id}`}
                      value={value}
                      onChange={(e) => setEdits((prev) => ({ ...prev, [x.id]: e.target.value }))}
                      rows={2}
                      className={cn(FIELD_BASE, "h-auto resize-none px-3 py-2")}
                    />
                  </li>
                );
              })}
            </ol>
            {blankEdit && <p className="text-caption text-bad-text">빈 문장은 저장할 수 없습니다.</p>}
          </div>
        )}
      </Modal>

      {/*
        보류 해제 확인 창. 납품 게이트에 예외를 만드는 판정이라 근거와 남는 기록을 함께 보여 준다.
        해제한 건은 보류 상태에서 벗어나 다시 보류할 수 없다(`hold._load`의 409 qc_not_held).
        삭제가 아닌 예외 적용이라 버튼은 먹색 채움이다.
        필수 표시는 `Label`이 한 번만 그린다. 입력란 옆에 `필수`를 또 붙이지 않는다.
      */}
      <Modal
        open={!!releasing}
        onClose={() => setReleasing(null)}
        title="보류 해제"
        footer={
          releasing && (
            <>
              <Button variant="secondary" size="sm" onClick={() => setReleasing(null)}>취소</Button>
              <Button
                size="sm"
                disabled={holdAct.isPending || !releaseReason.trim()}
                onClick={() => void holdAct.mutate({ kind: "release", row: releasing })}
              >
                {holdAct.isPending ? "해제 중…" : "해제"}
              </Button>
            </>
          )
        }
      >
        {releasing && (
          <div className="space-y-4">
            <div className="text-body">
              <b className="block font-semibold text-label">이 작업을 납품 대상에 넣습니다.</b>
              <span className="block text-footnote text-label-2">
                자동 검사 판정 대신 관리자 판단을 적용합니다. 해제한 작업은 다시 보류할 수 없습니다.
              </span>
            </div>
            <TargetBox row={releasing}>
              <p className="flex flex-wrap items-center gap-x-2 text-footnote text-label-2">
                <Verdict value={releasing.verdict} />
                <span>
                  {methodOf(releasing.model)}
                  {releasing.checked_at && <span className="text-caption"> · <When iso={releasing.checked_at} /></span>}
                </span>
              </p>
            </TargetBox>
            {/* 해제할 대상을 같은 창에서 보여 준다. 표에서 바로 열면 드로어를 거치지 않기 때문이다. */}
            <section>
              <h3 className="text-caption font-semibold text-label-2">
                넘기는 지적 <span className="tabular-nums">{(releasing.findings ?? []).length}</span>
              </h3>
              {(releasing.findings ?? []).length > 0 && <FindingRows findings={releasing.findings ?? []} />}
            </section>
            <div className="space-y-1.5">
              <Label htmlFor="qc-release-reason" required>해제 사유</Label>
              <textarea
                id="qc-release-reason"
                value={releaseReason}
                onChange={(e) => setReleaseReason(e.target.value)}
                rows={3}
                placeholder="예: 인용한 발언이라 비속어 사전 오탐입니다"
                aria-describedby="qc-release-hint"
                className={cn(FIELD_BASE, "h-auto resize-none px-3 py-2")}
                autoFocus
              />
              <p id="qc-release-hint" className="text-caption text-label-2">해제 사유는 넘기는 지적과 함께 기록됩니다.</p>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
