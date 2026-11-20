/**
 * 플랫폼 개요 화면(`/admin`). super_admin 전용이다.
 *
 * 최고관리자는 막힌 연동을 보려고 이 화면을 연다. 그래서 화면에는 `조치 필요` 카드 하나만 둔다.
 * 할 일이 없으면 조치 카드는 한 줄 요약으로 접히고 호박색이 빠진다.
 * 연동 상태는 서버의 15분 주기 점검 결과를 읽는다. 화면에는 점검을 직접 돌리는 버튼을 두지 않는다.
 * 하위 관리 화면으로 가는 링크는 사이드바에만 둔다. 같은 링크를 카드로 다시 늘어놓지 않는다.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { AccessDenied } from "@/shell/AccessDenied";
import { Card, CardHeader } from "@/shared/ui/Card";
import { ErrorState } from "@/shared/ui/ErrorState";
import { PageHeader } from "@/shared/ui/PageHeader";
import { Skeleton } from "@/shared/ui/Skeleton";
import type { DiagnosticsReport } from "@/shared/api/client";
import type { components } from "@reborn/contracts";
import { cn } from "@/shared/lib/cn";
import { relativeTime } from "@/shared/lib/time";
import { useAuth } from "@/store/auth";

import {
  fetchConnectionsHealth,
  fetchLastDiagnostics,
  platformKeys,
} from "../api";
import { diagnosticsSummary } from "../components/DiagnosticsList";
import {
  connectionHealthLine,
  type ConnectionHealth,
} from "../lib/connection_health";
import { analysisOffText } from "../lib/analysis_off";
import { PROVIDER_LABEL, providerGroupLabel } from "../lib/connection_provider";

type ConnectionProblem = components["schemas"]["ConnectionProblem"];
type DiagnosticCheck = DiagnosticsReport["checks"][number];

/** 연동 문제의 판정 낱말과 점 색. 실패·주의만 이 목록에 온다. */
const PROBLEM_TONE = {
  fail: { word: "실패", dot: "bg-bad", text: "text-bad-text" },
  warn: { word: "주의", dot: "bg-warn", text: "text-warn-text" },
} as const;

/** 개요 한 화면에 모으는 연동 문제 한 줄. 플랫폼 환경 점검과 등록한 연결을 같은 모양으로 그린다. */
type Problem = {
  key: string;
  status: "fail" | "warn";
  name: string;
  source: string;
  detail: string | null | undefined;
  note: string | null | undefined;
  href?: string;
};

/** 플랫폼 환경 점검 항목과 등록한 연결을 한 목록으로 모은다. 실패가 주의보다 먼저 온다. */
function collectProblems(report: DiagnosticsReport | null, conns: ConnectionProblem[]): Problem[] {
  const fromChecks: Problem[] = (report?.checks ?? [])
    .filter((c: DiagnosticCheck) => c.status === "fail" || c.status === "warn")
    // 분석 연동이 꺼진 상태는 점검마다 문장이 달라 한 문구로 맞춘다(`lib/analysis_off`).
    .map(analysisOffText)
    .map((c) => ({
      key: `check-${c.key}`,
      status: c.status as "fail" | "warn",
      name: c.label,
      source: "연동 점검 · 플랫폼 환경",
      detail: c.detail,
      note: c.hint,
    }));
  const fromConns: Problem[] = conns.map((p) => ({
    key: `conn-${p.id}`,
    status: p.status === "fail" ? "fail" : "warn",
    name: p.name,
    source: `${providerGroupLabel(p.provider)} · ${PROVIDER_LABEL[p.provider]}`,
    detail: p.detail,
    note: p.used_by?.length ? `사용처 ${p.used_by.length}곳: ${p.used_by.join(", ")}` : null,
    href: "/admin/connections",
  }));
  const rank = { fail: 0, warn: 1 };
  return [...fromChecks, ...fromConns].sort((a, b) => rank[a.status] - rank[b.status]);
}

/** 플랫폼 개요 화면을 그린다. super_admin이 아니면 접근 거부를 보여 준다. */
export function AdminGlobalPage() {
  const user = useAuth((s) => s.user);
  const isSuper = user?.global_role === "super_admin";
  /*
    마지막 점검 결과는 서버가 남긴 것을 읽는다(`/admin/diagnostics/last`).
    외부를 호출하지 않으므로 화면을 열 때 불러도 된다. 주기 점검(15분)이 남긴 결과가 늘 이 응답에 있다.
  */
  const lastDiag = useQuery({
    queryKey: platformKeys.diagnosticsLast(),
    queryFn: fetchLastDiagnostics,
    enabled: isSuper,
    refetchInterval: 60_000,
  });
  // 연결 행에 남은 마지막 점검 결과를 세고 문제 연결을 이름으로 받는다. 외부를 호출하지 않는다.
  const connHealth = useQuery({
    queryKey: platformKeys.connectionsHealth(),
    queryFn: fetchConnectionsHealth,
    enabled: isSuper,
    refetchInterval: 60_000,
  });
  // 라우트 가드(`RoleGuard`)가 이미 막으므로 이 분기에 오는 것은 세션 중에 역할이 바뀐 경우뿐이다.
  if (!isSuper) return <AccessDenied reason="global" />;

  const report = lastDiag.data ?? null;
  // 점검 시각은 서버가 준 값을 쓴다. 수신 시각은 새로고침하면 사라지고 다른 사람이 한 점검의 시각을 보여 주지 못한다.
  const ranAt = report?.checked_at ?? null;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="플랫폼 개요" />

      <ActionCard
        report={report}
        diagLoading={lastDiag.isLoading}
        diagError={lastDiag.isError}
        onDiagRetry={() => void lastDiag.refetch()}
        conn={connHealth.data}
        connError={connHealth.isError}
        onConnRetry={() => void connHealth.refetch()}
        ranAt={ranAt}
      />
    </div>
  );
}

/**
 * `조치 필요` 카드를 그린다. 실패·주의인 연동을 모은다.
 *
 * 비면 한 줄 요약으로 접고 호박색을 쓰지 않는다. 호박색이 보이면 할 일이 있다는 뜻이다.
 * 조회가 실패한 영역은 "없음"으로 그리지 않고 실패로 그린다.
 */
function ActionCard({
  report, diagLoading, diagError, onDiagRetry, conn, connError, onConnRetry, ranAt,
}: {
  report: DiagnosticsReport | null;
  diagLoading: boolean;
  diagError: boolean;
  onDiagRetry: () => void;
  conn: ConnectionHealth | undefined;
  connError: boolean;
  onConnRetry: () => void;
  ranAt: string | null;
}) {
  const problems = collectProblems(report, conn?.problems ?? []);
  const connLine = conn ? connectionHealthLine(conn) : null;
  const diagLine = report ? diagnosticsSummary(report) : "기록 없음";
  const loading = (conn === undefined && !connError) || diagLoading;

  /* 상대 시간으로 쓴다. 점검이 멈췄는지(워커가 멈췄는지)는 이 값이 커지는 것으로 드러난다. */
  const checkedAt = ranAt
    ? <span className="shrink-0 text-caption tabular-nums text-label-3">{relativeTime(ranAt)} 자동 점검</span>
    : null;

  if (loading) {
    return (
      <Card>
        <div className="px-5 py-4" role="status" aria-label="조치 필요 항목 불러오는 중">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="mt-3 h-12 w-full" />
        </div>
      </Card>
    );
  }

  const clear = !connError && !diagError && problems.length === 0;

  if (clear) {
    // 0도 분모와 함께 적는다. 조회 실패와 "없음"이 구별된다.
    const facts = [
      { ok: !!report, text: `연동 점검 ${diagLine}` },
      { ok: !!connLine, text: `자격증명 연결 ${connLine?.text ?? "—"}` },
    ];
    return (
      <Card className="flex flex-wrap items-center gap-x-6 gap-y-2 px-5 py-4">
        <h2 className="text-headline text-label">처리할 항목 없음</h2>
        <ul className="flex flex-wrap items-center gap-x-5 gap-y-1">
          {facts.map((f) => (
            <li key={f.text} className="flex items-center gap-1.5 text-footnote text-label-2">
              <span aria-hidden="true" className={cn("size-1.5 rounded-capsule", f.ok ? "bg-ok" : "bg-label-4")} />
              {f.text}
            </li>
          ))}
        </ul>
        {checkedAt && <span className="ml-auto">{checkedAt}</span>}
      </Card>
    );
  }

  return (
    <Card as="section" aria-labelledby="action-needed">
      <CardHeader className="px-5 py-4 flex items-center gap-2.5">
        <span aria-hidden="true" className="size-2 rounded-capsule bg-amber" />
        <h2 id="action-needed" className="text-headline text-label">조치 필요</h2>
      </CardHeader>
      <div>
        {/* 연동 문제 */}
        <div className="min-w-0 px-5 py-4">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <h3 className="text-body font-semibold text-label">연동 문제</h3>
            <span className="text-body font-semibold tabular-nums text-label">
              {problems.length.toLocaleString("ko-KR")}
              <span className="ml-0.5 text-caption font-normal text-label-3">건</span>
            </span>
            {checkedAt && <span className="ml-auto">{checkedAt}</span>}
          </div>
          {diagError && <ErrorState inline message="연동 점검 결과 조회 실패" onRetry={onDiagRetry} />}
          {connError && <ErrorState inline message="연결 점검 결과 조회 실패" onRetry={onConnRetry} />}
          {problems.length === 0 && !diagError && !connError ? (
            <p className="text-footnote text-label-3">문제 없음</p>
          ) : (
            <ul className="divide-y divide-separator">
              {problems.map((p) => {
                const t = PROBLEM_TONE[p.status];
                return (
                  <li key={p.key} className="flex gap-4 py-3 first:pt-0 last:pb-0">
                    <span className={cn("flex w-12 shrink-0 items-center gap-1.5 self-start text-footnote font-semibold", t.text)}>
                      <span aria-hidden="true" className={cn("size-1.5 rounded-capsule", t.dot)} />
                      {t.word}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="flex flex-wrap items-baseline gap-x-2">
                        <span className="text-body font-semibold text-label">{p.name}</span>
                        <span className="text-caption text-label-3">{p.source}</span>
                      </p>
                      {p.detail && <p className="mt-0.5 text-footnote text-label">{p.detail}</p>}
                      {p.note && <p className="mt-0.5 text-caption text-label-3">{p.note}</p>}
                    </div>
                    {p.href && (
                      <Link to={p.href} className="shrink-0 self-start text-footnote font-medium text-label underline underline-offset-4 hover:text-label-2">
                        연결 열기
                      </Link>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
      {/*
        정상 항목은 목록에 올리지 않고 분모만 남긴다. 세는 대상이 다른 두 점검을 따로 적는다.
        연동 점검은 플랫폼 환경과 등록한 저장소·분석 연결을 보고, 자격증명 연결은 등록한 연결의 점검 결과다.
      */}
      <p className="flex flex-wrap gap-x-6 gap-y-1 border-t border-separator bg-canvas-2 px-5 py-2.5 text-caption text-label-3">
        <span>연동 점검 {diagLine}</span>
        {connLine && (
          <span>
            <Link to="/admin/connections" className="hover:text-label-2 hover:underline">자격증명 연결</Link>{" "}
            {connLine.text}
          </span>
        )}
      </p>
    </Card>
  );
}
